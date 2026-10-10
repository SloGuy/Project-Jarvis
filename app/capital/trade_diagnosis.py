"""Retrospective journal diagnostics; no trading or research-state writes."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json


def _number(value, name):
    if value is None or isinstance(value, bool):
        raise ValueError(f"Missing or invalid {name}.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid {name}.") from None
    if not result.is_finite():
        raise ValueError(f"Nonfinite {name}.")
    return result


def _summary(rows):
    values = [
        _number(row["realized_gain_loss_usd"], "realized P/L")
        for row in rows
    ]
    profit = sum((v for v in values if v > 0), Decimal("0"))
    loss = -sum((v for v in values if v < 0), Decimal("0"))
    net = profit - loss
    count = len(values)
    return {
        "closed_trades": count,
        "wins": sum(v > 0 for v in values),
        "losses": sum(v < 0 for v in values),
        "breakeven": sum(v == 0 for v in values),
        "gross_profit_usd": str(profit),
        "gross_loss_usd": str(loss),
        "net_realized_usd": str(net),
        "profit_factor": str(profit / loss) if loss else None,
        "profit_factor_status": (
            "defined" if loss else "no_realized_losses"
        ),
        "expectancy_usd": str(net / count) if count else None,
    }


def _groups(rows, field):
    grouped = {}
    for row in rows:
        key = row[field]
        grouped.setdefault(key, []).append(row)
    return [
        {field: key, **_summary(grouped[key])}
        for key in sorted(grouped)
    ]


def build_trade_diagnosis(
    *,
    experiment_id,
    strategy_name,
    portfolio_id,
    journals,
    queried_at,
    minimum_profit_factor,
    stop_loss_percent,
    minimum_closed_trades=100,
    query_limit_reached=False,
):
    """Describe supplied closed journals without claiming causal evidence.

    The caller must bind the query to the specified portfolio/experiment.
    Thresholds must come from the applicable policy and committee criteria.
    Serialized journal values may already have lost database precision.
    """
    for name, value in (
        ("experiment ID", experiment_id),
        ("strategy name", strategy_name),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Invalid {name}.")
    if type(portfolio_id) is not int or portfolio_id < 1:
        raise ValueError("Invalid portfolio ID.")
    if type(minimum_closed_trades) is not int or minimum_closed_trades < 1:
        raise ValueError("Invalid trade minimum.")
    if type(query_limit_reached) is not bool:
        raise ValueError("Invalid query-limit flag.")
    if not isinstance(queried_at, str):
        raise ValueError("Query timestamp must be text.")
    measured = datetime.fromisoformat(queried_at)
    if measured.utcoffset() is None:
        raise ValueError("Query timestamp requires a timezone.")
    if not isinstance(journals, list) or len(journals) > 10000:
        raise ValueError("Supply at most 10,000 journal rows.")

    required_pf = _number(minimum_profit_factor, "profit-factor minimum")
    stop = _number(stop_loss_percent, "stop-loss threshold")
    if required_pf <= 0 or stop <= 0:
        raise ValueError("Thresholds must be positive.")

    identifiers = set()
    rows = []
    overruns = []
    for source in journals:
        if not isinstance(source, dict):
            raise ValueError("Invalid journal row.")
        identifier = source.get("id")
        if type(identifier) is not int or identifier < 1:
            raise ValueError("Invalid journal ID.")
        if identifier in identifiers:
            raise ValueError("Duplicate journal ID.")
        identifiers.add(identifier)
        if source.get("status") != "closed":
            raise ValueError("Only closed journals are supported.")
        if source.get("strategy_name") != strategy_name:
            raise ValueError("Journal strategy mismatch.")
        for field in ("symbol", "exit_rule"):
            if not isinstance(source.get(field), str) or not source[field].strip():
                raise ValueError(f"Missing journal {field}.")
        pnl = _number(source.get("realized_gain_loss_usd"), "realized P/L")
        row = {
            "id": identifier,
            "symbol": source["symbol"].strip().upper(),
            "exit_rule": source["exit_rule"].strip(),
            "realized_gain_loss_usd": str(pnl),
        }
        rows.append(row)
        if row["exit_rule"] == "stop_loss":
            observed = _number(source.get("return_percent"), "stop return")
            overrun = -observed - stop
            if overrun > 0:
                overruns.append({
                    **row,
                    "return_percent": str(observed),
                    "threshold_overrun_percentage_points": str(overrun),
                    "opened_at": source.get("opened_at"),
                    "closed_at": source.get("closed_at"),
                })

    summary = _summary(rows)
    pf = summary["profit_factor"]
    findings = []
    if query_limit_reached:
        findings.append("Query limit reached; history may be incomplete.")
    if len(rows) < minimum_closed_trades:
        findings.append("Closed-trade sample is below the supplied minimum.")
    if pf is not None and Decimal(pf) < required_pf:
        findings.append("Realized profit factor is below the supplied minimum.")
    if overruns:
        findings.append(
            "Stop exits exceed the supplied loss threshold; "
            "quote availability, price gaps and execution timing need review."
        )

    ordered_sources = sorted(journals, key=lambda row: row["id"])
    raw = json.dumps(
        ordered_sources, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return {
        "schema_version": 1,
        "designation": "retrospective_trade_diagnosis",
        "experiment_id": experiment_id,
        "strategy_name": strategy_name,
        "portfolio_id": portfolio_id,
        "queried_at": measured.isoformat(),
        "journal_sha256": hashlib.sha256(raw).hexdigest(),
        "query_limit_reached": query_limit_reached,
        "thresholds": {
            "minimum_profit_factor": str(required_pf),
            "stop_loss_percent": str(stop),
            "minimum_closed_trades": minimum_closed_trades,
        },
        "summary": summary,
        "by_symbol": _groups(rows, "symbol"),
        "by_exit_rule": _groups(rows, "exit_rule"),
        "stop_overruns": sorted(
            overruns,
            key=lambda row: (
                -Decimal(row["threshold_overrun_percentage_points"]),
                row["id"],
            ),
        ),
        "findings": findings,
        "limitations": [
            "Journal hash identifies supplied content, not its authenticity.",
            "Portfolio and experiment identity require caller verification.",
            "Closed journals do not reconstruct intervening price paths.",
            "Exit-group outcomes do not prove an alternative exit is better.",
            "Fees, quote freshness and accounting are not independently verified.",
            "Serialized journal numbers may have reduced precision.",
        ],
        "validation_verified": False,
        "strategy_change_authorized": False,
        "promotion_authorized": False,
        "live_capital_authorized": False,
    }
