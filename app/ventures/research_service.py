from __future__ import annotations

import uuid

from app.ventures.models import VenturesOpportunity
from app.ventures.research_models import (
    DiligenceQuestion,
    EvidenceQuality,
    EvidenceStatus,
    ResearchClaim,
    ResearchRecommendation,
    ResearchRisk,
    VenturesResearchReport,
)


def _claim(
    *,
    category: str,
    text: str,
    source: str,
) -> ResearchClaim:
    return ResearchClaim(
        claim_id=f"claim_{uuid.uuid4().hex[:12]}",
        category=category,
        claim=text,
        source=source,
        evidence_status=EvidenceStatus.UNVERIFIED,
        evidence_quality=EvidenceQuality.LOW,
        evidence_notes=None,
    )


def build_initial_research_report(
    opportunity: VenturesOpportunity,
) -> VenturesResearchReport:
    source = opportunity.source or "opportunity_record"

    claims: list[ResearchClaim] = []
    risks: list[ResearchRisk] = []
    questions: list[DiligenceQuestion] = []

    claims.append(
        _claim(
            category="asking_price",
            text=(
                f"Asking price is "
                f"${opportunity.asking_price_usd:,.2f}."
            ),
            source=source,
        )
    )

    if opportunity.annual_revenue_usd is not None:
        claims.append(
            _claim(
                category="revenue",
                text=(
                    f"Annual revenue is "
                    f"${opportunity.annual_revenue_usd:,.2f}."
                ),
                source=source,
            )
        )
    else:
        questions.append(
            DiligenceQuestion(
                category="revenue",
                question=(
                    "What is verified trailing-twelve-month revenue?"
                ),
                priority="high",
            )
        )

    if opportunity.annual_sde_usd is not None:
        claims.append(
            _claim(
                category="profitability",
                text=(
                    f"Annual SDE is "
                    f"${opportunity.annual_sde_usd:,.2f}."
                ),
                source=source,
            )
        )
    else:
        questions.append(
            DiligenceQuestion(
                category="profitability",
                question=(
                    "What is verified trailing-twelve-month SDE?"
                ),
                priority="high",
            )
        )

    if opportunity.owner_hours_per_week is not None:
        claims.append(
            _claim(
                category="owner_workload",
                text=(
                    "Owner workload is reported as "
                    f"{opportunity.owner_hours_per_week:.1f} "
                    "hours per week."
                ),
                source=source,
            )
        )
    else:
        questions.append(
            DiligenceQuestion(
                category="owner_workload",
                question=(
                    "What tasks does the owner perform each week, "
                    "and how many hours does each task require?"
                ),
                priority="high",
            )
        )

    questions.extend(
        [
            DiligenceQuestion(
                category="revenue_quality",
                question=(
                    "What percentage of revenue is recurring, "
                    "repeat, and one-time?"
                ),
                priority="high",
            ),
            DiligenceQuestion(
                category="customer_concentration",
                question=(
                    "What percentage of revenue comes from the "
                    "largest 1, 3, and 10 customers?"
                ),
                priority="high",
            ),
            DiligenceQuestion(
                category="customer_retention",
                question=(
                    "What are customer churn and revenue churn "
                    "over the last 12 months?"
                ),
                priority="high",
            ),
            DiligenceQuestion(
                category="operations",
                question=(
                    "Which recurring operating tasks currently "
                    "require human intervention?"
                ),
                priority="high",
            ),
            DiligenceQuestion(
                category="technology",
                question=(
                    "What software, infrastructure, APIs, vendors, "
                    "and third-party platforms does the business depend on?"
                ),
                priority="medium",
            ),
            DiligenceQuestion(
                category="growth",
                question=(
                    "What historically drove customer acquisition "
                    "and what are current acquisition costs?"
                ),
                priority="medium",
            ),
        ]
    )

    if (
        opportunity.owner_hours_per_week is not None
        and opportunity.owner_hours_per_week > 40
    ):
        risks.append(
            ResearchRisk(
                category="owner_dependency",
                description=(
                    "Reported owner workload exceeds 40 hours per week."
                ),
                severity="high",
            )
        )

    if (
        opportunity.annual_sde_usd is not None
        and opportunity.annual_sde_usd > 0
    ):
        multiple = (
            opportunity.asking_price_usd
            / opportunity.annual_sde_usd
        )

        if multiple > 8:
            risks.append(
                ResearchRisk(
                    category="valuation",
                    description=(
                        f"Asking price is approximately "
                        f"{multiple:.1f}x reported annual SDE."
                    ),
                    severity="high",
                )
            )

    evidence_score = 0.0

    recommendation = (
        ResearchRecommendation.MORE_RESEARCH_REQUIRED
    )

    summary = (
        "Initial research case created from opportunity data. "
        "Seller/listing claims remain unverified and require "
        "independent evidence before underwriting."
    )

    return VenturesResearchReport(
        opportunity_id=opportunity.opportunity_id,
        claims=tuple(claims),
        risks=tuple(risks),
        diligence_questions=tuple(questions),
        evidence_score=evidence_score,
        recommendation=recommendation,
        summary=summary,
    )
