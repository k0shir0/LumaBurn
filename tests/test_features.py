"""Resource bounds, streamed progress, read-only DVD checks, and task admission."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lumaburn import dvdtests, media, server


class FeatureTests(unittest.TestCase):
    def test_copy_progress_is_by_bytes_and_includes_readback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "large.bin"
            source.write_bytes(b"x" * (12 * 1024 * 1024))
            tiny = root / "tiny.bin"
            tiny.write_bytes(b"small")
            output = root / "output"
            output.mkdir()
            files = [{"path": str(p), "size": p.stat().st_size, "name": p.name, "duration": 0} for p in [source, tiny]]
            job = server.Job({"preset": "usb-original", "performance": "full"}, files)
            updates = []
            original = job.update

            def update(**changes):
                original(**changes)
                updates.append(dict(job.data))

            job.update = update
            media.encode_files(job.request, files, {}, output, job)
            progresses = [v["progress"] for v in updates if v["progress"] is not None]
            self.assertEqual(progresses, sorted(progresses))
            self.assertTrue(any(0 < value < 37 for value in progresses), progresses)
            self.assertEqual(progresses[-1], 75)
            self.assertEqual(media.sha256(output / "media" / "01_large.bin"), media.sha256(source))

    def test_gentle_limits_decode_encode_and_filter_threads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "movie.bin"
            source.write_bytes(b"source")
            output = root / "out"
            output.mkdir()
            file = {"path": str(source), "name": source.name, "size": 6, "video": True, "audio": False, "video_index": 0, "duration": 3}
            job = server.Job({"preset": "usb-compatible"}, [file])
            arguments = []

            def encode(args, *_, **__):
                arguments.extend(args)
                Path(args[-1]).write_bytes(b"converted")

            with patch("lumaburn.media.run_process", side_effect=encode), patch("lumaburn.media.probe", return_value={"duration": 3}), patch("lumaburn.media.tool_path", return_value="ffmpeg"), patch("lumaburn.media.os.cpu_count", return_value=32), patch("lumaburn.media.io_pause"):
                media.encode_files(job.request, [file], {}, output, job)
            input_index = arguments.index("-i")
            thread_positions = [i for i, value in enumerate(arguments) if value == "-threads"]
            self.assertEqual(len(thread_positions), 2)
            self.assertLess(thread_positions[0], input_index)
            self.assertGreater(thread_positions[1], input_index)
            self.assertTrue(all(arguments[i + 1] == "2" for i in thread_positions))
            self.assertEqual(arguments[arguments.index("-filter_threads") + 1], "2")

    def test_progress_never_goes_back_or_finishes_early(self):
        job = server.Job({}, [])
        job.update(progress=75)
        job.update(phase="authoring", progress=None)
        self.assertIsNone(job.data["progress"])
        job.update(progress=2)
        self.assertEqual(job.data["progress"], 75)
        job.update(progress=100)
        self.assertEqual(job.data["progress"], 99)
        job.update(state="completed", phase="completed", progress=100)
        self.assertEqual(job.data["progress"], 100)
        self.assertEqual(job.data["phase_progress"], 100)

    def test_dvd_structure_and_backup_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "VIDEO_TS"
            video.mkdir()
            for stem, signature in [("VIDEO_TS", b"DVDVIDEO-VMG"), ("VTS_01_0", b"DVDVIDEO-VTS")]:
                data = signature.ljust(2048, b"\0")
                (video / (stem + ".IFO")).write_bytes(data)
                (video / (stem + ".BUP")).write_bytes(data)
            (video / "VTS_01_1.VOB").write_bytes(b"\x00\x00\x01\xba" + b"sample")
            probe = {"video_codec": "mpeg2video", "width": 720, "height": 480, "frame_rate": "30000/1001", "audio_codec": "ac3", "sample_rate": "48000"}
            with patch("lumaburn.media.probe", return_value=probe), patch("lumaburn.media.tool_path", return_value="ffprobe"):
                self.assertTrue(dvdtests.inspect(root)["dvd_video"])
                (video / "VTS_01_1.VOB").rename(video / "VTS_01_2.VOB")
                self.assertFalse(dvdtests.inspect(root)["dvd_video"])
                (video / "VTS_01_2.VOB").rename(video / "VTS_01_1.VOB")
                probe.update(height=240)
                self.assertFalse(dvdtests.inspect(root)["dvd_video"])
                probe.update(height=480, audio_codec="pcm_dvd", sample_rate="96000")
                self.assertTrue(dvdtests.inspect(root)["dvd_video"])
                probe.update(frame_rate="25/1")
                self.assertFalse(dvdtests.inspect(root)["dvd_video"])
                probe.update(frame_rate="30000/1001")
                (video / "VIDEO_TS.BUP").write_bytes(b"bad")
                result = dvdtests.inspect(root)
                self.assertTrue(result["seen"] and result["readable"])
                self.assertFalse(result["dvd_video"])
                self.assertTrue(any(not c["passed"] and c["name"] == "VIDEO_TS.BUP" for c in result["checks"]))
            self.assertFalse(dvdtests.inspect(None, {"letter": None})["readable"])
            self.assertTrue(dvdtests.inspect(None, {"letter": None})["seen"])

    def test_estimator_job_snapshots_and_admission(self):
        original = server.CURRENT_JOB, server.COMPRESSION_TASK, server.TESTING
        try:
            server.CURRENT_JOB, server.TESTING = None, False
            task = server.CompressionTask(Path("input"), "lzma")
            server.COMPRESSION_TASK = task
            with self.assertRaises(ValueError):
                server.require_idle()
            result = {"total_bytes": 100, "estimated_bytes": 50, "saved_bytes": 50}
            with patch("lumaburn.compression.estimate", return_value=result):
                task.run()
            self.assertEqual(task.data["state"], "completed")
            self.assertEqual(task.data["saved_bytes"], 50)
            server.require_idle()
            state = server.snapshot()
            task.update(saved_bytes=1)
            self.assertEqual(state["compression"]["saved_bytes"], 50)
        finally:
            server.CURRENT_JOB, server.COMPRESSION_TASK, server.TESTING = original


if __name__ == "__main__":
    unittest.main()
