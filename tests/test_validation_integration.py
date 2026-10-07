from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause

from app.capital import research_store
from app.capital import validation_registry as registry
from app.capital import validation_access as access
from app.capital import run_evaluation as evaluation
from app.capital import run_validation as validation
from app.capital.research_models import (
    ResearchCandidate, ResearchStatus, ResearchVerdict,
)
from app.capital.validation_plan import research_snapshot
from app.capital.replay_manifest import capture_replay_manifest
from app.capital.offline_verification import verify_report
from app.capital.replay_analysis import analyze_report

fixture = runpy.run_path("tests/test_validation_plan.py")
START = datetime(2026, 9, 4, 14, tzinfo=timezone.utc)
END = START + timedelta(minutes=16)


class SyntheticSession(Session):
    commands = []

    def execute(self, statement, *args, **kwargs):
        if isinstance(statement, TextClause) and str(statement).startswith("SET "):
            command = str(statement)
            if command not in {
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
                "SET LOCAL statement_timeout = '30s'",
            }:
                raise AssertionError(f"Unexpected transaction command: {command}")
            self.commands.append(command)
            return None
        return super().execute(statement, *args, **kwargs)


class ValidationIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

        for target, name, value in (
            (registry, "DIRECTORY", self.directory / "registry"),
            (research_store, "STATE_DIRECTORY", self.directory / "research"),
            (research_store, "RESEARCH_STATE_FILE", self.directory / "research/state.json"),
            (research_store, "RESEARCH_LOCK_FILE", self.directory / "research/state.lock"),
            (evaluation, "EVALUATION_DIRECTORY", self.directory / "evaluations"),
        ):
            handle = patch.object(target, name, value)
            handle.start()
            self.addCleanup(handle.stop)

        for target in (registry, access):
            handle = patch.object(target, "now_utc", return_value=END + timedelta(days=1))
            handle.start()
            self.addCleanup(handle.stop)

        candidate = ResearchCandidate(
            research_id="synthetic_validation",
            strategy_name="mean_reversion_v2",
            display_name="Synthetic MR V2",
            hypothesis="Synthetic integration hypothesis",
            description="Synthetic only",
            market_regime="test",
            asset_universe=["SPY"],
            data_requirements=["prices"],
            risk_thesis="Synthetic only",
            success_criteria=["Synthetic integration criterion"],
            status=ResearchStatus.RESEARCHING,
            verdict=ResearchVerdict.PENDING,
            proposed_by="test",
            created_at=START.isoformat(),
            updated_at=START.isoformat(),
        )
        with research_store.locked_research_state(write=True) as state:
            state["candidates"][candidate.research_id] = candidate.to_dict()
        self.research_before = research_store.RESEARCH_STATE_FILE.read_bytes()

        draft = fixture["draft"]()
        draft.update({
            "research": research_snapshot(candidate),
            "strategy_version": "2.0",
            "start": START.isoformat(),
            "end_exclusive": END.isoformat(),
            "policy": json.loads(json.dumps(
                asdict(evaluation.POLICY), default=evaluation.encode
            )),
            "execution_manifest": capture_replay_manifest(),
        })
        with patch.object(
            registry, "now_utc", return_value=START - timedelta(days=1)
        ):
            self.registered = registry.register_plan(draft)

        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE market_assets "
                "(id INTEGER PRIMARY KEY, symbol TEXT, asset_type TEXT)"
            ))
            connection.execute(text(
                "CREATE TABLE price_observations "
                "(id INTEGER PRIMARY KEY, asset_id INTEGER, provider TEXT, "
                "price_usd NUMERIC, observed_at DATETIME)"
            ))
            connection.execute(text(
                "INSERT INTO market_assets VALUES (1, 'SPY', 'stock')"
            ))
            records = []
            for minute in range(-48, 16):
                price = 100 + (minute % 2) if minute < 0 else (
                    101 if minute == 15 else 98
                )
                records.append({
                    "id": minute + 49,
                    "price": price,
                    "time": (
                        START + timedelta(minutes=minute, seconds=-1)
                    ).strftime("%Y-%m-%d %H:%M:%S.%f"),
                })
            connection.execute(text(
                "INSERT INTO price_observations VALUES "
                "(:id, 1, 'Finnhub', :price, :time)"
            ), records)
            connection.execute(text("PRAGMA query_only = ON"))

        SyntheticSession.commands = []
        handle = patch.object(
            evaluation, "SessionLocal",
            side_effect=lambda: SyntheticSession(self.engine),
        )
        handle.start()
        self.addCleanup(handle.stop)

    def test_full_registered_replay(self):
        directory = validation.execute_registered(self.registered["plan_id"])
        report = json.loads((directory / "report.json").read_text())
        verification = json.loads((directory / "verification.json").read_text())
        analysis = json.loads((directory / "analysis.json").read_text())
        result = json.loads((directory / "result.json").read_text())

        self.assertEqual(report["designation"], "prospective_validation")
        self.assertEqual(len(report["windows"]), 16)
        self.assertEqual(verify_report(report), verification)
        self.assertEqual(analyze_report(report), analysis)
        for scenario in verification["scenarios"].values():
            self.assertEqual(scenario["matched_events"], 16)
            self.assertEqual(scenario["fills"], 2)
        for name, expected in result["artifacts_sha256"].items():
            self.assertEqual(
                hashlib.sha256((directory / name).read_bytes()).hexdigest(),
                expected,
            )
        self.assertEqual(
            report["validation_registration"]["sha256"],
            self.registered["registered_sha256"],
        )
        completed = registry.get_plan(self.registered["plan_id"])
        self.assertEqual(completed["status"], "completed")
        receipt = json.loads(completed["history"][-1]["detail"])
        assessment_path = directory / "assessment.json"
        assessment = json.loads(assessment_path.read_text())
        self.assertTrue(receipt["acceptance_assessed"])
        self.assertEqual(
            receipt["assessment_sha256"],
            hashlib.sha256(assessment_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            assessment["validation_status"], "insufficient_evidence"
        )
        self.assertFalse(assessment["promotion_authorized"])
        self.assertEqual(
            assessment["input_report_sha256"],
            hashlib.sha256((directory / "report.json").read_bytes()).hexdigest(),
        )
        self.assertFalse(result["promotion_authorized"])
        self.assertEqual(
            research_store.RESEARCH_STATE_FILE.read_bytes(), self.research_before
        )
        self.assertEqual(len(SyntheticSession.commands), 2)

        from app.capital.validation_research import attach_completed
        from app.capital.research_service import require_research_candidate
        attached = attach_completed(self.registered["plan_id"])
        self.assertEqual(
            attached["assessment"]["validation_status"], "insufficient_evidence"
        )
        from types import SimpleNamespace
        from app.capital.committee_models import CommitteeDecision
        from app.capital.committee_service import evaluate_strategy_committee
        experiment = SimpleNamespace(
            research_id="synthetic_validation", hypothesis_version=1,
            strategy_name="mean_reversion_v2", strategy_version="2.0",
        )
        with (
            patch("app.capital.committee_service.require_experiment",
                  return_value=experiment),
            patch("app.capital.committee_service.build_graduation_gates",
                  return_value=()),
            patch(
                "app.capital.committee_service.build_corporate_action_gate",
                return_value=None,
            ),
            patch(
                "app.capital.committee_service.build_corporate_action_gate",
                return_value=None,
                create=True,
            ),
            patch("app.capital.committee_service.get_experiment_provenance",
                  return_value={"status": "matched", "reasons": []}),
        ):
            committee = evaluate_strategy_committee(strategy_performance={
                "experiment_id": "synthetic", "strategy_name": "mean_reversion_v2",
                "portfolio_id": 1,
            })
        self.assertEqual(committee.decision, CommitteeDecision.CONTINUE)
        self.assertFalse(committee.graduation_eligible)
        validation_gate = next(
            gate for gate in committee.gate_results
            if gate.gate_name == "registered_validation"
        )
        self.assertEqual(validation_gate.status.value, "pending")
        self.assertIn("insufficient", validation_gate.rationale)
        again = attach_completed(self.registered["plan_id"])
        self.assertEqual(attached, again)
        candidate = require_research_candidate(research_id="synthetic_validation")
        self.assertEqual(len(candidate.validation_assessments), 1)
        self.assertEqual(candidate.status, ResearchStatus.RESEARCHING)
        self.assertEqual(candidate.verdict, ResearchVerdict.PENDING)

        original_assessment = assessment_path.read_bytes()
        assessment_path.write_bytes(original_assessment + b" ")
        with self.assertRaisesRegex(ValueError, "Assessment hash changed"):
            attach_completed(self.registered["plan_id"])
        assessment_path.write_bytes(original_assessment)

        with research_store.locked_research_state(write=True) as state:
            state["candidates"]["synthetic_validation"]["hypothesis"] = "Changed"
        with self.assertRaisesRegex(ValueError, "Research changed"):
            attach_completed(self.registered["plan_id"])


    def test_verified_recommendation_preserves_state(self):
        from app.capital.validation_research import inspect_recommendation

        plan_id = self.registered["plan_id"]
        directory = validation.execute_registered(plan_id)
        registry_before = (registry.DIRECTORY / "registry.json").read_bytes()
        assessment_before = (directory / "assessment.json").read_bytes()

        result = inspect_recommendation(plan_id)

        self.assertEqual(result["plan_id"], plan_id)
        self.assertEqual(
            result["plan_sha256"], self.registered["registered_sha256"]
        )
        self.assertEqual(result["research"]["research_id"], "synthetic_validation")
        self.assertEqual(result["validation_status"], "insufficient_evidence")
        self.assertEqual(result["recommendation"], "REVISE")
        self.assertEqual(result["research_verdict"], "inconclusive")
        self.assertIs(result["promotion_authorized"], False)
        self.assertIs(result["human_approval_required"], True)
        self.assertEqual(
            result["assessment_sha256"],
            hashlib.sha256(assessment_before).hexdigest(),
        )
        self.assertEqual(
            result["report_sha256"],
            hashlib.sha256((directory / "report.json").read_bytes()).hexdigest(),
        )
        self.assertEqual(result, inspect_recommendation(plan_id))
        self.assertEqual(
            research_store.RESEARCH_STATE_FILE.read_bytes(), self.research_before
        )
        self.assertEqual(
            (registry.DIRECTORY / "registry.json").read_bytes(), registry_before
        )
        self.assertEqual(
            (directory / "assessment.json").read_bytes(), assessment_before
        )

    def test_recommendation_rejects_tampered_assessment(self):
        from app.capital.validation_research import inspect_recommendation

        plan_id = self.registered["plan_id"]
        directory = validation.execute_registered(plan_id)
        path = directory / "assessment.json"
        path.write_bytes(path.read_bytes() + b" ")

        with self.assertRaisesRegex(ValueError, "Assessment hash changed"):
            inspect_recommendation(plan_id)
        self.assertEqual(
            research_store.RESEARCH_STATE_FILE.read_bytes(), self.research_before
        )

    def test_recommendation_rejects_changed_research(self):
        from app.capital.validation_research import inspect_recommendation

        plan_id = self.registered["plan_id"]
        validation.execute_registered(plan_id)
        with research_store.locked_research_state(write=True) as state:
            state["candidates"]["synthetic_validation"]["hypothesis"] = "Changed"
        before = research_store.RESEARCH_STATE_FILE.read_bytes()

        with self.assertRaisesRegex(ValueError, "Research changed"):
            inspect_recommendation(plan_id)
        self.assertEqual(research_store.RESEARCH_STATE_FILE.read_bytes(), before)

    def test_recommendation_rejects_unfinished_run(self):
        from app.capital.validation_research import inspect_recommendation

        with self.assertRaisesRegex(ValueError, "not completed"):
            inspect_recommendation(self.registered["plan_id"])
        self.assertEqual(
            research_store.RESEARCH_STATE_FILE.read_bytes(), self.research_before
        )

    def test_recommendation_blocks_source_mismatch(self):
        from app.capital.validation_research import inspect_recommendation

        plan_id = self.registered["plan_id"]
        validation.execute_registered(plan_id)
        changed = capture_replay_manifest()
        changed["source_sha256"] = {
            **changed["source_sha256"],
            "app/capital/position_simulation.py": "0" * 64,
        }

        with patch(
            "app.capital.replay_manifest.capture_replay_manifest",
            return_value=changed,
        ):
            with self.assertRaisesRegex(ValueError, "source_sha256"):
                inspect_recommendation(plan_id)
        self.assertEqual(
            research_store.RESEARCH_STATE_FILE.read_bytes(), self.research_before
        )


    def test_recommendation_persistence_is_idempotent(self):
        from app.capital.validation_research import (
            attach_completed, record_recommendation,
        )
        from app.capital.research_service import require_research_candidate

        plan_id = self.registered["plan_id"]
        directory = validation.execute_registered(plan_id)
        attach_completed(plan_id)
        before = require_research_candidate(
            research_id="synthetic_validation"
        ).to_dict()
        assessment_before = (directory / "assessment.json").read_bytes()
        registry_before = (registry.DIRECTORY / "registry.json").read_bytes()

        first = record_recommendation(plan_id)
        persisted_bytes = research_store.RESEARCH_STATE_FILE.read_bytes()
        second = record_recommendation(plan_id)
        after = require_research_candidate(
            research_id="synthetic_validation"
        ).to_dict()

        self.assertEqual(first, second)
        self.assertEqual(after["validation_recommendations"], [first])
        self.assertEqual(first["recommendation"], "REVISE")
        self.assertIs(first["promotion_authorized"], False)
        self.assertIs(first["human_approval_required"], True)
        self.assertTrue(first["recorded_at"])
        self.assertEqual(after["updated_at"], first["recorded_at"])
        for key in before:
            if key not in {"updated_at", "validation_recommendations"}:
                self.assertEqual(after[key], before[key], key)
        self.assertEqual(
            research_store.RESEARCH_STATE_FILE.read_bytes(), persisted_bytes
        )
        self.assertEqual(
            (directory / "assessment.json").read_bytes(), assessment_before
        )
        self.assertEqual(
            (registry.DIRECTORY / "registry.json").read_bytes(), registry_before
        )

    def test_persistence_rejects_conflicting_or_duplicate_records(self):
        from app.capital.validation_research import record_recommendation

        plan_id = self.registered["plan_id"]
        validation.execute_registered(plan_id)
        original = record_recommendation(plan_id)

        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                with research_store.locked_research_state(write=True) as state:
                    candidate = state["candidates"]["synthetic_validation"]
                    if duplicate:
                        candidate["validation_recommendations"] = [
                            dict(original), dict(original)
                        ]
                    else:
                        candidate["validation_recommendations"] = [{
                            **original, "recommendation": "PASS"
                        }]
                before = research_store.RESEARCH_STATE_FILE.read_bytes()
                message = "Duplicate" if duplicate else "differs"
                with self.assertRaisesRegex(ValueError, message):
                    record_recommendation(plan_id)
                self.assertEqual(
                    research_store.RESEARCH_STATE_FILE.read_bytes(), before
                )

    def test_persistence_reverifies_existing_recommendation(self):
        from app.capital.validation_research import record_recommendation

        plan_id = self.registered["plan_id"]
        directory = validation.execute_registered(plan_id)
        record_recommendation(plan_id)
        before = research_store.RESEARCH_STATE_FILE.read_bytes()

        path = directory / "assessment.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "Assessment hash changed"):
            record_recommendation(plan_id)
        self.assertEqual(
            research_store.RESEARCH_STATE_FILE.read_bytes(), before
        )


    def test_factory_blocks_verified_insufficient_evidence(self):
        from app.capital.validation_research import attach_completed
        from app.capital.experiment_factory_review import build_factory_review

        plan_id = self.registered["plan_id"]
        validation.execute_registered(plan_id)
        attach_completed(plan_id)

        with research_store.locked_research_state(write=True) as state:
            candidate = state["candidates"]["synthetic_validation"]
            candidate["status"] = "ready_for_experiment"
            candidate["verdict"] = "promising"

        before = research_store.RESEARCH_STATE_FILE.read_bytes()
        result = build_factory_review("synthetic_validation")

        self.assertFalse(result["eligible_for_operator_review"])
        self.assertEqual(result["validation_gate"]["status"], "pending")
        self.assertIn("insufficient", result["validation_gate"]["rationale"])
        self.assertIsNone(result["proposed_experiment"])
        self.assertFalse(result["creation_authorized"])
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["live_capital_authorized"])
        self.assertEqual(
            research_store.RESEARCH_STATE_FILE.read_bytes(), before
        )


    def test_analysis_failure_preserves_failure_packet(self):
        with patch.object(
            evaluation, "analyze_report",
            side_effect=RuntimeError("Synthetic analysis failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Synthetic analysis failure"):
                validation.execute_registered(self.registered["plan_id"])
        self.assertEqual(
            registry.get_plan(self.registered["plan_id"])["status"], "failed"
        )
        directories = list(evaluation.EVALUATION_DIRECTORY.iterdir())
        self.assertEqual(len(directories), 1)
        directory = directories[0]
        self.assertTrue((directory / "report.json").exists())
        self.assertTrue((directory / "verification.json").exists())
        self.assertTrue((directory / "failure.json").exists())
        self.assertFalse((directory / "result.json").exists())
        self.assertEqual(
            research_store.RESEARCH_STATE_FILE.read_bytes(), self.research_before
        )


    def test_assessment_failure_blocks_registry_completion(self):
        with patch(
            "app.capital.validation_assessment.assess_report",
            side_effect=RuntimeError("Synthetic assessment failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "assessment failure"):
                validation.execute_registered(self.registered["plan_id"])
        row = registry.get_plan(self.registered["plan_id"])
        self.assertEqual(row["status"], "failed")
        self.assertIn("assessment failure", row["history"][-1]["detail"])
        directory = next(evaluation.EVALUATION_DIRECTORY.iterdir())
        self.assertTrue((directory / "result.json").exists())
        self.assertFalse((directory / "assessment.json").exists())

    def test_changed_packet_blocks_assessment(self):
        original = validation.run

        def changed_packet(*args, **kwargs):
            directory = original(*args, **kwargs)
            path = directory / "report.json"
            path.write_bytes(path.read_bytes() + b" ")
            return directory

        with patch.object(validation, "run", side_effect=changed_packet):
            with self.assertRaisesRegex(ValueError, "artifact hashes"):
                validation.execute_registered(self.registered["plan_id"])
        self.assertEqual(
            registry.get_plan(self.registered["plan_id"])["status"], "failed"
        )
        directory = next(evaluation.EVALUATION_DIRECTORY.iterdir())
        self.assertFalse((directory / "assessment.json").exists())

    def test_assessment_write_failure_blocks_completion(self):
        original = validation.run

        def occupied_path(*args, **kwargs):
            directory = original(*args, **kwargs)
            (directory / "assessment.json").write_text("existing artifact")
            return directory

        with patch.object(validation, "run", side_effect=occupied_path):
            with self.assertRaises(FileExistsError):
                validation.execute_registered(self.registered["plan_id"])
        self.assertEqual(
            registry.get_plan(self.registered["plan_id"])["status"], "failed"
        )
        directory = next(evaluation.EVALUATION_DIRECTORY.iterdir())
        self.assertEqual(
            (directory / "assessment.json").read_text(), "existing artifact"
        )



    def test_registration_binding_failure_preserves_registry(self):
        draft = dict(self.registered["envelope"]["plan"])
        draft.pop("created_at")
        draft["research"] = dict(draft["research"])
        draft["research"]["hypothesis"] = "Not the registered research hypothesis"
        before = (registry.DIRECTORY / "registry.json").read_bytes()
        with patch.object(
            registry, "now_utc", return_value=START - timedelta(days=1)
        ):
            with self.assertRaisesRegex(ValueError, "Research hypothesis"):
                registry.register_plan(draft)
        self.assertEqual((registry.DIRECTORY / "registry.json").read_bytes(), before)

    def test_same_strategy_revision_preserves_lineage(self):
        from app.capital.research_revision import revise_research_candidate

        with research_store.locked_research_state(write=True) as state:
            parent = state["candidates"]["synthetic_validation"]
            parent["status"] = "revision_required"
            parent["verdict"] = "inconclusive"
            parent["validation_assessments"] = [{"plan_id": "historical"}]
            before = json.loads(json.dumps(parent))

        child = revise_research_candidate(
            parent_research_id="synthetic_validation",
            strategy_name="mean_reversion_v2",
            hypothesis="Test confirmation resets after each fill.",
            revision_reason="Position lifecycle correction.",
        )
        self.assertEqual(child.strategy_name, "mean_reversion_v2")
        self.assertEqual(child.parent_research_id, "synthetic_validation")
        self.assertEqual(child.hypothesis_version, before["hypothesis_version"] + 1)
        self.assertEqual(child.status, ResearchStatus.PROPOSED)
        self.assertEqual(child.verdict, ResearchVerdict.PENDING)
        self.assertEqual(child.validation_assessments, [])
        self.assertEqual(child.validation_recommendations, [])

        with research_store.locked_research_state() as state:
            parent = state["candidates"]["synthetic_validation"]
            self.assertEqual(parent["status"], "archived")
            for key, value in before.items():
                if key not in {"status", "updated_at"}:
                    self.assertEqual(parent[key], value, key)

        # Another eligible parent cannot create a competing active candidate.
        with research_store.locked_research_state(write=True) as state:
            other = dict(before)
            other["research_id"] = "other_parent"
            other["status"] = "rejected"
            state["candidates"]["other_parent"] = other
        saved = research_store.RESEARCH_STATE_FILE.read_bytes()

        with self.assertRaisesRegex(ValueError, "Active candidate already exists"):
            revise_research_candidate(
                parent_research_id="other_parent",
                strategy_name="mean_reversion_v2",
                hypothesis="Another revised hypothesis.",
                revision_reason="Duplicate protection test.",
            )
        self.assertEqual(research_store.RESEARCH_STATE_FILE.read_bytes(), saved)

    def test_revision_clears_validation_assessments(self):
        from app.capital.research_revision import revise_research_candidate

        recommendation = {
            "plan_id": "synthetic_previous",
            "recommendation": "REVISE",
            "promotion_authorized": False,
        }
        with research_store.locked_research_state(write=True) as state:
            parent = state["candidates"]["synthetic_validation"]
            parent["status"] = "rejected"
            parent["validation_assessments"] = [
                {"plan_id": "synthetic_previous"}
            ]
            parent["validation_recommendations"] = [recommendation]

        child = revise_research_candidate(
            parent_research_id="synthetic_validation",
            strategy_name="synthetic_revision",
            hypothesis="Revised synthetic hypothesis",
            revision_reason="Synthetic test",
        )
        self.assertEqual(child.validation_assessments, [])
        self.assertEqual(child.validation_recommendations, [])

        with research_store.locked_research_state() as state:
            parent = state["candidates"]["synthetic_validation"]
            self.assertEqual(
                parent["validation_assessments"],
                [{"plan_id": "synthetic_previous"}],
            )
            self.assertEqual(
                parent["validation_recommendations"],
                [recommendation],
            )




if __name__ == "__main__":
    unittest.main()
