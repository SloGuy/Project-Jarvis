"""Sampled-return eligibility tests; no database or filesystem access."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest

from app.capital.portfolio_sampled_returns import build_sampled_returns


BASE = datetime(2026, 9, 28, tzinfo=timezone.utc)
CUTOFF = BASE + timedelta(days=1)


def account(cash="100"):
    return {
        "id": 1,
        "portfolio_type": "paper",
        "cash_balance_usd": cash,
    }


def record(hour, value="100", seconds=10):
    at = BASE + timedelta(hours=hour, seconds=seconds)
    initial = account()
    audit = {
        "sampled_at": at.isoformat(),
        "installation": {"installation_id": "installation-1"},
        "events": [{
            "event_id": 1,
            "database_transaction_id": 100,
            "source_table": "portfolios",
            "source_row_id": 1,
            "operation": "BASELINE",
            "before": None,
            "after": initial,
        }],
        "current_rows": {
            "portfolios": [{
                "source_row_id": 1,
                "row_json": json.dumps(initial),
            }],
            "portfolio_positions": [],
            "portfolio_transactions": [],
        },
    }
    return {
        "record_id": f"record-{hour}-{seconds}",
        "checkpoint": {
            "methodology": "prospective_portfolio_evidence_snapshot_v1",
            "sampled_at": at.isoformat(),
            "finished_at": (at + timedelta(seconds=1)).isoformat(),
            "audit_snapshot": audit,
            "resolved_portfolios": [{
                "portfolio_id": 1,
                "portfolio_name": "Test Paper",
                "strategy_name": "test",
                "experiment_id": "test-1",
            }],
            "valuations": {
                "portfolios": [{
                    "portfolio_id": 1,
                    "measured_at": at.isoformat(),
                    "valuation_status": "indicative",
                    "total_value_usd": value,
                }],
            },
        },
    }


def deposit(row):
    checkpoint = row["checkpoint"]
    audit = checkpoint["audit_snapshot"]
    ledger = {
        "id": 20,
        "portfolio_id": 1,
        "asset_id": None,
        "transaction_type": "deposit",
        "quantity": "10",
        "price_usd": "1",
        "total_usd": "10",
        "fees_usd": "0",
        "created_at": checkpoint["sampled_at"],
    }
    audit["events"].extend([
        {
            "event_id": 2,
            "database_transaction_id": 200,
            "source_table": "portfolios",
            "source_row_id": 1,
            "operation": "UPDATE",
            "before": account(),
            "after": account("110"),
        },
        {
            "event_id": 3,
            "database_transaction_id": 200,
            "source_table": "portfolio_transactions",
            "source_row_id": 20,
            "operation": "INSERT",
            "before": None,
            "after": ledger,
        },
    ])
    audit["current_rows"]["portfolios"][0]["row_json"] = json.dumps(
        account("110")
    )
    audit["current_rows"]["portfolio_transactions"] = [{
        "source_row_id": 20,
        "row_json": json.dumps(ledger),
    }]


class SampledReturnTests(unittest.TestCase):
    def build(self, rows, cutoff=CUTOFF):
        return build_sampled_returns(records=rows, as_of=cutoff)

    def report(self, rows):
        return self.build(rows)["reports_by_portfolio"][1]

    def assert_blocked(self, rows, reason):
        report = self.report(rows)
        self.assertEqual(report["returns"], [])
        self.assertIn(reason, report["excluded_intervals"][0]["reasons"])

    def test_actual_timestamps_and_return_are_preserved(self):
        left, right = record(0), record(1, "110", seconds=40)
        report = self.report([left, right])
        interval = report["returns"][0]
        self.assertEqual(interval["return_fraction"], "0.1")
        self.assertEqual(interval["start"], left["checkpoint"]["sampled_at"])
        self.assertEqual(interval["end"], right["checkpoint"]["sampled_at"])
        self.assertEqual(report["status"], "available_indicative")

    def test_missing_hour_is_not_bridged(self):
        self.assert_blocked(
            [record(0), record(2)], "missing_hourly_checkpoint"
        )

    def test_manual_capture_outside_window_is_excluded(self):
        result = self.build([record(0, seconds=1800)])
        self.assertEqual(result["selected_checkpoint_count"], 0)
        self.assertEqual(
            result["excluded_samples"][0]["reason"],
            "outside_hourly_capture_window",
        )

    def test_five_minute_boundary_is_included(self):
        self.assertEqual(
            self.build([record(0, seconds=300)])["selected_checkpoint_count"],
            1,
        )

    def test_first_capture_in_slot_is_selected_regardless_of_input_order(self):
        early = record(0, "100", 10)
        later = record(0, "999", 20)
        result = self.build([later, record(1, "110"), early])
        interval = result["reports_by_portfolio"][1]["returns"][0]
        self.assertEqual(interval["start_record_id"], early["record_id"])
        self.assertEqual(interval["return_fraction"], "0.1")

    def test_selection_does_not_choose_later_good_valuation(self):
        early = record(0)
        early["checkpoint"]["valuations"]["portfolios"][0].update(
            valuation_status="incomplete", total_value_usd=None
        )
        self.assert_blocked(
            [record(0, seconds=20), early, record(1)],
            "incomplete_or_invalid_endpoint_valuation",
        )

    def test_unfinished_checkpoint_is_excluded(self):
        row = record(0)
        cutoff = row["checkpoint"]["finished_at"]
        self.assertEqual(
            self.build([row], cutoff)["selected_checkpoint_count"], 0
        )

    def test_duplicate_record_id_is_rejected(self):
        row = record(0)
        with self.assertRaisesRegex(ValueError, "Duplicate checkpoint"):
            self.build([row, deepcopy(row)])

    def test_duplicate_sampling_time_is_rejected(self):
        left = record(0)
        right = deepcopy(left)
        right["record_id"] = "different"
        with self.assertRaisesRegex(ValueError, "duplicate checkpoint sampling"):
            self.build([left, right])

    def test_incomplete_endpoint_is_excluded(self):
        right = record(1)
        right["checkpoint"]["valuations"]["portfolios"][0][
            "valuation_status"
        ] = "incomplete"
        self.assert_blocked(
            [record(0), right], "incomplete_or_invalid_endpoint_valuation"
        )

    def test_nonpositive_equity_is_excluded(self):
        self.assert_blocked(
            [record(0), record(1, "0")],
            "incomplete_or_invalid_endpoint_valuation",
        )

    def test_external_flow_interval_is_excluded(self):
        right = record(1, "110")
        deposit(right)
        self.assert_blocked([record(0), right], "external_cash_flow")

    def test_old_flow_does_not_exclude_later_interval(self):
        left = record(0, "110")
        deposit(left)
        right = record(1, "121")
        right["checkpoint"]["audit_snapshot"] = deepcopy(
            left["checkpoint"]["audit_snapshot"]
        )
        right["checkpoint"]["audit_snapshot"]["sampled_at"] = (
            right["checkpoint"]["sampled_at"]
        )
        self.assertEqual(
            self.report([left, right])["returns"][0]["return_fraction"], "0.1"
        )

    def test_changed_installation_is_excluded(self):
        right = record(1)
        right["checkpoint"]["audit_snapshot"]["installation"][
            "installation_id"
        ] = "installation-2"
        self.assert_blocked([record(0), right], "audit_installation_changed")

    def test_deleted_audit_event_is_excluded(self):
        right = record(1)
        right["checkpoint"]["audit_snapshot"]["events"] = []
        self.assert_blocked(
            [record(0), right], "audit_events_disappeared_or_changed"
        )

    def test_changed_binding_is_excluded(self):
        right = record(1)
        right["checkpoint"]["resolved_portfolios"][0]["strategy_name"] = "other"
        self.assert_blocked([record(0), right], "portfolio_binding_changed")

    def test_unmatched_cash_change_is_excluded(self):
        right = record(1)
        deposit(right)
        audit = right["checkpoint"]["audit_snapshot"]
        audit["events"] = audit["events"][:2]
        audit["current_rows"]["portfolio_transactions"] = []
        self.assert_blocked([record(0), right], "unresolved_interval_accounting")

    def test_split_transaction_visibility_is_excluded(self):
        right = record(1)
        deposit(right)
        for event in right["checkpoint"]["audit_snapshot"]["events"][1:]:
            event["database_transaction_id"] = 100
        self.assert_blocked(
            [record(0), right], "transaction_visibility_inconsistent"
        )

    def test_endpoint_time_mismatch_is_rejected(self):
        row = record(0)
        row["checkpoint"]["valuations"]["portfolios"][0]["measured_at"] = (
            BASE.isoformat()
        )
        with self.assertRaisesRegex(ValueError, "Valuation timestamp"):
            self.build([row])

    def test_empty_history_is_supported(self):
        result = self.build([])
        self.assertEqual(result["reports_by_portfolio"], {})
        self.assertEqual(result["selected_checkpoint_count"], 0)

    def test_inputs_and_authority_flags_are_preserved(self):
        rows = [record(0), record(1)]
        original = deepcopy(rows)
        result = self.build(rows)
        self.assertEqual(rows, original)
        for name in (
            "historical_completeness_verified",
            "execution_authorized",
            "allocation_authority",
            "live_capital_authority",
        ):
            self.assertIs(result[name], False)


if __name__ == "__main__":
    unittest.main()
