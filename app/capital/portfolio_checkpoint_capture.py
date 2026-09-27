"""Assemble prospective portfolio evidence without persisting a checkpoint."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.capital.portfolio_audit_reader import read_portfolio_audit
from app.capital.portfolio_audit_reconciliation import reconcile_audit_snapshot
from app.capital.portfolio_audit_effects import assess_audit_effects
from app.capital.portfolio_checkpoint_inputs import build_checkpoint_inputs
from app.capital.portfolio_provenance_valuation import value_provenance_snapshot
from app.capital.portfolio_intelligence_service import (
    PROVIDERS,
    _resolve_portfolios,
)
from app.capital.quote_provenance_store import load_quote_provenance


PROVENANCE_DIRECTORY = (
    Path(__file__).resolve().parents[2]
    / "runtime"
    / "capital"
    / "quote_provenance"
)


from app.capital.portfolio_sampled_regime import capture_market_context


def capture_portfolio_checkpoint():
    """Capture evidence using actual sampling time, not a daily boundary.

    The database snapshot and filesystem quote scan are separate.
    Incomplete valuations and unresolved accounting checks remain explicit.
    No checkpoint is marked eligible for historical returns here.
    """
    started_at = datetime.now(timezone.utc)
    resolved = _resolve_portfolios()
    audit = read_portfolio_audit(schema="public", include_assets=True)
    inputs = build_checkpoint_inputs(
        audit_snapshot=audit,
        resolved_portfolios=resolved,
    )
    reconciliation = reconcile_audit_snapshot(audit)
    effects = assess_audit_effects(audit)

    providers = {
        asset["id"]: PROVIDERS[(asset["asset_type"], asset["symbol"])]
        for asset in inputs["assets"]
        if (asset["asset_type"], asset["symbol"]) in PROVIDERS
    }
    valuations = value_provenance_snapshot(
        snapshot=inputs,
        directory=PROVENANCE_DIRECTORY,
        provider_by_asset=providers,
        maximum_provider_age=timedelta(minutes=20),
        maximum_capture_age=timedelta(minutes=2),
    )

    quote_records = {}
    for row in valuations["quote_coverage"]["assets"]:
        record_id = row["record_id"]
        if record_id is not None:
            quote_records[record_id] = load_quote_provenance(
                directory=PROVENANCE_DIRECTORY,
                record_id=record_id,
            )

    market_context = capture_market_context()
    return {
        "schema_version": 1,
        "methodology": "prospective_portfolio_evidence_snapshot_v1",
        "started_at": started_at.isoformat(),
        "sampled_at": audit["sampled_at"],
        "market_context": market_context,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "resolved_portfolios": [
            {"portfolio_id": identifier, **metadata}
            for identifier, metadata in sorted(resolved.items())
        ],
        "audit_snapshot": audit,
        "valuation_inputs": inputs,
        "row_reconciliation": reconciliation,
        "accounting_effects": effects,
        "valuations": valuations,
        "selected_quote_records": quote_records,
        "historical_return_eligible": False,
        "limitations": [
            "Sampling time is not an exact UTC daily boundary.",
            "Audit event IDs and timestamps do not establish commit order.",
            "Matching row images and amounts do not prove complete history.",
            "Quote files and database rows are not one atomic snapshot.",
            "Capture timestamps do not prove quote-file persistence times.",
            "Quote age checks do not verify exchange sessions or authenticity.",
            "The embedded audit snapshot grows as accounting events accumulate.",
        ],
        "checkpoint_persisted": False,
        "database_writes": False,
        "execution_authorized": False,
        "allocation_authority": False,
        "live_capital_authority": False,
    }
