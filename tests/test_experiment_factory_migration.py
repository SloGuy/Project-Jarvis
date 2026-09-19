"""Migration tests against isolated databases only."""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text

from app.capital.experiment_factory_store import ExperimentFactoryRecord
from app.market_db.models import Portfolio
from app.market_db.migrate_experiment_factory import migrate_experiment_factory


class FactoryMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        handle = patch("builtins.print")
        handle.start()
        self.addCleanup(handle.stop)

    def create_existing_portfolios(self):
        Portfolio.__table__.create(self.engine)
        with self.engine.begin() as connection:
            connection.execute(Portfolio.__table__.insert().values(
                name="Existing paper portfolio",
                portfolio_type="paper",
                cash_balance_usd=123,
                is_active=True,
            ))

    def portfolio_rows(self):
        with self.engine.connect() as connection:
            return list(connection.execute(
                Portfolio.__table__.select()
            ).mappings())

    def test_creates_only_factory_table_and_preserves_portfolios(self):
        self.create_existing_portfolios()
        before = self.portfolio_rows()

        migrate_experiment_factory(self.engine)

        inspector = inspect(self.engine)
        self.assertEqual(
            set(inspector.get_table_names()),
            {"portfolios", "capital_experiment_factory"},
        )
        self.assertEqual(self.portfolio_rows(), before)
        self.assertEqual(
            set(column["name"] for column in inspector.get_columns(
                "capital_experiment_factory"
            )),
            set(ExperimentFactoryRecord.__table__.columns.keys()),
        )

    def test_requires_existing_portfolios(self):
        with self.assertRaisesRegex(RuntimeError, "portfolios table"):
            migrate_experiment_factory(self.engine)
        self.assertEqual(inspect(self.engine).get_table_names(), [])

    def test_repeat_migration_refuses_without_changes(self):
        self.create_existing_portfolios()
        migrate_experiment_factory(self.engine)
        with self.engine.begin() as connection:
            connection.execute(ExperimentFactoryRecord.__table__.insert().values(
                request_key="test:request",
                research_id="research_test",
                requested_by="test",
                status="awaiting_review",
            ))
        before = self.portfolio_rows()

        with self.assertRaisesRegex(RuntimeError, "already exists"):
            migrate_experiment_factory(self.engine)

        self.assertEqual(self.portfolio_rows(), before)
        with self.engine.connect() as connection:
            records = connection.execute(
                ExperimentFactoryRecord.__table__.select()
            ).mappings().all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["request_key"], "test:request")

    def test_does_not_overwrite_an_unexpected_existing_table(self):
        self.create_existing_portfolios()
        with self.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE capital_experiment_factory "
                "(marker VARCHAR(30) PRIMARY KEY)"
            ))
            connection.execute(text(
                "INSERT INTO capital_experiment_factory VALUES ('preserve-me')"
            ))

        with self.assertRaisesRegex(RuntimeError, "already exists"):
            migrate_experiment_factory(self.engine)

        with self.engine.connect() as connection:
            self.assertEqual(
                connection.execute(text(
                    "SELECT marker FROM capital_experiment_factory"
                )).scalar_one(),
                "preserve-me",
            )

    def test_installs_uniqueness_and_foreign_key_constraints(self):
        self.create_existing_portfolios()
        migrate_experiment_factory(self.engine)
        inspector = inspect(self.engine)
        table = "capital_experiment_factory"

        self.assertEqual(
            inspector.get_pk_constraint(table)["constrained_columns"],
            ["request_key"],
        )
        unique_columns = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints(table)
        }
        self.assertIn(("research_id",), unique_columns)
        self.assertIn(("portfolio_id",), unique_columns)

        foreign_keys = inspector.get_foreign_keys(table)
        self.assertTrue(any(
            item["constrained_columns"] == ["portfolio_id"]
            and item["referred_table"] == "portfolios"
            and item["referred_columns"] == ["id"]
            and item["options"].get("ondelete") == "RESTRICT"
            for item in foreign_keys
        ))
        checks = {
            item["name"] for item in inspector.get_check_constraints(table)
        }
        self.assertIn("ck_factory_status", checks)
        self.assertIn("ck_factory_creation_requires_approval", checks)


if __name__ == "__main__":
    unittest.main()
