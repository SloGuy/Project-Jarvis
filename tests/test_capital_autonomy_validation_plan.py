"""Validation draft tests without production database access."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.capital import autonomy_validation_plan as builder
from app.capital.research_models import (
    ResearchCandidate,
    ResearchStatus,
    ResearchVerdict,
)


NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)


class CapitalValidationPlanTests(unittest.TestCase):
    def setUp(self):
        self.candidate = ResearchCandidate(
            research_id="research_test",
            strategy_name="mean_reversion_v2",
            display_name="Test research",
            hypothesis="A testable hypothesis.",
            description="Test description",
            market_regime="unspecified",
            asset_universe=["BTC"],
            data_requirements=["Verified observations"],
            risk_thesis="Losses remain possible.",
            success_criteria=["Meet registered criteria after costs"],
            status=ResearchStatus.PROPOSED,
            verdict=ResearchVerdict.PENDING,
            proposed_by="capital.research",
            created_at=NOW.isoformat(),
            updated_at=NOW.isoformat(),
        )
        self.strategy = SimpleNamespace(
            name="mean_reversion_v2",
            enabled=True,
            version="2.0",
            implementation_module=(
                "app.autonomous_trading.mean_reversion_v2_strategy"
            ),
            evaluator_name="evaluate_mean_reversion_v2_strategy",
        )

        self.mock("require_research_candidate", return_value=self.candidate)
        self.mock("require_strategy", return_value=self.strategy)
        self.mock(
            "capture_replay_manifest",
            return_value={"fixture_source": "fixture_hash"},
        )
        self.database = self.mock("SessionLocal")
        self.session = self.database.return_value.__enter__.return_value
        self.session.scalar.return_value = SimpleNamespace(
            id=42, is_active=True
        )

    def mock(self, name, **kwargs):
        mocked = patch.object(builder, name, **kwargs)
        result = mocked.start()
        self.addCleanup(mocked.stop)
        return result

    def build(self, **overrides):
        arguments = {
            "research_id": self.candidate.research_id,
            "start": (NOW + timedelta(hours=1)).isoformat(),
            "now": NOW,
        }
        arguments.update(overrides)
        return builder.build_validation_draft(**arguments)

    def test_supported_draft_uses_application_configuration(self):
        draft = self.build()
        self.assertEqual(draft["asset_id"], 42)
        self.assertEqual(draft["provider"], "CoinGecko")
        self.assertEqual(draft["created_by"], "capital.validation")
        self.assertEqual(draft["fee_bps"], "5")
        self.assertEqual(draft["slippage_bps"], "5")
        self.assertEqual(draft["criteria"], builder.CRITERIA)
        self.assertNotIn("created_at", draft)
        duration = (
            datetime.fromisoformat(draft["end_exclusive"])
            - datetime.fromisoformat(draft["start"])
        )
        self.assertEqual(duration, timedelta(days=6))

    def test_exact_minimum_lead_is_accepted(self):
        self.build(start=(NOW + timedelta(minutes=30)).isoformat())

    def test_short_lead_is_rejected_before_database_read(self):
        with self.assertRaises(ValueError):
            self.build(start=(NOW + timedelta(minutes=29)).isoformat())
        self.database.assert_not_called()

    def test_fractional_minute_start_is_rejected(self):
        with self.assertRaises(ValueError):
            self.build(
                start=(NOW + timedelta(hours=1, seconds=1)).isoformat()
            )
        self.database.assert_not_called()

    def test_naive_current_time_is_rejected(self):
        with self.assertRaises(ValueError):
            self.build(now=NOW.replace(tzinfo=None))

    def test_naive_start_is_rejected(self):
        with self.assertRaises(ValueError):
            self.build(start="2026-09-28T13:00:00")

    def test_closed_or_revision_required_research_is_rejected(self):
        for status in (
            ResearchStatus.ARCHIVED,
            ResearchStatus.REJECTED,
            ResearchStatus.REVISION_REQUIRED,
        ):
            with self.subTest(status=status):
                self.candidate.status = status
                with self.assertRaises(ValueError):
                    self.build()
        self.database.assert_not_called()

    def test_other_asset_universes_are_rejected(self):
        for universe in (["ETH"], ["BTC", "ETH"], []):
            with self.subTest(universe=universe):
                self.candidate.asset_universe = universe
                with self.assertRaises(ValueError):
                    self.build()

    def test_disabled_strategy_is_rejected(self):
        self.strategy.enabled = False
        with self.assertRaises(ValueError):
            self.build()
        self.database.assert_not_called()

    def test_changed_implementation_is_rejected(self):
        self.strategy.implementation_module = "another.module"
        with self.assertRaises(ValueError):
            self.build()

    def test_missing_or_inactive_asset_is_rejected(self):
        for asset in (None, SimpleNamespace(id=42, is_active=False)):
            with self.subTest(asset=asset):
                self.session.scalar.return_value = asset
                with self.assertRaises(ValueError):
                    self.build()

    def test_build_preserves_candidate_and_independent_criteria(self):
        original = deepcopy(self.candidate.to_dict())
        first = self.build()
        first["criteria"]["minimum_completed_trades"] = 1
        second = self.build()
        self.assertEqual(second["criteria"]["minimum_completed_trades"], 30)
        self.assertEqual(self.candidate.to_dict(), original)
        self.session.add.assert_not_called()
        self.session.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
