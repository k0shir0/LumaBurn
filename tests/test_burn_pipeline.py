"""Burn intent and completion evidence across the job pipeline; no hardware writes."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lumaburn import media, server


class BurnPipelineTests(unittest.TestCase):
    def run_job(self, action, result):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "movie.mkv"
            source.write_bytes(b"source")
            file = {"id": "movie", "name": source.name, "path": str(source), "size": 6, "duration": 3, "video": True, "audio": True}
            devices = {"drives": [{"id": "writer", "blank": True, "writable": True, "physical_media_type": 10}], "volumes": []}
            request = {"preset": "dvd-video", "action": action, "recorder_id": "writer", "output_dir": folder}
            job = server.Job(request, [file])

            def encode(request, files, plan, output, current_job):
                self.assertEqual(current_job, job)
                self.assertEqual(files[0]["path"], str(source))
                disc = output / "disc"
                (disc / "VIDEO_TS").mkdir(parents=True)
                (disc / "VIDEO_TS" / "VIDEO_TS.IFO").write_bytes(b"authored")
                return disc

            with patch("lumaburn.server.DEVICES", devices), patch("lumaburn.server.REFRESHING", False), patch("lumaburn.server.refresh_devices"), patch("lumaburn.media.tool_path", return_value="tool"), patch("lumaburn.media.encode_files", side_effect=encode), patch("lumaburn.server.native", return_value=result) as native:
                job.run()
                self.assertEqual(native.call_args.args[0], "burn" if action == "burn" else "image")
                self.assertTrue(native.call_args.args[1]["dvd_video"])
                self.assertTrue(native.call_args.args[1]["verify"])
                self.assertTrue(Path(native.call_args.args[1]["source_dir"]).is_dir())
            return job.data

    def test_burn_completes_only_after_writing_finalization_and_readback(self):
        result = {"ok": True, "burned": True, "finalized": True, "verification": {"ok": True, "verified": True, "files_expected": 5, "files_verified": 5}}
        completed = self.run_job("burn", result)
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(completed["action"], "burn")
        self.assertEqual(completed["progress"], 100)
        self.assertTrue(completed["burned"] and completed["finalized"])
        self.assertIn("read-back verified", completed["message"])
        self.assertNotIn("image_path", completed)

        for field in ["burned", "finalized", "verification"]:
            incomplete = copy.deepcopy(result)
            del incomplete[field]
            with self.subTest(missing=field):
                failed = self.run_job("burn", incomplete)
                self.assertEqual(failed["state"], "failed")
                self.assertNotEqual(failed["progress"], 100)
                self.assertTrue(failed["error"])
        for field, value in [("ok", False), ("verified", False), ("files_expected", 0), ("files_verified", 4)]:
            incomplete = copy.deepcopy(result)
            incomplete["verification"][field] = value
            with self.subTest(readback=field):
                failed = self.run_job("burn", incomplete)
                self.assertEqual(failed["state"], "failed")
                self.assertNotEqual(failed["progress"], 100)

    def test_iso_export_explicitly_reports_no_disc_write(self):
        exported = self.run_job("prepare", {"ok": True})
        self.assertEqual(exported["state"], "completed")
        self.assertEqual(exported["action"], "prepare")
        self.assertIn("image_path", exported)
        self.assertEqual(exported["message"], "ISO saved. No disc was written.")
        self.assertFalse(exported.get("burned", False))

    def test_application_source_folders_cannot_receive_output(self):
        plan = {"can_start": True, "estimated_bytes": 1, "capacity_bytes": None, "errors": [], "notes": []}
        with tempfile.TemporaryDirectory() as folder, patch.object(server, "ROOT", Path(folder)):
            for protected in ["app", "native", "lumaburn", "scripts", ".git", ".agents", ".codex"]:
                with self.subTest(folder=protected), patch("lumaburn.server.REFRESHING", False), patch("lumaburn.media.make_plan", return_value=plan):
                    request = {"preset": "usb-original", "action": "prepare", "output_dir": str(server.ROOT / protected)}
                    job = server.Job(request, [])
                    job.run()
                    self.assertEqual(job.data["state"], "failed")
                    self.assertIn("application source folders", job.data["error"])

    def test_file_output_cannot_claim_a_disc_burn(self):
        for preset in ["usb-original", "usb-compatible", "mp3"]:
            with self.subTest(preset=preset), patch("lumaburn.media.tool_path", return_value="tool"):
                plan = media.make_plan({"preset": preset, "action": "burn"}, [{"size": 6, "duration": 3, "video": True, "audio": True}], {})
                self.assertFalse(plan["can_start"])
                self.assertIn("Burning requires the DVD player or Data DVD preset.", plan["errors"])


if __name__ == "__main__":
    unittest.main()
