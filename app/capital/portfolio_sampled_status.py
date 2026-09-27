"""Read saved sampled analytics without scanning checkpoint evidence."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from app.capital.portfolio_daily_returns import utc_timestamp


REPORT_FILE = (
    Path(__file__).resolve().parents[2]
    / "runtime/capital/portfolio_sampled_report.json"
)
MAXIMUM_REPORT_AGE = timedelta(minutes=90)


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate report field.")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Nonfinite report number.")


def get_sampled_status(*, resolved_portfolios, now=None):
    checked = utc_timestamp(now or datetime.now(timezone.utc))
    result = {
        "status": "unavailable",
        "checked_at": checked.isoformat(),
        "report_as_of": None,
        "maximum_report_age_seconds": MAXIMUM_REPORT_AGE.total_seconds(),
        "report": None,
        "database_writes": False,
        "execution_authorized": False,
        "allocation_authority": False,
        "live_capital_authority": False,
    }
    try:
        with REPORT_FILE.open(encoding="utf-8") as handle:
            report = json.load(
                handle,
                object_pairs_hook=_unique_fields,
                parse_constant=_invalid_constant,
            )
        if (
            type(report.get("schema_version")) is not int
            or report["schema_version"] != 1
            or report["methodology"] != "portfolio_sampled_report_v1"
        ):
            raise ValueError("Unsupported report format.")

        for field in (
            "historical_completeness_verified",
            "database_writes",
            "allocation_authority",
            "live_capital_authority",
        ):
            if report.get(field) is not False:
                raise ValueError("Unexpected report authority or evidence claim.")

        expected = [
            {"portfolio_id": identifier, **metadata}
            for identifier, metadata in sorted(resolved_portfolios.items())
        ]
        if (
            report["portfolio_bindings"] != expected
            or report["portfolio_ids"] != sorted(resolved_portfolios)
        ):
            result["reason"] = "portfolio_bindings_changed"
            return result

        as_of = utc_timestamp(report["as_of"])
        generated = utc_timestamp(report["generated_at"])
        result["report_as_of"] = as_of.isoformat()
        if generated < as_of or generated > checked or as_of > checked:
            raise ValueError("Invalid report timestamps.")
        if checked - as_of > MAXIMUM_REPORT_AGE:
            result.update(status="stale", reason="report_refresh_overdue")
            return result

        if (
            report["analytics"]["methodology"]
            != "aligned_hourly_sampled_analytics_v1"
        ):
            raise ValueError("Unsupported analytics methodology.")
        result.update(status="current", report=report)
        return result

    except FileNotFoundError:
        result["reason"] = "report_not_published"
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        result["reason"] = "report_unreadable_or_invalid"
    return result
