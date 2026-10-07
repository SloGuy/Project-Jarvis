"""Run development tests with external database connections blocked."""

import argparse
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def main():
    if ROOT.name not in {
        "jarvis-provider-validation-dev",
        "jarvis-provider-validation-release",
    }:
        raise ValueError("Use a provider-validation worktree.")

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pattern",
        default="test_validation*.py",
    )
    arguments = parser.parse_args()

    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))

    # This only permits database-module construction during imports.
    # It supplies no working database credential.
    os.environ["POSTGRES_PASSWORD"] = "isolated-test-placeholder"

    from sqlalchemy.engine import Engine

    original_connect = Engine.connect
    original_raw_connection = Engine.raw_connection

    def connect(engine, *args, **kwargs):
        if engine.dialect.name != "sqlite":
            raise AssertionError(
                "External database access during isolated tests."
            )
        return original_connect(engine, *args, **kwargs)

    def raw_connection(engine, *args, **kwargs):
        if engine.dialect.name != "sqlite":
            raise AssertionError(
                "External database access during isolated tests."
            )
        return original_raw_connection(engine, *args, **kwargs)

    with (
        patch.object(Engine, "connect", connect),
        patch.object(Engine, "raw_connection", raw_connection),
    ):
        suite = unittest.defaultTestLoader.discover(
            start_dir="tests",
            pattern=arguments.pattern,
        )
        result = unittest.TextTestRunner(verbosity=2).run(suite)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
