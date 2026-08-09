import json
import os
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.market_db.portfolio_insights import (
    get_portfolio_insight,
)
from app.market_db.portfolio_queries import (
    get_portfolio_summary,
)


OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://127.0.0.1:11434",
).rstrip("/")

OLLAMA_MODEL = os.getenv(
    "JARVIS_PORTFOLIO_MODEL",
    "qwen3:8b",
)

OLLAMA_TIMEOUT_SECONDS = int(
    os.getenv(
        "JARVIS_PORTFOLIO_TIMEOUT_SECONDS",
        "120",
    )
)


SYSTEM_PROMPT = """
You are Jarvis Portfolio Intelligence.

Explain a paper-trading portfolio using only the supplied portfolio
facts and deterministic insights.

Rules:
1. Never invent prices, positions, transactions, performance, news,
   market conditions, or user goals.
2. Never claim certainty about future market performance.
3. Do not issue direct buy, sell, or hold instructions.
4. Clearly distinguish portfolio facts from interpretation.
5. Use plain language understandable to a nonprofessional investor.
6. Mention data-confidence limitations when relevant.
7. Keep each section concise.
8. Return valid JSON only.

Return exactly this JSON structure:

{
  "overview": "Two or three sentence portfolio overview.",
  "allocation": "Explanation of cash, invested exposure, and concentration.",
  "performance": "Explanation of realized and unrealized performance.",
  "risk": "Explanation of the current risk classification and major risks.",
  "watch_items": [
    "First item worth monitoring.",
    "Second item worth monitoring."
  ]
}
""".strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None

        return float(value)
    except (TypeError, ValueError):
        return None


def _format_money(value: Any) -> str:
    numeric_value = _safe_float(value)

    if numeric_value is None:
        return "unavailable"

    return f"${numeric_value:,.2f}"


def _format_percent(value: Any) -> str:
    numeric_value = _safe_float(value)

    if numeric_value is None:
        return "unknown"

    return f"{numeric_value:.2f}%"


def _build_supporting_facts(
    portfolio_data: dict[str, Any],
    insight_data: dict[str, Any],
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []

    facts.append(
        {
            "type": "total_value",
            "title": "Total portfolio value",
            "value": portfolio_data.get("total_value_usd"),
            "message": (
                "The portfolio is currently valued at "
                f"{_format_money(
                    portfolio_data.get('total_value_usd')
                )}."
            ),
        }
    )

    facts.append(
        {
            "type": "cash_allocation",
            "title": "Cash allocation",
            "value": portfolio_data.get(
                "cash_allocation_percent"
            ),
            "message": (
                f"{_format_percent(
                    portfolio_data.get(
                        'cash_allocation_percent'
                    )
                )} of total portfolio value is held in cash."
            ),
        }
    )

    facts.append(
        {
            "type": "invested_allocation",
            "title": "Invested allocation",
            "value": portfolio_data.get(
                "invested_allocation_percent"
            ),
            "message": (
                f"{_format_percent(
                    portfolio_data.get(
                        'invested_allocation_percent'
                    )
                )} of total portfolio value is invested."
            ),
        }
    )

    facts.append(
        {
            "type": "total_gain_loss",
            "title": "Total gain or loss",
            "value": portfolio_data.get(
                "total_gain_loss_usd"
            ),
            "message": (
                "Combined realized and unrealized performance is "
                f"{_format_money(
                    portfolio_data.get(
                        'total_gain_loss_usd'
                    )
                )}."
            ),
        }
    )

    facts.append(
        {
            "type": "risk_level",
            "title": "Risk classification",
            "value": insight_data.get("risk_level"),
            "message": (
                "Deterministic portfolio risk is classified as "
                f"{insight_data.get('risk_level', 'unknown')}."
            ),
        }
    )

    positions = portfolio_data.get("positions", [])

    if isinstance(positions, list) and positions:
        valid_positions = [
            position
            for position in positions
            if isinstance(position, dict)
        ]

        if valid_positions:
            largest_position = max(
                valid_positions,
                key=lambda position: (
                    position.get("allocation_percent") or 0
                ),
            )

            symbol = largest_position.get(
                "symbol",
                "Unknown",
            )

            facts.append(
                {
                    "type": "largest_position",
                    "title": "Largest position",
                    "symbol": symbol,
                    "value": largest_position.get(
                        "allocation_percent"
                    ),
                    "message": (
                        f"{symbol} is the largest position at "
                        f"{_format_percent(
                            largest_position.get(
                                'allocation_percent'
                            )
                        )} of total portfolio value."
                    ),
                }
            )

    return facts


