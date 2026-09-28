"""Lifecycle orchestration tests without production state changes."""

import fcntl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.capital import autonomy_lifecycle_worker as worker
from app.capital.autonomy_policy import CapitalOperatingPolicy


class LifecycleWorkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

        self.policy = CapitalOperatingPolicy(enabled=True)
        self.control = self.patch(
            "read_operating_policy", return_value=self.policy
        )
        self.session = MagicMock()
        self.session.scalars.return_value = ["request-1"]
        self.session.scalar.return_value = "request-1"
        factory = MagicMock()
        factory.return_value.__enter__.return_value = self.session
        self.patch("SessionLocal", factory)

        self.evidence = {
            "request_key": "request-1",
            "research_id": "research_test_1",
            "portfolio_id": 1,
            "status": "active",
            "version": 2,
            "accounting_valid": True,
            "issues": [],
            "realized_gain_loss_usd": "0",
            "sell_fill_count": 0,
            "holding_count": 0,
            "active_age_seconds": 0,
        }
        self.reader = self.patch(
            "read_paper_accounting", return_value=self.evidence
        )
        self.review = self.patch("build_factory_review", return_value={
            "eligible_for_operator_review": True,
            "blockers": [],
            "validation_gate": {"status": "passed"},
            "verified_plan_bindings": [{"plan_id": "test-plan"}],
        })
        self.transition = self.patch(
            "transition_paper_experiment",
            return_value={"status": "test-transition"},
        )
        self.creation = self.patch(
            "create_autonomous_paper_experiment",
            return_value={"status": "created"},
        )
        self.submit = self.patch("submit_factory_request")

    def patch(self, name, *args, **kwargs):
        patcher = patch.object(worker, name, *args, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def cycle(self):
        return worker.run_lifecycle_cycle(directory=self.directory)

    def research_state(self):
        context = MagicMock()
        context.__enter__.return_value = {
            "candidates": {
                "research_test_1": {
                    "research_id": "research_test_1",
                    "status": "ready_for_experiment",
                    "verdict": "promising",
                    "strategy_name": "mean_reversion_v2",
                    "asset_universe": ["BTC"],
                }
            }
        }
        self.patch("locked_research_state", return_value=context)

    def test_disabled_cycle_does_no_work(self):
        self.control.return_value = CapitalOperatingPolicy(enabled=False)
        self.assertEqual(self.cycle()["status"], "disabled")
        self.session.scalars.assert_not_called()
        self.reader.assert_not_called()
        self.creation.assert_not_called()

    def test_overlapping_cycle_is_busy(self):
        with (self.directory / "lifecycle-worker.lock").open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.cycle()["status"], "busy")
        self.reader.assert_not_called()

    def test_healthy_active_experiment_stays_active(self):
        result = self.cycle()
        self.assertEqual(result["outcomes"][0]["status"], "unchanged")
        self.transition.assert_not_called()
        self.creation.assert_not_called()

    def test_loss_dispatches_version_bound_demotion(self):
        self.evidence["realized_gain_loss_usd"] = "-50"
        self.cycle()
        arguments = self.transition.call_args.kwargs
        self.assertEqual(arguments["target"], "demoted")
        self.assertEqual(arguments["expected_version"], 2)
        self.assertEqual(arguments["decision_key"], "automatic:2")

    def test_bad_accounting_dispatches_pause(self):
        self.evidence["accounting_valid"] = False
        self.evidence["issues"] = ["Cash mismatch."]
        self.cycle()
        self.assertEqual(
            self.transition.call_args.kwargs["target"], "paused"
        )

    def test_planned_activation_requires_verified_review(self):
        self.evidence["status"] = "planned"
        self.evidence["version"] = 1
        self.cycle()
        self.review.assert_called_once_with("research_test_1")
        self.assertEqual(
            self.transition.call_args.kwargs["target"], "active"
        )

    def test_blocked_review_does_not_activate(self):
        self.evidence["status"] = "planned"
        self.review.return_value["eligible_for_operator_review"] = False
        self.cycle()
        self.transition.assert_not_called()

    def test_global_pause_allows_demotion_but_not_creation(self):
        self.control.return_value = CapitalOperatingPolicy(
            enabled=True, paused=True
        )
        self.evidence["realized_gain_loss_usd"] = "-60"
        self.cycle()
        self.assertEqual(
            self.transition.call_args.kwargs["target"], "demoted"
        )
        self.creation.assert_not_called()

    def test_failed_portfolio_does_not_skip_next_or_create(self):
        self.session.scalars.return_value = ["broken", "request-1"]
        self.session.scalar.return_value = None
        self.reader.side_effect = [RuntimeError("Unreadable."), self.evidence]
        result = self.cycle()
        self.assertEqual(result["status"], "partial_failure")
        self.assertEqual(self.reader.call_count, 2)
        self.assertEqual(result["outcomes"][1]["status"], "unchanged")
        self.creation.assert_not_called()

    def test_empty_capacity_submits_and_creates_ready_research(self):
        self.session.scalars.return_value = []
        self.session.scalar.return_value = None
        self.research_state()
        result = self.cycle()
        request = self.submit.call_args.args[0]
        self.assertEqual(request.research_id, "research_test_1")
        self.assertEqual(request.requested_by, "capital.lifecycle")
        self.creation.assert_called_once_with(request.request_key)
        self.assertEqual(result["creation"]["status"], "created")
        self.transition.assert_not_called()
        self.assertFalse(result["live_capital_authorized"])

    def test_existing_awaiting_request_is_reused(self):
        self.research_state()
        existing = MagicMock()
        existing.request_key = "existing-request"
        existing.status = "awaiting_review"
        self.session.scalar.return_value = existing
        worker.create_next()
        self.submit.assert_not_called()
        self.creation.assert_called_once_with("existing-request")

    def test_empty_demoted_experiment_dispatches_retirement(self):
        self.evidence["status"] = "demoted"
        self.cycle()
        self.assertEqual(
            self.transition.call_args.kwargs["target"], "retired"
        )


if __name__ == "__main__":
    unittest.main()
