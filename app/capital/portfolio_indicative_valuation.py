"""Indicative portfolio values from selected stored observations.

Recent database observations do not prove fresh underlying market quotes.
Incomplete valuations never report a partial total as portfolio equity.
"""

from decimal import Decimal, InvalidOperation, localcontext

from app.capital.portfolio_daily_returns import utc_timestamp


def _number(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a nonnegative finite number.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError(
            f"{name} must be a nonnegative finite number."
        ) from error
    if not result.is_finite() or result < 0:
        raise ValueError(f"{name} must be a nonnegative finite number.")
    return result


def value_balance(*, balance, quotes):
    """Combine one reconstructed balance with explicitly selected quotes.

    quotes maps integer asset IDs to select_historical_quote results.
    A quote's "usable" status means its stored timestamp passed the age
    check, not that the underlying market quote is independently fresh.
    """
    measured = utc_timestamp(balance["measured_at"])
    cash = _number(balance["cash_balance_usd"], "cash")
    if not isinstance(quotes, dict):
        raise ValueError("quotes must map asset IDs to selected quotes.")

    holdings = []
    missing = []
    seen = set()
    known_value = Decimal("0")

    with localcontext() as context:
        context.prec = 60

        for position in balance["positions"]:
            asset_id = position["asset_id"]
            if (
                isinstance(asset_id, bool)
                or not isinstance(asset_id, int)
                or asset_id <= 0
                or asset_id in seen
            ):
                raise ValueError("Invalid or duplicate position asset ID.")
            seen.add(asset_id)
            quantity = _number(position["quantity"], "quantity")
            if quantity == 0:
                continue

            quote = quotes.get(asset_id)
            reason = "missing_quote"
            market_value = None
            provider = None
            observed_at = None

            if quote is not None:
                if quote["asset_id"] != asset_id:
                    raise ValueError("Quote belongs to a different asset.")
                if utc_timestamp(quote["measured_at"]) != measured:
                    raise ValueError("Quote belongs to a different measurement.")

                provider = quote.get("provider")
                observed_at = quote.get("observed_at")
                reason = quote["status"]

                if reason == "usable":
                    if not isinstance(provider, str) or not provider.strip():
                        raise ValueError("Selected quote has no provider.")
                    if observed_at is None:
                        raise ValueError("Selected quote has no timestamp.")
                    if utc_timestamp(observed_at) > measured:
                        raise ValueError("Selected quote is future-dated.")
                    price = _number(quote["price_usd"], "price")
                    if price == 0:
                        raise ValueError("Selected price must be positive.")
                    market_value = quantity * price
                    known_value += market_value
                    reason = None

            if reason is not None:
                missing.append({
                    "asset_id": asset_id,
                    "reason": reason,
                })

            holdings.append({
                "asset_id": asset_id,
                "quantity": str(quantity),
                "market_value_usd": (
                    str(market_value) if market_value is not None else None
                ),
                "provider": provider,
                "observed_at": observed_at,
                "exclusion_reason": reason,
            })

        total = cash + known_value if not missing else None

    return {
        "measured_at": measured.isoformat(),
        "valuation_status": "incomplete" if missing else "indicative",
        "cash_balance_usd": str(cash),
        "known_market_value_usd": str(known_value),
        "total_value_usd": str(total) if total is not None else None,
        "holdings": holdings,
        "excluded_holdings": missing,
        "market_quote_freshness_verified": False,
        "historical_availability_verified": False,
        "historical_completeness_verified": False,
        "limitations": [
            "Stored observation age does not establish market quote age.",
            "Reconstructed balances do not prove complete ledger history.",
        ],
        "database_writes": False,
        "allocation_authority": False,
        "live_capital_authority": False,
    }
