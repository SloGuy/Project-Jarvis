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
        self.assertFalse(result["promotion_authorized"])
        self.assertEqual(
            research_store.RESEARCH_STATE_FILE.read_bytes(), self.research_before
        )
        self.assertEqual(len(SyntheticSession.commands), 2)

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


if __name__ == "__main__":
    unittest.main()
