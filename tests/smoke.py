"""Real synthetic encoding check. Creates no physical disc writes."""
from pathlib import Path
import json
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lumaburn import media, server


def main():
    root = media.ROOT / ".local" / ("smoke-" + uuid.uuid4().hex[:8])
    root.mkdir(parents=True)
    source = root / "source.mkv"
    ffmpeg = media.tool_path("ffmpeg")
    if not all(media.tools_status()[name] for name in ["ffmpeg", "ffprobe", "dvdauthor"]):
        raise SystemExit("Install FFmpeg/FFprobe and dvdauthor before the smoke check.")
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "flac", str(source)], check=True)
    info = media.probe(source)
    assert info["video"] and info["audio"] and info["duration"] > 2.9, info
    info.update(id="fixture", path=str(source))
    results = []
    for preset, standard in [("usb-original", "ntsc"), ("data-disc", "ntsc"), ("usb-compatible", "ntsc"), ("mp3", "ntsc"), ("dvd-video", "ntsc"), ("dvd-video", "pal")]:
        request = {"preset": preset, "standard": standard, "quality": "balanced", "label": "LUMABURN"}
        output = root / (preset + "-" + standard)
        output.mkdir()
        job = server.Job(request, [info])
        plan = media.make_plan(request, [info], {"drives": []})
        assert plan["can_start"], plan
        content = media.encode_files(request, [info], plan, output, job)
        if preset in {"usb-original", "data-disc"}:
            assert media.sha256(next(content.iterdir())) == media.sha256(source)
        elif preset == "dvd-video":
            vob = next((content / "VIDEO_TS").glob("VTS_01_*.VOB"))
            probe = subprocess.run([media.tool_path("ffprobe"), "-v", "error", "-show_streams", "-of", "json", str(vob)], capture_output=True, text=True, check=True)
            streams = json.loads(probe.stdout)["streams"]
            video = next(s for s in streams if s.get("codec_type") == "video")
            audio = next(s for s in streams if s.get("codec_type") == "audio")
            assert video["codec_name"] == "mpeg2video" and video["width"] == 720
            assert video["height"] == (480 if standard == "ntsc" else 576)
            assert audio["codec_name"] == "ac3" and audio["sample_rate"] == "48000"
            iso = output / "disc.iso"
            result = server.native("image", {"source_dir": str(content), "dvd_video": True, "label": "LUMABURN", "output_path": str(iso)})
            assert result.get("ok") and iso.stat().st_size > 0, result
        else:
            encoded = media.probe(next(content.iterdir()))
            assert encoded["audio"] and encoded["duration"] > 2.8, encoded
            assert bool(encoded["video"]) == (preset == "usb-compatible")
        results.append({"preset": preset, "standard": standard, "verified": True})
        print(f"PASS: {preset} ({standard})", flush=True)
    # MOV video without audio must gain a valid silent AC-3 stream on the DVD.
    silent_source = root / "silent.MOV"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25", "-t", "2", "-an", "-c:v", "libx264", str(silent_source)], check=True)
    silent = media.probe(silent_source)
    silent.update(id="silent", path=str(silent_source))
    assert silent["video"] and not silent["audio"], silent
    mixed_output = root / "mixed-titles"
    mixed_output.mkdir()
    mixed_request = {"preset": "dvd-video", "standard": "pal"}
    mixed_plan = media.make_plan(mixed_request, [info, silent], {"drives": []})
    mixed_disc = media.encode_files(mixed_request, [info, silent], mixed_plan, mixed_output, server.Job(mixed_request, [info, silent]))
    assert (mixed_disc / "VIDEO_TS" / "VTS_01_0.IFO").is_file()
    assert not (mixed_output / "media").exists(), "Successful DVD intermediates should be removed"
    print("PASS: two ordered PAL titles, including silent MOV", flush=True)
    # Exercise the HDR-to-SDR filter with real 10-bit HEVC input.
    hdr_source = root / "hdr.mkv"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24", "-t", "1", "-vf", "format=yuv420p10le,setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc", "-c:v", "libx265", "-x265-params", "log-level=error:colorprim=9:transfer=16:colormatrix=9", "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc", str(hdr_source)], check=True)
    hdr = media.probe(hdr_source)
    hdr.update(id="hdr", path=str(hdr_source))
    assert hdr["color_transfer"] == "smpte2084"
    hdr_output = root / "hdr-to-sdr"
    hdr_output.mkdir()
    hdr_request = {"preset": "usb-compatible"}
    hdr_plan = media.make_plan(hdr_request, [hdr], {"drives": []})
    hdr_media = media.encode_files(hdr_request, [hdr], hdr_plan, hdr_output, server.Job(hdr_request, [hdr]))
    assert media.probe(next(hdr_media.iterdir()))["color_transfer"] not in {"smpte2084", "arib-std-b67"}
    print("PASS: HDR HEVC input tone-mapped to SDR H.264", flush=True)
    (root / "results.json").write_text(json.dumps(results, indent=2))
    print(f"Synthetic outputs: {root}")


if __name__ == "__main__":
    main()
