"""One governed provider-time collection cycle.

Used only for version-2 plans. No legacy observations or trades
are written. Provider failures are reported without credential URLs.
"""

import os
from datetime import timedelta

from app.capital import validation_registry as registry
from app.capital.quote_provenance_capture import (
    capture_quote_provenance,
)
from app.capital.quote_provenance_store import (
    load_quote_provenance,
)
from app.capital.validation_plan import timestamp
from app.capital.validation_provider_collection import (
    authorize_collection,
    bind_provider_collection,
    check_current_binding,
    quote_directory,
    refresh_collection,
    require_provider_plan,
    seal_provider_collection,
    store_for,
    validate_provider_collection,
)
from app.capital.validation_quote_receipt import (
    make_validation_quote_receipt,
)
from app.capital.validation_quote_store import MAX_RECEIPTS


WARMUP = timedelta(minutes=30)


class ProviderCaptureError(RuntimeError):
    pass


def read_plan_asset(plan):
    from sqlalchemy import select
    from app.market_db.database import SessionLocal
    from app.market_db.models import MarketAsset

    with SessionLocal() as session:
        asset = session.execute(
            select(
                MarketAsset.id,
                MarketAsset.symbol,
                MarketAsset.asset_type,
                MarketAsset.provider_id,
                MarketAsset.is_active,
            ).where(MarketAsset.id == plan["asset_id"])
        ).mappings().one_or_none()

    if asset is None or asset["is_active"] is not True:
        raise ValueError("Plan asset is unavailable or inactive.")

    expected_type = (
        "crypto" if plan["provider"] == "CoinGecko"
        else "stock"
    )

    if (
        asset["symbol"].strip().upper()
        != plan["symbol"].strip().upper()
        or asset["asset_type"] != expected_type
    ):
        raise ValueError("Asset registry identity differs from plan.")

    return dict(asset)


def capture_plan_quote(plan, directory):
    try:
        asset = read_plan_asset(plan)
        return capture_quote_provenance(
            asset=asset,
            directory=directory,
            finnhub_api_key=(
                os.getenv("FINNHUB_API_KEY", "").strip()
                if plan["provider"] == "Finnhub"
                else None
            ),
        )
    except Exception:
        raise ProviderCaptureError(
            "Provider capture failed; no receipt was appended. "
            "A provenance file may already exist."
        ) from None


def capture_provider_cycle(plan_id):
    authorize_collection()
    row = registry.get_plan(plan_id)
    plan = require_provider_plan(row)

    if row["status"] != "registered":
        return {
            "plan_id": plan_id,
            "status": "skipped",
        }

    now = registry.now_utc()
    start = timestamp(plan["start"])
    end = timestamp(plan["end_exclusive"])

    if "provider_collection" not in row:
        if now < start - WARMUP:
            return {
                "plan_id": plan_id,
                "status": "waiting_for_collection_window",
            }

        bind_provider_collection(plan_id)
        return {
            "plan_id": plan_id,
            "status": "bound",
        }

    collection = validate_provider_collection(row)

    if collection["status"] == "sealed":
        return {
            "plan_id": plan_id,
            "status": "sealed",
        }

    if now >= end:
        seal_provider_collection(plan_id)
        return {
            "plan_id": plan_id,
            "status": "sealed",
        }

    # Serialize the provider fetch and receipt publication with registry
    # updates. This prevents cooperative writers from racing this cycle.
    with registry.locked_state(write=True) as state:
        row = state["plans"][plan_id]
        plan = require_provider_plan(row)

        if row["status"] != "registered":
            raise ValueError("Plan state changed before capture.")

        collection = validate_provider_collection(row)

        if collection["status"] != "collecting":
            raise ValueError("Provider collection is not open.")

        now = registry.now_utc()

        if now >= timestamp(plan["end_exclusive"]):
            return {
                "plan_id": plan_id,
                "status": "waiting_for_seal",
            }

        store = store_for(row, collection)
        checkpoint = collection["store_checkpoint"]

        if checkpoint["count"] >= MAX_RECEIPTS:
            raise ValueError("Provider receipt limit reached.")

        if checkpoint["count"]:
            entry, previous = store._read_entry(
                checkpoint["count"]
            )

            if entry["sha256"] != checkpoint["head"]:
                raise ValueError("Provider chain head mismatch.")

            elapsed = (
                now - timestamp(previous["recorded_at"])
            ).total_seconds()

            if elapsed < plan["input_contract"][
                "capture_interval_seconds"
            ]:
                return {
                    "plan_id": plan_id,
                    "status": "waiting_for_capture",
                    "receipt_count": checkpoint["count"],
                }

        check_current_binding(plan)
        require_provider_plan(row)
        authorize_collection()

        directory = quote_directory(row)
        captured = capture_plan_quote(plan, directory)
        record_id = captured["record_id"]

        # Verify readability before sampling the receipt logging time.
        load_quote_provenance(
            directory=directory,
            record_id=record_id,
        )
        recorded = registry.now_utc()

        if recorded >= timestamp(plan["end_exclusive"]):
            return {
                "plan_id": plan_id,
                "status": "window_ended_during_capture",
                "receipt_count": checkpoint["count"],
            }

        check_current_binding(plan)
        require_provider_plan(row)
        authorize_collection()

        receipt = make_validation_quote_receipt(
            envelope=row["envelope"],
            expected_sha256=row["registered_sha256"],
            bound_at=collection["bound_at"],
            recorded_at=recorded.isoformat(),
            directory=directory,
            record_id=record_id,
        )

        collection["store_checkpoint"] = store.append(receipt)
        refresh_collection(collection)

        return {
            "plan_id": plan_id,
            "status": "captured",
            "receipt_count": collection[
                "store_checkpoint"
            ]["count"],
            "record_id": record_id,
            "live_capital_authorized": False,
        }
