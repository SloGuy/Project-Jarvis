"""Report publication tests using temporary files and mocked evidence."""
from contextlib import redirect_stderr, redirect_stdout
import fcntl
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.capital import portfolio_sampled_report_runner as runner


class SampledReportRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.report_file = self.root / "report.json"
        self.lock_file = self.root / "report.lock"
        self.checkpoints = self.root / "checkpoints"
        self.result = {
            "as_of": "2026-09-27T23:05:00+00:00",
            "loaded_checkpoint_count": 2,
            "selected_hourly_checkpoint_count": 1,
            "return_reports": [{
                "portfolio_id": 1,
                "return_count": 0,
            }],
            "analytics": {
                "risk_contribution": {"status": "insufficient_data"},
            },
        }
        self.resolved = {
            1: {
                "portfolio_name": "Test Paper",
                "strategy_name": "test",
                "experiment_id": "test-1",
            },
        }
        self.patch("REPORT_FILE", new=self.report_file)
        self.patch("LOCK_FILE", new=self.lock_file)
        self.patch("CHECKPOINT_DIRECTORY", new=self.checkpoints)
        self.resolver = self.patch(
            "_resolve_portfolios", return_value=self.resolved
        )
        self.reader = self.patch(
            "read_sampled_report_inputs", return_value=[]
        )
        self.builder = self.patch(
            "build_sampled_report", return_value=self.result
        )

    def patch(self, name, **kwargs):
        patcher = patch.object(runner, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def test_publishes_insufficient_data_report_with_bindings(self):
        code, summary = runner.run_once()
        self.assertEqual(code, 0)
        self.assertEqual(summary["status"], "published")
        self.assertEqual(summary["risk_status"], "insufficient_data")
        saved = json.loads(self.report_file.read_text())
        self.assertEqual(saved["portfolio_bindings"], [{
            "portfolio_id": 1, **self.resolved[1],
        }])
        self.reader.assert_called_once_with(directory=self.checkpoints)
        self.assertEqual(
            self.builder.call_args.kwargs["portfolio_ids"], [1]
        )

    def test_atomic_replacement_leaves_one_complete_report(self):
        self.report_file.write_text('{"old":true}')
        runner.publish_report({"new": [1, 2, 3]})
        self.assertEqual(
            json.loads(self.report_file.read_text()), {"new": [1, 2, 3]}
        )
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_invalid_json_value_preserves_previous_report(self):
        self.report_file.write_text('{"old":true}')
        with self.assertRaises(ValueError):
            runner.publish_report({"bad": float("nan")})
        self.assertEqual(self.report_file.read_text(), '{"old":true}')

    def test_replace_failure_preserves_previous_report(self):
        self.report_file.write_text('{"old":true}')
        with patch.object(
            runner.os, "replace", side_effect=OSError("replace failed")
        ):
            with self.assertRaises(OSError):
                runner.publish_report({"new": True})
        self.assertEqual(self.report_file.read_text(), '{"old":true}')
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_file_sync_failure_preserves_previous_report(self):
        self.report_file.write_text('{"old":true}')
        with patch.object(runner.os, "fsync", side_effect=OSError("sync failed")):
            with self.assertRaises(OSError):
                runner.publish_report({"new": True})
        self.assertEqual(self.report_file.read_text(), '{"old":true}')
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_directory_sync_failure_is_reported_after_publication(self):
        real_fsync = runner.os.fsync
        calls = 0

        def fail_second(descriptor):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("directory sync failed")
            return real_fsync(descriptor)

        with patch.object(runner.os, "fsync", side_effect=fail_second):
            with self.assertRaises(OSError):
                runner.publish_report({"new": True})
        self.assertEqual(
            json.loads(self.report_file.read_text()), {"new": True}
        )
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_busy_lock_prevents_work(self):
        with self.lock_file.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            code, result = runner.run_once()
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "busy")
        self.resolver.assert_not_called()
        self.reader.assert_not_called()

    def test_evidence_failure_preserves_report_and_releases_lock(self):
        self.report_file.write_text('{"old":true}')
        self.reader.side_effect = ValueError("corrupt evidence")
        with self.assertRaises(ValueError):
            runner.run_once()
        self.assertEqual(self.report_file.read_text(), '{"old":true}')
        self.reader.side_effect = None
        self.assertEqual(runner.run_once()[0], 0)

    def test_build_failure_does_not_publish(self):
        self.builder.side_effect = RuntimeError("build failed")
        with self.assertRaises(RuntimeError):
            runner.run_once()
        self.assertFalse(self.report_file.exists())

    def test_cli_success_outputs_summary(self):
        output = StringIO()
        with redirect_stdout(output):
            code = runner.main([])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "published")

    def test_cli_failure_suppresses_raw_exception(self):
        self.reader.side_effect = RuntimeError("private-error-content")
        errors = StringIO()
        with redirect_stderr(errors):
            code = runner.main([])
        self.assertEqual(code, 1)
        self.assertNotIn("private-error-content", errors.getvalue())
        self.assertEqual(json.loads(errors.getvalue())["status"], "failed")

    def test_summary_does_not_grant_authority(self):
        _, result = runner.run_once()
        self.assertIs(result["database_writes"], False)
        self.assertIs(result["execution_authorized"], False)


if __name__ == "__main__":
    unittest.main()
