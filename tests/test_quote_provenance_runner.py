"""Runner tests with a real temporary lock and mocked collection."""

from contextlib import redirect_stderr, redirect_stdout
import fcntl
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.capital import quote_provenance_runner as runner


class ProvenanceRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.lock_path = Path(self.temporary.name) / "runtime" / "cycle.lock"

        lock_patch = patch.object(runner, "LOCK_FILE", self.lock_path)
        cycle_patch = patch.object(
            runner,
            "collect_held_quote_provenance",
            return_value={"status": "captured", "captured_count": 2},
        )
        lock_patch.start()
        self.collect = cycle_patch.start()
        self.addCleanup(lock_patch.stop)
        self.addCleanup(cycle_patch.stop)

    def invoke(self):
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = runner.main([])
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_lock_available(self):
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def test_success_returns_zero_and_json(self):
        code, stdout, stderr = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout)["status"], "captured")
        self.collect.assert_called_once_with()

    def test_empty_universe_returns_zero(self):
        self.collect.return_value = {"status": "empty"}
        code, stdout, stderr = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["status"], "empty")
        self.assertEqual(stderr, "")

    def test_partial_and_failed_cycles_return_one(self):
        for status in ("partial", "failed"):
            with self.subTest(status=status):
                self.collect.return_value = {"status": status}
                code, stdout, stderr = self.invoke()
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(stdout)["status"], status)
                self.assertEqual(stderr, "")

    def test_existing_lock_returns_busy_without_collection(self):
        self.lock_path.parent.mkdir(parents=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                code, stdout, stderr = self.invoke()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout)["status"], "busy")
        self.assertEqual(stderr, "")
        self.collect.assert_not_called()

    def test_lock_is_held_during_collection(self):
        def collect():
            with self.lock_path.open("a+", encoding="utf-8") as handle:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(
                        handle.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
            return {"status": "captured"}

        self.collect.side_effect = collect
        self.assertEqual(runner.run_once()[0], 0)

    def test_lock_is_released_after_success(self):
        runner.run_once()
        self.assert_lock_available()

    def test_lock_is_released_after_collection_failure(self):
        self.collect.side_effect = RuntimeError("failed")
        with self.assertRaises(RuntimeError):
            runner.run_once()
        self.assert_lock_available()

    def test_exception_details_are_suppressed(self):
        self.collect.side_effect = RuntimeError(
            "https://example.invalid/?token=private-secret"
        )
        code, stdout, stderr = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["status"], "failed")
        self.assertNotIn("private-secret", stderr)
        self.assertNotIn("example.invalid", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_unknown_result_status_fails_and_releases_lock(self):
        self.collect.return_value = {"status": "unexpected"}
        code, stdout, stderr = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["status"], "failed")
        self.assert_lock_available()

    def test_malformed_result_fails_without_success_claim(self):
        self.collect.return_value = None
        code, stdout, stderr = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["status"], "failed")
        self.assert_lock_available()

    def test_lock_setup_failure_prevents_collection(self):
        with patch.object(
            runner,
            "LOCK_FILE",
            self.lock_path / "child.lock",
        ):
            self.lock_path.parent.mkdir(parents=True)
            self.lock_path.write_text("not a directory", encoding="utf-8")
            code, stdout, stderr = self.invoke()

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["status"], "failed")
        self.collect.assert_not_called()

    def test_repeated_runs_do_not_leave_lock_held(self):
        self.assertEqual(runner.run_once()[0], 0)
        self.assertEqual(runner.run_once()[0], 0)
        self.assertEqual(self.collect.call_count, 2)
        self.assert_lock_available()

    def test_keyboard_interrupt_propagates_and_releases_lock(self):
        self.collect.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            runner.run_once()
        self.assert_lock_available()

    def test_cli_rejects_unrecognized_arguments(self):
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised:
                runner.main(["--activate-trading"])
        self.assertEqual(raised.exception.code, 2)
        self.collect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
