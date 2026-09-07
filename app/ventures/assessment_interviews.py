from __future__ import annotations

import hashlib
import json
from math import isfinite

from app.ventures.opportunities import get_opportunity


INTERVIEW_INPUT_VERSION = "timestamped_excerpts_v1"


def build_interview_input(record: dict) -> dict:
    segments = record["segments"]
    if not isinstance(segments, list) or not segments:
        raise ValueError("Interview segments are required.")

    for segment in segments:
        if (
            not isinstance(segment, dict)
            or not isinstance(segment.get("text"), str)
            or not isinstance(segment.get("start"), (int, float))
            or isinstance(segment["start"], bool)
            or not isfinite(segment["start"])
            or segment["start"] < 0
        ):
            raise ValueError("Invalid interview segment.")

    # Six evenly spaced starting points. Include adjacent captions
    # within each excerpt to retain more conversational context.
    count = min(6, len(segments))
    starts = [
        index * (len(segments) - 1) // max(1, count - 1)
        for index in range(count)
    ]
    claims = []

    for position, start in enumerate(starts):
        stop = (
            starts[position + 1]
            if position + 1 < len(starts) else len(segments)
        )
        parts = []
        size = 0

        for segment in segments[start:stop]:
            text = segment["text"].strip()
            if not text:
                continue
            remaining = 500 - size
            if remaining <= 0:
                break
            parts.append(text[:remaining])
            size += len(parts[-1]) + 1

        if not parts:
            continue

        seconds = segments[start]["start"]
        source = record["source_url"] + f"&t={int(seconds)}s"
        text = " ".join(parts)
        identity = json.dumps(
            [source, text, INTERVIEW_INPUT_VERSION],
            ensure_ascii=False,
        )
        claim_id = "interview_claim_" + hashlib.sha256(
            identity.encode()
        ).hexdigest()

        claims.append({
            "claim_id": claim_id,
            "category": "seller_interview_excerpt",
            "claim": text,
            "source": source,
            "evidence_status": "unverified",
            "evidence_quality": "low",
            "evidence_notes": (
                "Sampled seller-interview captions, not independent "
                "verification. Text may be cut short and contain "
                "transcription errors. Speaker identity is not established."
            ),
        })

    return {
        "input_version": INTERVIEW_INPUT_VERSION,
        "source_url": record["source_url"],
        "collected_at": record["collected_at"],
        "language": record.get("language"),
        "auto_generated": record.get("auto_generated"),
        "transcript_sha256": hashlib.sha256(
            json.dumps(
                segments, sort_keys=True, ensure_ascii=False,
                allow_nan=False,
            ).encode()
        ).hexdigest(),
        "selection": "up_to_six_evenly_spaced_500_character_excerpts",
        "claims": claims,
    }


def current_interview_inputs(opportunity_id: str) -> list[dict]:
    # Lazy import avoids the collector/worker/runner import cycle.
    from app.ventures.interview_collector import list_interviews

    opportunity = get_opportunity(opportunity_id)
    if opportunity is None:
        return []

    records = [
        record
        for record in list_interviews(opportunity_id)
        if (
            record.get("status") == "collected"
            and opportunity.source
            == f"empire_flippers:{record.get('source_listing_id')}"
        )
    ]
    if not records:
        return []

    # Use the latest successfully collected interview only.
    return [build_interview_input(records[-1])]
