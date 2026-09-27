"""Indicative paper valuations using verified saved quote provenance."""
from collections import defaultdict

from app.capital.portfolio_daily_returns import utc_timestamp
from app.capital.portfolio_indicative_valuation import value_balance
from app.capital.quote_provenance_coverage import get_quote_provenance_coverage
from app.capital.quote_provenance_eligibility import assess_quote_provenance
from app.capital.quote_provenance_store import load_quote_provenance


def value_provenance_snapshot(
    *,
    snapshot,
    directory,
    provider_by_asset,
    maximum_provider_age,
    maximum_capture_age,
):
    """Value current balances at their database snapshot timestamp.

    Filesystem coverage is not part of the database transaction.
    Timestamp eligibility never grants historical or trading authority.
    """
    measured = utc_timestamp(snapshot["snapshot_at"])
    if not isinstance(provider_by_asset, dict):
        raise ValueError("provider_by_asset must be a dictionary.")

    assets = {}
    for asset in snapshot["assets"]:
        if asset["id"] in assets:
            raise ValueError("Duplicate asset metadata.")
        assets[asset["id"]] = asset

    portfolios = {}
    for portfolio in snapshot["portfolios"]:
        if portfolio["id"] in portfolios:
            raise ValueError("Duplicate portfolio.")
        if portfolio["portfolio_type"] != "paper":
            raise ValueError("Only paper portfolios are supported.")
        portfolios[portfolio["id"]] = portfolio

    positions = defaultdict(list)
    held = set()
    for position in snapshot["positions"]:
        portfolio_id = position["portfolio_id"]
        if portfolio_id not in portfolios:
            raise ValueError("Position belongs to an unknown portfolio.")
        positions[portfolio_id].append({
            "asset_id": position["asset_id"],
            "quantity": position["quantity"],
        })

    # Validate balances and identify positive holdings before scanning files.
    for portfolio_id, portfolio in portfolios.items():
        preliminary = value_balance(
            balance={
                "measured_at": measured.isoformat(),
                "cash_balance_usd": portfolio["cash_balance_usd"],
                "positions": positions[portfolio_id],
            },
            quotes={},
        )
        held.update(row["asset_id"] for row in preliminary["holdings"])

    requested = []
    for asset_id in sorted(held):
        if asset_id not in assets or asset_id not in provider_by_asset:
            continue
        asset = assets[asset_id]
        requested.append({
            "asset_id": asset_id,
            "symbol": asset["symbol"],
            "asset_type": asset["asset_type"],
            "provider": provider_by_asset[asset_id],
        })

    coverage = get_quote_provenance_coverage(
        directory=directory,
        requested_assets=requested,
        measured_at=measured,
        maximum_provider_age=maximum_provider_age,
        maximum_capture_age=maximum_capture_age,
    )

    quotes = {}
    provenance = {}
    for row in coverage["assets"]:
        asset_id = row["asset_id"]
        provenance[asset_id] = row
        quote = {
            "asset_id": asset_id,
            "measured_at": measured.isoformat(),
            "provider": row["provider"],
            "status": row["status"],
        }
        if row["record_id"] is not None:
            record = load_quote_provenance(
                directory=directory,
                record_id=row["record_id"],
            )
            for name in ("asset_id", "symbol", "asset_type", "provider"):
                if record[name] != row[name]:
                    raise ValueError("Selected provenance identity changed.")
            assessment = assess_quote_provenance(
                record=record,
                measured_at=measured,
                maximum_provider_age=maximum_provider_age,
                maximum_capture_age=maximum_capture_age,
            )
            if assessment != row["assessment"]:
                raise ValueError("Selected provenance assessment changed.")
            quote["observed_at"] = record["provider_observed_at"]
            if assessment["status"] == "eligible":
                quote["status"] = "usable"
                quote["price_usd"] = record["price_usd"]
        quotes[asset_id] = quote

    results = []
    for portfolio_id, portfolio in sorted(portfolios.items()):
        valuation = value_balance(
            balance={
                "measured_at": measured.isoformat(),
                "cash_balance_usd": portfolio["cash_balance_usd"],
                "positions": positions[portfolio_id],
            },
            quotes=quotes,
        )
        for holding in valuation["holdings"]:
            asset_id = holding["asset_id"]
            metadata = assets.get(asset_id, {})
            selected = provenance.get(asset_id)
            holding["symbol"] = metadata.get("symbol")
            holding["asset_type"] = metadata.get("asset_type")
            holding["quote_provenance"] = selected
            if asset_id not in assets:
                holding["coverage_note"] = "missing_asset_metadata"
            elif asset_id not in provider_by_asset:
                holding["coverage_note"] = "provider_not_selected"

        valuation["limitations"] = [
            "Provider and capture timestamps passed only explicit age checks "
            "where a holding was valued.",
            "Filesystem evidence and database balances are not one transaction.",
            "Capture time does not prove when a record was persisted.",
            "Provider authenticity and exchange-session rules are unverified.",
            "Current balances do not establish complete accounting history.",
        ]
        results.append({
            "portfolio_id": portfolio_id,
            "portfolio_name": portfolio["name"],
            **valuation,
        })

    return {
        "snapshot_at": measured.isoformat(),
        "valuation_basis": "saved_quote_provenance_indicative",
        "portfolios": results,
        "quote_coverage": coverage,
        "complete_indicative_count": sum(
            row["valuation_status"] == "indicative" for row in results
        ),
        "incomplete_count": sum(
            row["valuation_status"] == "incomplete" for row in results
        ),
        "same_database_and_filesystem_snapshot": False,
        "market_quote_freshness_verified": False,
        "historical_completeness_verified": False,
        "historical_availability_verified": False,
        "database_writes": False,
        "allocation_authority": False,
        "live_capital_authority": False,
    }
