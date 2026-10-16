"""Capacity, no-overwrite, and trust-boundary regression checks."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lumaburn import media, server


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.video = {"id": "one", "name": "movie.mkv", "size": 100_000_000, "duration": 3600, "video": True, "audio": True, "width": 1920, "height": 1080}
        self.devices = {"drives": [{"id": "drive", "blank": True, "writable": True, "physical_media_type": 10, "capacity_bytes": 4_706_074_624}], "volumes": []}
        self.tools = patch("lumaburn.media.tool_path", return_value="fake-tool.exe")
        self.tools.start()
        self.addCleanup(self.tools.stop)

    def plan(self, preset="dvd-video", files=None, **kwargs):
        return media.make_plan({"preset": preset, "recorder_id": "drive", **kwargs}, [self.video] if files is None else files, self.devices)

    def test_dvd_bitrate_fits_and_caps_standard(self):
        one = self.plan()
        long = self.plan(files=[dict(self.video, duration=7200)])
        self.assertTrue(one["can_start"])
        self.assertLessEqual(one["video_bitrate"], 8_000_000)
        self.assertLess(long["video_bitrate"], one["video_bitrate"])
        self.assertLess(long["estimated_bytes"], long["capacity_bytes"] * 0.98)

    def test_rejects_unreasonable_duration_and_audio_only_dvd(self):
        self.assertFalse(self.plan(files=[dict(self.video, duration=20 * 3600)])["can_start"])
        self.assertFalse(self.plan(files=[dict(self.video, video=False)])["can_start"])
        self.assertFalse(self.plan(files=[dict(self.video, duration=0)])["can_start"])
        self.assertFalse(self.plan(files=[])["can_start"])

    def test_nonblank_never_burns_but_allows_export(self):
        self.devices["drives"][0]["blank"] = False
        self.assertFalse(self.plan(action="burn")["can_start"])
        self.assertTrue(self.plan(action="prepare")["can_start"])

    def test_video_disc_preflight_rejects_cd_and_bluray(self):
        for physical_type in [2, 3, 18, 19]:
            self.devices["drives"][0]["physical_media_type"] = physical_type
            self.assertFalse(self.plan(action="burn")["can_start"])

    def test_originals_need_no_encoder(self):
        with patch("lumaburn.media.tool_path", return_value=None):
            self.assertTrue(self.plan("usb-original", files=[dict(self.video, video=False, duration=0)])["can_start"])
            self.assertFalse(self.plan()["can_start"])

    def test_data_capacity_and_unknown_options(self):
        self.assertFalse(self.plan("data-disc", files=[dict(self.video, size=5_000_000_000)])["can_start"])
        for field, value in [("standard", "secam"), ("quality", "magic")]:
            with self.assertRaises(ValueError):
                self.plan(**{field: value})
        with self.assertRaises(ValueError):
            self.plan("unknown")

    def test_safe_names(self):
        for name in ["../CON", "..\\evil.mov", "NUL", "a\x00b.mp4", 'x"; whoami.mov']:
            cleaned = media.clean_name(name)
            self.assertEqual(Path(cleaned).name, cleaned)
            self.assertNotIn("\\", cleaned)
            self.assertNotIn("\x00", cleaned)
        self.assertEqual(media.clean_name("summer trip.mkv"), "summer trip.mkv")

    def test_invalid_and_duplicate_queue_ids(self):
        with self.assertRaises(ValueError):
            server.selected_files({"file_ids": ["missing"]})
        with self.assertRaises(ValueError):
            server.selected_files({"file_ids": ["same", "same"]})
        with self.assertRaises(ValueError):
            server.selected_files({"file_ids": "one"})


if __name__ == "__main__":
    unittest.main()
