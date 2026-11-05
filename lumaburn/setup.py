"""Prepare missing Windows media tools locally using hash-pinned downloads."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
from urllib.request import Request, urlopen
from zipfile import ZipFile

from . import media

# Gyan's release build is linked from https://ffmpeg.org/download.html.
# Checksum: https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip.sha256
FFMPEG = (
    "ffmpeg-9.0.2-essentials_build.zip",
    "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip",
    "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba",
)
DVDSTYLER = (
    "DVDStyler-3.2.1-win64.exe",
    "https://sourceforge.net/projects/dvdstyler/files/dvdstyler/3.2.1/DVDStyler-3.2.1-win64.exe/download",
    "99b74055e9b5cceae29b153158563e454905531c966f3cdbe98ea390a6a0e713",
)
INNOEXTRACT = (
    "innoextract-1.9-windows.zip",
    "https://github.com/dscharrer/innoextract/releases/download/1.9/innoextract-1.9-windows.zip",
    "6989342c9b026a00a72a38f23b62a8e6a22cc5de69805cf47d68ac2fec993065",
)
REQUIRED = ("ffmpeg", "ffprobe", "dvdauthor")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(package, cache: Path, update) -> Path:
    name, url, checksum = package
    target = cache / name
    if target.is_file() and digest(target) == checksum:
        return target
    partial = target.with_suffix(target.suffix + ".part")
    update(message=f"Downloading {name}…", progress=None)
    try:
        request = Request(url, headers={"User-Agent": "LumaBurn/0.1"})
        with urlopen(request, timeout=30) as response, partial.open("wb") as output:
            total = int(response.headers.get("Content-Length", 0))
            received = 0
            while block := response.read(1024 * 1024):
                output.write(block)
                received += len(block)
                update(progress=min(99, int(received * 100 / total)) if total else None)
        update(message=f"Verifying {name}…", progress=None)
        if digest(partial) != checksum:
            raise RuntimeError(f"Download verification failed for {name}. Retry setup to download it again.")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def extract_zip(archive: Path, destination: Path):
    root = destination.resolve()
    with ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            if not (root / entry.filename).resolve().is_relative_to(root):
                raise ValueError("The tool archive contains an unsafe path.")
        bundle.extractall(root)


def publish(source: Path, destination: Path, executables):
    if any(not (source / "bin" / (name + ".exe")).is_file() for name in executables):
        raise RuntimeError("The downloaded package is missing its media tools.")
    destination.mkdir(parents=True, exist_ok=True)
    ready = destination / ".ready"
    ready.unlink(missing_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True)
    # Detection ignores an interrupted installation until every file has been copied.
    ready.write_text("ready\n", encoding="ascii")


def install(update):
    missing = [name for name in REQUIRED if not media.tool_path(name)]
    if not missing:
        return
    if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("Automatic tool setup requires 64-bit Intel/AMD Windows. See Help for custom tool paths.")
    cache = media.ROOT / ".local" / "dependencies"
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="setup-", dir=cache) as temporary:
        staging = Path(temporary)
        if "ffmpeg" in missing or "ffprobe" in missing:
            archive = download(FFMPEG, cache, update)
            update(message="Preparing FFmpeg and FFprobe…", progress=None)
            extract_zip(archive, staging / "ffmpeg")
            publish(staging / "ffmpeg" / "ffmpeg-9.0.2-essentials_build", media.ROOT / "tools" / "ffmpeg", ("ffmpeg", "ffprobe"))
        if "dvdauthor" in missing:
            installer = download(DVDSTYLER, cache, update)
            extractor = download(INNOEXTRACT, cache, update)
            update(message="Preparing DVD authoring tools…", progress=None)
            extract_zip(extractor, staging / "innoextract")
            result = subprocess.run(
                [str(staging / "innoextract" / "innoextract.exe"), "--silent", "--extract", "--output-dir", str(staging / "dvdstyler"), str(installer)],
                capture_output=True, text=True, errors="replace", timeout=180,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode:
                raise RuntimeError("Could not prepare the DVD authoring tools. Retry setup or see Help.")
            publish(staging / "dvdstyler" / "app", media.ROOT / "tools" / "authoring", ("dvdauthor",))
    if any(not media.tool_path(name) for name in REQUIRED):
        raise RuntimeError("Some media tools are still missing. Retry setup or check custom paths in Help.")
