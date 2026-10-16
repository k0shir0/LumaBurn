"""Compression estimates, source preservation, sampling and resource limits."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lumaburn import compression, media


class CompressionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "sources"
        self.source.mkdir()
        self.staging = self.root / "staging"
        patcher = patch.object(compression, "TEMP_ROOT", self.staging)
        patcher.start()
        self.addCleanup(patcher.stop)

    def file(self, name, data):
        path = self.source / name
        path.write_bytes(data)
        return path

    def test_distinguishes_repetitive_data_from_random_without_modifying_source(self):
        for name, data, compressible in [("text.txt", b"repeatable line\n" * 20_000, True), ("packed.bin", os.urandom(256 * 1024), False)]:
            with self.subTest(name=name):
                path = self.file(name, data)
                before = path.stat()
                digest = hashlib.sha256(data).hexdigest()
                result = compression.estimate(path)
                self.assertEqual(result["total_bytes"], len(data))
                self.assertEqual(result["sampled_bytes"], len(data))
                self.assertEqual(result["sampled_files"], 1)
                if compressible:
                    self.assertGreater(result["saved_percent"], 95)
                else:
                    self.assertLess(result["saved_percent"], 1)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
                self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
                self.assertEqual(list(self.staging.iterdir()), [])

    def test_large_file_reads_beginning_middle_and_end_within_ceiling(self):
        length = 3 * compression.MAX_FILE_SAMPLE_BYTES
        data = b"A" * (length // 3) + b"B" * (length // 3) + b"C" * (length // 3)
        path = self.file("spread.bin", data)
        sample = compression._sample(path, path.stat(), compression.MAX_FILE_SAMPLE_BYTES, None, time.monotonic() + 5)
        third = len(sample) // 3
        self.assertEqual(sample[:third], b"A" * third)
        self.assertEqual(sample[third:2 * third], b"B" * third)
        self.assertEqual(sample[2 * third:], b"C" * (len(sample) - 2 * third))
        self.assertEqual(len(sample), compression.MAX_FILE_SAMPLE_BYTES)

    def test_folder_weights_by_original_bytes_instead_of_file_count(self):
        self.file("movie.mp4", os.urandom(800 * 1024))
        for index in range(12):
            self.file(f"small-{index}.txt", b"a" * 1024)
        result = compression.estimate(self.source)
        self.assertEqual(result["file_count"], 13)
        self.assertLess(result["saved_percent"], 3)
        self.assertGreater(result["estimated_bytes"], 790 * 1024)

    def test_global_byte_and_file_limits_and_unsampled_groups_are_conservative(self):
        for index in range(5):
            self.file(f"data.{index}", b"a" * (64 * 1024))
        with patch.object(compression, "MAX_SAMPLE_BYTES", 80 * 1024), patch.object(compression, "MAX_FILE_SAMPLE_BYTES", 64 * 1024), patch.object(compression, "MAX_SAMPLE_FILES", 2):
            result = compression.estimate(self.source)
        self.assertEqual(result["sampled_bytes"], 80 * 1024)
        self.assertEqual(result["sampled_files"], 2)
        self.assertEqual(result["untested_bytes"], 3 * 64 * 1024)
        self.assertGreaterEqual(result["estimated_bytes"], result["untested_bytes"])

    def test_scan_limit_fails_without_compressing_anything(self):
        for index in range(4):
            self.file(f"file-{index}", b"a")
        with patch.object(compression, "MAX_SCAN_ENTRIES", 3), patch.object(compression, "_compressed_size") as compress:
            with self.assertRaisesRegex(ValueError, "scan limit"):
                compression.estimate(self.source)
            compress.assert_not_called()

    def test_empty_and_zero_byte_sources(self):
        result = compression.estimate(self.source)
        self.assertEqual((result["total_bytes"], result["estimated_bytes"], result["saved_percent"], result["file_count"]), (0, 0, 0, 0))
        self.file("empty", b"")
        result = compression.estimate(self.source)
        self.assertEqual(result["file_count"], 1)
        self.assertEqual(result["sampled_files"], 0)
        self.assertIn("metadata overhead", result["note"])

    def test_skips_symlinks_and_rejects_link_source(self):
        target = self.file("regular", b"a" * 1024)
        link = self.source / "alias"
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"Symlink creation unavailable: {exc}")
        result = compression.estimate(self.source)
        self.assertEqual(result["file_count"], 1)
        self.assertEqual(result["total_bytes"], 1024)
        self.assertEqual(result["skipped_entries"], 1)
        with self.assertRaisesRegex(ValueError, "symlinks or junctions"):
            compression.estimate(link)

    def test_windows_reparse_attribute_is_treated_as_a_link(self):
        self.assertTrue(compression._linked(SimpleNamespace(st_mode=0, st_file_attributes=0x400)))
        self.assertFalse(compression._linked(SimpleNamespace(st_mode=0, st_file_attributes=0)))

    @unittest.skipUnless(os.name == "nt", "Windows junction check")
    def test_windows_junction_is_excluded_and_rejected_as_source(self):
        target = self.source / "regular"
        target.mkdir()
        (target / "file").write_bytes(b"a" * 1024)
        link = self.source / "junction"
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            self.skipTest("Junction creation unavailable")
        estimate = compression.estimate(self.source)
        self.assertEqual(estimate["file_count"], 1)
        self.assertEqual(estimate["skipped_entries"], 1)
        with self.assertRaisesRegex(ValueError, "symlinks or junctions"):
            compression.estimate(link)

    def test_own_sample_directory_is_excluded_even_when_it_is_under_source(self):
        self.file("ordinary", b"a" * 1024)
        with patch.object(compression, "TEMP_ROOT", self.source / "samples"):
            result = compression.estimate(self.source)
        self.assertEqual(result["file_count"], 1)
        self.assertEqual(result["skipped_entries"], 1)

    def test_inaccessible_sample_and_changed_source_do_not_return_estimates(self):
        path = self.file("ordinary", b"a" * 1024)
        with patch.object(compression, "_sample", side_effect=PermissionError("unreadable")):
            with self.assertRaises(PermissionError):
                compression.estimate(path)

        def changed(**changes):
            if changes.get("message", "").startswith("Tested "):
                path.write_bytes(b"changed")

        with self.assertRaisesRegex(RuntimeError, "Source changed"):
            compression.estimate(path, update=changed)
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_new_file_during_sampling_invalidates_folder_snapshot(self):
        self.file("ordinary", b"a" * 1024)

        def changed(**changes):
            if changes.get("message", "").startswith("Tested "):
                self.file("added", b"new")

        with self.assertRaisesRegex(RuntimeError, "Source changed"):
            compression.estimate(self.source, update=changed)

    def test_cancel_and_time_limit(self):
        path = self.file("ordinary", b"a" * 1024)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(media.Cancelled):
            compression.estimate(path, cancel=cancel)
        with patch.object(compression, "MAX_SECONDS", -1):
            with self.assertRaisesRegex(RuntimeError, "time limit"):
                compression.estimate(path)

    def test_missing_tools_and_gui_winrar_are_rejected(self):
        path = self.file("ordinary", b"a" * 1024)
        for engine in ["7z", "winrar"]:
            with self.assertRaisesRegex(ValueError, "not installed"):
                compression.estimate(path, engine)
        with self.assertRaisesRegex(ValueError, "Rar.exe"):
            compression.estimate(path, "winrar", {"rar": sys.executable})
        with self.assertRaises(ValueError):
            compression.estimate(path, "unknown")

    def test_external_commands_use_only_disposable_samples_and_safe_settings(self):
        path = self.file("ordinary", b"a" * 1024)
        calls = []

        def launch(args, **kwargs):
            calls.append((args, kwargs))
            folder = Path(kwargs["cwd"])
            archive = folder / ("sample.7z" if args[0].endswith("7z.exe") else "sample.rar")
            self.assertEqual((folder / "sample.bin").read_bytes(), path.read_bytes())
            archive.write_bytes(b"archive" * 4)
            process = SimpleNamespace(returncode=0, poll=lambda: 0)
            context = unittest.mock.MagicMock()
            context.__enter__.return_value = process
            return context

        for engine, name, key in [("7z", "7z.exe", "7z"), ("winrar", "Rar.exe", "rar")]:
            executable = self.root / name
            executable.touch()
            with patch.object(subprocess, "Popen", side_effect=launch):
                result = compression.estimate(path, engine, {key: str(executable)})
            self.assertEqual(result["engine"], engine)
            self.assertEqual(list(self.staging.iterdir()), [])
        for args, kwargs in calls:
            self.assertNotIn(str(path), args)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIn("-mx=9", calls[0][0])
        self.assertIn("-mmt=1", calls[0][0])
        self.assertIn("-md=8m", calls[0][0])
        self.assertIn("-cfg-", calls[1][0])
        self.assertIn("-m5", calls[1][0])
        self.assertIn("-mt1", calls[1][0])
        self.assertIn("-md8m", calls[1][0])

    def test_external_cancellation_terminates_process_and_cleans_samples(self):
        cancel = threading.Event()
        process = unittest.mock.MagicMock()
        stopped = False

        def poll():
            cancel.set()
            return -1 if stopped else None

        def terminate():
            nonlocal stopped
            stopped = True

        process.poll.side_effect = poll
        process.terminate.side_effect = terminate
        process.__enter__.return_value = process
        with patch.object(subprocess, "Popen", return_value=process):
            with self.assertRaises(media.Cancelled):
                compression._compressed_size(b"source data", "7z", "fake.exe", self.root, cancel, time.monotonic() + 5)
        process.terminate.assert_called_once()
        self.assertFalse((self.root / "sample.bin").exists())
        self.assertFalse((self.root / "sample.7z").exists())

    def test_external_tool_failure_returns_error_and_cleans_samples(self):
        process = unittest.mock.MagicMock()
        process.__enter__.return_value = process
        process.poll.return_value = 2
        process.returncode = 2
        with patch.object(subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "exit code 2"):
                compression._compressed_size(b"source data", "7z", "fake.exe", self.root, None, time.monotonic() + 5)
        self.assertFalse((self.root / "sample.bin").exists())
        self.assertFalse((self.root / "sample.7z").exists())


if __name__ == "__main__":
    unittest.main()
