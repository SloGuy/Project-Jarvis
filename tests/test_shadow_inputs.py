from copy import deepcopy
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy.orm import Session
from app.capital import validation_registry
from app.capital.allocator import build_shadow_allocation
from app.capital.shadow_inputs import load_shadow_inputs, shadow_configuration

history = runpy.run_path("tests/test_historical_observations.py")


def allocation():
    names = ["mean_reversion_v2", "volatility_breakout_v1"]
    return build_shadow_allocation(
        rankings=[
            {"rank": index + 1, "strategy_name": name, "capital_score": 50,
             "evidence": {"label": "insufficient"}}
            for index, name in enumerate(names)
        ],
        committee_reports=[
            {"strategy_name": name, "decision": "continue"} for name in names
        ],
        current_regime="uncertain", regime_results=[],
    )


class ShadowInputTests(unittest.TestCase):
    timestamp = staticmethod(history["HistoricalObservationTests"].timestamp)

    def setUp(self):
        history["HistoricalObservationTests"].setUp(self)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        handle = patch.object(validation_registry, "DIRECTORY", Path(temporary.name))
        handle.start()
        self.addCleanup(handle.stop)

    def test_same_stream_with_distinct_lookbacks(self):
        with Session(self.engine) as session:
            result = load_shadow_inputs(
                session, asset_id=1, provider="Finnhub", decision_at=history["NOW"]
            )
        self.assertEqual(result["observation_ids"], list(range(1, 51)))
        self.assertEqual(
            result["snapshots"][("mean_reversion_v2", "TEST")].observation_count, 48
        )
        self.assertEqual(
            result["snapshots"][("volatility_breakout_v1", "TEST")].observation_count, 50
        )
        self.assertEqual(float(result["quote"][0]), 101)
        self.assertIsNotNone(result["quote"][1].utcoffset())
        self.assertFalse(result["availability_verified"])

    def test_real_allocator_output_is_accepted(self):
        policy, caps = shadow_configuration(allocation())
        self.assertEqual(set(caps), {"mean_reversion_v2", "volatility_breakout_v1"})
        self.assertEqual(sum(caps.values()), 30)
        self.assertGreaterEqual(policy.minimum_cash_reserve_percent, 40)
        self.assertLessEqual(policy.max_total_exposure_percent, 60)

    def test_invalid_allocation_rejected(self):
        original = allocation()
        cases = []
        value = deepcopy(original)
        value["live_capital_authority"] = True
        cases.append(value)
        value = deepcopy(original)
        value["recommendations"][0]["recommended_allocation_percent"] = 16
        cases.append(value)
        value = deepcopy(original)
        value["recommendations"][0]["committee_decision"] = "kill"
        cases.append(value)
        value = deepcopy(original)
        value["recommendations"].append(deepcopy(value["recommendations"][0]))
        cases.append(value)
        value = deepcopy(original)
        value["cash_reserve_percent"] = 0
        cases.append(value)
        for value in cases:
            with self.assertRaises(ValueError):
                shadow_configuration(value)


if __name__ == "__main__":
    unittest.main()
