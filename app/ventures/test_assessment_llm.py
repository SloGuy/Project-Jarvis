import json
from copy import deepcopy
from io import BytesIO
from unittest.mock import patch
from urllib.error import URLError

from app.ventures import assessment_llm as llm
from app.ventures.assessment_models import validate_assessment


def payload():
    return {
        "business_type": "saas",
        "thesis_fit": "uncertain",
        "classification_rationale": "The source describes subscription software.",
        "classification_claim_ids": ["claim_test"],
        "findings": [{
            "category": "evidence_gap",
            "text": "Subscription revenue requires independent verification.",
            "claim_ids": ["claim_test"],
        }],
    }


def report():
    return {
        "opportunity_id": "assessment_test",
        "created_at": "2026-01-01T00:00:00+00:00",
        "report": {
            "opportunity_id": "assessment_test",
            "claims": [{
                "claim_id": "claim_test",
                "category": "source_summary",
                "claim": "Seller describes subscription software.",
                "source": "https://example.invalid/listing",
                "evidence_status": "unverified",
                "evidence_quality": "low",
                "evidence_notes": "Synthetic fixture.",
            }],
            "risks": [],
            "diligence_questions": [],
        },
    }


def expect_error(action, error_type=ValueError):
    try:
        action()
    except error_type:
        return
    raise AssertionError(f"Expected {error_type.__name__}")


def response_bytes(content=None, **changes):
    response = {
        "done": True,
        "done_reason": "stop",
        "message": {
            "content": json.dumps(payload()) if content is None else content,
        },
    }
    response.update(changes)
    return BytesIO(json.dumps(response).encode())


def main():
    result = validate_assessment(
        payload(), allowed_claim_ids={"claim_test"}
    )
    assert result["advisory_only"] is True
    assert result["independent_verification_performed"] is False
    assert result["automatic_purchase_authority"] is False
    assert result["capital_transfer_authority"] is False

    for field, value in (
        ("business_type", "invented_type"),
        ("thesis_fit", "approved"),
        ("classification_claim_ids", ["unknown_claim"]),
        ("classification_rationale", " "),
        ("findings", []),
    ):
        broken = payload()
        broken[field] = value
        expect_error(lambda: validate_assessment(
            broken, allowed_claim_ids={"claim_test"}
        ))

    for change in (
        {"claim_ids": ["unknown_claim"]},
        {"claim_ids": ["claim_test", "claim_test"]},
        {"category": "execute_purchase"},
        {"text": " "},
        {"text": "x" * 1201},
        {"verified": True},
    ):
        broken = payload()
        broken["findings"][0].update(change)
        expect_error(lambda: validate_assessment(
            broken, allowed_claim_ids={"claim_test"}
        ))

    broken = dict(payload(), acquisition_authority=True)
    expect_error(lambda: validate_assessment(
        broken, allowed_claim_ids={"claim_test"}
    ))
    print("schema_citations_and_authority_validation: PASS")

    original = report()
    before = deepcopy(original)
    with (
        patch.object(llm, "ASSESSMENT_MODEL", "qwen3:8b"),
        patch.object(
            llm, "urlopen", return_value=response_bytes()
        ) as network,
    ):
        result = llm.assess_research_report(original)
        request = network.call_args.args[0]
        body = json.loads(request.data)
        assert body["model"] == "qwen3:8b"
        assert body["stream"] is False
        assert body["think"] is False
        assert body["format"] == "json"
        assert "tools" not in body
        assert result["research_created_at"] == original["created_at"]
        assert original == before
    print("bounded_request_and_input_preservation: PASS")

    with patch.object(llm, "urlopen") as network:
        oversized = report()
        oversized["report"]["claims"][0]["claim"] = "x" * 25_000
        expect_error(lambda: llm.assess_research_report(oversized))
        network.assert_not_called()
    print("oversized_context_rejected_before_request: PASS")

    for response in (
        response_bytes(done=False),
        response_bytes(done_reason="length"),
        response_bytes(content="not JSON"),
        response_bytes(content=""),
        response_bytes(error="Synthetic model error"),
    ):
        with patch.object(llm, "urlopen", return_value=response):
            expect_error(lambda: llm.assess_research_report(report()))

    with (
        patch.object(llm, "MAX_RESPONSE_BYTES", 10),
        patch.object(llm, "urlopen", return_value=BytesIO(b"x" * 11)),
    ):
        expect_error(lambda: llm.assess_research_report(report()))
    print("malformed_and_truncated_responses_rejected: PASS")

    for error in (
        URLError("Synthetic connection failure"),
        TimeoutError("Synthetic timeout"),
    ):
        with patch.object(llm, "urlopen", side_effect=error):
            expect_error(
                lambda: llm.assess_research_report(report()),
                type(error),
            )
    print("network_failures_propagate: PASS")


if __name__ == "__main__":
    main()
