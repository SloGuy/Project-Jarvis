"""Install the paper experiment factory table without changing portfolios."""
from sqlalchemy import inspect

from app.market_db.database import engine
from app.capital.experiment_factory_store import ExperimentFactoryRecord


def migrate_experiment_factory(database_engine=engine):
    table = ExperimentFactoryRecord.__table__

    with database_engine.begin() as connection:
        inspector = inspect(connection)
        if not inspector.has_table("portfolios"):
            raise RuntimeError("The existing portfolios table is required.")

        if inspector.has_table(table.name):
            raise RuntimeError(
                "Factory table already exists. Inspect its schema before "
                "retrying; this migration will not alter an existing table."
            )

        # Do not use Base.metadata.create_all(): only this table is in scope.
        table.create(bind=connection, checkfirst=False)

    print("Experiment factory table created.")
    print("Existing portfolios and experiments were not modified.")


if __name__ == "__main__":
    migrate_experiment_factory()
