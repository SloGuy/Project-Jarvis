import asyncio
from typing import Any
from datetime import datetime, timezone

from app.live_market.live_state import (
    update_provider_status,
    update_quote,
    utc_now,
)
from app.market_status import _get_crypto_quotes


POLL_INTERVAL_SECONDS = 60


def _process_crypto_response(
    response: dict[str, Any],
) -> None:
    assets = response.get("assets", [])

    if not isinstance(assets, list):
        return

    for asset in assets:
        if not isinstance(asset, dict):
            continue

        symbol = str(
            asset.get("symbol", "")
        ).strip().upper()

        price = asset.get("price_usd")

        if not symbol or price is None:
            continue

        update_quote(
            symbol,
            {
                "asset_type": "crypto",
                "price_usd": float(price),
                "change_24h_percent": asset.get(
                    "change_24h_percent"
                ),
                "provider": "CoinGecko REST",
                "provider_id": asset.get("id"),
                "observed_at": (
                    datetime.fromtimestamp(
                        asset["last_updated_at"],
                        tz=timezone.utc,
                    ).isoformat()
                    if isinstance(
                        asset.get("last_updated_at"),
                        (int, float),
                    )
                    else utc_now()
                ),
                "received_at": utc_now(),
            },
        )


async def run_coingecko_poller() -> None:
    update_provider_status(
        "coingecko",
        connected=True,
        connected_at=utc_now(),
        last_error=None,
        poll_interval_seconds=(
            POLL_INTERVAL_SECONDS
        ),
    )

    while True:
        try:
            response = await asyncio.to_thread(
                _get_crypto_quotes
            )

            _process_crypto_response(response)

            update_provider_status(
                "coingecko",
                connected=True,
                last_message_at=utc_now(),
                last_error=response.get("error"),
            )

        except asyncio.CancelledError:
            update_provider_status(
                "coingecko",
                connected=False,
            )
            raise

        except Exception as exc:
            update_provider_status(
                "coingecko",
                connected=False,
                last_error=str(exc),
            )

        await asyncio.sleep(
            POLL_INTERVAL_SECONDS
        )
