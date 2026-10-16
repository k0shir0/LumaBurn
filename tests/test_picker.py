"""Picker concurrency and cleanup without opening Windows dialogs."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lumaburn import server


class PickerTests(unittest.TestCase):
    def test_second_picker_is_rejected_while_first_is_open(self):
        opened, finish = threading.Event(), threading.Event()

        def dialog(*args, **kwargs):
            opened.set()
            self.assertTrue(finish.wait(3))
            return subprocess.CompletedProcess([], 0, '{"paths": []}', '')

        with patch.object(server.subprocess, 'run', side_effect=dialog) as run:
            with ThreadPoolExecutor(max_workers=1) as pool:
                first = pool.submit(server.picker, 'pick-files')
                try:
                    self.assertTrue(opened.wait(3))
                    with self.assertRaisesRegex(ValueError, 'already open'):
                        server.picker('pick-folder')
                    self.assertEqual(run.call_count, 1)
                finally:
                    finish.set()
                self.assertEqual(first.result(), {'paths': []})

    def test_cancel_releases_picker(self):
        with patch.object(server.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{"paths": []}', '')):
            self.assertEqual(server.picker('pick-files'), {'paths': []})
            self.assertEqual(server.picker('pick-files'), {'paths': []})

    def test_failure_releases_picker(self):
        with patch.object(server.subprocess, 'run', side_effect=[subprocess.TimeoutExpired('picker', 600), subprocess.CompletedProcess([], 0, '{"paths": []}', '')]):
            with self.assertRaises(subprocess.TimeoutExpired):
                server.picker('pick-files')
            self.assertEqual(server.picker('pick-files'), {'paths': []})


if __name__ == '__main__':
    unittest.main()
