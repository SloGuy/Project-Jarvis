from __future__ import annotations

from app.ventures.models import BusinessType


ASSESSMENT_VERSION = "ventures_assessment_v1"

FINDING_CATEGORIES = {
    "business_model",
    "operating_work",
    "automation_hypothesis",
    "platform_dependency",
    "owner_dependency",
    "evidence_gap",
}

FIT_VALUES = {"potential_fit", "poor_fit", "uncertain"}


def _text(value, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonempty text.")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters.")
    return value.strip()


def validate_assessment(
    payload: dict,
    *,
    allowed_claim_ids: set[str],
) -> dict:
    required = {
        "business_type",
        "thesis_fit",
        "classification_rationale",
        "classification_claim_ids",
        "findings",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("Assessment fields do not match the schema.")

    business_type = BusinessType(payload["business_type"]).value
    thesis_fit = payload["thesis_fit"]
    if not isinstance(thesis_fit, str) or thesis_fit not in FIT_VALUES:
        raise ValueError("Invalid thesis fit.")

    def references(values):
        if (
            not isinstance(values, list)
            or not 1 <= len(values) <= 8
            or any(not isinstance(value, str) for value in values)
        ):
            raise ValueError("Provide between 1 and 8 claim references.")
        if len(values) != len(set(values)):
            raise ValueError("Duplicate claim references.")
        if not set(values) <= allowed_claim_ids:
            raise ValueError("Assessment cites an unknown claim.")
        return list(values)

    classification_refs = references(payload["classification_claim_ids"])
    findings = payload["findings"]
    if not isinstance(findings, list) or not 1 <= len(findings) <= 8:
        raise ValueError("Provide between 1 and 8 findings.")

    validated = []
    for finding in findings:
        if (
            not isinstance(finding, dict)
            or set(finding) != {"category", "text", "claim_ids"}
        ):
            raise ValueError("Invalid finding fields.")
        category = finding["category"]
        if (
            not isinstance(category, str)
            or category not in FINDING_CATEGORIES
        ):
            raise ValueError("Invalid finding category.")

        validated.append({
            "category": category,
            "text": _text(finding["text"], "finding text", 1200),
            "claim_ids": references(finding["claim_ids"]),
            "basis": "model_inference_from_supplied_claims",
        })

    return {
        "assessment_version": ASSESSMENT_VERSION,
        "suggested_business_type": business_type,
        "thesis_fit": thesis_fit,
        "classification_rationale": _text(
            payload["classification_rationale"],
            "classification rationale",
            1200,
        ),
        "classification_claim_ids": classification_refs,
        "findings": validated,
        "advisory_only": True,
        "independent_verification_performed": False,
        "automatic_purchase_authority": False,
        "capital_transfer_authority": False,
    }
