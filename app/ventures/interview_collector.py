from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from requests import Session
from youtube_transcript_api import YouTubeTranscriptApi

from app.ventures.assessment_worker import _latest_scan
from app.ventures.discovery_screening_store import list_research_queue
from app.ventures.discovery_store import discovery_run_lock
from app.ventures.opportunities import get_opportunity


DIRECTORY = (
    Path(__file__).resolve().parents[2]
    / "state" / "ventures" / "interviews"
)


class BoundedSession(Session):
    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", 20)
        return super().request(method, url, **kwargs)


def video_id(url: str) -> str:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"www.youtube.com", "youtube.com"}
        or parsed.path != "/watch"
        or parsed.username is not None
        or parsed.port not in {None, 443}
    ):
        raise ValueError("Unsupported interview URL.")
    values = parse_qs(parsed.query).get("v", [])
    if len(values) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{11}", values[0]):
        raise ValueError("Invalid YouTube video ID.")
    return values[0]


def list_interviews(opportunity_id: str) -> list[dict]:
    if not DIRECTORY.exists():
        return []
    records = []
    for path in DIRECTORY.glob("interview_*.json"):
        with path.open(encoding="utf-8") as file:
            record = json.load(file)
        if record["opportunity_id"] == opportunity_id:
            records.append(record)
    return sorted(records, key=lambda item: item["collected_at"])


def _save(record: dict, path: Path):
    payload = json.dumps(record, indent=2, allow_nan=False)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=DIRECTORY, suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def collect_one_interview() -> dict:
    with discovery_run_lock():
        run = _latest_scan()
        listings = {
            item["id"]: item for item in run["snapshot"]["listings"]
        }
        linked = {
            item["opportunity_id"]: item["source_id"]
            for item in run["intake_results"]
            if item["outcome"] == "linked"
        }
        cached = 0
        unavailable = 0

        for entry in list_research_queue():
            if (
                entry["status"] != "collected"
                or entry["discovery_run_id"] != run["run_id"]
            ):
                continue
            opportunity = get_opportunity(entry["opportunity_id"])
            if opportunity is None or opportunity.status.value not in {
                "discovered", "screening", "research"
            }:
                continue

            source_id = linked.get(opportunity.opportunity_id)
            listing = listings.get(source_id)
            if (
                listing is None
                or opportunity.source != f"empire_flippers:{source_id}"
            ):
                continue
            url = listing.get("seller_interview_link")
            if not url:
                unavailable += 1
                continue

            try:
                identifier = video_id(url)
            except (ValueError, TypeError, AttributeError):
                unavailable += 1
                continue
            canonical = f"https://www.youtube.com/watch?v={identifier}"
            key = hashlib.sha256(
                f"{opportunity.opportunity_id}:{identifier}".encode()
            ).hexdigest()
            path = DIRECTORY / f"interview_{key}.json"

            if path.exists():
                with path.open(encoding="utf-8") as file:
                    previous = json.load(file)

                if previous["status"] == "collected":
                    cached += 1
                    continue

                attempted_at = datetime.fromisoformat(
                    previous["collected_at"]
                )
                if attempted_at.tzinfo is None:
                    raise ValueError(
                        "Interview attempt timestamp must include timezone."
                    )

                age = (
                    datetime.now(timezone.utc) - attempted_at
                ).total_seconds()
                if age < 6 * 60 * 60:
                    cached += 1
                    continue

            try:
                with BoundedSession() as session:
                    transcript = YouTubeTranscriptApi(
                        http_client=session
                    ).fetch(identifier)
                segments = transcript.to_raw_data()
                if not segments:
                    raise ValueError("Transcript is empty.")
                if sum(len(item["text"]) for item in segments) > 150_000:
                    raise ValueError("Transcript exceeds collection limit.")

                record = {
                    "opportunity_id": opportunity.opportunity_id,
                    "source_listing_id": source_id,
                    "discovery_run_id": run["run_id"],
                    "source_url": canonical,
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                    "status": "collected",
                    "language": transcript.language_code,
                    "auto_generated": transcript.is_generated,
                    "segments": segments,
                    "evidence_status": "unverified",
                    "limitations": [
                        "Seller interview, not independent verification.",
                        "Captions may contain transcription errors.",
                        "Cached transcript; subsequent caption edits "
                        "are not automatically detected.",
                    ],
                }
            except Exception as exc:
                record = {
                    "opportunity_id": opportunity.opportunity_id,
                    "source_listing_id": source_id,
                    "discovery_run_id": run["run_id"],
                    "source_url": canonical,
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                    "status": "unavailable",
                    "segments": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }

            _save(record, path)
            return {
                "status": record["status"],
                "opportunity_id": opportunity.opportunity_id,
                "segments": len(record["segments"]),
                "error": record.get("error"),
            }

        return {
            "status": "no_interview_due",
            "cached": cached,
                       "eligible_without_interview_link": unavailable,
        }


if __name__ == "__main__":
    print(json.dumps(collect_one_interview(), indent=2))
