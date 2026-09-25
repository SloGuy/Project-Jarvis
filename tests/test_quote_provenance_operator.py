"""Operator tests without real database, provider, or storage access."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import patch

from app.capital import quote_provenance_operator as operator


class ProvenanceOperatorTests(unittest.TestCase):
    def setUp(self):
        self.asset = {
            "id": 212,
            "symbol": "CTVA",
            "asset_type": "stock",
            "provider_id": None,
            "is_active": True,
        }
        self.captured = {
            "record_id": "a" * 64,
            "path": "/temporary/provenance.json",
            "asset_id": 212,
            "symbol": "CTVA",
            "provider": "Finnhub REST",
            "provider_observed_at": "2026-09-25T12:00:00+00:00",
            "provider_time_status": "reported",
            "captured_at": "2026-09-25T12:00:05+00:00",
            "market_quote_freshness_verified": False,
            "trading_state_writes": False,
        }

        asset_patch = patch.object(
            operator, "read_asset", return_value=self.asset
        )
        capture_patch = patch.object(
            operator, "capture_quote_provenance", return_value=self.captured
        )
        environment_patch = patch.dict(
            operator.os.environ, {"FINNHUB_API_KEY": " test-secret "}
        )

        self.read_asset = asset_patch.start()
        self.capture = capture_patch.start()
        environment_patch.start()
        self.addCleanup(asset_patch.stop)
        self.addCleanup(capture_patch.stop)
        self.addCleanup(environment_patch.stop)

    def invoke(self, arguments):
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = operator.main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_run_capture_uses_resolved_asset_and_fixed_directory(self):
        result = operator.run_capture(212)
        self.read_asset.assert_called_once_with(212)
        self.capture.assert_called_once_with(
            asset=self.asset,
            directory=operator.PROVENANCE_DIRECTORY,
            finnhub_api_key="test-secret",
        )
        self.assertEqual(result["status"], "captured")
        self.assertEqual(result["record_id"], "a" * 64)

    def test_success_prints_json_and_returns_zero(self):
        code, stdout, stderr = self.invoke(["--asset-id", "212"])
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        result = json.loads(stdout)
        self.assertEqual(result["asset_id"], 212)
        self.assertEqual(result["status"], "captured")

    def test_asset_lookup_failure_prevents_provider_call(self):
        self.read_asset.side_effect = ValueError("asset unavailable")
        code, stdout, stderr = self.invoke(["--asset-id", "212"])
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["status"], "failed")
        self.capture.assert_not_called()

    def test_provider_error_cannot_expose_credentials(self):
        self.capture.side_effect = RuntimeError(
            "Request failed: https://example.invalid/quote?token=test-secret"
        )
        code, stdout, stderr = self.invoke(["--asset-id", "212"])
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertNotIn("test-secret", stderr)
        self.assertNotIn("example.invalid", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(json.loads(stderr)["status"], "failed")

    def test_storage_failure_does_not_claim_no_record_exists(self):
        self.capture.side_effect = OSError("directory sync failed")
        code, stdout, stderr = self.invoke(["--asset-id", "212"])
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        result = json.loads(stderr)
        self.assertIn("a record may exist", result["message"])
        self.assertNotIn("directory sync failed", stderr)

    def test_success_does_not_expose_credentials(self):
        code, stdout, stderr = self.invoke(["--asset-id", "212"])
        self.assertEqual(code, 0)
        self.assertNotIn("test-secret", stdout + stderr)

    def test_success_keeps_authority_boundaries(self):
        result = operator.run_capture(212)
        for field in (
            "database_writes",
            "legacy_observation_writes",
            "validation_receipt_writes",
            "execution_authorized",
            "live_capital_authorized",
            "trading_state_writes",
            "market_quote_freshness_verified",
        ):
            with self.subTest(field=field):
                self.assertFalse(result[field])

    def test_missing_asset_argument_is_rejected(self):
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised:
                operator.main([])
        self.assertEqual(raised.exception.code, 2)
        self.read_asset.assert_not_called()
        self.capture.assert_not_called()

    def test_noninteger_asset_argument_is_rejected(self):
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised:
                operator.main(["--asset-id", "BTC"])
        self.assertEqual(raised.exception.code, 2)
        self.read_asset.assert_not_called()

    def test_output_directory_cannot_be_overridden_through_cli(self):
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised:
                operator.main([
                    "--asset-id", "212",
                    "--directory", "/unexpected",
                ])
        self.assertEqual(raised.exception.code, 2)
        self.capture.assert_not_called()

    def test_keyboard_interrupt_is_not_hidden_as_capture_failure(self):
        self.capture.side_effect = KeyboardInterrupt()
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                operator.main(["--asset-id", "212"])


if __name__ == "__main__":
    unittest.main()
