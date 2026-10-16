"""Bounded, sample-based archive estimates. Source files are opened read-only."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import lzma
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time

from .media import Cancelled


MAX_SAMPLE_BYTES = 24 * 1024 * 1024
MAX_FILE_SAMPLE_BYTES = 512 * 1024
MAX_SAMPLE_FILES = 64
MAX_SCAN_ENTRIES = 20_000
MAX_SECONDS = 120
DICTIONARY_BYTES = 8 * 1024 * 1024
TEMP_ROOT = Path(__file__).resolve().parent.parent / ".local" / "compression"


def _check(cancel, deadline):
    if cancel is not None and cancel.is_set():
        raise Cancelled("Compression estimate cancelled.")
    if time.monotonic() > deadline:
        raise RuntimeError("Compression estimate exceeded its two-minute time limit. Choose a smaller folder.")


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _regular_path(path):
    # Check ancestors too: resolving a junction first would hide the alias.
    for ancestor in (path, *path.parents):
        if _linked(ancestor.lstat()):
            raise ValueError("Choose a regular file or folder, without symlinks or junctions in its path.")
    return path.lstat()


def _scan(path, cancel, deadline):
    info = _regular_path(path)
    if path == TEMP_ROOT or TEMP_ROOT in path.parents:
        raise ValueError("The compression sample directory cannot be used as a source.")
    files, directories, skipped = [], [], 0
    if stat.S_ISREG(info.st_mode):
        return [(path, info)], directories, skipped
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("Choose a regular file or folder.")
    pending = [(path, info)]
    entries = 0
    while pending:
        _check(cancel, deadline)
        folder, snapshot = pending.pop()
        if _linked(folder.lstat()) or _identity(folder.lstat()) != _identity(snapshot):
            raise RuntimeError(f"Source folder changed during the estimate: {folder.name}")
        directories.append((folder, snapshot))
        with os.scandir(folder) as iterator:
            for entry in iterator:
                _check(cancel, deadline)
                entries += 1
                if entries > MAX_SCAN_ENTRIES:
                    raise ValueError(f"The folder exceeds the {MAX_SCAN_ENTRIES:,}-entry scan limit. Choose a smaller folder.")
                child = Path(entry.path)
                # Windows DirEntry.stat() can report zero file IDs, unlike lstat/fstat.
                child_info = child.lstat()
                if _linked(child_info) or child == TEMP_ROOT:
                    skipped += 1
                elif stat.S_ISDIR(child_info.st_mode):
                    pending.append((child, child_info))
                elif stat.S_ISREG(child_info.st_mode):
                    files.append((child, child_info))
                else:
                    raise ValueError(f"The source contains a non-regular file: {child.name}")
    return files, directories, skipped


def _sample(path, snapshot, budget, cancel, deadline):
    _check(cancel, deadline)
    current = _regular_path(path)
    if _identity(current) != _identity(snapshot):
        raise RuntimeError(f"Source file changed during the estimate: {path.name}")
    count = min(snapshot.st_size, budget)
    if count == snapshot.st_size:
        ranges = [(0, count)]
    else:
        part = count // 3
        ranges = [(0, part), ((snapshot.st_size - part) // 2, part), (snapshot.st_size - (count - 2 * part), count - 2 * part)]
    blocks = []
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as source:
        if _identity(os.fstat(source.fileno())) != _identity(snapshot):
            raise RuntimeError(f"Source file changed during the estimate: {path.name}")
        for offset, length in ranges:
            source.seek(offset)
            while length:
                _check(cancel, deadline)
                block = source.read(min(length, 64 * 1024))
                if not block:
                    raise RuntimeError(f"Source file changed during the estimate: {path.name}")
                blocks.append(block)
                length -= len(block)
        if _identity(os.fstat(source.fileno())) != _identity(snapshot):
            raise RuntimeError(f"Source file changed during the estimate: {path.name}")
    if _identity(_regular_path(path)) != _identity(snapshot):
        raise RuntimeError(f"Source file changed during the estimate: {path.name}")
    return b"".join(blocks)


def _compressed_size(data, engine, executable, folder, cancel, deadline):
    if engine == "lzma":
        # Independent samples omit solid-archive gains; a full archive is needed to measure them.
        compressor = lzma.LZMACompressor(filters=[{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME, "dict_size": min(DICTIONARY_BYTES, max(4096, len(data)))}])
        size = 0
        for offset in range(0, len(data), 64 * 1024):
            _check(cancel, deadline)
            size += len(compressor.compress(data[offset:offset + 64 * 1024]))
        _check(cancel, deadline)
        return size + len(compressor.flush())
    sample = folder / "sample.bin"
    archive = folder / ("sample.7z" if engine == "7z" else "sample.rar")
    sample.write_bytes(data)
    if engine == "7z":
        args = [executable, "a", "-t7z", "-m0=LZMA2", "-mx=9", "-mmt=1", "-md=8m", "-ms=off", "-y", "-bd", "-bb0", archive.name, sample.name]
    else:
        # -cfg- prevents user defaults from adding destructive switches such as -df.
        args = [executable, "a", "-cfg-", "-m5", "-md8m", "-mt1", "-s-", "-y", "-idq", "-ep", archive.name, sample.name]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    try:
        with subprocess.Popen(args, cwd=folder, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags) as process:
            try:
                while process.poll() is None:
                    _check(cancel, deadline)
                    try:
                        process.wait(timeout=0.1)
                    except subprocess.TimeoutExpired:
                        pass
                _check(cancel, deadline)
                if process.returncode:
                    raise RuntimeError(f"{engine} sample compression failed (exit code {process.returncode}). Check the installed command-line tool.")
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
        return archive.stat().st_size
    finally:
        sample.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)


def estimate(path: Path, engine: str = "lzma", tool_paths: dict | None = None, update=None, cancel=None) -> dict:
    """Estimate separate-file compression, bounded to 24 MiB and 20,000 entries.

    Engines use their highest compression level with an 8 MiB dictionary ceiling.
    Whole-file archive size, larger dictionaries and solid similarity remain unknown.
    Raises on inaccessible or visibly changed sources, missing tools, timeout or cancellation.
    """
    engine = "winrar" if engine == "rar" else engine
    if engine not in {"lzma", "7z", "winrar"}:
        raise ValueError("Choose lzma, 7z or winrar compression.")
    executable = (tool_paths or {}).get("rar" if engine == "winrar" else engine)
    if engine != "lzma":
        if not executable or not Path(executable).is_file():
            raise ValueError(f"The {engine} command-line tool is not installed.")
        if engine == "winrar" and Path(executable).name.lower() not in {"rar.exe", "rar"}:
            raise ValueError("WinRAR estimates require the Rar.exe command-line tool.")
        executable = str(Path(executable).absolute())
    report = update or (lambda **changes: None)
    deadline = time.monotonic() + MAX_SECONDS
    path = Path(os.path.abspath(path))
    _check(cancel, deadline)
    existing = TEMP_ROOT
    while not existing.exists():
        existing = existing.parent
    _regular_path(existing)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    _regular_path(TEMP_ROOT)
    report(progress=0, message="Scanning source file sizes")
    files, directories, skipped = _scan(path, cancel, deadline)
    groups = defaultdict(list)
    total = sum(info.st_size for _, info in files)
    for file, info in files:
        if info.st_size:
            band = 0 if info.st_size <= 64 * 1024 else 1 if info.st_size <= 1024 * 1024 else 2
            groups[(file.suffix.lower(), band)].append((file, info))
    ordered = sorted(groups.values(), key=lambda group: sum(info.st_size for _, info in group), reverse=True)
    for group in ordered:
        group.sort(key=lambda item: hashlib.blake2b(os.fsencode(item[0]), digest_size=8).digest())
        largest = max(range(len(group)), key=lambda index: group[index][1].st_size)
        group.insert(0, group.pop(largest))
    sampled_bytes, sampled_files, measured = 0, 0, {}
    report(progress=10, message=f"Testing bounded samples from {len(files):,} files")
    with tempfile.TemporaryDirectory(prefix="estimate-", dir=TEMP_ROOT) as temporary:
        folder = Path(temporary)
        for round_index in range(MAX_SAMPLE_FILES):
            for group_index, group in enumerate(ordered):
                if round_index >= len(group):
                    continue
                if sampled_bytes >= MAX_SAMPLE_BYTES or sampled_files >= MAX_SAMPLE_FILES:
                    break
                file, info = group[round_index]
                budget = min(MAX_FILE_SAMPLE_BYTES, MAX_SAMPLE_BYTES - sampled_bytes)
                data = _sample(file, info, budget, cancel, deadline)
                compressed = _compressed_size(data, engine, executable, folder, cancel, deadline)
                measured.setdefault(group_index, []).append((info.st_size, compressed / len(data)))
                sampled_bytes += len(data)
                sampled_files += 1
                report(progress=round(10 + 80 * max(sampled_bytes / MAX_SAMPLE_BYTES, sampled_files / MAX_SAMPLE_FILES), 1), message=f"Tested {sampled_files:,} files; {sampled_bytes / 1024 / 1024:.1f} MiB sampled")
            if sampled_bytes >= MAX_SAMPLE_BYTES or sampled_files >= MAX_SAMPLE_FILES:
                break
    estimated = 0.0
    untested_bytes = 0
    for index, group in enumerate(ordered):
        size = sum(info.st_size for _, info in group)
        samples = measured.get(index, [])
        if samples:
            estimated += size * sum(weight * ratio for weight, ratio in samples) / sum(weight for weight, _ in samples)
        else:
            estimated += size
            untested_bytes += size
    report(progress=95, message="Checking that source files remained unchanged")
    for source, snapshot in files + directories:
        _check(cancel, deadline)
        current = source.lstat()
        if _linked(current) or _identity(current) != _identity(snapshot):
            raise RuntimeError(f"Source changed during the estimate: {source.name}")
    estimated_bytes = round(estimated)
    saved_bytes = max(0, total - estimated_bytes)
    method = {"lzma": "LZMA2 level 9 extreme", "7z": "7-Zip Ultra (level 9)", "winrar": "RAR Best (level 5)"}[engine]
    note = f"Sample estimate, not a full archive: at most 24 MiB, 512 KiB per file and 64 files; {MAX_SCAN_ENTRIES:,}-entry scan limit. {method}, a one-thread setting and an 8 MiB dictionary ceiling; full maximum settings may compress differently. Beginning, middle and end samples are weighted by file size within file-type/size groups. Solid-archive similarity and final archive metadata are not measured. Originals are never modified."
    if not total:
        note += " No nonempty file data; an empty archive still has metadata overhead."
    if skipped:
        note += f" Excluded {skipped:,} symlinks, junctions or app sample folders."
    if untested_bytes:
        note += f" {untested_bytes:,} bytes in unsampled groups were counted without savings."
    report(progress=100, message="Compression estimate complete")
    return {"total_bytes": total, "estimated_bytes": estimated_bytes, "saved_bytes": saved_bytes, "saved_percent": round(saved_bytes / total * 100, 1) if total else 0, "sampled_bytes": sampled_bytes, "file_count": len(files), "sampled_files": sampled_files, "engine": engine, "method": method, "note": note, "untested_bytes": untested_bytes, "skipped_entries": skipped, "sample_limit_bytes": MAX_SAMPLE_BYTES}
