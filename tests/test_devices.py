"""Idle hot-plug monitoring and optical-operation isolation."""
import copy
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lumaburn import server


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.original = (server.REFRESHING, server.CURRENT_JOB, copy.deepcopy(server.DEVICES))
        server.REFRESHING = False
        server.CURRENT_JOB = None
        server.DEVICES = {"drives": [], "volumes": [], "events": []}

    def tearDown(self):
        server.REFRESHING, server.CURRENT_JOB, server.DEVICES = self.original

    def test_monitor_rechecks_and_removes_disconnected_device(self):
        inserted = {"drives": [{"id": "dvd", "can_write": True}], "volumes": [], "events": []}
        removed = {"drives": [], "volumes": [], "events": []}
        stop = unittest.mock.Mock()
        stop.is_set.return_value = False
        stop.wait.side_effect = [False, True]
        with patch("lumaburn.server.native", side_effect=[inserted, removed]) as native:
            server.monitor_devices(stop)
        self.assertEqual(native.call_count, 2)
        self.assertEqual(server.DEVICES["drives"], [])
        stop.wait.assert_called_with(server.DEVICE_REFRESH_SECONDS)

    def test_monitor_does_not_inspect_during_jobs(self):
        for state in ["queued", "running"]:
            server.CURRENT_JOB = SimpleNamespace(data={"state": state})
            stop = unittest.mock.Mock()
            stop.is_set.return_value = False
            stop.wait.return_value = True
            with patch("lumaburn.server.native") as native:
                server.monitor_devices(stop)
                native.assert_not_called()
            self.assertFalse(server.REFRESHING)

    def test_explicit_burn_preflight_can_refresh(self):
        server.CURRENT_JOB = SimpleNamespace(data={"state": "running"})
        detected = {"drives": [{"id": "dvd", "blank": False}], "volumes": [], "events": []}
        with patch("lumaburn.server.native", return_value=detected) as native:
            self.assertTrue(server.refresh_devices(allow_active_job=True))
            native.assert_called_once()
        self.assertFalse(server.DEVICES["drives"][0]["blank"])

    def test_simultaneous_refreshes_do_not_duplicate_inspection(self):
        started, release = threading.Event(), threading.Event()

        def inspect(*args, **kwargs):
            started.set()
            release.wait(3)
            return {"drives": [], "volumes": [], "events": []}

        with patch("lumaburn.server.native", side_effect=inspect) as native:
            worker = threading.Thread(target=server.refresh_devices)
            worker.start()
            try:
                self.assertTrue(started.wait(2))
                self.assertFalse(server.refresh_devices())
                self.assertEqual(native.call_count, 1)
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(server.REFRESHING)

    def test_new_job_waits_for_pending_scan_before_preflight(self):
        inspecting, release, waiting = threading.Event(), threading.Event(), threading.Event()
        detected = {"drives": [{"id": "dvd", "blank": False}], "volumes": [], "events": []}
        original_wait = server.DEVICE_READY.wait_for

        def inspect(*args, **kwargs):
            inspecting.set()
            release.wait(3)
            return detected

        def wait(predicate, timeout):
            waiting.set()
            return original_wait(predicate, timeout)

        job = server.Job({"preset": "dvd-video", "action": "burn"}, [])
        with patch("lumaburn.server.native", side_effect=inspect), patch.object(server.DEVICE_READY, "wait_for", side_effect=wait), patch("lumaburn.media.make_plan", return_value={"can_start": False, "errors": ["Disc changed"]}) as planner:
            scanner = threading.Thread(target=server.refresh_devices)
            scanner.start()
            worker = threading.Thread(target=job.run)
            try:
                self.assertTrue(inspecting.wait(2))
                server.CURRENT_JOB = job
                worker.start()
                self.assertTrue(waiting.wait(2))
                planner.assert_not_called()
            finally:
                release.set()
                scanner.join(3)
                if worker.ident is not None:
                    worker.join(3)
        self.assertFalse(planner.call_args.args[2]["drives"][0]["blank"])
        self.assertEqual(job.data["error"], "Disc changed")


if __name__ == "__main__":
    unittest.main()
