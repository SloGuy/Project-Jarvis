"""Runner tests using temporary locks and mocked capture/persistence."""
from contextlib import redirect_stdout, redirect_stderr
import fcntl
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.capital import portfolio_checkpoint_runner as runner


class CheckpointRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.lock = self.root / "runtime/checkpoint.lock"
        self.directory = self.root / "checkpoints"

        self.captured = {
            "sampled_at": "2026-09-27T23:00:00+00:00",
            "row_reconciliation": {"status": "row_images_match"},
            "accounting_effects": {"status": "amounts_match"},
            "valuations": {
                "complete_indicative_count": 3,
                "incomplete_count": 1,
            },
        }
        self.saved = {
            "record_id": "a" * 64,
            "path": str(self.directory / ("a" * 64 + ".json")),
            "bytes": 1234,
            "checkpoint_persisted": True,
            "historical_return_eligible": False,
            "database_writes": False,
            "execution_authorized": False,
        }

        self.patch("LOCK_FILE", new=self.lock)
        self.patch("CHECKPOINT_DIRECTORY", new=self.directory)
        self.capture = self.patch(
            "capture_portfolio_checkpoint", return_value=self.captured
        )
        self.save = self.patch(
            "save_portfolio_checkpoint", return_value=self.saved
        )

    def patch(self, name, **kwargs):
        patcher = patch.object(runner, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def test_success_captures_and_saves_once(self):
        code, result = runner.run_once()
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "persisted")
        self.capture.assert_called_once_with()
        self.save.assert_called_once_with(
            directory=self.directory,
            checkpoint=self.captured,
        )
        self.assertTrue(result["checkpoint_persisted"])
        self.assertEqual(result["record_id"], "a" * 64)

    def test_lock_parent_is_created(self):
        self.assertFalse(self.lock.parent.exists())
        runner.run_once()
        self.assertTrue(self.lock.is_file())

    def test_busy_lock_prevents_capture_and_save(self):
        self.lock.parent.mkdir(parents=True)
        with self.lock.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            code, result = runner.run_once()
            self.assertEqual(code, 2)
            self.assertEqual(result["status"], "busy")
            self.assertFalse(result["checkpoint_persisted"])
            self.capture.assert_not_called()
            self.save.assert_not_called()

    def test_lock_is_held_during_capture_and_save(self):
        def assert_locked():
            with self.lock.open("a+") as other:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(
                        other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                    )

        def capture():
            assert_locked()
            return self.captured

        def save(**kwargs):
            assert_locked()
            return self.saved

        self.capture.side_effect = capture
        self.save.side_effect = save
        self.assertEqual(runner.run_once()[0], 0)

    def test_capture_failure_releases_lock(self):
        self.capture.side_effect = RuntimeError("capture failed")
        with self.assertRaises(RuntimeError):
            runner.run_once()
        self.save.assert_not_called()
        self.capture.side_effect = None
        self.assertEqual(runner.run_once()[0], 0)

    def test_save_failure_releases_lock(self):
        self.save.side_effect = OSError("disk failed")
        with self.assertRaises(OSError):
            runner.run_once()
        self.save.side_effect = None
        self.assertEqual(runner.run_once()[0], 0)

    def test_keyboard_interrupt_releases_lock(self):
        self.capture.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            runner.run_once()
        self.capture.side_effect = None
        self.assertEqual(runner.run_once()[0], 0)

    def test_incomplete_evidence_can_be_persisted_without_eligibility(self):
        self.captured["row_reconciliation"]["status"] = "unresolved"
        self.captured["accounting_effects"]["status"] = "unresolved"
        self.captured["valuations"].update(
            complete_indicative_count=0,
            incomplete_count=4,
        )
        code, result = runner.run_once()
        self.assertEqual(code, 0)
        self.assertEqual(result["incomplete_count"], 4)
        self.assertEqual(result["row_reconciliation"], "unresolved")
        self.assertFalse(result["historical_return_eligible"])

    def test_authority_remains_disabled(self):
        _, result = runner.run_once()
        for key in (
            "historical_return_eligible",
            "database_writes",
            "execution_authorized",
        ):
            self.assertIs(result[key], False)

    def test_cli_success_prints_receipt(self):
        output = StringIO()
        with redirect_stdout(output):
            code = runner.main([])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "persisted")

    def test_cli_failure_suppresses_raw_error(self):
        self.capture.side_effect = RuntimeError("secret-provider-token")
        output = StringIO()
        errors = StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = runner.main([])
        self.assertEqual(code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn("secret-provider-token", errors.getvalue())
        result = json.loads(errors.getvalue())
        self.assertEqual(result["status"], "failed")
        self.assertIn("may already have been published", result["message"])

    def test_cli_rejects_unknown_arguments_before_capture(self):
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised:
                runner.main(["--approve"])
        self.assertEqual(raised.exception.code, 2)
        self.capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
