"""Opt-in lifecycle tests using isolated PostgreSQL paper portfolios."""

import os
import unittest
from copy import deepcopy
from unittest.mock import patch

from app.market_db.models import Portfolio, PortfolioPosition, MarketAsset
from app.capital import autonomy_paper_lifecycle as lifecycle
from app.capital.autonomy_policy import CapitalOperatingPolicy
from app.capital.experiment_factory_store import ExperimentFactoryRecord
from app.capital.paper_lifecycle_store import PaperLifecycleRecord
import test_capital_autonomy_paper_creation as fixtures


@unittest.skipUnless(
    os.environ.get("PAPER_LIFECYCLE_POSTGRES_TEST") == "1",
    "Set PAPER_LIFECYCLE_POSTGRES_TEST=1 for isolated database tests.",
)
class AutonomousPaperLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AutonomousPaperCreationTests(
            methodName="test_creation_is_inactive_and_policy_attributed"
        )
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        with self.fixture.db.begin() as connection:
            MarketAsset.__table__.create(connection)
            PortfolioPosition.__table__.create(connection)
        self.fixture.create()
        self.sessions = self.fixture.sessions
        self.patch("SessionLocal", self.sessions)
        self.control = self.patch(
            "read_operating_policy",
            return_value=CapitalOperatingPolicy(enabled=True),
        )
        self.review = self.patch(
            "build_factory_review", side_effect=self.fixture.make_review
        )

    def patch(self, name, *args, **kwargs):
        patcher = patch.object(lifecycle, name, *args, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def change(self, target="active", version=1, key="decision-1"):
        return lifecycle.transition_paper_experiment(
            request_key="request-1",
            target=target,
            decision_key=key,
            expected_version=version,
            reason="Isolated lifecycle test.",
        )

    def snapshot(self):
        with self.sessions() as session:
            row = session.get(PaperLifecycleRecord, "request-1")
            portfolio = session.get(Portfolio, row.portfolio_id)
            factory = session.get(ExperimentFactoryRecord, "request-1")
            return {
                "status": row.status,
                "version": row.version,
                "allocation": row.allocation_usd,
                "history": deepcopy(row.transition_history),
                "active": portfolio.is_active,
                "cash": portfolio.cash_balance_usd,
                "approval": deepcopy(factory.approval_snapshot),
            }

    def test_activation_preserves_cash_and_creation_evidence(self):
        before = self.snapshot()
        result = self.change()
        after = self.snapshot()
        self.assertTrue(result["entry_enabled"])
        self.assertFalse(result["live_capital_authorized"])
        self.assertEqual(after["status"], "active")
        self.assertEqual(after["version"], 2)
        self.assertEqual(after["allocation"], 1000)
        self.assertTrue(after["active"])
        self.assertEqual(after["cash"], before["cash"])
        self.assertEqual(after["approval"], before["approval"])

    def test_changed_review_blocks_activation(self):
        self.fixture.review["strategy"]["version"] = "changed"
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.change()
        self.assertEqual(self.snapshot(), before)

    def test_failed_validation_blocks_activation(self):
        self.fixture.review["validation_gate"]["status"] = "pending"
        with self.assertRaises(ValueError):
            self.change()
        self.assertEqual(self.snapshot()["status"], "planned")

    def test_stale_version_changes_nothing(self):
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.change(version=2)
        self.assertEqual(self.snapshot(), before)

    def test_identical_retry_preserves_history(self):
        first = self.change()
        before = self.snapshot()
        self.assertEqual(first, self.change())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.review.call_count, 1)

    def test_conflicting_decision_key_is_rejected(self):
        self.change()
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.change(target="paused", version=2)
        self.assertEqual(self.snapshot(), before)

    def test_old_retry_does_not_reactivate_paused_account(self):
        activation = self.change()
        self.change(target="paused", version=2, key="pause-1")
        before = self.snapshot()
        self.assertEqual(self.change(), activation)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(before["status"], "paused")

    def test_pause_and_demotion_preserve_exit_access(self):
        self.change()
        self.control.return_value = CapitalOperatingPolicy(
            enabled=True, paused=True
        )
        self.change(target="paused", version=2, key="pause-1")
        paused = self.snapshot()
        self.assertEqual(paused["allocation"], 0)
        self.assertTrue(paused["active"])
        self.change(target="demoted", version=3, key="demote-1")
        demoted = self.snapshot()
        self.assertEqual(demoted["allocation"], 0)
        self.assertTrue(demoted["active"])
        self.assertEqual(demoted["cash"], paused["cash"])

    def test_paused_control_blocks_activation(self):
        self.control.return_value = CapitalOperatingPolicy(
            enabled=True, paused=True
        )
        with self.assertRaises(PermissionError):
            self.change()
        self.assertEqual(self.snapshot()["status"], "planned")

    def test_control_rechecked_after_evidence_review(self):
        self.control.side_effect = [
            CapitalOperatingPolicy(enabled=True),
            CapitalOperatingPolicy(enabled=False),
        ]
        before = self.snapshot()
        with self.assertRaises(PermissionError):
            self.change()
        self.assertEqual(self.snapshot(), before)

    def test_empty_portfolio_can_retire_but_cannot_restart(self):
        self.change(target="retired")
        retired = self.snapshot()
        self.assertEqual(retired["status"], "retired")
        self.assertFalse(retired["active"])
        self.assertEqual(retired["allocation"], 0)
        with self.assertRaises(ValueError):
            self.change(target="active", version=2, key="restart")
        self.assertEqual(self.snapshot(), retired)

    def test_remaining_holdings_block_retirement(self):
        with self.sessions.begin() as session:
            asset = MarketAsset(
                symbol="BTC", asset_type="crypto", is_active=True
            )
            session.add(asset)
            session.flush()
            row = session.get(PaperLifecycleRecord, "request-1")
            session.add(PortfolioPosition(
                portfolio_id=row.portfolio_id,
                asset_id=asset.id,
                quantity=1,
                average_cost_usd=100,
            ))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "remaining holdings"):
            self.change(target="retired")
        self.assertEqual(self.snapshot(), before)

    def test_nonpaper_portfolio_is_rejected(self):
        with self.sessions.begin() as session:
            row = session.get(PaperLifecycleRecord, "request-1")
            portfolio = session.get(Portfolio, row.portfolio_id)
            portfolio.portfolio_type = "live"
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.change()
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