def _build_llm_context(
    portfolio_data: dict[str, Any],
    insight_data: dict[str, Any],
) -> dict[str, Any]:
    positions = portfolio_data.get("positions", [])

    clean_positions = []

    if isinstance(positions, list):
        for position in positions:
            if not isinstance(position, dict):
                continue

            clean_positions.append(
                {
                    "symbol": position.get("symbol"),
                    "name": position.get("name"),
                    "asset_type": position.get("asset_type"),
                    "quantity": position.get("quantity"),
                    "average_cost_usd": position.get(
                        "average_cost_usd"
                    ),
                    "latest_price_usd": position.get(
                        "latest_price_usd"
                    ),
                    "market_value_usd": position.get(
                        "market_value_usd"
                    ),
                    "allocation_percent": position.get(
                        "allocation_percent"
                    ),
                    "unrealized_gain_loss_usd": position.get(
                        "unrealized_gain_loss_usd"
                    ),
                    "unrealized_gain_loss_percent": (
                        position.get(
                            "unrealized_gain_loss_percent"
                        )
                    ),
                    "price_observed_at": position.get(
                        "price_observed_at"
                    ),
                }
            )

    return {
        "portfolio": {
            "name": (
                portfolio_data.get("portfolio", {}).get("name")
                if isinstance(
                    portfolio_data.get("portfolio"),
                    dict,
                )
                else None
            ),
            "cash_balance_usd": portfolio_data.get(
                "cash_balance_usd"
            ),
            "market_value_usd": portfolio_data.get(
                "market_value_usd"
            ),
            "total_value_usd": portfolio_data.get(
                "total_value_usd"
            ),
            "realized_gain_loss_usd": portfolio_data.get(
                "realized_gain_loss_usd"
            ),
            "unrealized_gain_loss_usd": portfolio_data.get(
                "unrealized_gain_loss_usd"
            ),
            "total_gain_loss_usd": portfolio_data.get(
                "total_gain_loss_usd"
            ),
            "cash_allocation_percent": portfolio_data.get(
                "cash_allocation_percent"
            ),
            "invested_allocation_percent": portfolio_data.get(
                "invested_allocation_percent"
            ),
            "position_count": portfolio_data.get(
                "position_count"
            ),
            "positions": clean_positions,
        },
        "deterministic_insight": {
            "executive_summary": insight_data.get(
                "executive_summary"
            ),
            "risk_level": insight_data.get("risk_level"),
            "confidence_percent": insight_data.get(
                "confidence_percent"
            ),
            "confidence_reasons": insight_data.get(
                "confidence_reasons",
                [],
            ),
            "key_observations": insight_data.get(
                "key_observations",
                [],
            ),
        },
    }


def _extract_message_content(
    response_data: dict[str, Any],
) -> str:
    message = response_data.get("message")

    if not isinstance(message, dict):
        raise ValueError(
            "Ollama response did not contain a message object."
        )

    content = message.get("content")

    if not isinstance(content, str) or not content.strip():
        raise ValueError(
            "Ollama response did not contain message content."
        )

    cleaned = content.strip()

    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]

    if cleaned.startswith("```"):
        cleaned = cleaned[3:]

    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]

    return cleaned.strip()


def _validate_explanation(
    explanation: Any,
) -> dict[str, Any]:
    if not isinstance(explanation, dict):
        raise ValueError(
            "Portfolio explanation was not a JSON object."
        )

    required_text_fields = (
        "overview",
        "allocation",
        "performance",
        "risk",
    )

    for field in required_text_fields:
        value = explanation.get(field)

        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"Portfolio explanation is missing {field}."
            )

    watch_items = explanation.get("watch_items")

    if not isinstance(watch_items, list):
        raise ValueError(
            "Portfolio explanation watch_items must be a list."
        )

    clean_watch_items = [
        str(item).strip()
        for item in watch_items
        if str(item).strip()
    ][:5]

    return {
        "overview": explanation["overview"].strip(),
        "allocation": explanation["allocation"].strip(),
        "performance": explanation["performance"].strip(),
        "risk": explanation["risk"].strip(),
        "watch_items": clean_watch_items,
    }


