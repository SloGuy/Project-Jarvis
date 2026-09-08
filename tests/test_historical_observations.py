import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.capital.historical_observations import (
    historical_window_statement,
    load_historical_snapshot,
)

NOW = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


class HistoricalObservationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE market_assets "
                "(id INTEGER PRIMARY KEY, symbol TEXT, asset_type TEXT)"
            ))
            connection.execute(text(
                "CREATE TABLE price_observations "
                "(id INTEGER PRIMARY KEY, asset_id INTEGER, "
                "provider TEXT, price_usd NUMERIC, observed_at DATETIME)"
            ))
            connection.execute(text(
                "INSERT INTO market_assets VALUES "
                "(1, 'TEST', 'stock'), (2, 'TEST', 'crypto')"
            ))
            insert = text(
                "INSERT INTO price_observations VALUES "
                "(:id, :asset, :provider, :price, :time)"
            )
            for index in range(1, 51):
                connection.execute(insert, {
                    "id": index, "asset": 1, "provider": "Finnhub",
                    "price": 100 + index,
                    "time": self.timestamp(NOW - timedelta(minutes=index)),
                })
            for ident, asset, provider, when in (
                (101, 1, "Finnhub", NOW),
                (102, 1, "Finnhub", NOW + timedelta(minutes=1)),
                (103, 1, "FMP Historical", NOW - timedelta(seconds=1)),
                (104, 2, "Finnhub", NOW - timedelta(seconds=1)),
            ):
                connection.execute(insert, {
                    "id": ident, "asset": asset, "provider": provider,
                    "price": 999,
                    "time": self.timestamp(when),
                })

    @staticmethod
    def timestamp(value):
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")

    def load(self, **changes):
        arguments = {
            "asset_id": 1, "provider": "Finnhub", "decision_at": NOW
        }
        arguments.update(changes)
        with Session(self.engine) as session:
            return load_historical_snapshot(session, **arguments)

    def test_boundary_provider_identity_and_window(self):
        result = self.load()
        self.assertEqual(result["observation_ids"], list(range(1, 49)))
        self.assertEqual(result["snapshot"].observation_count, 48)
        self.assertEqual(float(result["snapshot"].latest_price_usd), 101)
        self.assertFalse(result["availability_verified"])

    def test_future_rows_cannot_change_prior_snapshot(self):
        before = self.load()
        with self.engine.begin() as connection:
            connection.execute(text(
                "UPDATE price_observations SET price_usd=100000 "
                "WHERE id IN (101, 102)"
            ))
        self.assertEqual(self.load(), before)

    def test_empty_window_and_unknown_asset(self):
        result = self.load(decision_at=NOW - timedelta(days=1))
        self.assertFalse(result["snapshot"].usable)
        self.assertEqual(result["observation_ids"], [])
        with self.assertRaises(ValueError):
            self.load(asset_id=999)

    def test_invalid_inputs(self):
        for changes in (
            {"asset_id": True},
            {"asset_id": 0},
            {"provider": "FMP Historical"},
            {"provider": "CoinGecko Historical"},
            {"decision_at": NOW.replace(tzinfo=None)},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.load(**changes)

    def test_equivalent_timezone(self):
        offset = timezone(timedelta(hours=-4))
        self.assertEqual(self.load(), self.load(decision_at=NOW.astimezone(offset)))


if __name__ == "__main__":
    unittest.main()
