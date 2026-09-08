import copy
import unittest
from datetime import datetime, timedelta, timezone

from app.capital.observation_witness import make_receipt
from app.capital.witnessed_history import WitnessedHistory

NOW = datetime(2026, 9, 8, 22, tzinfo=timezone.utc)


def row(ident=1, **changes):
    value = {
        "id": ident,
        "asset_id": 8,
        "provider": "CoinGecko",
        "price_usd": "500",
        "observed_at": (NOW - timedelta(minutes=10)).isoformat(),
    }
    value.update(changes)
    return value


def receipt(rows, when=NOW):
    return make_receipt(rows, when)


def load(history, when):
    return history.window(
        asset_id=8, provider="CoinGecko",
        symbol="XMR", decision_at=when,
    )


class WitnessedHistoryTests(unittest.TestCase):
    def test_late_arrival_and_exact_boundary_excluded(self):
        history = WitnessedHistory([receipt([row()])])
        for when in (NOW - timedelta(minutes=1), NOW):
            self.assertEqual(load(history, when)["observation_ids"], [])
        self.assertEqual(
            load(history, NOW + timedelta(seconds=1))["observation_ids"],
            [1],
        )

    def test_repeated_receipt_keeps_earliest_witness(self):
        early = receipt([row()])
        late = receipt([row()], NOW + timedelta(minutes=5))
        history = WitnessedHistory([late, early, early])
        result = load(history, NOW + timedelta(seconds=1))
        self.assertEqual(result["observation_ids"], [1])
        self.assertEqual(
            result["visibility_evidence"][0]["receipt_sha256"],
            early["sha256"],
        )

    def test_conflicting_values_fail_closed(self):
        with self.assertRaises(ValueError):
            WitnessedHistory([
                receipt([row()]),
                receipt([row(price_usd="501")]),
            ])

    def test_asset_provider_and_lookback(self):
        rows = [
            row(
                ident=i,
                observed_at=(NOW - timedelta(minutes=i)).isoformat(),
                price_usd=str(500 + i),
            )
            for i in range(1, 61)
        ]
        rows += [
            row(100, asset_id=9),
            row(101, provider="Finnhub"),
        ]
        history = WitnessedHistory([receipt(rows)])
        result = load(history, NOW + timedelta(seconds=1))
        self.assertEqual(result["observation_ids"], list(range(1, 49)))
        self.assertTrue(result["snapshot"].usable)
        self.assertFalse(result["availability_verified"])

    def test_tampered_receipt_rejected(self):
        damaged = copy.deepcopy(receipt([row()]))
        damaged["payload"]["observations"][0]["price_usd"] = "1"
        with self.assertRaises(ValueError):
            WitnessedHistory([damaged])

    def test_later_receipts_do_not_change_earlier_window(self):
        first = receipt([row()])
        later = receipt(
            [row(2, price_usd="900")],
            NOW + timedelta(minutes=5),
        )
        at = NOW + timedelta(minutes=1)
        self.assertEqual(
            load(WitnessedHistory([first]), at),
            load(WitnessedHistory([first, later]), at),
        )


if __name__ == "__main__":
    unittest.main()