def _generate_llm_explanation(
    context: dict[str, Any],
) -> dict[str, Any]:
    request_body = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": "json",
        "think": False,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    "Explain this portfolio using only the "
                    "following JSON context:\n\n"
                    + json.dumps(
                        context,
                        indent=2,
                        default=str,
                    )
                ),
            },
        ],
        "options": {
            "temperature": 0.2,
            "num_predict": 700,
        },
    }

    request = Request(
        url=f"{OLLAMA_BASE_URL}/api/chat",
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(
            request,
            timeout=OLLAMA_TIMEOUT_SECONDS,
        ) as response:
            response_data = json.loads(
                response.read().decode("utf-8")
            )
    except HTTPError as exc:
        error_body = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            f"Ollama returned HTTP {exc.code}: {error_body}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"Could not connect to Ollama: {exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise RuntimeError(
            "Ollama portfolio explanation timed out."
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Ollama returned invalid response JSON."
        ) from exc

    message_content = _extract_message_content(
        response_data
    )

    try:
        explanation = json.loads(message_content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Ollama did not return a valid JSON explanation."
        ) from exc

    return _validate_explanation(explanation)


def _build_deterministic_explanation(
    portfolio_data: dict[str, Any],
    insight_data: dict[str, Any],
) -> dict[str, Any]:
    cash_allocation = _format_percent(
        portfolio_data.get("cash_allocation_percent")
    )
    invested_allocation = _format_percent(
        portfolio_data.get(
            "invested_allocation_percent"
        )
    )

    position_count = int(
        portfolio_data.get("position_count") or 0
    )

    return {
        "overview": insight_data.get(
            "executive_summary",
            "Portfolio analysis is available from deterministic "
            "accounting data.",
        ),
        "allocation": (
            f"The portfolio currently holds {cash_allocation} in "
            f"cash and has {invested_allocation} invested across "
            f"{position_count} open positions."
        ),
        "performance": (
            "Unrealized performance is "
            f"{_format_money(
                portfolio_data.get(
                    'unrealized_gain_loss_usd'
                )
            )}, realized performance is "
            f"{_format_money(
                portfolio_data.get(
                    'realized_gain_loss_usd'
                )
            )}, and combined performance is "
            f"{_format_money(
                portfolio_data.get(
                    'total_gain_loss_usd'
                )
            )}."
        ),
        "risk": (
            "The deterministic risk classification is "
            f"{insight_data.get('risk_level', 'unknown')}. "
            "This classification is based on invested exposure "
            "and does not predict future losses."
        ),
        "watch_items": [
            observation.get("detail")
            for observation in insight_data.get(
                "key_observations",
                [],
            )
            if (
                isinstance(observation, dict)
                and observation.get("detail")
            )
        ][:5],
    }


def _suggested_follow_ups() -> list[dict[str, str]]:
    return [
        {
            "label": "Analyze portfolio risk",
            "action": "analyze_portfolio_risk",
        },
        {
            "label": "Explain my largest position",
            "action": "explain_largest_position",
        },
        {
            "label": "What should I watch today?",
            "action": "portfolio_watch_items",
        },
    ]


def explain_portfolio(
    portfolio_id: int | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    portfolio_data = get_portfolio_summary(
        portfolio_id=portfolio_id,
        transaction_limit=10,
    )

    if portfolio_data.get("status") != "success":
        return {
            "status": portfolio_data.get(
                "status",
                "unavailable",
            ),
            "generated_at": _utc_now(),
            "portfolio_id": portfolio_id,
            "summary": portfolio_data.get(
                "summary",
                "Portfolio explanation is unavailable.",
            ),
            "supporting_facts": [],
            "explanation": None,
            "suggested_follow_ups": [],
            "model": {
                "name": OLLAMA_MODEL,
                "used_llm": False,
            },
        }

    insight_data = get_portfolio_insight(
        portfolio_id=portfolio_id,
    )

    supporting_facts = _build_supporting_facts(
        portfolio_data=portfolio_data,
        insight_data=insight_data,
    )

    context = _build_llm_context(
        portfolio_data=portfolio_data,
        insight_data=insight_data,
    )

    used_llm = False
    generation_error = None

    if use_llm:
        try:
            explanation = _generate_llm_explanation(
                context=context,
            )
            used_llm = True
        except Exception as exc:
            generation_error = str(exc)

            explanation = _build_deterministic_explanation(
                portfolio_data=portfolio_data,
                insight_data=insight_data,
            )
    else:
        explanation = _build_deterministic_explanation(
            portfolio_data=portfolio_data,
            insight_data=insight_data,
        )

    response = {
        "status": "success",
        "generated_at": _utc_now(),
        "portfolio_id": portfolio_data["portfolio"]["id"],
        "portfolio": portfolio_data,
        "insight": insight_data,
        "supporting_facts": supporting_facts,
        "explanation": explanation,
        "suggested_follow_ups": _suggested_follow_ups(),
        "model": {
            "name": OLLAMA_MODEL,
            "used_llm": used_llm,
        },
    }

    if generation_error is not None:
        response["generation_warning"] = (
            "Jarvis used the deterministic fallback because the "
            f"LLM explanation was unavailable: {generation_error}"
        )

    return response
