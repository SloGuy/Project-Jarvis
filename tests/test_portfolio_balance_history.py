"""Tests for read-only reconstruction of recorded portfolio balances."""

from copy import deepcopy
from decimal import Decimal
import unittest

from app.capital.portfolio_balance_history import reconstruct_balances


def at(hour):
    return f"2026-09-20T{hour:02d}:00:00+00:00"


def ledger():
    return [
        {
            "id": 1,
            "created_at": at(10),
            "transaction_type": "buy",
            "asset_id": 7,
            "quantity": "2",
            "price_usd": "50",
            "total_usd": "100",
            "fees_usd": "1",
        },
        {
            "id": 2,
            "created_at": at(12),
            "transaction_type": "sell",
            "asset_id": 7,
            "quantity": "1",
            "price_usd": "60",
            "total_usd": "60",
            "fees_usd": "1",
        },
        {
            "id": 3,
            "created_at": at(14),
            "transaction_type": "deposit",
            "asset_id": None,
            "quantity": "50",
            "price_usd": "1",
            "total_usd": "50",
            "fees_usd": "0",
        },
        {
            "id": 4,
            "created_at": at(16),
            "transaction_type": "withdrawal",
            "asset_id": None,
            "quantity": "8",
            "price_usd": "1",
            "total_usd": "8",
            "fees_usd": "0",
        },
    ]


def reconstruct(**changes):
    arguments = {
        "snapshot_at": at(18),
        "cash_balance_usd": "1000",
        "positions": {7: "1"},
        "transactions": ledger(),
        "measurement_times": [at(10), at(12), at(14), at(16), at(18)],
    }
    arguments.update(changes)
    return reconstruct_balances(**arguments)


class BalanceHistoryTests(unittest.TestCase):
    def test_reverses_trades_fees_and_cash_flows(self):
        result = reconstruct()
        self.assertEqual(
            [Decimal(row["cash_balance_usd"]) for row in result["points"]],
            list(map(Decimal, ("899", "958", "1008", "1000", "1000"))),
        )
        self.assertEqual(
            [
                Decimal(row["positions"][0]["quantity"])
                for row in result["points"]
            ],
            list(map(Decimal, ("2", "1", "1", "1", "1"))),
        )
        self.assertEqual(result["external_flow_times"], [at(14), at(16)])

    def test_transactions_at_measurement_time_are_included(self):
        result = reconstruct(measurement_times=[at(11), at(12)])
        before, after = result["points"]
        self.assertEqual(Decimal(before["cash_balance_usd"]), Decimal("899"))
        self.assertEqual(Decimal(after["cash_balance_usd"]), Decimal("958"))
        self.assertEqual(Decimal(before["positions"][0]["quantity"]), 2)
        self.assertEqual(Decimal(after["positions"][0]["quantity"]), 1)

    def test_dates_before_surviving_history_are_excluded(self):
        result = reconstruct(measurement_times=[at(9), at(10)])
        self.assertEqual(len(result["points"]), 1)
        self.assertEqual(result["points"][0]["measured_at"], at(10))
        self.assertEqual(
            result["excluded_points"],
            [{
                "measured_at": at(9),
                "reason": "before_surviving_transaction_history",
            }],
        )

    def test_empty_ledger_only_supports_current_snapshot(self):
        result = reconstruct(
            transactions=[],
            measurement_times=[at(10), at(18)],
        )
        self.assertEqual(len(result["points"]), 1)
        self.assertEqual(result["points"][0]["measured_at"], at(18))
        self.assertIsNone(result["earliest_surviving_transaction_at"])
        self.assertEqual(
            result["excluded_points"][0]["reason"],
            "no_surviving_transaction_history",
        )

    def test_no_supported_dates_returns_insufficient_data(self):
        result = reconstruct(measurement_times=[at(9)])
        self.assertEqual(result["status"], "insufficient_data")
        self.assertEqual(result["points"], [])

    def test_closed_position_is_restored_when_reversing_sale(self):
        records = ledger()[:2]
        records[1].update(
            quantity="2",
            total_usd="120",
        )
        result = reconstruct(
            cash_balance_usd="1018",
            positions={},
            transactions=records,
            measurement_times=[at(10), at(18)],
        )
        earlier, current = result["points"]
        self.assertEqual(Decimal(earlier["cash_balance_usd"]), Decimal("899"))
        self.assertEqual(
            earlier["positions"],
            [{"asset_id": 7, "quantity": "2"}],
        )
        self.assertEqual(current["positions"], [])

    def test_negative_position_during_reversal_is_rejected(self):
        records = ledger()[:1]
        records.append({
            **records[0],
            "id": 2,
            "created_at": at(12),
        })
        with self.assertRaisesRegex(ValueError, "negative position"):
            reconstruct(
                transactions=records,
                positions={7: "1"},
                measurement_times=[at(10)],
            )

    def test_negative_cash_during_reversal_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "negative cash"):
            reconstruct(
                cash_balance_usd="0",
                measurement_times=[at(10)],
            )

    def test_future_transactions_and_measurements_are_rejected(self):
        records = ledger()
        records[-1]["created_at"] = at(19)
        with self.assertRaisesRegex(ValueError, "Transaction occurs after"):
            reconstruct(transactions=records)

        with self.assertRaisesRegex(ValueError, "Measurement occurs after"):
            reconstruct(measurement_times=[at(19)])

    def test_equivalent_duplicate_timestamps_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate measurement"):
            reconstruct(measurement_times=[
                at(12),
                "2026-09-20T08:00:00-04:00",
            ])

    def test_invalid_snapshot_balances_are_rejected(self):
        for value in (True, None, "-1", "NaN", "Infinity"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    reconstruct(cash_balance_usd=value)
                with self.assertRaises(ValueError):
                    reconstruct(positions={7: value})

        for asset_id in (True, 0, -1, "7"):
            with self.subTest(asset_id=asset_id):
                with self.assertRaises(ValueError):
                    reconstruct(positions={asset_id: "1"})

    def test_ordering_is_deterministic_and_inputs_are_preserved(self):
        records = list(reversed(ledger()))
        positions = {7: "1", 8: "0"}
        marks = [at(18), at(10), at(12)]
        original = deepcopy((records, positions, marks))

        result = reconstruct(
            transactions=records,
            positions=positions,
            measurement_times=marks,
        )

        self.assertEqual((records, positions, marks), original)
        self.assertEqual(
            [row["measured_at"] for row in result["points"]],
            [at(10), at(12), at(18)],
        )
        self.assertEqual(
            result,
            reconstruct(
                transactions=records,
                positions=positions,
                measurement_times=marks,
            ),
        )

    def test_same_timestamp_transactions_reverse_in_descending_id_order(self):
        records = ledger()[:2]
        records[1]["created_at"] = at(10)
        result = reconstruct(
            cash_balance_usd="958",
            transactions=records,
            measurement_times=[at(10), at(18)],
        )
        for point in result["points"]:
            self.assertEqual(Decimal(point["cash_balance_usd"]), Decimal("958"))
            self.assertEqual(Decimal(point["positions"][0]["quantity"]), 1)

    def test_reconstruction_does_not_claim_verified_valuations_or_authority(self):
        result = reconstruct()
        self.assertFalse(result["historical_completeness_verified"])
        self.assertFalse(result["database_writes"])
        self.assertFalse(result["allocation_authority"])
        self.assertFalse(result["live_capital_authority"])
        for point in result["points"]:
            self.assertNotIn("valuation_status", point)
            self.assertNotIn("total_value_usd", point)


if __name__ == "__main__":
    unittest.main()
