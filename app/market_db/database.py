import os
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _build_database_url() -> str:
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB", "jarvis_market")
    username = os.getenv("POSTGRES_USER", "jarvis")
    password = os.getenv("POSTGRES_PASSWORD", "")

    if not password:
        raise RuntimeError("POSTGRES_PASSWORD is not configured.")

    return (
        f"postgresql+psycopg://{quote_plus(username)}:"
        f"{quote_plus(password)}@{host}:{port}/{database}"
    )


class Base(DeclarativeBase):
    pass


engine = create_engine(
    _build_database_url(),
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)
