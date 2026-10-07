"""Exercise provider replay through persisted assessment.

Quotes, registry, and receipts use temporary storage.
Input-source fingerprints, execution manifests, receipt checks,
strategy arithmetic, replay, analysis, and assessment use their real
implementations.
"""

from contextlib import redirect_stdout
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_validation_provider_verification as provider_fixture

from app.capital import validation_registry as registry
from app.capital import run_evaluation
from app.capital.offline_verification import verify_report
from app.capital.quote_provenance import make_quote_provenance
from app.capital.quote_provenance_store import save_quote_provenance
from app.capital.run_validation import persist_assessment
from app.capital.replay_manifest import capture_replay_manifest
from app.capital.validation_plan import seal_plan
from app.capital.validation_provider_collection import (
    quote_directory,
    refresh_collection,
    store_for,
)
from app.capital.validation_quote_receipt import (
    make_validation_quote_receipt,
)


class ProviderIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = provider_fixture.ProviderVerificationTests(
            methodName="test_matching_provider_report_is_verified"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

        base = self.fixture.fixture
        self.base = base

        draft = deepcopy(self.fixture.packet["envelope"]["plan"])
        draft.pop("created_at")
        draft["execution_manifest"] = capture_replay_manifest()
        draft["end_exclusive"] = (
            base.start + timedelta(minutes=3)
        ).isoformat()

        self.envelope = seal_plan(draft, now=base.created)
        self.plan = self.envelope["plan"]
        self.sha = self.envelope["sha256"]
        self.plan_id = "validation_provider_integration"

        self.row = {
            "plan_id": self.plan_id,
            "status": "running",
            "run_token": "isolated-run-token",
            "envelope": self.envelope,
            "registered_sha256": self.sha,
            "history": [{
                "status": "registered",
                "at": base.created.isoformat(),
            }],
        }

        root = base.directory / "integration"

        self.start_patch(
            "app.capital.validation_registry.DIRECTORY",
            root / "registry",
        )
        self.start_patch(
            "app.capital.run_evaluation.EVALUATION_DIRECTORY",
            root / "evaluations",
        )
        self.start_patch(
            "app.capital.validation_access.now_utc",
            return_value=base.end + timedelta(minutes=1),
        )

        self.database = self.start_patch(
            "app.capital.run_evaluation.SessionLocal",
            side_effect=AssertionError(
                "Provider replay attempted database access."
            ),
        )

    def start_patch(self, target, *arguments, **keywords):
        pending = patch(target, *arguments, **keywords)
        result = pending.start()
        self.addCleanup(pending.stop)
        return result

    def prepare_collection(self, *, include_period_quotes=True):
        collection = {
            "schema_version": 1,
            "kind": "provider_time_v1",
            "plan_sha256": self.sha,
            "bound_at": self.base.bound.isoformat(),
            "status": "collecting",
            "store_checkpoint": None,
        }
        store = store_for(self.row, collection)
        store.initialize()

        captures = [
            self.base.start
            - timedelta(minutes=20 - index)
            + timedelta(seconds=10)
            for index in range(20)
        ]

        if include_period_quotes:
            captures.extend(
                self.base.start
                + timedelta(seconds=10 + index * 60)
                for index in range(3)
            )

        directory = quote_directory(self.row)

        for index, captured in enumerate(captures):
            record = make_quote_provenance(
                asset_id=self.plan["asset_id"],
                symbol="BTC",
                asset_type="crypto",
                provider="CoinGecko REST",
                price_usd=str(Decimal("60000") + index * 10),
                provider_timestamp=int(
                    (captured - timedelta(seconds=10)).timestamp()
                ),
                captured_at=captured,
            )
            saved = save_quote_provenance(
                directory=directory,
                record=record,
            )
            receipt = make_validation_quote_receipt(
                envelope=self.envelope,
                expected_sha256=self.sha,
                bound_at=collection["bound_at"],
                recorded_at=(
                    captured + timedelta(seconds=1)
                ).isoformat(),
                directory=directory,
                record_id=saved["record_id"],
            )
            store.append(receipt)

        collection["store_checkpoint"] = store.seal()
        collection["status"] = "sealed"
        refresh_collection(collection)
        self.row["provider_collection"] = collection

        with registry.locked_state(write=True) as state:
            state["plans"][self.plan_id] = deepcopy(self.row)

    def evaluate(self, row=None):
        args = SimpleNamespace(
            asset_id=self.plan["asset_id"],
            provider=self.plan["provider"],
            start=self.plan["start"],
            end=self.plan["end_exclusive"],
            purpose="Isolated provider integration",
            fee_bps=Decimal(self.plan["fee_bps"]),
            slippage_bps=Decimal(self.plan["slippage_bps"]),
        )
        with redirect_stdout(io.StringIO()):
            return Path(run_evaluation.run(
                args,
                validation_record=self.row if row is None else row,
            ))

    def assess(self, directory):
        return persist_assessment(
            self.plan,
            directory,
            {"plan_id": self.plan_id, "sha256": self.sha},
        )

    def test_replay_and_assessment_complete_without_database(self):
        self.prepare_collection()
        directory = self.evaluate()
        result = self.assess(directory)

        report = json.loads(
            (directory / "report.json").read_text()
        )
        assessment = json.loads(
            (directory / "assessment.json").read_text()
        )

        self.assertEqual(len(report["windows"]), 3)
        self.assertIn("provider_evidence", report)
        self.assertNotIn("witness_evidence", report)
        self.assertTrue(report["availability_verified"])
        self.assertFalse(assessment["promotion_authorized"])
        self.assertEqual(
            result["validation_status"], "insufficient_evidence"
        )
        self.database.assert_not_called()

    def test_saved_packet_verifies_after_original_quotes_are_removed(self):
        self.prepare_collection()
        directory = self.evaluate()

        for path in quote_directory(self.row).glob("*.json"):
            path.unlink()

        report = json.loads(
            (directory / "report.json").read_text()
        )
        self.assertEqual(verify_report(report)["status"], "matched")
        self.assess(directory)
        self.database.assert_not_called()

    def test_stale_period_remains_insufficient(self):
        self.prepare_collection(include_period_quotes=False)
        directory = self.evaluate()
        result = self.assess(directory)

        report = json.loads(
            (directory / "report.json").read_text()
        )
        analysis = json.loads(
            (directory / "analysis.json").read_text()
        )

        self.assertFalse(report["availability_verified"])
        self.assertGreater(
            analysis["scenarios"]["specified_costs"]["data_quality"][
                "missing_or_stale_reference_ticks"
            ],
            0,
        )
        self.assertEqual(
            result["validation_status"], "insufficient_evidence"
        )

    def test_wrong_run_token_is_rejected(self):
        self.prepare_collection()
        changed = deepcopy(self.row)
        changed["run_token"] = "another-run-token"

        with self.assertRaises(ValueError):
            self.evaluate(changed)

    def test_rehashed_changed_packet_cannot_receive_assessment(self):
        self.prepare_collection()
        directory = self.evaluate()

        report_path = directory / "report.json"
        report = json.loads(report_path.read_text())
        report["provider_evidence"]["receipts"] = []
        report_path.write_text(json.dumps(report))

        result_path = directory / "result.json"
        result = json.loads(result_path.read_text())
        result["artifacts_sha256"]["report.json"] = hashlib.sha256(
            report_path.read_bytes()
        ).hexdigest()
        result_path.write_text(json.dumps(result))

        with self.assertRaises(ValueError):
            self.assess(directory)

        self.assertFalse((directory / "assessment.json").exists())


if __name__ == "__main__":
    unittest.main()
