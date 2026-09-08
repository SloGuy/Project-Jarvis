import copy
import unittest
from app.capital.observation_witness import make_receipt, verify_receipt


class WitnessTests(unittest.TestCase):
    def row(self, **changes):
        row = {
            "id": 1, "asset_id": 8, "provider": "CoinGecko",
            "price_usd": "500.12345678",
            "observed_at": "2026-09-01T12:00:00+00:00",
        }
        row.update(changes)
        return row

    def test_old_observation_is_not_backdated(self):
        witnessed = "2026-09-08T22:00:00+00:00"
        receipt = make_receipt([self.row()], witnessed)
        payload = verify_receipt(receipt)
        self.assertEqual(payload["witnessed_at"], witnessed)
        self.assertEqual(
            payload["observations"][0]["observed_at"],
            "2026-09-01T12:00:00+00:00",
        )

    def test_tampered_values_rejected(self):
        receipt = make_receipt(
            [self.row()], "2026-09-08T22:00:00+00:00"
        )
        altered = copy.deepcopy(receipt)
        altered["payload"]["observations"][0]["price_usd"] = "1"
        with self.assertRaises(ValueError):
            verify_receipt(altered)

    def test_future_naive_and_invalid_values_rejected(self):
        for changes in (
            {"observed_at": "2026-09-09T00:00:00+00:00"},
            {"observed_at": "2026-09-01T12:00:00"},
            {"price_usd": "NaN"},
            {"price_usd": "0"},
            {"id": True},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    make_receipt(
                        [self.row(**changes)],
                        "2026-09-08T22:00:00+00:00",
                    )

    def test_empty_and_duplicate_rows_rejected(self):
        for rows in ([], [self.row(), self.row()]):
            with self.assertRaises(ValueError):
                make_receipt(rows, "2026-09-08T22:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
