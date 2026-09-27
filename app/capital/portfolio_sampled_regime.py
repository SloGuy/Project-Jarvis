"""Prospective market-context capture and descriptive sampled-return grouping."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext

from app.capital.portfolio_daily_returns import utc_timestamp


MAXIMUM_CONTEXT_AGE = timedelta(hours=2)


def capture_market_context():
    """Capture the existing advisory SPY classifier without blocking evidence."""
    from app.capital.market_regime import get_market_regime

    try:
        report = get_market_regime()
        return {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "scope": "spy_market_proxy_not_portfolio_regime",
            "report": report,
        }
    except Exception:
        return {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "scope": "spy_market_proxy_not_portfolio_regime",
            "report": {
                "status": "unavailable",
                "reason": "Market context capture failed.",
            },
        }


def analyze_sampled_regimes(*, records, reports_by_portfolio):
    """Use only context captured before each interval's actual start.

    The latest completed context wins, including unavailable context.
    No fallback to an older successful label is allowed.
    Group means are descriptive, not causal regime attribution.
    """
    contexts = {}
    completed_at = {}
    for record in records:
        checkpoint = record["checkpoint"]
        context = checkpoint.get("market_context")
        if context is None:
            continue
        if context.get("scope") != "spy_market_proxy_not_portfolio_regime":
            raise ValueError("Unsupported regime context scope.")
        observed = utc_timestamp(context["observed_at"])
        finished = utc_timestamp(checkpoint["finished_at"])
        sampled = utc_timestamp(checkpoint["sampled_at"])
        if not sampled <= observed <= finished:
            raise ValueError("Regime context timestamps differ from capture.")
        if observed in contexts and contexts[observed] != context:
            raise ValueError("Conflicting regime contexts at the same time.")
        contexts[observed] = context
        completed_at[observed] = finished

    results = []
    for identifier, report in sorted(reports_by_portfolio.items()):
        groups = {}
        excluded = []
        for interval in report["returns"]:
            start = utc_timestamp(interval["start"])
            eligible = [
                at for at in contexts
                if at <= start and completed_at[at] <= start
            ]
            reason = None
            label = None
            if not eligible:
                reason = "no_prior_market_context"
            else:
                at = max(eligible)
                context_report = contexts[at]["report"]
                if start - at > MAXIMUM_CONTEXT_AGE:
                    reason = "prior_market_context_too_old"
                elif context_report.get("status") != "success":
                    reason = "latest_prior_market_context_unavailable"
                else:
                    label = context_report.get("combined_regime")
                    if not isinstance(label, str) or not label.strip():
                        reason = "market_regime_label_missing"

            if reason:
                excluded.append({
                    "start": interval["start"],
                    "end": interval["end"],
                    "reason": reason,
                })
                continue
            value = Decimal(interval["return_fraction"])
            if not value.is_finite() or value <= -1:
                raise ValueError("Invalid sampled return.")
            groups.setdefault(label, []).append(value)

        rows = []
        with localcontext() as precision:
            precision.prec = 60
            for label, values in sorted(groups.items()):
                count = len(values)
                rows.append({
                    "market_regime": label,
                    "observations": count,
                    "status": (
                        "available_indicative" if count >= 30
                        else "insufficient_data"
                    ),
                    "mean_sampled_return_percent": (
                        str(sum(values, Decimal("0")) / count * 100)
                        if count >= 30 else None
                    ),
                })
        results.append({
            "portfolio_id": identifier,
            "groups": rows,
            "excluded_intervals": excluded,
        })

    return {
        "status": (
            "available_indicative"
            if any(
                group["status"] == "available_indicative"
                for row in results for group in row["groups"]
            )
            else "insufficient_data"
        ),
        "methodology": "prior_observed_spy_context_sampled_returns_v1",
        "scope": "sampled_returns_grouped_by_prior_spy_market_context",
        "maximum_context_age_seconds": MAXIMUM_CONTEXT_AGE.total_seconds(),
        "minimum_observations_per_group": 30,
        "portfolios": results,
        "limitations": [
            "SPY classification is a market proxy, not each portfolio's regime.",
            "Only context captured before interval start is used.",
            "Missing, old, or unavailable context excludes an interval.",
            "Grouped means do not establish causation or future performance.",
            "No portfolio regime-exposure or allocation authority is asserted.",
        ],
        "allocation_authority": False,
        "live_capital_authority": False,
    }
