from __future__ import annotations

from dataclasses import replace

from app.ventures.evidence import calculate_evidence_score
from app.ventures.research_gate import evaluate_research_gate
from app.ventures.research_models import (
    DiligenceQuestion,
    EvidenceQuality,
    EvidenceStatus,
    ResearchClaim,
    ResearchRecommendation,
    ResearchRisk,
    VenturesResearchReport,
)
from app.ventures.research_service import build_initial_research_report
from app.ventures.research_source_claims import extract_source_claims
from app.ventures.research_store import (
    get_latest_research_report,
    research_write_lock,
    save_research_report,
)


def _restore_report(data: dict) -> VenturesResearchReport:
    return VenturesResearchReport(
        opportunity_id=data["opportunity_id"],
        claims=tuple(
            ResearchClaim(
                **dict(
                    claim,
                    evidence_status=EvidenceStatus(
                        claim["evidence_status"]
                    ),
                    evidence_quality=EvidenceQuality(
                        claim["evidence_quality"]
                    ),
                )
            )
            for claim in data["claims"]
        ),
        risks=tuple(ResearchRisk(**risk) for risk in data["risks"]),
        diligence_questions=tuple(
            DiligenceQuestion(**question)
            for question in data["diligence_questions"]
        ),
        evidence_score=data["evidence_score"],
        recommendation=ResearchRecommendation(data["recommendation"]),
        summary=data["summary"],
    )


def merge_source_research(
    *,
    opportunity,
    listing: dict,
    source_url: str,
    fetched_at: str,
) -> dict:
    if opportunity.source != f"empire_flippers:{listing.get('id')}":
        raise ValueError("Source listing does not match opportunity.")
    if opportunity.source_url != source_url:
        raise ValueError("Source URL does not match opportunity.")

    incoming = extract_source_claims(
        listing=listing,
        source_url=source_url,
        fetched_at=fetched_at,
    )

    with research_write_lock():
        latest = get_latest_research_report(opportunity.opportunity_id)
        report = (
            _restore_report(latest["report"])
            if latest is not None
            else build_initial_research_report(opportunity)
        )

        claims = list(report.claims)
        known_ids = {claim.claim_id for claim in claims}
        risks = list(report.risks)
        risk_descriptions = {risk.description for risk in risks}
        added = 0

        for claim in incoming:
            if claim.claim_id in known_ids:
                continue

            previous_versions = [
                old for old in claims
                if old.category == claim.category
                and old.source == claim.source
                and old.claim_id.startswith("source_claim_")
            ]
            if previous_versions:
                description = (
                    f"Source disclosure changed for {claim.category}. "
                    "Earlier claims and their evidence are retained; "
                    "reconcile versions before relying on this field."
                )
                if description not in risk_descriptions:
                    risks.append(ResearchRisk(
                        category="source_disclosure_change",
                        description=description,
                        severity="high",
                    ))
                    risk_descriptions.add(description)

            claims.append(claim)
            known_ids.add(claim.claim_id)
            added += 1

        if latest is not None and added == 0:
            return {
                "changed": False,
                "added_claims": 0,
                "record": latest,
            }

        updated = replace(
            report,
            claims=tuple(claims),
            risks=tuple(risks),
            evidence_score=calculate_evidence_score(tuple(claims)),
        )
        updated = replace(
            updated,
            recommendation=evaluate_research_gate(updated),
        )

        return {
            "changed": True,
            "added_claims": added,
            "record": save_research_report(updated),
        }
