"""Opt-in autonomous creation tests in disposable PostgreSQL schemas."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.market_db.database import engine
from app.market_db.models import Portfolio
from app.market_db.migrate_paper_lifecycle import migrate_paper_lifecycle
from app.capital import autonomy_paper_creation as creation
from app.capital.autonomy_policy import CapitalOperatingPolicy
from app.capital.experiment_factory_store import ExperimentFactoryRecord
from app.capital.paper_lifecycle_store import PaperLifecycleRecord


@unittest.skipUnless(
    os.environ.get("PAPER_LIFECYCLE_POSTGRES_TEST") == "1",
    "Set PAPER_LIFECYCLE_POSTGRES_TEST=1 for isolated database tests.",
)
class AutonomousPaperCreationTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(engine.dialect.name, "postgresql")
        self.schema = "test_auto_paper_" + uuid4().hex
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.addCleanup(self.drop_schema)
        self.db = engine.execution_options(
            schema_translate_map={None: self.schema}
        )
        with self.db.begin() as connection:
            Portfolio.__table__.create(connection)
            ExperimentFactoryRecord.__table__.create(connection)
        migrate_paper_lifecycle(schema=self.schema, database_engine=engine)
        self.sessions = sessionmaker(bind=self.db, expire_on_commit=False)
        self.add_request("request-1", "research-1")
        self.review = {
            "eligible_for_operator_review": True,
            "blockers": [],
            "validation_gate": {"status": "passed"},
            "verified_plan_bindings": [
                {"plan_id": "test-plan", "plan_sha256": "a" * 64}
            ],
            "research": {
                "research_id": "research-1",
                "strategy_name": "mean_reversion_v2",
                "hypothesis_version": 1,
                "asset_universe": ["BTC"],
            },
            "strategy": {"version": "2.0"},
            "proposed_experiment": {
                "portfolio_type": "paper",
                "portfolio_active": False,
                "status": "planned",
                "execution_mode": "disabled",
                "starting_capital_usd": "1000",
                "duration_days": 180,
                "policy": {"name": "test-policy"},
            },
            "creation_authorized": False,
            "execution_authorized": False,
            "live_capital_authorized": False,
            "human_approval_required": True,
        }
        self.patch("SessionLocal", self.sessions)
        self.control = self.patch(
            "read_operating_policy",
            return_value=CapitalOperatingPolicy(enabled=True),
        )
        self.review_mock = self.patch(
            "build_factory_review", side_effect=self.make_review
        )

    def patch(self, name, *args, **kwargs):
        patcher = patch.object(creation, name, *args, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def drop_schema(self):
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))

    def make_review(self, research_id):
        review = deepcopy(self.review)
        review["research"]["research_id"] = research_id
        return review

    def add_request(self, key, research):
        with self.sessions.begin() as session:
            session.add(ExperimentFactoryRecord(
                request_key=key,
                research_id=research,
                requested_by="capital.lifecycle",
                status="awaiting_review",
            ))

    def create(self, key="request-1"):
        return creation.create_autonomous_paper_experiment(key)

    def assert_empty(self):
        with self.sessions() as session:
            for model in (Portfolio, PaperLifecycleRecord):
                self.assertEqual(
                    session.scalar(select(func.count()).select_from(model)), 0
                )
            record = session.get(ExperimentFactoryRecord, "request-1")
            self.assertEqual(record.status, "awaiting_review")
            self.assertIsNone(record.portfolio_id)

    def test_creation_is_inactive_and_policy_attributed(self):
        result = self.create()
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["live_capital_authorized"])
        with self.sessions() as session:
            record = session.get(ExperimentFactoryRecord, "request-1")
            lifecycle = session.get(PaperLifecycleRecord, "request-1")
            portfolio = session.get(Portfolio, record.portfolio_id)
            self.assertFalse(portfolio.is_active)
            self.assertEqual(portfolio.portfolio_type, "paper")
            self.assertEqual(portfolio.cash_balance_usd, 1000)
            self.assertEqual(lifecycle.status, "planned")
            self.assertEqual(lifecycle.allocation_usd, 0)
            self.assertEqual(record.approved_by, "capital.lifecycle")
            authorization = record.approval_snapshot["authorization"]
            self.assertEqual(authorization["basis"], "operating_policy")
            self.assertFalse(authorization["human_approval_recorded"])
            self.assertEqual(
                lifecycle.authorization_snapshot, record.approval_snapshot
            )

    def test_identical_retry_preserves_creation(self):
        first = self.create()
        with self.sessions() as session:
            original = deepcopy(session.get(
                ExperimentFactoryRecord, "request-1"
            ).approval_snapshot)
        self.assertEqual(first, self.create())
        self.assertEqual(self.review_mock.call_count, 1)
        with self.sessions() as session:
            self.assertEqual(original, session.get(
                ExperimentFactoryRecord, "request-1"
            ).approval_snapshot)

    def test_blocked_evidence_creates_nothing(self):
        self.review["eligible_for_operator_review"] = False
        self.review["blockers"] = ["Validation has not passed."]
        with self.assertRaises(ValueError):
            self.create()
        self.assert_empty()

    def test_missing_verified_bindings_creates_nothing(self):
        self.review["verified_plan_bindings"] = []
        with self.assertRaises(ValueError):
            self.create()
        self.assert_empty()

    def test_disabled_and_paused_controls_block_creation(self):
        for policy in (
            CapitalOperatingPolicy(enabled=False),
            CapitalOperatingPolicy(enabled=True, paused=True),
        ):
            with self.subTest(policy=policy):
                self.control.return_value = policy
                with self.assertRaises(PermissionError):
                    self.create()
                self.assert_empty()

    def test_rechecks_control_after_evidence_review(self):
        self.control.side_effect = [
            CapitalOperatingPolicy(enabled=True),
            CapitalOperatingPolicy(enabled=False),
        ]
        with self.assertRaises(PermissionError):
            self.create()
        self.assert_empty()

    def test_wrong_scope_and_cash_are_rejected(self):
        original = deepcopy(self.review)
        for field, value in (
            ("portfolio_type", "live"),
            ("portfolio_active", True),
            ("starting_capital_usd", "2000"),
            ("starting_capital_usd", "NaN"),
        ):
            with self.subTest(field=field, value=value):
                self.review = deepcopy(original)
                self.review["proposed_experiment"][field] = value
                with self.assertRaises(ValueError):
                    self.create()
                self.assert_empty()

    def test_failure_after_flush_rolls_back_everything(self):
        with patch.object(creation, "result", side_effect=RuntimeError("test")):
            with self.assertRaises(RuntimeError):
                self.create()
        self.assert_empty()

    def test_corrupted_retry_binding_is_rejected(self):
        self.create()
        with self.sessions.begin() as session:
            record = session.get(ExperimentFactoryRecord, "request-1")
            record.approval_sha256 = "0" * 64
        with self.assertRaises(ValueError):
            self.create()

    def test_concurrent_requests_share_capacity_limit(self):
        self.add_request("request-2", "research-2")

        def attempt(key):
            try:
                return self.create(key)["status"]
            except ValueError as error:
                if "capacity is full" not in str(error):
                    raise
                return "capacity_blocked"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, ["request-1", "request-2"]))
        self.assertCountEqual(outcomes, ["created", "capacity_blocked"])
        with self.sessions() as session:
            self.assertEqual(session.scalar(
                select(func.count()).select_from(Portfolio)
            ), 1)


if __name__ == "__main__":
    unittest.main()
