import json
from datetime import timedelta
from unittest.mock import patch
import runpy
import unittest

from app.capital import validation_collection as collection
from app.capital.observation_witness import make_receipt

context = runpy.run_path("tests/test_validation_integration.py")
Base = context["ValidationIntegrationTests"]
START, END = context["START"], context["END"]
registry = context["registry"]
validation = context["validation"]
evaluation = context["evaluation"]


class WitnessExecutionTests(Base):
    def prepare_collection(self):
        plan_id = self.registered["plan_id"]
        with patch.object(
            registry, "now_utc", return_value=START - timedelta(hours=2)
        ):
            collection.bind(plan_id)

        for minute in range(16):
            rows = []
            for source_minute in range(-48, minute + 1):
                price = (
                    100 + source_minute % 2 if source_minute < 0
                    else 101 if source_minute == 15 else 98
                )
                rows.append({
                    "id": source_minute + 49,
                    "asset_id": 1,
                    "provider": "Finnhub",
                    "price_usd": str(price),
                    "observed_at": (
                        START + timedelta(
                            minutes=source_minute, seconds=-1
                        )
                    ).isoformat(),
                })
            witnessed = START + timedelta(
                minutes=minute, milliseconds=-500
            )
            receipt = make_receipt(rows[-60:], witnessed)
            path = self.directory / f"witness_{minute}.json"
            path.write_text(json.dumps(receipt))
            with (
                patch.object(collection, "capture", return_value=path),
                patch.object(registry, "now_utc", return_value=witnessed),
            ):
                collection.capture_next(plan_id)

        with patch.object(
            registry, "now_utc", return_value=END + timedelta(seconds=1)
        ):
            collection.seal(plan_id)

    def test_witnessed_registered_execution(self):
        self.prepare_collection()
        with patch.object(
            evaluation, "load_historical_snapshot",
            side_effect=AssertionError("Legacy observation loader used"),
        ):
            directory = validation.execute_registered(
                self.registered["plan_id"]
            )
        report = json.loads((directory / "report.json").read_text())
        self.assertTrue(report["availability_verified"])
        self.assertEqual(len(report["witness_evidence"]["receipts"]), 16)
        self.assertEqual(
            context["verify_report"](report),
            json.loads((directory / "verification.json").read_text()),
        )
        assessment = json.loads((directory / "assessment.json").read_text())
        self.assertNotIn(
            "Historical data availability remains unverified.",
            assessment["insufficient_evidence_reasons"],
        )
        self.assertFalse(assessment["promotion_authorized"])
        self.assertEqual(
            registry.get_plan(self.registered["plan_id"])["status"],
            "completed",
        )

    def test_collection_substitution_blocks_completion(self):
        self.prepare_collection()
        original = validation.run

        def substitute(*args, **kwargs):
            directory = original(*args, **kwargs)
            path = directory / "report.json"
            report = json.loads(path.read_text())
            report["witness_collection"]["receipts"].pop()
            path.write_text(json.dumps(report))

            # Recompute outer artifact hashes: registry binding must
            # still reject substitution even when these hashes match.
            result_path = directory / "result.json"
            result = json.loads(result_path.read_text())
            import hashlib
            result["artifacts_sha256"]["report.json"] = (
                hashlib.sha256(path.read_bytes()).hexdigest()
            )
            result_path.write_text(json.dumps(result))
            return directory

        with patch.object(validation, "run", side_effect=substitute):
            with self.assertRaisesRegex(ValueError, "registered witness"):
                validation.execute_registered(self.registered["plan_id"])
        self.assertEqual(
            registry.get_plan(self.registered["plan_id"])["status"],
            "failed",
        )


if __name__ == "__main__":
    suite = unittest.TestSuite(
        WitnessExecutionTests(name) for name in (
            "test_witnessed_registered_execution",
            "test_collection_substitution_blocks_completion",
        )
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
