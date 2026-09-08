"""Historical inputs selected from verified visibility receipts."""
import json
from pathlib import Path

from app.capital.observation_witness import timestamp, verify_receipt
from app.capital.mean_reversion_math import (
    LOOKBACK_OBSERVATIONS,
    calculate_mean_reversion_snapshot,
)


class WitnessedHistory:
    def __init__(self, receipts):
        self.rows = {}
        self.receipt_hashes = set()
        for receipt in receipts:
            payload = verify_receipt(receipt)
            witnessed = timestamp(payload["witnessed_at"])
            receipt_hash = receipt["sha256"]
            self.receipt_hashes.add(receipt_hash)
            for supplied in payload["observations"]:
                row = dict(supplied)
                ident = row["id"]
                previous = self.rows.get(ident)
                if previous is not None:
                    if previous["observation"] != row:
                        raise ValueError(
                            f"Conflicting witnessed values for observation {ident}."
                        )
                    if witnessed >= previous["witnessed_at"]:
                        continue
                self.rows[ident] = {
                    "observation": row,
                    "witnessed_at": witnessed,
                    "receipt_sha256": receipt_hash,
                }
        if not self.receipt_hashes:
            raise ValueError("At least one receipt is required.")

    @classmethod
    def from_directory(cls, directory):
        paths = sorted(Path(directory).glob("*.json"))
        # Corrupt receipts raise; they are never silently skipped.
        return cls([json.loads(path.read_text()) for path in paths])

    def window(self, *, asset_id, provider, symbol, decision_at):
        if type(asset_id) is not int or asset_id <= 0:
            raise ValueError("Invalid asset ID.")
        if provider not in {"Finnhub", "CoinGecko"}:
            raise ValueError("Unsupported provider.")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("Symbol is required.")
        decision = timestamp(decision_at)
        eligible = []
        for item in self.rows.values():
            row = item["observation"]
            if row["asset_id"] != asset_id or row["provider"] != provider:
                continue
            if (
                timestamp(row["observed_at"]) < decision
                and item["witnessed_at"] < decision
            ):
                eligible.append(item)
        eligible.sort(
            key=lambda item: (
                timestamp(item["observation"]["observed_at"]),
                item["observation"]["id"],
            ),
            reverse=True,
        )
        selected = eligible[:LOOKBACK_OBSERVATIONS]
        observations = [
            {
                "price_usd": float(item["observation"]["price_usd"]),
                "observed_at": item["observation"]["observed_at"],
            }
            for item in selected
        ]
        return {
            "snapshot": calculate_mean_reversion_snapshot(
                symbol=symbol, observations=observations
            ),
            "asset_id": asset_id,
            "provider": provider,
            "decision_at": decision.isoformat(),
            "observation_ids": [
                item["observation"]["id"] for item in selected
            ],
            "visibility_evidence": [
                {
                    "observation": dict(item["observation"]),
                    "witnessed_at": item["witnessed_at"].isoformat(),
                    "receipt_sha256": item["receipt_sha256"],
                }
                for item in selected
            ],
            "cutoff_rule": (
                "observed_at and witnessed_at strictly before decision_at"
            ),
            "selected_values_witnessed": bool(selected),
            # Full report verification and plan binding are separate.
            "availability_verified": False,
        }
