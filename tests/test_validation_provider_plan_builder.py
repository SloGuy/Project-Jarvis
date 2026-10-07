"""Test future provider-time drafts without database access."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.capital import autonomy_validation_plan as builder
from app.capital.research_models import ResearchStatus
from app.capital.validation_input_contract import validate_input_contract


class ResearchFixture(SimpleNamespace):
    def to_dict(self):
        return {
            name: getattr(self, name)
            for name in (
                "research_id",
                "hypothesis_version",
                "strategy_name",
                "hypothesis",
                "asset_universe",
                "success_criteria",
            )
        }

class ProviderPlanBuilderTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(
            2026, 10, 7, 12, 0, tzinfo=timezone.utc
        )
        self.start = self.now + timedelta(minutes=30)

        self.candidate = ResearchFixture(
            research_id="research_isolated_builder",
            hypothesis_version=4,
            strategy_name="mean_reversion_v2",
            hypothesis="Test provider-timed validation inputs.",
            asset_universe=["BTC"],
            success_criteria=["At least 30 completed trades."],
            status=ResearchStatus.RESEARCHING,
        )
        self.strategy = SimpleNamespace(
            name="mean_reversion_v2",
            enabled=True,
            implementation_module=(
                "app.autonomous_trading.mean_reversion_v2_strategy"
            ),
            evaluator_name="evaluate_mean_reversion_v2_strategy",
            version="isolated-test-version",
        )

        self.start_patch(
            "require_research_candidate",
            return_value=self.candidate,
        )
        self.start_patch(
            "require_strategy",
            return_value=self.strategy,
        )
        self.asset_reader = self.start_patch(
            "read_btc_asset_id",
            return_value=1,
        )

    def start_patch(self, name, **arguments):
        pending = patch.object(builder, name, **arguments)
        result = pending.start()
        self.addCleanup(pending.stop)
        return result

    def build(self, **overrides):
        arguments = {
            "research_id": self.candidate.research_id,
            "start": self.start.isoformat(),
            "now": self.now,
        }
        arguments.update(overrides)
        return builder.build_validation_draft(**arguments)

    def test_new_draft_has_provider_time_contract(self):
        draft = self.build()

        self.assertEqual(draft["schema_version"], 2)
        self.assertEqual(
            draft["input_contract"]["kind"], "provider_time_v1"
        )
        validate_input_contract(
            draft["input_contract"],
            policy=draft["policy"],
            verify_sources=True,
        )
        self.assertNotIn("created_at", draft)

    def test_criteria_costs_and_window_are_preserved(self):
        draft = self.build()

        self.assertEqual(draft["criteria"], builder.CRITERIA)
        self.assertEqual(
            draft["criteria"]["minimum_completed_trades"], 30
        )
        self.assertEqual(draft["fee_bps"], "5")
        self.assertEqual(draft["slippage_bps"], "5")
        self.assertEqual(
            datetime.fromisoformat(draft["end_exclusive"]),
            self.start + timedelta(days=6),
        )

    def test_freshness_limit_matches_execution_policy(self):
        draft = self.build()
        contract = draft["input_contract"]

        self.assertEqual(
            contract["maximum_provider_age_seconds"],
            draft["policy"]["max_price_age_seconds"],
        )
        self.assertEqual(contract["capture_interval_seconds"], 60)

    def test_returned_criteria_do_not_mutate_defaults(self):
        original = deepcopy(builder.CRITERIA)
        draft = self.build()
        draft["criteria"]["minimum_completed_trades"] = 1

        self.assertEqual(builder.CRITERIA, original)

    def test_insufficient_lead_is_rejected_before_asset_lookup(self):
        with self.assertRaises(ValueError):
            self.build(
                start=(
                    self.now + timedelta(minutes=29)
                ).isoformat()
            )

        self.asset_reader.assert_not_called()

    def test_partial_minute_start_is_rejected(self):
        with self.assertRaises(ValueError):
            self.build(
                start=(
                    self.start + timedelta(seconds=1)
                ).isoformat()
            )

    def test_ineligible_research_is_rejected(self):
        self.candidate.status = "ineligible-test-fixture"

        with self.assertRaises(ValueError):
            self.build()

        self.asset_reader.assert_not_called()

    def test_wrong_asset_universe_is_rejected(self):
        self.candidate.asset_universe = ["ETH"]

        with self.assertRaises(ValueError):
            self.build()

    def test_disabled_strategy_is_rejected(self):
        self.strategy.enabled = False

        with self.assertRaises(ValueError):
            self.build()

    def test_asset_lookup_failure_is_propagated(self):
        self.asset_reader.side_effect = ValueError(
            "An active BTC asset is required."
        )

        with self.assertRaises(ValueError):
            self.build()


if __name__ == "__main__":
    unittest.main()
