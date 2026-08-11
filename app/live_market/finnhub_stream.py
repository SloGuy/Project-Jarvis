import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any

from websockets.asyncio.client import connect
from app.live_market.live_state import (
    update_provider_status,
    update_quote,
    utc_now,
)


FINNHUB_WS_URL = "wss://ws.finnhub.io"

DEFAULT_SYMBOLS = (
    "SPY",
    "QQQ",
    "DIA",
    "TSLA",
    "AAPL",
)


def _handle_trade_message(
    payload: dict[str, Any],
) -> None:
    if payload.get("type") != "trade":
        return

    trades = payload.get("data")

    if not isinstance(trades, list):
        return

    for trade in trades:
        if not isinstance(trade, dict):
            continue

        symbol = str(
            trade.get("s", "")
        ).strip().upper()

        price = trade.get("p")
        timestamp_ms = trade.get("t")
        volume = trade.get("v")

        if not symbol or price is None:
            continue

        observed_at = None

        if isinstance(timestamp_ms, (int, float)):
            observed_at = datetime.fromtimestamp(
                timestamp_ms / 1000,
                tz=timezone.utc,
            ).isoformat()

        update_quote(
            symbol,
            {
                "asset_type": "stock",
                "price_usd": float(price),
                "volume": (
                    float(volume)
                    if volume is not None
                    else None
                ),
                "provider": "Finnhub WebSocket",
                "provider_timestamp_ms": timestamp_ms,
                "observed_at": observed_at or utc_now(),
                "received_at": utc_now(),
            },
        )


async def _subscribe(
    websocket: Any,
) -> None:
    for symbol in DEFAULT_SYMBOLS:
        await websocket.send(
            json.dumps(
                {
                    "type": "subscribe",
                    "symbol": symbol,
                }
            )
        )


async def run_finnhub_stream() -> None:
    api_key = os.getenv(
        "FINNHUB_API_KEY",
        "",
    ).strip()

    if not api_key:
        raise RuntimeError(
            "FINNHUB_API_KEY is not configured."
        )

    uri = f"{FINNHUB_WS_URL}?token={api_key}"

    async for websocket in connect(
        uri,
        open_timeout=10,
        ping_interval=20,
        ping_timeout=20,
        close_timeout=10,
    ):
        try:
            connected_at = utc_now()

            update_provider_status(
                "finnhub",
                connected=True,
                connected_at=connected_at,
                last_error=None,
                symbols=list(DEFAULT_SYMBOLS),
            )

            print(
                f"Connected to Finnhub WebSocket at "
                f"{connected_at}",
                flush=True,
            )

            await _subscribe(websocket)

            print(
                "Subscribed to: "
                + ", ".join(DEFAULT_SYMBOLS),
                flush=True,
            )

            async for message in websocket:
                update_provider_status(
                    "finnhub",
                    last_message_at=utc_now(),
                )

                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    continue

                if isinstance(payload, dict):
                    _handle_trade_message(payload)

                    if payload.get("type") == "trade":
                        for trade in payload.get("data", []):
                            print(
                                trade.get("s"),
                                trade.get("p"),
                                trade.get("t"),
                                flush=True,
                            )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            update_provider_status(
                "finnhub",
                last_error=str(exc),
            )

        finally:
            update_provider_status(
                "finnhub",
                connected=False,
            )


async def main() -> None:
    await run_finnhub_stream()


if __name__ == "__main__":
    asyncio.run(main())
