"""One prospective collection cycle for registered paper holdings."""

from datetime import datetime, timezone
import os

from app.capital.portfolio_intelligence_reader import read_portfolio_inputs
from app.capital.portfolio_intelligence_service import _resolve_portfolios
from app.capital.quote_provenance_capture import capture_quote_provenance
from app.capital.quote_provenance_crypto_batch import capture_crypto_batch
from app.capital.quote_provenance_operator import PROVENANCE_DIRECTORY
from app.capital.quote_provenance_universe import select_provenance_assets


def read_collection_universe():
    from app.watchlist_quotes import CRYPTO_PROVIDER_IDS

    resolved = _resolve_portfolios()
    snapshot = read_portfolio_inputs(
        portfolio_ids=sorted(resolved),
        quote_window_start=datetime.now(timezone.utc),
    )
    return select_provenance_assets(
        snapshot=snapshot,
        resolved_portfolios=resolved,
        crypto_provider_ids=CRYPTO_PROVIDER_IDS,
    )


def collect_held_quote_provenance():
    """Capture the held-asset set observed at the start of this cycle.

    Holdings may change during collection. This is quote collection, not
    a simultaneous portfolio valuation or proof of complete price history.
    """
    started_at = datetime.now(timezone.utc)
    universe = read_collection_universe()

    crypto = [
        asset for asset in universe["assets"]
        if asset["asset_type"] == "crypto"
    ]
    stocks = [
        asset for asset in universe["assets"]
        if asset["asset_type"] == "stock"
    ]

    outcomes = []
    crypto_request_attempted = False

    if crypto:
        try:
            result = capture_crypto_batch(
                assets=crypto,
                directory=PROVENANCE_DIRECTORY,
            )
            crypto_request_attempted = result["request_attempted"]
            outcomes.extend(result["outcomes"])
        except Exception:
            # No raw exception text: URLs may contain credentials.
            # Request/publication state is unknown after an unexpected error.
            crypto_request_attempted = None
            outcomes.extend({
                "asset_id": asset["id"],
                "symbol": asset["symbol"],
                "status": "failed",
                "reason": "crypto_batch_failed_records_may_exist",
            } for asset in crypto)

    key = os.getenv("FINNHUB_API_KEY", "").strip()
    stock_attempts = 0

    for asset in stocks:
        if not key:
            outcomes.append({
                "asset_id": asset["id"],
                "symbol": asset["symbol"],
                "status": "failed",
                "reason": "finnhub_credentials_unavailable",
            })
            continue

        stock_attempts += 1
        try:
            saved = capture_quote_provenance(
                asset=asset,
                directory=PROVENANCE_DIRECTORY,
                finnhub_api_key=key,
            )
            outcomes.append({
                **saved,
                "status": "captured",
            })
        except Exception:
            outcomes.append({
                "asset_id": asset["id"],
                "symbol": asset["symbol"],
                "status": "failed",
                "reason": "stock_capture_failed_record_may_exist",
            })

    outcomes.sort(key=lambda row: row["asset_id"])
    captured = sum(row["status"] == "captured" for row in outcomes)
    unsupported = universe["unsupported_assets"]
    failed = len(outcomes) - captured
    expected = universe["held_asset_count"]

    if expected == 0:
        status = "empty"
    elif not failed and not unsupported and captured == expected:
        status = "captured"
    elif captured:
        status = "partial"
    else:
        status = "failed"

    return {
        "schema_version": 1,
        "status": status,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "holdings_snapshot_at": universe["snapshot_at"],
        "held_asset_count": expected,
        "captured_count": captured,
        "failed_count": failed,
        "unsupported_assets": unsupported,
        "crypto_request_attempted": crypto_request_attempted,
        "stock_capture_attempts": stock_attempts,
        "outcomes": outcomes,
        "scope": "held_assets_at_cycle_start",
        "timestamp_eligibility_assessed": False,
        "database_writes": False,
        "legacy_observation_writes": False,
        "validation_receipt_writes": False,
        "execution_authorized": False,
        "live_capital_authorized": False,
    }
