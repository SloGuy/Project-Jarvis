from sqlalchemy import text

from app.market_db.database import Base, engine
from app.market_db import models  # noqa: F401


def initialize_database() -> None:
    Base.metadata.create_all(bind=engine)

    with engine.connect() as connection:
        database_name = connection.execute(
            text("SELECT current_database()")
        ).scalar_one()

    print(f"Market database initialized successfully: {database_name}")


if __name__ == "__main__":
    initialize_database()
