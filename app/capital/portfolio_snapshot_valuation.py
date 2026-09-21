"""Indicative current valuations from one read-only database snapshot."""

from collections import defaultdict
from datetime import timedelta

from app.capital.portfolio_daily_returns import utc_timestamp
from app.capital.portfolio_historical_quotes import select_historical_quote
from app.capital.portfolio_indicative_valuation import value_balance


def value_portfolio_snapshot(
    *,
    snapshot,
    provider_by_asset,
    maximum_observation_age,
):
    """Value current holdings using explicitly selected stored providers.

    Observation age measures stored-row recency, not market quote freshness.
    No provider fallback or partial portfolio total is permitted.
    """
    measured = utc_timestamp(snapshot["snapshot_at"])

    if not isinstance(provider_by_asset, dict):
        raise ValueError("provider_by_asset must be a dictionary.")
    if (
        not isinstance(maximum_observation_age, timedelta)
        or maximum_observation_age <= timedelta(0)
    ):
        raise ValueError("maximum_observation_age must be positive.")

    for asset_id, provider in provider_by_asset.items():
        if (
            isinstance(asset_id, bool)
            or not isinstance(asset_id, int)
            or asset_id <= 0
        ):
            raise ValueError("Provider asset IDs must be positive integers.")
        if not isinstance(provider, str) or not provider.strip():
            raise ValueError("Each selected provider must be nonempty.")

    assets = {}
    for asset in snapshot["assets"]:
        if asset["id"] in assets:
            raise ValueError("Duplicate asset metadata.")
        assets[asset["id"]] = asset

    portfolios = {}
    for portfolio in snapshot["portfolios"]:
        portfolio_id = portfolio["id"]
        if portfolio_id in portfolios:
            raise ValueError("Duplicate portfolio records.")
        if portfolio["portfolio_type"] != "paper":
            raise ValueError("Only paper portfolios are supported.")
        portfolios[portfolio_id] = portfolio

    positions_by_portfolio = defaultdict(list)
    for position in snapshot["positions"]:
        portfolio_id = position["portfolio_id"]
        if portfolio_id not in portfolios:
            raise ValueError("Position belongs to an unknown portfolio.")
        positions_by_portfolio[portfolio_id].append({
            "asset_id": position["asset_id"],
            "quantity": position["quantity"],
        })

    observations_by_asset = defaultdict(list)
    for observation in snapshot["observations"]:
        observations_by_asset[observation["asset_id"]].append(observation)

    selected_quotes = {}
    held_assets = {
        position["asset_id"]
        for position in snapshot["positions"]
    }
    for asset_id in sorted(held_assets):
        provider = provider_by_asset.get(asset_id)
        if provider is None or asset_id not in assets:
            continue
        selected_quotes[asset_id] = select_historical_quote(
            observations=observations_by_asset[asset_id],
            asset_id=asset_id,
            provider=provider,
            measured_at=measured,
            maximum_age=maximum_observation_age,
        )

    results = []
    for portfolio_id, portfolio in sorted(portfolios.items()):
        valuation = value_balance(
            balance={
                "measured_at": measured.isoformat(),
                "cash_balance_usd": portfolio["cash_balance_usd"],
                "positions": positions_by_portfolio[portfolio_id],
            },
            quotes=selected_quotes,
        )

        for holding in valuation["holdings"]:
            metadata = assets.get(holding["asset_id"], {})
            holding["symbol"] = metadata.get("symbol")
            holding["asset_type"] = metadata.get("asset_type")
            if holding["asset_id"] not in assets:
                holding["coverage_note"] = "missing_asset_metadata"
            elif holding["asset_id"] not in provider_by_asset:
                holding["coverage_note"] = "provider_not_selected"

        results.append({
            "portfolio_id": portfolio_id,
            "portfolio_name": portfolio["name"],
            **valuation,
        })

    return {
        "snapshot_at": measured.isoformat(),
        "valuation_basis": "stored_observations_indicative",
        "maximum_observation_age_seconds": (
            maximum_observation_age.total_seconds()
        ),
        "portfolios": results,
        "complete_indicative_count": sum(
            row["valuation_status"] == "indicative"
            for row in results
        ),
        "incomplete_count": sum(
            row["valuation_status"] == "incomplete"
            for row in results
        ),
        "market_quote_freshness_verified": False,
        "historical_availability_verified": False,
        "database_writes": False,
        "allocation_authority": False,
        "live_capital_authority": False,
    }
