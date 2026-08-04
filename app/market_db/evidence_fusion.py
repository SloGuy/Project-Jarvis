from __future__ import annotations

import re

from typing import Iterable

EVENT_STOP_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "but",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "what",
    "when",
    "why",
    "with",
}


def _headline_tokens(title: str | None) -> set[str]:
    normalized = re.sub(
        r"[^a-z0-9\s]",
        " ",
        str(title or "").lower(),
    )

    return {
        token
        for token in normalized.split()
        if len(token) >= 3
        and token not in EVENT_STOP_WORDS
    }


def _headline_similarity(
    first_title: str | None,
    second_title: str | None,
) -> float:
    first_tokens = _headline_tokens(first_title)
    second_tokens = _headline_tokens(second_title)

    if not first_tokens or not second_tokens:
        return 0.0

    intersection = first_tokens & second_tokens
    union = first_tokens | second_tokens

    return len(intersection) / len(union)


def rank_evidence(
    evidence: Iterable[dict],
) -> list[dict]:
    ranked = []

    for item in evidence:
        score = float(
            item.get("evidence_score", 0.0)
        )

        enriched = {
            **item,
            "overall_score": round(score, 4),
        }

        ranked.append(enriched)

    ranked.sort(
        key=lambda row: row["overall_score"],
        reverse=True,
    )

    return ranked


EVENT_TOPICS = {
    "price_move": (
        "stock rises",
        "stock surges",
        "stock jumps",
        "stock climbs",
        "stock falls",
        "stock drops",
        "price prediction",
        "rally",
        "selloff",
        "recovery",
    ),
    "earnings": (
        "earnings",
        "revenue",
        "guidance",
        "quarter",
        "profit",
        "loss",
    ),
    "robotaxi": (
        "robotaxi",
        "autonomous",
        "self driving",
    ),
    "regulation": (
        "probe",
        "investigation",
        "lawsuit",
        "settlement",
        "regulator",
        "safety",
    ),
    "ai_robotics": (
        "artificial intelligence",
        " ai ",
        "robot",
        "robotics",
        "optimus",
    ),
    "analyst": (
        "analyst",
        "price target",
        "upgrade",
        "downgrade",
        "bull",
        "bear",
    ),
}


def _detect_event_topic(item: dict) -> str:
    text = " ".join(
        (
            str(item.get("title") or ""),
            str(item.get("summary") or ""),
        )
    ).lower()

    for topic, phrases in EVENT_TOPICS.items():
        if any(phrase in text for phrase in phrases):
            return topic

    return "other"


def cluster_evidence_events(
    evidence: Iterable[dict],
    similarity_threshold: float = 0.20,
) -> list[dict]:
    ranked = rank_evidence(evidence)
    events = []

    for item in ranked:
        item_topic = _detect_event_topic(item)
        matched_event = None

        for event in events:
            if event["topic"] != item_topic:
                continue
            similarity = _headline_similarity(
                item.get("title"),
                event["representative_title"],
            )

            if similarity >= similarity_threshold:
                matched_event = event
                break

        if matched_event is None:
            events.append(
                {
                    "event_id": len(events) + 1,
                    "topic": item_topic,
                    "representative_title": item.get("title"),
                    "highest_score": item["overall_score"],
                    "article_count": 1,
                    "articles": [item],
                }
            )
            continue

        matched_event["articles"].append(item)
        matched_event["article_count"] += 1

        if item["overall_score"] > matched_event["highest_score"]:
            matched_event["highest_score"] = item[
                "overall_score"
            ]
            matched_event["representative_title"] = item.get(
                "title"
            )

    events.sort(
        key=lambda event: event["highest_score"],
        reverse=True,
    )

    return events


