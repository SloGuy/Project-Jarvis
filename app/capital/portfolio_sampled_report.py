"""Build sampled portfolio diagnostics from integrity-checked checkpoints.

This potentially expensive scan belongs in an operator or scheduled job,
not a frequently refreshed HTTP request.
"""
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path
import re

from app.capital.portfolio_checkpoint_store import load_portfolio_checkpoint
from app.capital.portfolio_daily_returns import utc_timestamp
from app.capital.portfolio_sampled_returns import build_sampled_returns, METHOD
from app.capital.portfolio_sampled_analytics import analyze_sampled_returns


def build_sampled_report(*, records, portfolio_ids, as_of):
    identifiers = list(portfolio_ids)
    if (
        not identifiers
        or any(type(value) is not int or value <= 0 for value in identifiers)
        or len(set(identifiers)) != len(identifiers)
    ):
        raise ValueError("Explicit unique positive portfolio IDs are required.")
    identifiers.sort()
    cutoff = utc_timestamp(as_of)

    built = build_sampled_returns(records=records, as_of=cutoff)
    reports = {}
    for identifier in identifiers:
        reports[identifier] = built["reports_by_portfolio"].get(identifier, {
            "methodology": METHOD,
            "as_of": cutoff.isoformat(),
            "status": "insufficient_data",
            "return_count": 0,
            "returns": [],
            "excluded_intervals": [],
        })

    # Explicit diagnostic scenario, independent of actual balances/allocation.
    with localcontext() as context:
        context.prec = 60
        equal = Decimal("1") / len(identifiers)
        weights = {identifier: equal for identifier in identifiers[:-1]}
        weights[identifiers[-1]] = (
            Decimal("1") - sum(weights.values(), Decimal("0"))
        )

    analytics = analyze_sampled_returns(
        reports_by_portfolio=reports,
        weights_by_portfolio=weights,
        minimum_observations=30,
    )
    completed = [
        record for record in records
        if utc_timestamp(record["checkpoint"]["finished_at"]) < cutoff
    ]
    latest = max(
        completed,
        key=lambda record: utc_timestamp(record["checkpoint"]["sampled_at"]),
        default=None,
    )

    return {
        "schema_version": 1,
        "methodology": "portfolio_sampled_report_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": cutoff.isoformat(),
        "loaded_checkpoint_count": len(records),
        "selected_hourly_checkpoint_count": built["selected_checkpoint_count"],
        "latest_completed_checkpoint": (
            {
                "record_id": latest["record_id"],
                "sampled_at": latest["checkpoint"]["sampled_at"],
            } if latest else None
        ),
        "portfolio_ids": identifiers,
        "return_reports": [
            {"portfolio_id": identifier, **reports[identifier]}
            for identifier in identifiers
        ],
        "excluded_samples": built["excluded_samples"],
        "analytics": analytics,
        "risk_weighting": {
            "methodology": "equal_paper_account_diagnostic_scenario",
            "description": (
                "Equal fixed account weights; not actual capital weights "
                "or an allocation recommendation."
            ),
            "weights": [
                {
                    "portfolio_id": identifier,
                    "weight_fraction": str(weights[identifier]),
                }
                for identifier in identifiers
            ],
        },
        "regime_attribution": {
            "status": "unavailable",
            "reason": (
                "Checkpoints do not contain contemporaneously captured "
                "portfolio regime labels. Current market context must not "
                "be assigned retrospectively to these returns."
            ),
        },
        "historical_completeness_verified": False,
        "database_writes": False,
        "allocation_authority": False,
        "live_capital_authority": False,
        "limitations": [
            "Metrics use sampled indicative hourly returns, not daily returns.",
            "Thirty aligned observations are a minimum diagnostic sample only.",
            "Missing or ineligible evidence remains excluded.",
            "Risk weights describe an explicit equal-account scenario.",
            "A report is a point-in-time calculation, not continuous monitoring.",
        ],
    }


def read_sampled_report_inputs(*, directory):
    """Verify every discovered checkpoint; never skip corrupt evidence.

    Files published during the scan may appear only on the next run.
    No directory modification or record deletion is performed.
    """
    root = Path(directory)
    if root.exists() and not root.is_dir():
        raise ValueError("Checkpoint directory is not a directory.")
    records = []
    for path in sorted(root.glob("*.json")):
        if re.fullmatch(r"[0-9a-f]{64}", path.stem) is None:
            raise ValueError("Unexpected checkpoint filename.")
        records.append({
            "record_id": path.stem,
            "checkpoint": load_portfolio_checkpoint(
                directory=root, record_id=path.stem
            ),
        })
    return records
