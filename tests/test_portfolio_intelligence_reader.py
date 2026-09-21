"""Isolated reader tests. No database connection is opened."""

from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import Column, Integer, MetaData, Table

from app.capital.portfolio_intelligence_reader import read_portfolio_inputs


SNAPSHOT = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
QUOTE_START = "2026-09-19T00:00:00+00:00"


def fake_models():
    module = ModuleType("app.market_db.models")
    metadata = MetaData()
    definitions = {
        "Portfolio": ("id",),
        "PortfolioPosition": ("portfolio_id", "asset_id"),
        "PortfolioTransaction": (
            "portfolio_id", "asset_id", "created_at", "id",
        ),
        "MarketAsset": ("id",),
        "PriceObservation": ("asset_id", "observed_at", "id"),
    }
    for name, columns in definitions.items():
        table = Table(
            name,
            metadata,
            *(Column(column, Integer) for column in columns),
        )
        model = SimpleNamespace(
            __table__=table,
            **{column: table.c[column] for column in columns},
        )
        setattr(module, name, model)
    return module


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.engine = MagicMock()
        self.engine.dialect.name = "postgresql"
        self.connection = MagicMock()
        self.engine.connect.return_value.__enter__.return_value = (
            self.connection
        )
        self.connection.execution_options.return_value = self.connection
        self.connection.scalar.return_value = SNAPSHOT

        self.data = {
            "Portfolio": [{"id": 1, "portfolio_type": "paper"}],
            "PortfolioPosition": [{"portfolio_id": 1, "asset_id": 7}],
            "PortfolioTransaction": [{
                "id": 1,
                "portfolio_id": 1,
                "asset_id": 7,
                "created_at": datetime(
                    2026, 9, 20, 12, tzinfo=timezone.utc
                ),
            }],
            "MarketAsset": [{"id": 7}],
            "PriceObservation": [{"id": 10, "asset_id": 7}],
        }
        self.statements = []

        def execute(statement):
            self.statements.append(statement)
            result = MagicMock()
            if hasattr(statement, "get_final_froms"):
                table = statement.get_final_froms()[0].name
                result.mappings.return_value = self.data[table]
            return result

        self.connection.execute.side_effect = execute
        self.models_patch = patch.dict(
            "sys.modules",
            {"app.market_db.models": fake_models()},
        )
        self.models_patch.start()
        self.addCleanup(self.models_patch.stop)

    def read(self, **changes):
        arguments = {
            "portfolio_ids": [1],
            "quote_window_start": QUOTE_START,
            "database_engine": self.engine,
        }
        arguments.update(changes)
        return read_portfolio_inputs(**arguments)

    def test_reads_all_inputs_from_one_read_only_snapshot(self):
        result = self.read()

        self.engine.connect.assert_called_once()
        self.connection.execution_options.assert_called_once_with(
            isolation_level="REPEATABLE READ"
        )
        self.connection.begin.assert_called_once()
        self.assertEqual(
            str(self.statements[0]),
            "SET TRANSACTION READ ONLY",
        )
        self.assertEqual(result["snapshot_at"], SNAPSHOT.isoformat())
        self.assertTrue(result["database_read_only"])
        self.assertEqual(result["database_snapshot"], "repeatable_read")
        self.assertEqual(result["portfolios"], self.data["Portfolio"])
        self.assertEqual(result["positions"], self.data["PortfolioPosition"])
        self.assertEqual(
            result["transactions"], self.data["PortfolioTransaction"]
        )
        self.assertEqual(result["assets"], self.data["MarketAsset"])
        self.assertEqual(
            result["observations"], self.data["PriceObservation"]
        )
        for statement in self.statements[1:]:
            self.assertTrue(statement.is_select)

    def test_invalid_portfolio_ids_fail_before_connecting(self):
        for identifiers in ([], [True], [0], [-1], ["1"], [1, 1]):
            with self.subTest(identifiers=identifiers):
                with self.assertRaises(ValueError):
                    self.read(portfolio_ids=identifiers)
        self.engine.connect.assert_not_called()

    def test_missing_portfolio_is_rejected(self):
        self.data["Portfolio"] = []
        with self.assertRaisesRegex(ValueError, "not found"):
            self.read()

    def test_nonpaper_portfolio_is_rejected(self):
        self.data["Portfolio"][0]["portfolio_type"] = "live"
        with self.assertRaisesRegex(ValueError, "Only paper"):
            self.read()

    def test_nonpostgres_engine_is_rejected(self):
        self.engine.dialect.name = "sqlite"
        with self.assertRaisesRegex(ValueError, "PostgreSQL"):
            self.read()
        self.engine.connect.assert_not_called()

    def test_future_quote_window_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Quote window"):
            self.read(quote_window_start="2026-09-22T00:00:00+00:00")

    def test_future_transaction_is_rejected(self):
        self.data["PortfolioTransaction"][0]["created_at"] = datetime(
            2026, 9, 22, tzinfo=timezone.utc
        )
        with self.assertRaisesRegex(ValueError, "Future-dated"):
            self.read()

    def test_cash_only_portfolio_needs_no_asset_queries(self):
        self.data["PortfolioPosition"] = []
        self.data["PortfolioTransaction"] = []

        result = self.read()

        self.assertEqual(result["assets"], [])
        self.assertEqual(result["observations"], [])
        tables = [
            statement.get_final_froms()[0].name
            for statement in self.statements
            if hasattr(statement, "get_final_froms")
        ]
        self.assertEqual(
            tables,
            ["Portfolio", "PortfolioPosition", "PortfolioTransaction"],
        )

    def test_observations_are_bounded_by_requested_window_and_snapshot(self):
        self.read()
        statement = next(
            statement
            for statement in self.statements
            if hasattr(statement, "get_final_froms")
            and statement.get_final_froms()[0].name == "PriceObservation"
        )
        parameters = statement.compile().params
        self.assertIn([7], parameters.values())
        self.assertIn(
            datetime(2026, 9, 19, tzinfo=timezone.utc),
            parameters.values(),
        )
        self.assertIn(SNAPSHOT, parameters.values())
        self.assertIn(">=", str(statement))
        self.assertIn("<=", str(statement))

    def test_database_failure_propagates_and_closes_contexts(self):
        self.connection.execute.side_effect = RuntimeError("read failed")
        with self.assertRaisesRegex(RuntimeError, "read failed"):
            self.read()
        self.connection.begin.return_value.__exit__.assert_called_once()
        self.engine.connect.return_value.__exit__.assert_called_once()

    def test_naive_quote_window_is_rejected_before_connecting(self):
        with self.assertRaises(ValueError):
            self.read(quote_window_start="2026-09-19T00:00:00")
        self.engine.connect.assert_not_called()

    def test_snapshot_does_not_claim_verified_history(self):
        result = self.read()
        self.assertFalse(result["historical_completeness_verified"])
        self.assertFalse(result["historical_availability_verified"])


if __name__ == "__main__":
    unittest.main()
