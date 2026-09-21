"""Read-only concentration diagnostics from indicative valuations."""

from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext

from app.capital.portfolio_daily_returns import utc_timestamp


def _amount(value):
    if isinstance(value, bool):
        raise ValueError("Amounts must be finite and nonnegative.")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError("Invalid amount.") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError("Amounts must be finite and nonnegative.")
    return amount


def _identifier(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("IDs must be positive integers.")
    return value


def _weights(cash, values):
    total = cash + sum(values.values(), Decimal("0"))
    rows = [
        {
            "asset_id": asset_id,
            "market_value_usd": str(amount),
            "equity_weight_percent": (
                str(amount / total * 100) if total > 0 else None
            ),
        }
        for asset_id, amount in sorted(
            values.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    return {
        "total_value_usd": str(total),
        "cash_balance_usd": str(cash),
        "cash_weight_percent": (
            str(cash / total * 100) if total > 0 else None
        ),
        "invested_weight_percent": (
            str((total - cash) / total * 100) if total > 0 else None
        ),
        "largest_asset_weight_percent": (
            rows[0]["equity_weight_percent"]
            if rows else ("0" if total > 0 else None)
        ),
        "assets": rows,
    }


def analyze_concentration(valuation):
    """Report nominal exposure, not covariance-based risk contribution.

    Combined weights require complete indicative values for every supplied
    portfolio. Holdings overlap remains available without complete prices.
    """
    measured = utc_timestamp(valuation["snapshot_at"])
    portfolios = valuation["portfolios"]
    seen_portfolios = set()
    owners = defaultdict(list)
    reports = []
    blockers = []
    combined_values = defaultdict(lambda: Decimal("0"))
    combined_cash = Decimal("0")

    with localcontext() as context:
        context.prec = 60

        for portfolio in portfolios:
            portfolio_id = _identifier(portfolio["portfolio_id"])
            if portfolio_id in seen_portfolios:
                raise ValueError("Duplicate portfolio ID.")
            seen_portfolios.add(portfolio_id)

            if utc_timestamp(portfolio["measured_at"]) != measured:
                raise ValueError("Portfolio measurement times differ.")

            status = portfolio["valuation_status"]
            if status not in ("indicative", "incomplete"):
                raise ValueError("Unsupported valuation status.")

            cash = _amount(portfolio["cash_balance_usd"])
            values = {}
            seen_assets = set()
            missing_price = False

            for holding in portfolio["holdings"]:
                asset_id = _identifier(holding["asset_id"])
                if asset_id in seen_assets:
                    raise ValueError("Duplicate holding asset ID.")
                seen_assets.add(asset_id)

                quantity = _amount(holding["quantity"])
                if quantity <= 0:
                    raise ValueError("Holdings must have positive quantities.")

                owners[asset_id].append({
                    "portfolio_id": portfolio_id,
                    "portfolio_name": portfolio["portfolio_name"],
                    "quantity": str(quantity),
                    "symbol": holding.get("symbol"),
                })

                amount = holding["market_value_usd"]
                if amount is None or holding.get("exclusion_reason") is not None:
                    missing_price = True
                else:
                    values[asset_id] = _amount(amount)

            complete = (
                status == "indicative"
                and not missing_price
                and not portfolio.get("excluded_holdings")
            )
            if status == "indicative" and not complete:
                raise ValueError("Indicative portfolio has excluded holdings.")

            report = {
                "portfolio_id": portfolio_id,
                "portfolio_name": portfolio["portfolio_name"],
                "status": "indicative" if complete else "unavailable",
                "concentration": None,
            }
            if complete:
                weights = _weights(cash, values)
                if _amount(portfolio["total_value_usd"]) != _amount(
                    weights["total_value_usd"]
                ):
                    raise ValueError("Portfolio total does not reconcile.")
                report["concentration"] = weights
                combined_cash += cash
                for asset_id, amount in values.items():
                    combined_values[asset_id] += amount
            else:
                blockers.append({
                    "portfolio_id": portfolio_id,
                    "reason": "incomplete_valuation",
                })
            reports.append(report)

        combined = {
            "status": "unavailable",
            "concentration": None,
            "blockers": blockers,
        }
        if portfolios and not blockers:
            combined["status"] = "indicative"
            combined["concentration"] = _weights(
                combined_cash, combined_values
            )

    return {
        "snapshot_at": measured.isoformat(),
        "valuation_basis": "stored_observations_indicative",
        "portfolios": sorted(reports, key=lambda row: row["portfolio_id"]),
        "combined": combined,
        "shared_holdings": [
            {
                "asset_id": asset_id,
                "portfolio_count": len(holdings),
                "holdings": sorted(
                    holdings, key=lambda row: row["portfolio_id"]
                ),
            }
            for asset_id, holdings in sorted(owners.items())
            if len(holdings) > 1
        ],
        "limitations": [
            "Weights describe nominal dollar exposure, not risk contribution.",
            "Combined values sum separate paper accounts.",
            "Shared holdings do not establish return correlation.",
            "Stored observation recency does not prove market quote freshness.",
        ],
        "database_writes": False,
        "allocation_authority": False,
        "live_capital_authority": False,
    }
