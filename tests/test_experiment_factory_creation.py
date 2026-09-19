"""Atomic creation tests using a temporary database and synthetic approval."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import hashlib
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.capital import experiment_factory_creation as creation
from app.capital.experiment_factory_packet import packet_digest
from app.capital.experiment_factory_store import ExperimentFactoryRecord
from app.market_db.models import Portfolio


class FactoryCreationTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "factory.sqlite"
        self.engine = create_engine(f"sqlite:///{path}")
        self.addCleanup(self.engine.dispose)
        Portfolio.__table__.create(self.engine)
        ExperimentFactoryRecord.__table__.create(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.mock("SessionLocal", new=self.sessions)
        self.mock("os.getuid", return_value=1234)
        self.mock(
            "pwd.getpwuid", return_value=SimpleNamespace(pw_name="test-operator")
        )

        self.request = {
            "request_key": "agent:request-1",
            "research_id": "research_test",
            "requested_by": "research-agent",
        }
        with self.sessions.begin() as session:
            session.add(ExperimentFactoryRecord(
                **self.request, status="awaiting_review"
            ))

        self.payload = {
            "schema_version": 1,
            "action": "create_inactive_paper_experiment",
            "request": dict(self.request),
            "review": {
                "research": {
                    "research_id": "research_test",
                    "strategy_name": "mean_reversion_v2",
                    "hypothesis_version": 2,
                },
                "strategy": {"version": "2.0"},
                "proposed_experiment": {
                    "status": "planned",
                    "execution_mode": "disabled",
                    "portfolio_type": "paper",
                    "portfolio_active": False,
                    "duration_days": 180,
                    "starting_capital_usd": "1000.00",
                    "policy": {"name": "mean_reversion_v2_1000"},
                },
            },
        }
        self.packet = {
            "payload": self.payload,
            "sha256": packet_digest(self.payload),
        }
        self.prepare = self.mock("prepare_factory_packet")
        self.prepare.return_value = self.packet
        self.confirmation = {
            "request_key": self.request["request_key"],
            "packet_sha256": self.packet["sha256"],
            "operator": "test-operator",
            "operator_uid": 1234,
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
            "action": "create_inactive_paper_experiment",
        }

    def mock(self, name, **kwargs):
        handle = patch(
            f"app.capital.experiment_factory_creation.{name}", **kwargs
        )
        result = handle.start()
        self.addCleanup(handle.stop)
        return result

    def create(self):
        return creation.create_confirmed_experiment(self.confirmation)

    def assert_no_creation(self):
        with self.sessions() as session:
            row = session.get(
                ExperimentFactoryRecord, self.request["request_key"]
            )
            self.assertEqual(row.status, "awaiting_review")
            self.assertIsNone(row.portfolio_id)
            self.assertIsNone(row.approval_snapshot)
            self.assertEqual(session.scalars(select(Portfolio)).all(), [])

    def reseal(self):
        self.packet["sha256"] = packet_digest(self.payload)
        self.confirmation["packet_sha256"] = self.packet["sha256"]

    def test_atomic_creation_preserves_inactive_paper_boundary(self):
        result = self.create()
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["experiment"]["status"], "planned")
        self.assertEqual(result["experiment"]["execution_mode"], "disabled")
        self.assertIsNone(result["experiment"]["started_at"])
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["live_capital_authorized"])
        with self.sessions() as session:
            row = session.get(
                ExperimentFactoryRecord, self.request["request_key"]
            )
            portfolio = session.get(Portfolio, row.portfolio_id)
            self.assertEqual(portfolio.portfolio_type, "paper")
            self.assertIs(portfolio.is_active, False)
            self.assertEqual(str(portfolio.cash_balance_usd), "1000.00000000")
            self.assertEqual(row.approval_sha256, self.packet["sha256"])
            self.assertEqual(row.approval_snapshot["packet"], self.payload)
            self.assertEqual(row.approval_snapshot["confirmation"], self.confirmation)

    def test_identical_retry_creates_no_second_portfolio(self):
        first = self.create()
        with self.sessions() as session:
            before = deepcopy(session.get(
                ExperimentFactoryRecord, self.request["request_key"]
            ).approval_snapshot)
        self.assertEqual(first, self.create())
        with self.sessions() as session:
            self.assertEqual(len(session.scalars(select(Portfolio)).all()), 1)
            self.assertEqual(session.get(
                ExperimentFactoryRecord, self.request["request_key"]
            ).approval_snapshot, before)

    def test_existing_creation_rejects_different_approval(self):
        self.create()
        self.confirmation["packet_sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "different approval"):
            self.create()

    def test_changed_packet_requires_new_confirmation(self):
        self.payload["review"]["proposed_experiment"]["duration_days"] = 90
        self.packet["sha256"] = packet_digest(self.payload)
        with self.assertRaisesRegex(ValueError, "Review changed"):
            self.create()
        self.assert_no_creation()

    def test_blocked_reverification_creates_nothing(self):
        self.prepare.side_effect = ValueError("Evidence no longer passes")
        with self.assertRaisesRegex(ValueError, "Evidence"):
            self.create()
        self.assert_no_creation()

    def test_wrong_operator_is_rejected(self):
        self.confirmation["operator_uid"] = 5678
        with self.assertRaises(PermissionError):
            self.create()
        self.prepare.assert_not_called()
        self.assert_no_creation()

    def test_expired_confirmation_is_rejected(self):
        self.confirmation["confirmed_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=11)
        ).isoformat()
        with self.assertRaisesRegex(ValueError, "expired"):
            self.create()
        self.assert_no_creation()

    def test_unsafe_portfolio_configuration_is_rejected(self):
        self.payload["review"]["proposed_experiment"]["portfolio_active"] = True
        self.reseal()
        with self.assertRaisesRegex(ValueError, "inactive paper"):
            self.create()
        self.assert_no_creation()

    def test_invalid_capital_is_rejected(self):
        for value in ("NaN", "Infinity", "-1", "0"):
            with self.subTest(value=value):
                self.payload["review"]["proposed_experiment"][
                    "starting_capital_usd"
                ] = value
                self.reseal()
                with self.assertRaisesRegex(ValueError, "finite and positive"):
                    self.create()
                self.assert_no_creation()

    def test_portfolio_name_collision_is_not_reused(self):
        identity = hashlib.sha256(
            self.request["request_key"].encode("utf-8")
        ).hexdigest()[:32]
        with self.sessions.begin() as session:
            session.add(Portfolio(
                name=f"Jarvis Factory - {identity}",
                portfolio_type="paper",
                cash_balance_usd=25,
                is_active=True,
            ))
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.create()
        with self.sessions() as session:
            portfolios = session.scalars(select(Portfolio)).all()
            self.assertEqual(len(portfolios), 1)
            self.assertEqual(portfolios[0].cash_balance_usd, 25)
            self.assertIsNone(session.get(
                ExperimentFactoryRecord, self.request["request_key"]
            ).portfolio_id)

    def test_failure_after_portfolio_insert_rolls_back_both(self):
        original_flush = Session.flush

        def fail_record_flush(session, *args, **kwargs):
            if any(
                isinstance(row, ExperimentFactoryRecord)
                and row.status == "created"
                for row in session.dirty
            ):
                raise RuntimeError("Synthetic record write failure")
            return original_flush(session, *args, **kwargs)

        with patch.object(Session, "flush", fail_record_flush):
            with self.assertRaisesRegex(RuntimeError, "record write failure"):
                self.create()
        self.assert_no_creation()


if __name__ == "__main__":
    unittest.main()