def fuse_evidence(
    evidence: Iterable[dict],
    primary_limit: int = 3,
    supporting_limit: int = 5,
) -> dict:
    ranked = rank_evidence(evidence)

    events = cluster_evidence_events(ranked)

    primary_events = [
        event
        for event in events
        if event["highest_score"] >= 0.85
    ][:primary_limit]

    supporting_events = [
        event
        for event in events
        if 0.70 <= event["highest_score"] < 0.85
    ][:supporting_limit]

    contextual_events = [
        event
        for event in events
        if event["highest_score"] < 0.70
    ]

    primary = [
        item
        for item in ranked
        if item["overall_score"] >= 0.85
    ][:primary_limit]

    supporting = [
        item
        for item in ranked
        if 0.70 <= item["overall_score"] < 0.85
    ][:supporting_limit]

    context = [
        item
        for item in ranked
        if item["overall_score"] < 0.70
    ]

    if primary_events:
        overall_confidence = "high"
    elif len(supporting_events) >= 3:
        overall_confidence = "medium"
    elif supporting_events:
        overall_confidence = "low"
    else:
        overall_confidence = "insufficient"

    strongest_score = (
        ranked[0]["overall_score"]
        if ranked
        else 0.0
    )

    return {
        "overall_confidence": overall_confidence,
        "strongest_evidence_score": strongest_score,
        "primary_evidence": primary,
        "supporting_evidence": supporting,
        "contextual_evidence": context,
        "primary_count": len(primary),
        "supporting_count": len(supporting),
        "context_count": len(context),
        "total_evidence_count": len(ranked),
        "events": events,
        "primary_events": primary_events,
        "supporting_events": supporting_events,
        "contextual_events": contextual_events,
        "event_count": len(events),
        "primary_event_count": len(primary_events),
        "supporting_event_count": len(supporting_events),
        "contextual_event_count": len(contextual_events),
        "duplicate_article_count": len(ranked) - len(events),
    }


def build_evidence_summary(
    symbol: str,
    move_percent: float,
    fusion: dict,
) -> str:
    direction = "rose" if move_percent >= 0 else "fell"
    absolute_move = abs(move_percent)

    primary_events = fusion["primary_events"]
    supporting_events = fusion["supporting_events"]
    contextual_events = fusion["contextual_events"]

    if primary_events:
        strongest = primary_events[0]

        return (
            f"{symbol} {direction} {absolute_move:.3f}%. "
            f"The strongest evidence event was "
            f"\"{strongest['representative_title']}\" "
            f"with an event score of "
            f"{strongest['highest_score']:.3f}. "
            f"{len(supporting_events)} supporting event"
            f"{'s' if len(supporting_events) != 1 else ''} "
            "also aligned with the move. "
            f"Jarvis grouped {fusion['total_evidence_count']} "
            f"articles into {fusion['event_count']} independent "
            f"events. Overall explanation confidence is "
            f"{fusion['overall_confidence']}."
        )

    if supporting_events:
        strongest = supporting_events[0]

        return (
            f"{symbol} {direction} {absolute_move:.3f}%. "
            "No single event reached the primary-evidence "
            "threshold. The strongest supporting event was "
            f"\"{strongest['representative_title']}\" "
            f"with an event score of "
            f"{strongest['highest_score']:.3f}. "
            f"{len(supporting_events)} supporting event"
            f"{'s' if len(supporting_events) != 1 else ''} "
            f"and {len(contextual_events)} contextual event"
            f"{'s' if len(contextual_events) != 1 else ''} "
            "were identified. "
            f"Jarvis grouped {fusion['total_evidence_count']} "
            f"articles into {fusion['event_count']} independent "
            f"events and collapsed "
            f"{fusion['duplicate_article_count']} duplicate "
            f"article"
            f"{'s' if fusion['duplicate_article_count'] != 1 else ''}. "
            f"Overall explanation confidence is "
            f"{fusion['overall_confidence']}."
        )

    return (
        f"{symbol} {direction} {absolute_move:.3f}%, "
        "but Jarvis found insufficient independent evidence "
        "to explain the move confidently."
    )
