"""Read-only DVD checks. A real standalone player remains the playback test."""
from pathlib import Path

from . import media


def inspect(path: Path | None, drive: dict | None = None) -> dict:
    checks = []

    def check(name, passed, message):
        checks.append({"name": name, "passed": bool(passed), "message": message})

    seen = bool(drive) if path is None else path.is_dir()
    if path is None and drive and drive.get("letter"):
        path = Path(drive["letter"] + "\\")
    check("Seen by Windows", seen, "The drive or folder is visible." if seen else "Connect a drive or choose an existing DVD folder.")
    readable, dvd_video = False, False
    try:
        if path is None or not path.is_dir():
            raise ValueError("Insert a recorded disc and wait for Windows to mount it, or choose an authored DVD folder. Mount ISO/IMG files in Windows before testing.")
        entries = list(path.iterdir())
        readable = True
        check("Readable filesystem", True, "Windows can list the selected location.")
        video_ts = path if path.name.upper() == "VIDEO_TS" else next((p for p in entries if p.name.upper() == "VIDEO_TS" and p.is_dir()), path / "VIDEO_TS")
        manager = video_ts / "VIDEO_TS.IFO"
        if not manager.is_file():
            raise ValueError("VIDEO_TS/VIDEO_TS.IFO is missing. A data disc of movie files is not a DVD-Video disc.")
        check("DVD navigation", True, "Found VIDEO_TS.IFO; checking its signature and backup below.")
        titles = sorted(video_ts.glob("VTS_[0-9][0-9]_0.IFO"))
        check("DVD titles", bool(titles), f"Found {len(titles)} title sets.")
        for ifo in [manager, *titles]:
            # Navigation files are small; reject pathological files instead of loading them into RAM.
            if ifo.stat().st_size > 4 * 1024 * 1024:
                raise ValueError(f"Navigation file is unexpectedly large: {ifo.name}")
            data = ifo.read_bytes()
            signature = b"DVDVIDEO-VMG" if ifo == manager else b"DVDVIDEO-VTS"
            backup = ifo.with_suffix(".BUP")
            check(ifo.name, data[:12] == signature and len(data) >= 2048 and len(data) % 2048 == 0, "DVD navigation signature and sector-aligned length checked.")
            check(backup.name, backup.is_file() and backup.stat().st_size == len(data) and backup.read_bytes() == data, "Backup navigation must match its IFO.")
        for ifo in titles:
            vobs = sorted(video_ts.glob(ifo.stem[:-1] + "[1-9].VOB"))
            sequence = [p.stem[-1] for p in vobs] == [str(n) for n in range(1, len(vobs) + 1)]
            check(ifo.stem[:6] + " video data", bool(vobs) and sequence and all(p.stat().st_size > 0 for p in vobs), f"Found {len(vobs)} non-menu VOB files; numbering must start at 1 without gaps.")
            if vobs:
                with vobs[0].open("rb") as source:
                    check(vobs[0].name + " container", source.read(4) == b"\x00\x00\x01\xba", "MPEG program-stream pack header checked.")
                if media.tool_path("ffprobe"):
                    info = media.probe(vobs[0])
                    dimensions = info.get("width"), info.get("height")
                    ntsc = {(720, 480), (704, 480), (352, 480), (352, 240)}
                    pal = {(720, 576), (704, 576), (352, 576), (352, 288)}
                    codec = info.get("video_codec")
                    video_ok = (codec == "mpeg2video" and dimensions in ntsc | pal) or (codec == "mpeg1video" and dimensions in {(352, 240), (352, 288)})
                    rate_ok = info.get("frame_rate") in ({"30000/1001", "24000/1001"} if dimensions in ntsc else {"25/1"})
                    audio_ok = info.get("audio_codec") in {"ac3", "mp2", "pcm_dvd", "dts"} and info.get("sample_rate") == "48000"
                    audio_ok |= info.get("audio_codec") == "pcm_dvd" and info.get("sample_rate") == "96000"
                    compatible = not info.get("error") and video_ok and rate_ok and audio_ok
                    check(ifo.stem[:6] + " video/audio", compatible, "FFprobe checks paired DVD dimensions, TV frame rate, and DVD audio in the first VOB of each title set.")
                else:
                    check("Media inspection", False, "Install FFmpeg with FFprobe to check DVD codecs.")
        dvd_video = all(c["passed"] for c in checks)
    except (OSError, ValueError) as exc:
        check("DVD-Video check", False, str(exc))
    return {"seen": seen, "readable": readable, "dvd_video": dvd_video, "checks": checks,
            "summary": "DVD-Video structure checks passed." if dvd_video else "DVD-Video checks did not pass.",
            "scope": "Read-only Windows filesystem, navigation backups, and first-VOB format checks.",
            "note": "This does not prove every sector, DVD navigation command, or standalone-player playback. Try the finalized disc in your DVD player."}
