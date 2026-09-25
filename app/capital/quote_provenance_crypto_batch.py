"""One-request crypto provenance capture with per-asset outcomes."""

from datetime import datetime, timezone

from app.capital.quote_provenance_adapter import coingecko_rest_provenance
from app.capital.quote_provenance_store import save_quote_provenance


def capture_crypto_batch(*, assets, directory):
    """Validate identities, fetch once, and persist each valid asset.

    Failures are sanitized because provider exceptions may contain URLs.
    A storage failure may occur after publication; no rollback is claimed.
    """
    from app.watchlist_quotes import (
        CRYPTO_PROVIDER_IDS,
        _fetch_crypto_quotes,
    )

    selected = []
    ids = set()
    symbols = set()

    for asset in assets:
        asset_id = asset["id"]
        symbol = asset["symbol"]
        if type(asset_id) is not int or asset_id <= 0 or asset_id in ids:
            raise ValueError("Invalid or duplicate asset ID.")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("Invalid crypto symbol.")
        symbol = symbol.strip().upper()
        if symbol in symbols:
            raise ValueError("Duplicate crypto symbol.")
        if asset["asset_type"] != "crypto":
            raise ValueError("Batch accepts crypto assets only.")

        provider_id = asset.get("provider_id")
        if (
            not isinstance(provider_id, str)
            or not provider_id
            or CRYPTO_PROVIDER_IDS.get(symbol) != provider_id
        ):
            raise ValueError("Crypto registry and fetcher identities differ.")

        ids.add(asset_id)
        symbols.add(symbol)
        selected.append({
            "id": asset_id,
            "symbol": symbol,
            "provider_id": provider_id,
        })

    selected.sort(key=lambda asset: asset["id"])
    if not selected:
        return {
            "status": "empty",
            "request_attempted": False,
            "outcomes": [],
        }

    try:
        quotes = _fetch_crypto_quotes([
            asset["symbol"] for asset in selected
        ])
        captured_at = datetime.now(timezone.utc)
        if not isinstance(quotes, dict):
            raise ValueError("Unexpected crypto response.")
    except Exception:
        return {
            "status": "failed",
            "request_attempted": True,
            "outcomes": [
                {
                    "asset_id": asset["id"],
                    "symbol": asset["symbol"],
                    "status": "failed",
                    "reason": "provider_request_or_response_failed",
                }
                for asset in selected
            ],
        }

    outcomes = []
    for asset in selected:
        outcome = {
            "asset_id": asset["id"],
            "symbol": asset["symbol"],
            "status": "failed",
        }

        try:
            quote = quotes.get(asset["symbol"])
            if not isinstance(quote, dict):
                raise ValueError("Requested quote unavailable.")
            if quote.get("source") != "coingecko_snapshot":
                raise ValueError("Unexpected quote source.")

            record = coingecko_rest_provenance(
                asset_id=asset["id"],
                symbol=asset["symbol"],
                provider_id=asset["provider_id"],
                quote={
                    **quote,
                    "available": True,
                    "id": asset["provider_id"],
                    "last_updated_at": quote.get("quote_timestamp"),
                },
                captured_at=captured_at,
            )
        except Exception:
            outcome["reason"] = "quote_missing_or_invalid"
            outcomes.append(outcome)
            continue

        try:
            saved = save_quote_provenance(directory=directory, record=record)
        except Exception:
            outcome["reason"] = "storage_failed_record_may_exist"
            outcomes.append(outcome)
            continue

        outcome.update(
            status="captured",
            record_id=saved["record_id"],
            provider_time_status=record["provider_time_status"],
            provider_observed_at=record["provider_observed_at"],
            captured_at=record["captured_at"],
        )
        outcomes.append(outcome)

    successes = sum(row["status"] == "captured" for row in outcomes)
    return {
        "status": (
            "captured" if successes == len(outcomes)
            else "partial" if successes else "failed"
        ),
        "request_attempted": True,
        "outcomes": outcomes,
    }
