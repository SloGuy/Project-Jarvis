"""Isolated runner tests; strategy signals and execution are mocked."""

import os
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from app.market_db import database
from app.capital import mean_reversion_v2_paper_runner as runner
from app.autonomous_trading.strategy import StrategyAction
import test_capital_autonomy_paper_execution as fixtures


@unittest.skipUnless(
    os.environ.get("PAPER_LIFECYCLE_POSTGRES_TEST") == "1",
    "Set PAPER_LIFECYCLE_POSTGRES_TEST=1 for isolated database tests.",
)
class ManagedPaperRunnerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ManagedPaperExecutionTests(
            methodName="test_active_buy_updates_cash_position_and_transaction"
        )
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.portfolio_id = self.fixture.portfolio_id
        self.patch(database, "SessionLocal", self.fixture.sessions)
        self.portfolio = {
            "status": "success",
            "total_value_usd": 1000,
            "cash_balance_usd": 1000,
            "positions": [],
        }
        self.patch(
            runner, "get_portfolio_summary", return_value=self.portfolio
        )
        self.legacy = self.patch(
            runner, "get_or_create_mean_reversion_v2_portfolio",
            return_value=SimpleNamespace(id=999),
        )
        self.universe = self.patch(
            runner, "get_mean_reversion_universe", return_value=["ETH"]
        )
        self.patch(
            runner, "_position_context",
            side_effect=lambda **kw: SimpleNamespace(
                has_position=kw["position"] is not None
            ),
        )
        self.risk = self.patch(
            runner, "evaluate_exit_rules",
            return_value=SimpleNamespace(should_exit=False),
        )
        self.snapshots = self.patch(
            runner, "get_mean_reversion_snapshot",
            return_value=SimpleNamespace(
                latest_price_usd=Decimal("100"),
                mean_price_usd=Decimal("110"),
                standard_deviation_usd=Decimal("5"),
                z_score=Decimal("-2"),
                observation_count=30,
            ),
        )
        self.signal = self.patch(
            runner, "evaluate_mean_reversion_v2_strategy",
            return_value=self.candidate(StrategyAction.HOLD),
        )
        self.patch(
            runner, "create_strategy_candidate",
            side_effect=lambda **kw: SimpleNamespace(**kw),
        )
        self.patch(
            runner, "load_entry_exit_context",
            return_value=(Decimal("110"), runner.datetime.now(runner.timezone.utc)),
        )
        self.fixed_exit = self.patch(
            runner, "evaluate_fixed_exit", return_value=None
        )
        self.pipeline = self.patch(
            runner, "process_candidate",
            return_value=SimpleNamespace(
                proposal_created=False,
                decision_logged=False,
                risk_approved=None,
                risk_reasons=(),
                decision_id=None,
                execution_attempted=False,
                execution_status="not_applicable",
                execution_reason="Isolated test.",
                transaction_id=None,
            ),
        )

    def patch(self, module, name, *args, **kwargs):
        patcher = patch.object(module, name, *args, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def candidate(self, action):
        return SimpleNamespace(
            action=action,
            confidence_percent=Decimal("100"),
            rationale="Isolated signal.",
        )

    def holding(self):
        self.portfolio["positions"] = [{
            "symbol": "BTC",
            "asset_id": 1,
            "quantity": 1,
            "latest_price_usd": 100,
        }]

    def run_managed(self, **kwargs):
        return runner.run_mean_reversion_v2_paper_cycle(
            portfolio_id=self.portfolio_id, **kwargs
        )

    def test_managed_cycle_uses_explicit_portfolio_and_btc(self):
        result = self.run_managed()
        self.assertEqual(result["portfolio_id"], self.portfolio_id)
        self.snapshots.assert_called_once_with(symbol="BTC")
        self.legacy.assert_not_called()
        self.universe.assert_not_called()
        self.assertEqual(
            self.pipeline.call_args.kwargs["portfolio_id"],
            self.portfolio_id,
        )

    def test_legacy_cycle_retains_default_portfolio_and_universe(self):
        result = runner.run_mean_reversion_v2_paper_cycle()
        self.assertEqual(result["portfolio_id"], 999)
        self.legacy.assert_called_once()
        self.universe.assert_called_once()
        self.snapshots.assert_called_once_with(symbol="ETH")

    def test_unknown_managed_portfolio_never_reaches_pipeline(self):
        with self.assertRaises(ValueError):
            runner.run_mean_reversion_v2_paper_cycle(portfolio_id=999999)
        self.pipeline.assert_not_called()

    def test_boolean_portfolio_id_is_rejected(self):
        with self.assertRaises(ValueError):
            runner.run_mean_reversion_v2_paper_cycle(portfolio_id=True)
        self.pipeline.assert_not_called()

    def test_sell_without_exit_rule_is_rejected_before_execution(self):
        self.holding()
        self.signal.return_value = self.candidate(StrategyAction.SELL)
        with self.assertRaisesRegex(RuntimeError, "no exit rule"):
            self.run_managed()
        self.pipeline.assert_not_called()

    def test_paused_cycle_still_evaluates_fixed_exit(self):
        self.fixture.fixture.change(
            target="paused", version=2, key="pause-runner"
        )
        self.holding()
        self.fixed_exit.return_value = "timeout"
        result = self.run_managed()
        self.fixed_exit.assert_called_once()
        self.assertEqual(
            self.pipeline.call_args.kwargs["candidate"].action,
            StrategyAction.SELL,
        )
        self.assertEqual(result["results"][0]["exit_rule"], "timeout")

    def test_risk_exit_reaches_pipeline(self):
        self.holding()
        self.risk.return_value = SimpleNamespace(
            should_exit=True,
            rationale="Risk exit.",
            rule=SimpleNamespace(value="stop_loss"),
        )
        result = self.run_managed()
        self.fixed_exit.assert_not_called()
        self.assertEqual(
            self.pipeline.call_args.kwargs["candidate"].action,
            StrategyAction.SELL,
        )
        self.assertEqual(result["results"][0]["exit_rule"], "stop_loss")


if __name__ == "__main__":
    unittest.main()
