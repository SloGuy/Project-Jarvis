import runpy
import sys
import unittest
from pathlib import Path
from datetime import timedelta

sys.path.insert(0, str(Path.cwd()))
helpers = runpy.run_path("tests/test_position_simulation.py")
Base = helpers["PositionTests"]
snapshot = helpers["snapshot"]
NOW = helpers["NOW"]


class FreshConfirmationTests(unittest.TestCase):
    def test_each_exit_requires_three_new_observations(self):
        cases = (
            ("fixed_mean_recovery", timedelta(minutes=20), "100", False),
            ("recovery_timeout", timedelta(hours=24, minutes=10), "98", False),
            ("stop_loss", timedelta(minutes=11), "90", True),
        )
        for rule, offset, price, risk_only in cases:
            with self.subTest(exit_rule=rule):
                helper = Base()
                simulation = helper.make()
                helper.enter(simulation)

                exited_at = NOW + offset
                event = simulation.step(
                    snapshot(exited_at, price),
                    decision_at=exited_at,
                    risk_only=risk_only,
                )
                self.assertTrue(event["executed"])
                self.assertEqual(event["exit_rule"], rule)

                for index in (1, 2, 3):
                    at = exited_at + timedelta(minutes=5 * index)
                    event = simulation.step(
                        snapshot(at), decision_at=at
                    )
                    self.assertEqual(
                        event["executed"], index == 3,
                        f"{rule}: post-exit observation {index}",
                    )


if __name__ == "__main__":
    unittest.main()
