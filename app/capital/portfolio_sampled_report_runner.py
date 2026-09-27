"""Build and atomically publish sampled diagnostics outside HTTP requests."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile

from app.capital.portfolio_intelligence_service import _resolve_portfolios
from app.capital.portfolio_sampled_report import (
    build_sampled_report,
    read_sampled_report_inputs,
)


ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "runtime/capital"
CHECKPOINT_DIRECTORY = DIRECTORY / "portfolio_checkpoints"
REPORT_FILE = DIRECTORY / "portfolio_sampled_report.json"
LOCK_FILE = DIRECTORY / "portfolio_sampled_report.lock"


def publish_report(report):
    """Replace the report only after a complete file has been flushed.

    A publication failure is reported even if replacement already occurred.
    Report timestamps allow readers to distinguish old results from fresh ones.
    """
    raw = json.dumps(
        report, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8")
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=".sampled-report-", suffix=".tmp", dir=REPORT_FILE.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, REPORT_FILE)
        directory_fd = os.open(REPORT_FILE.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def run_once():
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 2, {"status": "busy"}

        try:
            resolved = _resolve_portfolios()
            records = read_sampled_report_inputs(
                directory=CHECKPOINT_DIRECTORY
            )
            result = build_sampled_report(
                records=records,
                portfolio_ids=sorted(resolved),
                as_of=datetime.now(timezone.utc),
            )
            result["portfolio_bindings"] = [
                {"portfolio_id": identifier, **metadata}
                for identifier, metadata in sorted(resolved.items())
            ]
            publish_report(result)
            return 0, {
                "status": "published",
                "path": str(REPORT_FILE),
                "as_of": result["as_of"],
                "loaded_checkpoints": result["loaded_checkpoint_count"],
                "selected_hourly_checkpoints": (
                    result["selected_hourly_checkpoint_count"]
                ),
                "returns_by_portfolio": {
                    row["portfolio_id"]: row["return_count"]
                    for row in result["return_reports"]
                },
                "risk_status": result["analytics"]["risk_contribution"]["status"],
                "database_writes": False,
                "execution_authorized": False,
            }
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Publish sampled portfolio diagnostics from saved checkpoints."
    )
    parser.parse_args(argv)
    try:
        code, result = run_once()
    except Exception:
        print(json.dumps({
            "status": "failed",
            "message": (
                "Sampled report generation or publication failed. "
                "Inspect service logs and report timestamps before retrying."
            ),
            "database_writes": False,
            "execution_authorized": False,
        }), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
