from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.capital import validation_registry as registry
from app.capital import validation_access as access
from app.capital.historical_observations import load_historical_snapshot


def register_fixture_plan(draft):
    return registry._register_plan(draft, validate_binding=lambda plan: None)


fixture = runpy.run_path("tests/test_validation_plan.py")
AFTER = datetime(2030, 1, 3, tzinfo=timezone.utc)
INSIDE = datetime(2030, 1, 2, 15, tzinfo=timezone.utc)


class AccessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        patcher = patch.object(registry, "DIRECTORY", Path(temporary.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        clock = patch.object(access, "now_utc", return_value=AFTER)
        clock.start()
        self.addCleanup(clock.stop)
        with patch.object(registry, "now_utc", return_value=fixture["NOW"]):
            self.row = register_fixture_plan(fixture["draft"]())

    def test_development_period_blocked_before_database(self):
        session = Mock()
        with self.assertRaisesRegex(ValueError, "Reserved"):
            load_historical_snapshot(
                session, asset_id=1, provider="Finnhub", decision_at=INSIDE
            )
        session.execute.assert_not_called()

    def test_warmup_blocks_snapshot_calculation(self):
        session = Mock()
        session.no_autoflush = nullcontext()
        session.execute.side_effect = [
            SimpleNamespace(one_or_none=lambda: SimpleNamespace(
                symbol="SPY", asset_type="stock"
            )),
            SimpleNamespace(all=lambda: [
                SimpleNamespace(
                    id=1, price_usd=Decimal("100"), observed_at=INSIDE,
                    provider="Finnhub",
                )
            ]),
        ]
        with patch(
            "app.capital.historical_observations.calculate_mean_reversion_snapshot"
        ) as calculate:
            with self.assertRaisesRegex(ValueError, "Reserved"):
                load_historical_snapshot(
                    session, asset_id=1, provider="Finnhub", decision_at=AFTER
                )
            calculate.assert_not_called()

    def test_owned_run_and_wrong_token(self):
        with patch.object(registry, "now_utc", return_value=AFTER):
            running = registry.claim_plan(self.row["plan_id"])
        args = dict(
            asset_id=1, provider="Finnhub", decision_at=INSIDE,
            validation_plan_id=running["plan_id"],
            run_token=running["run_token"],
        )
        with access.historical_access(**args) as check:
            check([INSIDE])
        args["run_token"] = "wrong"
        with self.assertRaisesRegex(ValueError, "ownership"):
            with access.historical_access(**args):
                pass

    def test_future_time_rejected(self):
        with self.assertRaisesRegex(ValueError, "future"):
            with access.historical_access(
                asset_id=1, provider="Finnhub",
                decision_at=datetime(2031, 1, 1, tzinfo=timezone.utc),
            ):
                pass

    def test_completed_reservation_stays_protected(self):
        with patch.object(registry, "now_utc", return_value=AFTER):
            running = registry.claim_plan(self.row["plan_id"])
            registry.finish_plan(
                running["plan_id"], running["run_token"],
                succeeded=True, detail="synthetic result",
            )
        with self.assertRaisesRegex(ValueError, "Reserved"):
            with access.historical_access(
                asset_id=1, provider="Finnhub", decision_at=INSIDE
            ):
                pass


if __name__ == "__main__":
    unittest.main()
