from __future__ import annotations

import hashlib
import json
import re

from app.ventures.improvement_models import ImprovementProposal


PROPOSAL_VERSION = "task_workflows_v1"

WORKFLOWS = (
    {
        "key": "customer_support",
        "pattern": r"\bcustomer (?:service|support)\b",
        "title": "Draft customer-support replies with human escalation",
        "workflow": (
            "Connect an authorized support inbox and approved knowledge base.",
            "Classify incoming questions and retrieve relevant approved answers.",
            "Draft replies for human review; escalate refunds, complaints, "
            "account access, and low-confidence cases.",
            "Log accepted and corrected drafts to measure usefulness.",
        ),
        "requirements": (
            "Authorized inbox access and documented support policies.",
            "Representative historical tickets and approved product answers.",
            "Privacy controls and an escalation owner.",
        ),
        "validation": (
            "Measure current ticket volume and handling time.",
            "Evaluate drafts against historical tickets before live use.",
            "Measure factual accuracy, escalation accuracy, review time, "
            "and recurring tool costs in a supervised pilot.",
        ),
        "oversight": (
            "A human approves outbound replies during the pilot and retains "
            "authority over refunds, sensitive cases, and policy exceptions."
        ),
    },
    {
        "key": "marketing",
        "pattern": r"\b(?:marketing|seo)\b",
        "title": "Automate marketing reporting and prepare reviewed experiments",
        "workflow": (
            "Collect authorized traffic, conversion, and campaign metrics.",
            "Produce a recurring report identifying changes and evidence gaps.",
            "Draft a prioritized experiment brief tied to an observed issue.",
            "Track approved experiments and compare results with their baseline.",
        ),
        "requirements": (
            "Authorized analytics access and reliable conversion tracking.",
            "Documented marketing channels, brand standards, and ownership.",
            "Baseline acquisition costs and conversion metrics where available.",
        ),
        "validation": (
            "Verify reporting against the original analytics systems.",
            "Measure reporting time saved separately from campaign outcomes.",
            "Test one approved experiment with explicit success criteria; "
            "do not assume revenue uplift from reporting automation.",
        ),
        "oversight": (
            "A human approves publishing, community interactions, campaign "
            "changes, and all advertising expenditure."
        ),
    },
    {
        "key": "application_operations",
        "pattern": r"\b(?:maintaining|maintenance|monitoring)\b.*"
                   r"\b(?:web app|application|infrastructure|server)\b",
        "title": "Automate application checks and prepare maintenance triage",
        "workflow": (
            "Connect authorized read-only health metrics and error logs.",
            "Run scheduled availability checks and summarize recurring errors.",
            "Create maintenance recommendations linked to observed failures.",
            "Verify fixes in staging after separate engineering approval.",
        ),
        "requirements": (
            "Architecture documentation, monitoring access, and runbooks.",
            "A staging environment and documented backup and recovery procedures.",
            "An engineering owner for investigation and approved changes.",
        ),
        "validation": (
            "Establish incident frequency and current triage effort.",
            "Test detection and escalation using controlled staging failures.",
            "Measure false alerts and review effort before estimating savings.",
        ),
        "oversight": (
            "Production changes, deployments, credential changes, and recovery "
            "actions remain subject to existing engineering approvals."
        ),
    },
)


def build_improvement_proposals(research_record: dict) -> list[dict]:
    opportunity_id = research_record["opportunity_id"]
    report = research_record["report"]
    if report["opportunity_id"] != opportunity_id:
        raise ValueError("Research opportunity mismatch.")

    claims = report["claims"]
    claim_ids = [claim["claim_id"] for claim in claims]
    if len(claim_ids) != len(set(claim_ids)):
        raise ValueError("Duplicate research claim IDs.")

    proposals = []
    for template in WORKFLOWS:
        matched = []
        for claim in claims:
            if claim["category"] != "source_work_required":
                continue
            if claim["evidence_status"] == "contradicted":
                continue
            if re.search(template["pattern"], claim["claim"], re.IGNORECASE):
                matched.append(claim)

        if not matched:
            continue

        references = tuple(sorted(claim["claim_id"] for claim in matched))
        identity = json.dumps({
            "version": PROPOSAL_VERSION,
            "opportunity_id": opportunity_id,
            "research_created_at": research_record["created_at"],
            "workflow": template["key"],
            "claim_ids": references,
        }, sort_keys=True)

        proposal = ImprovementProposal(
            proposal_id="improvement_" + hashlib.sha256(
                identity.encode()
            ).hexdigest(),
            opportunity_id=opportunity_id,
            research_created_at=research_record["created_at"],
            title=template["title"],
            source_claim_ids=references,
            current_task="\n".join(claim["claim"] for claim in matched),
            proposed_workflow=template["workflow"],
            implementation_requirements=template["requirements"],
            validation_steps=template["validation"],
            human_oversight=template["oversight"],
            economics=None,
        )
        data = proposal.to_dict()
        data["generator_version"] = PROPOSAL_VERSION
        data["limitations"] = [
            "Template matched to disclosed work; applicability requires review.",
            "Task mentions may be incomplete, historical, or qualified.",
            "All referenced disclosure versions are retained; reconcile "
            "conflicting versions before implementation.",
            "No implementation costs, savings, or revenue uplift established.",
        ]
        proposals.append(data)

    return proposals
