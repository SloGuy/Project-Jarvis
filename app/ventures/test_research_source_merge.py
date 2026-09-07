from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.ventures import research_store as store
from app.ventures.models import (
    BusinessType,
    OpportunityStatus,
    VenturesOpportunity,
    utc_now,
)
from app.ventures.research_models import EvidenceQuality, EvidenceStatus
from app.ventures.research_source_merge import merge_source_research
from app.ventures.research_updates import update_claim_evidence


def main():
    now = utc_now()
    opportunity = VenturesOpportunity(
        opportunity_id="source_merge_test",
        name="Synthetic SaaS",
        business_type=BusinessType.SAAS,
        asking_price_usd=50_000,
        annual_revenue_usd=None,
        annual_sde_usd=None,
        owner_hours_per_week=10,
        source="empire_flippers:synthetic",
        source_url="https://example.invalid/listing/123",
        notes="Synthetic fixture.",
        status=OpportunityStatus.DISCOVERED,
        created_at=now,
        updated_at=now,
    )
    listing = {
        "id": "synthetic",
        "listing_price": 50_000,
        "average_monthly_gross_revenue": 5_000,
        "average_monthly_net_profit": 2_000,
        "hours_worked_per_week": 10,
        "summary": "Synthetic subscription software.",
        "risks": [],
    }
    original_opportunity = opportunity.to_dict()

    with TemporaryDirectory() as directory:
        root = Path(directory)
        with (
            patch.object(store, "RESEARCH_DIRECTORY", root),
            patch.object(store, "RESEARCH_FILE", root / "research.json"),
        ):
            def merge(data, timestamp="2026-01-01T00:00:00+00:00"):
                return merge_source_research(
                    opportunity=opportunity,
                    listing=data,
                    source_url=opportunity.source_url,
                    fetched_at=timestamp,
                )

            first = merge(listing)
            report = first["record"]["report"]
            assert first["changed"] is True
            assert first["added_claims"] == 5
            assert report["evidence_score"] == 0
            assert report["recommendation"] == "more_research_required"
            assert all(
                claim["evidence_status"] == "unverified"
                for claim in report["claims"]
            )
            assert not any(
                claim["category"] == "source_risks"
                for claim in report["claims"]
            )
            profit = next(
                claim for claim in report["claims"]
                if claim["category"] == "source_average_monthly_net_profit"
            )
            assert "monthly" in profit["claim"]
            assert "not verified SDE" in profit["claim"]
            print("source_claims_unverified_and_periods_preserved: PASS")

            repeated = merge(listing, "2026-01-02T00:00:00+00:00")
            assert repeated["changed"] is False
            assert repeated["record"] == first["record"]
            assert len(store.list_research_reports()) == 1
            print("unchanged_collection_no_duplicate_report: PASS")

            reviewed = update_claim_evidence(
                opportunity_id=opportunity.opportunity_id,
                claim_id=profit["claim_id"],
                evidence_status=EvidenceStatus.SUPPORTED,
                evidence_quality=EvidenceQuality.HIGH,
                evidence_notes="Synthetic independent verification.",
            )
            before_count = len(store.list_research_reports())
            repeated = merge(listing)
            assert repeated["changed"] is False
            assert repeated["record"] == reviewed
            assert len(store.list_research_reports()) == before_count
            print("reviewed_evidence_preserved: PASS")

            changed = deepcopy(listing)
            changed["average_monthly_net_profit"] = 1_500
            updated = merge(changed)
            updated_report = updated["record"]["report"]
            assert updated["added_claims"] == 1
            versions = [
                claim for claim in updated_report["claims"]
                if claim["category"] == "source_average_monthly_net_profit"
            ]
            assert len(versions) == 2
            old = next(
                claim for claim in versions
                if claim["claim_id"] == profit["claim_id"]
            )
            assert old["evidence_status"] == "supported"
            assert old["evidence_notes"] == (
                "Synthetic independent verification."
            )
            new = next(
                claim for claim in versions
                if claim["claim_id"] != profit["claim_id"]
            )
            assert new["evidence_status"] == "unverified"
            assert any(
                risk["category"] == "source_disclosure_change"
                and risk["severity"] == "high"
                for risk in updated_report["risks"]
            )
            assert updated_report["recommendation"] == "more_research_required"
            assert updated_report["diligence_questions"] == (
                reviewed["report"]["diligence_questions"]
            )
            assert updated_report["summary"] == reviewed["report"]["summary"]
            assert store.list_research_reports()[0] == first["record"]
            assert merge(changed)["changed"] is False
            print("changed_disclosure_retains_history_and_flags_review: PASS")

            before = store.RESEARCH_FILE.read_bytes()
            try:
                merge(dict(listing, id="wrong_listing"))
            except ValueError:
                pass
            else:
                raise AssertionError("Expected source mismatch rejection")
            assert store.RESEARCH_FILE.read_bytes() == before
            assert opportunity.to_dict() == original_opportunity
            print("source_mismatch_no_write_and_opportunity_unchanged: PASS")


if __name__ == "__main__":
    main()
