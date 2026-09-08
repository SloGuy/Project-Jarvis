from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.capital import validation_registry as registry
from app.capital import run_validation as runner
from app.capital.run_evaluation import run

fixture = runpy.run_path("tests/test_validation_plan.py")
AFTER = datetime(2030, 1, 3, tzinfo=timezone.utc)


class RegisteredRunnerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        for target, name, value in (
            (registry, "DIRECTORY", self.directory / "registry"),
            (registry, "now_utc", AFTER),
        ):
            patcher = patch.object(
                target, name,
                **({"return_value": value} if name == "now_utc" else {"new": value})
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        with patch.object(registry, "now_utc", return_value=fixture["NOW"]):
            self.row = registry.register_plan(fixture["draft"]())

    def fake_run(self, args, *, validation_record):
        self.assertEqual(args.asset_id, 1)
        self.assertEqual(str(args.fee_bps), "5")
        self.assertEqual(validation_record["status"], "running")
        packet = self.directory / "packet"
        packet.mkdir()
        (packet / "result.json").write_text(json.dumps({
            "status": "completed",
            "designation": "prospective_validation",
            "promotion_authorized": False,
            "validation_registration": {
                "plan_id": self.row["plan_id"],
                "sha256": self.row["registered_sha256"],
            },
        }))
        return packet

    def test_completion_and_no_second_run(self):
        with (
            patch.object(runner, "current_binding") as binding,
            patch.object(runner, "run", side_effect=self.fake_run) as execute,
        ):
            runner.execute_registered(self.row["plan_id"])
            self.assertEqual(binding.call_count, 3)
            with self.assertRaises(ValueError):
                runner.execute_registered(self.row["plan_id"])
            self.assertEqual(execute.call_count, 1)
        row = registry.get_plan(self.row["plan_id"])
        self.assertEqual(row["status"], "completed")
        receipt = json.loads(row["history"][-1]["detail"])
        self.assertFalse(receipt["acceptance_assessed"])

    def test_failure_recorded(self):
        with (
            patch.object(runner, "current_binding"),
            patch.object(runner, "run", side_effect=RuntimeError("Synthetic failure")),
        ):
            with self.assertRaisesRegex(RuntimeError, "Synthetic"):
                runner.execute_registered(self.row["plan_id"])
        self.assertEqual(
            registry.get_plan(self.row["plan_id"])["status"], "failed"
        )

    def test_preflight_binding_failure_does_not_claim(self):
        with (
            patch.object(runner, "current_binding", side_effect=ValueError("Changed")),
            patch.object(runner, "run") as execute,
        ):
            with self.assertRaises(ValueError):
                runner.execute_registered(self.row["plan_id"])
            execute.assert_not_called()
        self.assertEqual(
            registry.get_plan(self.row["plan_id"])["status"], "registered"
        )

    def test_actual_runner_rejects_changed_arguments_before_database(self):
        running = registry.claim_plan(self.row["plan_id"])
        plan = running["envelope"]["plan"]
        from decimal import Decimal
        args = SimpleNamespace(
            asset_id=plan["asset_id"], provider=plan["provider"],
            start=plan["start"], end=plan["end_exclusive"],
            fee_bps=Decimal("999"), slippage_bps=Decimal("5"),
        )
        with patch("app.capital.run_evaluation.SessionLocal") as database:
            with self.assertRaisesRegex(ValueError, "arguments differ"):
                run(args, validation_record=running)
            database.assert_not_called()

    def test_binding_change_after_execution_marks_failure(self):
        with (
            patch.object(
                runner, "current_binding",
                side_effect=[None, None, ValueError("Changed during run")],
            ),
            patch.object(runner, "run", side_effect=self.fake_run),
        ):
            with self.assertRaisesRegex(ValueError, "during run"):
                runner.execute_registered(self.row["plan_id"])
        self.assertEqual(
            registry.get_plan(self.row["plan_id"])["status"], "failed"
        )


if __name__ == "__main__":
    unittest.main()
