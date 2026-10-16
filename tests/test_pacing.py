"""Gentle-mode I/O pacing and cancellation, independent of scheduler timing."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lumaburn import media


def job(performance="gentle", cancelled=False):
    cancel = Mock()
    cancel.wait.return_value = cancelled
    return SimpleNamespace(request={"performance": performance}, cancel=cancel)


class PacingTests(unittest.TestCase):
    def test_slow_operation_adds_no_delay(self):
        current = job()
        media.io_pause(current, 4 * 1024 * 1024, elapsed=0.5)
        current.cancel.wait.assert_called_once_with(0.0)

    def test_fast_operation_waits_only_for_remaining_budget(self):
        current = job()
        media.io_pause(current, 8 * 1024 * 1024, elapsed=0.025)
        current.cancel.wait.assert_called_once()
        self.assertAlmostEqual(current.cancel.wait.call_args.args[0], 0.1)

    def test_full_speed_does_not_wait(self):
        current = job("full")
        media.io_pause(current, 64 * 1024 * 1024)
        current.cancel.wait.assert_not_called()

    def test_cancellation_is_observed_with_and_without_a_delay(self):
        for elapsed in (0.0, 0.25):
            with self.subTest(elapsed=elapsed), self.assertRaises(media.Cancelled):
                media.io_pause(job(cancelled=True), 4 * 1024 * 1024, elapsed=elapsed)


if __name__ == "__main__":
    unittest.main()
