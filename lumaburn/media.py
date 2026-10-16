"""LumaBurn's media planner and encoder. Only the Python standard library."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
DVD_BYTES = 4_700_000_000
PRESETS = [
    {"id": "dvd-video", "name": "DVD player", "target": "disc", "description": "A real video DVD for standalone players.", "details": "MPEG-2 · AC-3 · DVD-Video · fit to disc"},
    {"id": "data-disc", "name": "Data DVD", "target": "disc", "description": "Keep your original files on a disc.", "details": "Original quality · computer playback"},
    {"id": "usb-compatible", "name": "USB / TV", "target": "folder", "description": "Broadly compatible video and audio files.", "details": "MP4 · H.264 · AAC · up to 1080p"},
    {"id": "usb-original", "name": "Original files", "target": "folder", "description": "A verified copy with no quality loss.", "details": "All file types · SHA-256 read-back"},
    {"id": "mp3", "name": "MP3 audio", "target": "folder", "description": "Music or audio extracted from video.", "details": "LAME V2 · variable bitrate · ID3 tags"},
]


def tool_path(name: str) -> str | None:
    override = os.environ.get(f"LUMABURN_{name.upper()}")
    if override and Path(override).is_file():
        return str(Path(override).resolve())
    local = ROOT / "tools" / (name + ".exe")
    if local.is_file():
        return str(local)
    found = shutil.which(name)
    if found:
        return found
    if name in {"ffmpeg", "ffprobe", "dvdauthor"}:
        bundle = ROOT / "tools" / ("authoring" if name == "dvdauthor" else "ffmpeg")
        candidate = bundle / "bin" / (name + ".exe")
        if (bundle / ".ready").is_file() and candidate.is_file():
            return str(candidate)
    if name == "dvdauthor":
        bundled = ROOT / "tools" / "DVDStyler" / "bin" / "dvdauthor.exe"
        if bundled.is_file():
            return str(bundled)
        extracted = ROOT / "tools" / "DVDStyler" / "app" / "bin" / "dvdauthor.exe"
        if extracted.is_file():
            return str(extracted)
        for base in [os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")]:
            candidate = Path(base) / "DVDStyler" / "bin" / "dvdauthor.exe"
            if candidate.is_file():
                return str(candidate)
    if name.lower() in {"7z", "7zfm", "rar", "winrar"}:
        folder = "7-Zip" if name.lower().startswith("7z") else "WinRAR"
        for base in [os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")]:
            candidate = Path(base) / folder / (name + ".exe")
            if candidate.is_file():
                return str(candidate)
    return None


def tools_status() -> dict:
    return {name: bool(tool_path(name)) for name in ["ffmpeg", "ffprobe", "dvdauthor", "7z", "winrar"]}


def probe(path: Path) -> dict:
    result = {"name": path.name, "size": path.stat().st_size, "duration": 0, "video": False, "audio": False, "width": 0, "height": 0}
    executable = tool_path("ffprobe")
    if not executable:
        result["error"] = "FFprobe is missing. Install FFmpeg to inspect media."
        return result
    try:
        completed = subprocess.run([executable, "-v", "error", "-threads", "1", "-protocol_whitelist", "file,pipe,crypto,data", "-show_format", "-show_streams", "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45, creationflags=process_flags())
        if completed.returncode:
            result["error"] = "Not recognized as playable media. Original-file presets can still copy it."
            return result
        data = json.loads(completed.stdout)
        streams = data.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        duration = float(data.get("format", {}).get("duration", 0))
        result.update(duration=duration if math.isfinite(duration) and duration > 0 else 0, video=bool(video), audio=bool(audio))
        if video:
            result.update(width=int(video.get("width", 0)), height=int(video.get("height", 0)), color_transfer=video.get("color_transfer"), color_space=video.get("color_space"), video_codec=video.get("codec_name"), frame_rate=video.get("r_frame_rate"))
        if audio:
            result["audio_index"] = audio["index"]
            result.update(audio_codec=audio.get("codec_name"), sample_rate=audio.get("sample_rate"))
        if video:
            result["video_index"] = video["index"]
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result["error"] = f"Media inspection failed: {exc}"
    return result


def clean_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(" .")[:150]
    if not name or name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        name = "media_" + name
    return name


def make_plan(request: dict, files: list[dict], devices: dict) -> dict:
    preset = request.get("preset", "dvd-video")
    if preset not in {p["id"] for p in PRESETS}:
        raise ValueError("Unknown preset.")
    if request.get("standard", "ntsc") not in {"ntsc", "pal"}:
        raise ValueError("Choose NTSC or PAL.")
    if request.get("quality", "balanced") not in {"balanced", "compact"}:
        raise ValueError("Choose balanced or compact quality.")
    if request.get("performance", "gentle") not in {"gentle", "full"}:
        raise ValueError("Choose gentle or full performance.")
    if not isinstance(request.get("label", "LUMABURN"), str):
        raise ValueError("The disc label must be text.")
    errors, notes = [], []
    if request.get("action") == "burn" and preset not in {"dvd-video", "data-disc"}:
        errors.append("Burning requires the DVD player or Data DVD preset.")
    drive = next((d for d in devices.get("drives", []) if d["id"] == request.get("recorder_id")), None)
    capacity = int(drive.get("capacity_bytes") or DVD_BYTES) if drive else DVD_BYTES
    duration = sum(f["duration"] for f in files)
    estimated = sum(f["size"] for f in files)
    bitrate = None
    if not files:
        errors.append("Add at least one file.")
    if len(files) > 99 and preset == "dvd-video":
        errors.append("A video DVD supports at most 99 titles.")
    if preset in {"dvd-video", "usb-compatible", "mp3"}:
        if not all(tools_status()[n] for n in ["ffmpeg", "ffprobe"]):
            errors.append("FFmpeg and FFprobe are required for this preset.")
        if any(f.get("duration", 0) <= 0 for f in files):
            errors.append("Every file must have a readable, positive duration for conversion.")
        if preset == "dvd-video" and any(not f["video"] for f in files):
            errors.append("DVD player mode needs video files. Use MP3 audio or Data DVD for music.")
        if preset == "mp3" and any(not f["audio"] for f in files):
            errors.append("MP3 mode needs an audio stream in every file.")
        if preset == "usb-compatible" and any(not f["video"] and not f["audio"] for f in files):
            errors.append("USB / TV mode accepts video and audio. Use Original files for other data.")
        if any(f.get("color_transfer") in {"smpte2084", "arib-std-b67"} for f in files):
            notes.append("HDR video will be tone-mapped to SDR for older players.")
        notes.append("Conversion uses the first video and audio streams. Alternate tracks and subtitles are not carried over; keep originals to preserve them.")
    if preset == "dvd-video":
        if not tool_path("dvdauthor"):
            errors.append("DVD authoring tools are missing. Check automatic setup at the top of the app.")
        if duration > 0:
            bitrate = min(8_000_000, int(capacity * 0.92 * 8 / duration - 192_000))
            if bitrate < 1_000_000:
                errors.append("These videos are too long for a good-quality single DVD. Split them across discs.")
            estimated = int(duration * (max(bitrate, 1_000_000) + 192_000) / 8 / 0.96) + 8_000_000
        notes += ["Two-pass MPEG-2 fits the total playing time to the disc. DVD output is standard definition.", "The disc starts with the first title and plays titles in queue order. No menu or region restriction.", "DVD-RW support varies by standalone player. Choose the TV standard your player supports."]
    elif preset == "usb-compatible":
        # CRF sizes are content-dependent; measure actual output before copying.
        estimated = int(sum(f["duration"] * (4_000_000 if f["video"] else 192_000) / 8 for f in files))
        notes.append("Output size is an estimate. H.264/AAC support depends on your TV; USB-C describes the connector, not a media format.")
    elif preset == "mp3":
        estimated = int(duration * 190_000 / 8)
        notes.append("LAME V2 favors perceptual quality and size. Re-encoding existing lossy audio can reduce quality.")
    else:
        notes.append("Original files are copied without compression or quality loss and verified with SHA-256.")
    if preset in {"dvd-video", "data-disc"}:
        if estimated > capacity * 0.98:
            errors.append("The estimated output does not fit this disc.")
        if drive and not drive.get("blank"):
            notes.append("This disc already contains data. Export an image or insert a blank disc; LumaBurn will not erase it.")
        if request.get("action") == "burn":
            if not drive:
                errors.append("Select a connected DVD writer.")
            elif not drive.get("writable") or not drive.get("blank"):
                errors.append("Burning requires blank, supported, writable media. Existing discs are never erased.")
            elif preset == "dvd-video" and drive.get("physical_media_type") not in {5, 6, 7, 8, 9, 10, 11, 13}:
                errors.append("DVD player mode needs a blank DVD, rather than CD or Blu-ray media.")
    return {"estimated_bytes": estimated, "capacity_bytes": capacity if preset in {"dvd-video", "data-disc"} else None, "video_bitrate": bitrate, "notes": notes, "can_start": not errors, "errors": errors, "requires_authoring": preset == "dvd-video"}


def process_flags(gentle=True):
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) | (getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) if gentle else 0)


GENTLE_BYTES_PER_SECOND = 64 * 1024 * 1024


def io_pause(job, size, elapsed=0.0):
    # Cap gentle-mode copies/hashes near 64 MiB/s, but credit the time the operation
    # already took: a slow read/write/hash that exceeded the cap is not delayed again.
    # The wait stays interruptible through the job's cancellation event.
    if job and job.request.get("performance", "gentle") == "gentle":
        remaining = max(0.0, size / GENTLE_BYTES_PER_SECOND - max(0.0, elapsed))
        if job.cancel.wait(remaining):
            raise Cancelled("Cancelled before disc writing.")


def sha256(path: Path, job=None, start=None, span=0) -> str:
    digest = hashlib.sha256()
    read, size, last_update = 0, max(path.stat().st_size, 1), 0
    with path.open("rb") as source:
        while True:
            block_timer = time.monotonic()
            block = source.read(4 * 1024 * 1024)
            if not block:
                break
            if job and job.cancel.is_set():
                raise Cancelled("Cancelled before disc writing.")
            digest.update(block)
            read += len(block)
            io_pause(job, len(block), time.monotonic() - block_timer)
            if job and start is not None and (time.monotonic() - last_update >= 0.15 or read >= size):
                job.update(progress=round(start + read / size * span, 1), phase_progress=round(read / size * 100, 1))
                last_update = time.monotonic()
    return digest.hexdigest()


class Cancelled(Exception):
    pass


def run_process(args: list[str], job, duration: float = 0, start: float = 0, span: float = 1, env: dict | None = None):
    job.update(phase_progress=0 if duration > 0 else None)
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env, creationflags=process_flags(job.request.get("performance", "gentle") == "gentle")) as process:
        job.process = process
        try:
            for line in process.stdout:
                if job.cancel.is_set():
                    process.terminate()
                    raise Cancelled("Cancelled before disc writing.")
                line = line.strip()
                if line.startswith("out_time_us=") and duration > 0:
                    try:
                        value = min(float(line.split("=", 1)[1]) / 1_000_000 / duration, 1)
                        job.update(progress=round(start + value * span, 1), phase_progress=round(value * 100, 1))
                    except ValueError:
                        pass
                elif line and not re.match(r"^(frame|fps|stream_.*|bitrate|total_size|out_time.*|dup_frames|drop_frames|speed|progress)=", line):
                    job.log(line)
            if job.cancel.is_set():
                raise Cancelled("Cancelled before disc writing.")
            if process.wait():
                raise RuntimeError("The media tool failed. " + "\n".join(job.data["logs"][-6:]))
            if duration > 0:
                job.update(progress=round(start + span, 1), phase_progress=100)
        finally:
            job.process = None
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


def video_filter(file: dict, dvd: bool, standard: str) -> str:
    filters = []
    if file.get("color_transfer") in {"smpte2084", "arib-std-b67"}:
        filters += ["zscale=t=linear:npl=100", "format=gbrpf32le", "zscale=p=bt709", "tonemap=tonemap=mobius:desat=0", "zscale=t=bt709:m=bt709:r=limited", "format=yuv420p"]
    if dvd:
        height = 480 if standard == "ntsc" else 576
        square_width = 854 if standard == "ntsc" else 1024
        # A DVD's pixels are non-square. Compose at 16:9 square pixels, then encode anamorphic.
        filters += [f"scale={square_width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1:flags=lanczos", f"pad={square_width}:{height}:(ow-iw)/2:(oh-ih)/2", f"scale=720:{height}:flags=lanczos:out_color_matrix=bt601", "setsar=32/27" if standard == "ntsc" else "setsar=64/45"]
    else:
        filters += ["scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1:flags=lanczos", "format=yuv420p"]
    return ",".join(filters)


def encode_files(request: dict, files: list[dict], plan: dict, output: Path, job) -> Path:
    preset = request["preset"]
    destination = output / ("disc" if preset == "data-disc" else "media")
    destination.mkdir()
    total = max(sum(f.get("duration", 0) for f in files), 1)
    total_bytes = max(sum(f["size"] for f in files), 1)
    copied_bytes = 0
    elapsed = 0
    manifest = []
    for index, file in enumerate(files, 1):
        if job.cancel.is_set():
            raise Cancelled("Cancelled before disc writing.")
        source = Path(file["path"])
        if not source.is_file() or source.stat().st_size != file["size"]:
            raise RuntimeError(f"Source file changed or disappeared: {file['name']}")
        name = f"{index:02d}_" + clean_name(file["name"])
        job.update(phase="preparing", phase_progress=0, message=f"Preparing {index} of {len(files)}: {file['name']}")
        if preset in {"data-disc", "usb-original"}:
            target = destination / name
            digest = hashlib.sha256()
            file_bytes, last_update = 0, 0
            with source.open("rb") as incoming, target.open("xb") as outgoing:
                while True:
                    block_timer = time.monotonic()
                    block = incoming.read(4 * 1024 * 1024)
                    if not block:
                        break
                    if job.cancel.is_set():
                        raise Cancelled("Copy cancelled.")
                    outgoing.write(block)
                    digest.update(block)
                    file_bytes += len(block)
                    io_pause(job, len(block), time.monotonic() - block_timer)
                    if time.monotonic() - last_update >= 0.15 or file_bytes == file["size"]:
                        job.update(progress=round((copied_bytes + file_bytes) / (2 * total_bytes) * 75, 1), phase_progress=round(file_bytes / max(file["size"], 1) * 100, 1))
                        last_update = time.monotonic()
                outgoing.flush()
                os.fsync(outgoing.fileno())
            job.update(phase_progress=0, message=f"Verifying {index} of {len(files)}: {file['name']}")
            if sha256(target, job, (copied_bytes + file["size"]) / (2 * total_bytes) * 75, file["size"] / (2 * total_bytes) * 75) != digest.hexdigest():
                raise RuntimeError(f"Copy verification failed: {file['name']}")
            manifest.append({"file": target.name, "sha256": digest.hexdigest(), "bytes": target.stat().st_size})
            copied_bytes += file["size"] * 2
            job.update(progress=round(copied_bytes / (2 * total_bytes) * 75, 1))
            continue
        ffmpeg = tool_path("ffmpeg")
        threads = max(1, min(2, (os.cpu_count() or 1) // 4)) if request.get("performance", "gentle") == "gentle" else 0
        base = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-nostats", "-stats_period", "0.25", "-filter_threads", str(threads), "-filter_complex_threads", str(threads), "-threads", str(threads), "-protocol_whitelist", "file,pipe,crypto,data", "-i", str(source)]
        encoder_threads = ["-threads", str(threads)]
        if preset == "dvd-video":
            target = destination / (Path(name).stem + ".mpg")
            standard = request.get("standard", "ntsc")
            video_options = ["-map", f"0:{file['video_index']}", "-target", standard + "-dvd", "-vf", video_filter(file, True, standard), "-aspect", "16:9", "-b:v", str(plan["video_bitrate"]), "-maxrate:v", "9000000", "-minrate:v", "0", "-bufsize:v", "1835008", "-pix_fmt", "yuv420p", "-color_primaries", "smpte170m" if standard == "ntsc" else "bt470bg", "-color_trc", "bt709", "-colorspace", "smpte170m" if standard == "ntsc" else "bt470bg", "-passlogfile", str(output / f"pass-{index}")]
            start, span = elapsed / total * 70, file["duration"] / total * 70
            job.update(message=f"Analyzing video {index} of {len(files)} (pass 1 of 2)")
            run_process(base + video_options + encoder_threads + ["-pass", "1", "-an", "-progress", "pipe:1", "-f", "null", os.devnull], job, file["duration"], start, span / 2)
            second_base = base if file["audio"] else base + ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
            audio_options = ["-map", f"0:{file['audio_index']}" if file["audio"] else "1:a:0", "-c:a", "ac3", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-t", str(file["duration"])]
            job.update(message=f"Encoding video {index} of {len(files)} (pass 2 of 2)")
            run_process(second_base + video_options + ["-pass", "2"] + audio_options + encoder_threads + ["-progress", "pipe:1", str(target)], job, file["duration"], start + span / 2, span / 2)
        else:
            video = preset == "usb-compatible" and file["video"]
            extension = ".mp4" if video else ".mp3"
            target = destination / (Path(name).stem + extension)
            if video:
                options = ["-map", f"0:{file['video_index']}", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "medium", "-crf", "24" if request.get("quality") == "compact" else "20", "-vf", video_filter(file, False, "ntsc"), "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-movflags", "+faststart", "-map_metadata", "0"]
            else:
                options = ["-map", f"0:{file['audio_index']}", "-vn", "-c:a", "libmp3lame", "-q:a", "2", "-id3v2_version", "3", "-map_metadata", "0"]
            run_process(base + options + encoder_threads + ["-progress", "pipe:1", str(target)], job, file["duration"], elapsed / total * 75, file["duration"] / total * 75)
        checked = probe(target)
        if checked.get("error") or not checked.get("duration"):
            raise RuntimeError(f"Encoded file did not pass media inspection: {target.name}")
        manifest.append({"file": target.name, "sha256": sha256(target, job), "bytes": target.stat().st_size})
        elapsed += file["duration"]
    if preset == "dvd-video":
        disc = output / "disc"
        dvd = ET.Element("dvdauthor", {"dest": str(disc)})
        vmgm = ET.SubElement(dvd, "vmgm")
        menus = ET.SubElement(vmgm, "menus")
        ET.SubElement(menus, "video", {"format": request.get("standard", "ntsc")})
        # dvdauthor's default first-play chain jumps to the first title when there is no menu.
        titleset = ET.SubElement(dvd, "titleset")
        titles = ET.SubElement(titleset, "titles")
        ET.SubElement(titles, "video", {"format": request.get("standard", "ntsc"), "aspect": "16:9", "widescreen": "nopanscan"})
        for index, encoded in enumerate(sorted(destination.glob("*.mpg")), 1):
            pgc = ET.SubElement(titles, "pgc")
            ET.SubElement(pgc, "vob", {"file": str(encoded)})
            ET.SubElement(pgc, "post").text = f"jump title {index + 1};" if index < len(files) else "exit;"
        xml = output / "author.xml"
        ET.ElementTree(dvd).write(xml, encoding="utf-8", xml_declaration=True)
        job.update(phase="authoring", progress=None, message="Building the DVD navigation and VIDEO_TS structure")
        env = dict(os.environ, VIDEO_FORMAT=request.get("standard", "ntsc"))
        run_process([tool_path("dvdauthor"), "-x", str(xml)], job, env=env)
        if not (disc / "VIDEO_TS" / "VIDEO_TS.IFO").is_file() or not (disc / "VIDEO_TS" / "VIDEO_TS.BUP").is_file():
            raise RuntimeError("DVD authoring did not create the required navigation files.")
        (disc / "AUDIO_TS").mkdir(exist_ok=True)
        # Encoding intermediates are disposable; originals are kept in the queue's source paths.
        shutil.rmtree(destination)
        for pass_log in output.glob("pass-*"):
            if pass_log.is_file():
                pass_log.unlink()
        return disc
    (output / "checksums.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return destination
