"""Select held assets for prospective provenance collection."""

from decimal import Decimal, InvalidOperation

from app.capital.portfolio_daily_returns import utc_timestamp


def _identifier(value):
    if type(value) is not int or value <= 0:
        raise ValueError("IDs must be positive integers.")
    return value


def _quantity(value):
    if isinstance(value, bool):
        raise ValueError("Invalid holding quantity.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError("Invalid holding quantity.") from error
    if not result.is_finite() or result < 0:
        raise ValueError("Holding quantities must be finite and nonnegative.")
    return result


def select_provenance_assets(
    *,
    snapshot,
    resolved_portfolios,
    crypto_provider_ids,
):
    """Select unique positive holdings within the resolved paper universe.

    resolved_portfolios is the experiment-to-portfolio mapping used by
    Portfolio Intelligence. crypto_provider_ids is the fetcher's explicit
    symbol-to-provider-ID mapping.
    """
    if not isinstance(resolved_portfolios, dict) or not resolved_portfolios:
        raise ValueError("An explicit resolved portfolio mapping is required.")
    if not isinstance(crypto_provider_ids, dict):
        raise ValueError("An explicit crypto provider mapping is required.")

    for portfolio_id in resolved_portfolios:
        _identifier(portfolio_id)

    measured = utc_timestamp(snapshot["snapshot_at"])
    portfolios = {}
    for portfolio in snapshot["portfolios"]:
        portfolio_id = _identifier(portfolio["id"])
        if portfolio_id in portfolios:
            raise ValueError("Duplicate portfolio.")
        if portfolio_id not in resolved_portfolios:
            raise ValueError("Unexpected portfolio.")
        expected = resolved_portfolios[portfolio_id]
        if (
            portfolio["name"] != expected["portfolio_name"]
            or portfolio["portfolio_type"] != "paper"
            or portfolio["is_active"] is not True
        ):
            raise ValueError("Portfolio identity or eligibility changed.")
        portfolios[portfolio_id] = portfolio

    if set(portfolios) != set(resolved_portfolios):
        raise ValueError("Portfolio snapshot is incomplete.")

    assets = {}
    for asset in snapshot["assets"]:
        asset_id = _identifier(asset["id"])
        if asset_id in assets:
            raise ValueError("Duplicate asset metadata.")
        assets[asset_id] = asset

    owners = {}
    seen_positions = set()
    for position in snapshot["positions"]:
        portfolio_id = _identifier(position["portfolio_id"])
        asset_id = _identifier(position["asset_id"])
        if portfolio_id not in portfolios:
            raise ValueError("Holding belongs to an unexpected portfolio.")
        key = (portfolio_id, asset_id)
        if key in seen_positions:
            raise ValueError("Duplicate portfolio holding.")
        seen_positions.add(key)
        if _quantity(position["quantity"]) > 0:
            owners.setdefault(asset_id, []).append(portfolio_id)

    selected = []
    unsupported = []

    for asset_id, portfolio_ids in sorted(owners.items()):
        asset = assets.get(asset_id)
        reason = None
        provider = None

        if asset is None:
            reason = "missing_asset_metadata"
        elif not isinstance(asset.get("symbol"), str) or not asset["symbol"].strip():
            reason = "invalid_asset_symbol"
        elif asset.get("is_active") is not True:
            reason = "inactive_asset"
        elif asset["asset_type"] == "stock":
            provider = "Finnhub REST"
        elif asset["asset_type"] == "crypto":
            expected_id = crypto_provider_ids.get(asset["symbol"].strip().upper())
            if (
                not isinstance(expected_id, str)
                or not expected_id
                or asset.get("provider_id") != expected_id
            ):
                reason = "crypto_provider_identity_unresolved"
            else:
                provider = "CoinGecko REST"
        else:
            reason = "unsupported_asset_type"

        if reason:
            unsupported.append({
                "asset_id": asset_id,
                "symbol": asset.get("symbol") if asset else None,
                "portfolio_ids": sorted(portfolio_ids),
                "reason": reason,
            })
        else:
            selected.append({
                "id": asset_id,
                "symbol": asset["symbol"].strip().upper(),
                "asset_type": asset["asset_type"],
                "provider_id": asset.get("provider_id"),
                "provider": provider,
                "portfolio_ids": sorted(portfolio_ids),
            })

    return {
        "snapshot_at": measured.isoformat(),
        "status": "incomplete" if unsupported else "resolved",
        "portfolio_count": len(portfolios),
        "held_asset_count": len(owners),
        "assets": selected,
        "unsupported_assets": unsupported,
        "scope": "positive_holdings_in_registered_paper_portfolios",
        "network_requests": False,
        "database_writes": False,
        "execution_authorized": False,
    }
