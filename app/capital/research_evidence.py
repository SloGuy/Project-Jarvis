"""Attach verified development packets without changing research verdicts."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from app.capital.offline_verification import verify_report
from app.capital.replay_analysis import analyze_report
from app.capital.research_models import ResearchCandidate
from app.capital.research_store import locked_research_state, utc_now_iso

ARTIFACTS = ("plan.json", "report.json", "verification.json", "analysis.json")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def inspect_packet(directory):
    directory = Path(directory).resolve(strict=True)
    require(directory.is_dir(), "Expected an evaluation directory.")
    require(not (directory / "failure.json").exists(),
            "Packet contains a failure record.")
    raw = {
        name: (directory / name).read_bytes()
        for name in (*ARTIFACTS, "result.json")
    }
    data = {name: json.loads(value) for name, value in raw.items()}
    result = data["result.json"]
    require(result.get("status") == "completed", "Evaluation is incomplete.")
    require(result.get("designation") == "development",
            "Only development packets are supported.")
    require(result.get("promotion_authorized") is False,
            "Unexpected promotion authorization.")
    hashes = {name: digest(raw[name]) for name in ARTIFACTS}
    require(result.get("artifacts_sha256") == hashes,
            "Artifact hashes do not match.")

    plan, report = data["plan.json"], data["report.json"]
    require(plan.get("designation") == report.get("designation") == "development",
            "Development designation mismatch.")
    require(plan.get("holdout_protected") is False,
            "Unexpected holdout designation.")
    require(report.get("availability_verified") is False,
            "Unsupported historical-availability claim.")
    for key in (
        "asset_id", "provider", "start", "end_exclusive",
        "policy", "execution_manifest",
    ):
        require(key in plan and key in report and plan[key] == report[key],
                f"Plan/report mismatch: {key}")
    require(bool(plan["execution_manifest"]), "Execution manifest is missing.")
    require(plan.get("decision_ticks") == len(report["windows"]),
            "Decision count differs from plan.")
    scenarios = report["scenarios"]
    require(set(scenarios) == {"zero_cost", "specified_costs"},
            "Unexpected scenarios.")
    for name, fee, slippage in (
        ("zero_cost", "0", "0"),
        ("specified_costs", plan["fee_bps"], plan["slippage_bps"]),
    ):
        require(
            scenarios[name]["fee_bps"] == fee
            and scenarios[name]["slippage_bps"] == slippage,
            f"Scenario costs differ from plan: {name}",
        )

    verification = verify_report(report)
    require(verification == data["verification.json"],
            "Saved verification differs from recomputed verification.")
    analysis = analyze_report(report)
    require(analysis == data["analysis.json"],
            "Saved analysis differs from recomputed analysis.")

    summary = {
        name: {
            key: value
            for key, value in scenario.items()
            if key != "equity_curve"
        }
        for name, scenario in analysis["scenarios"].items()
    }
    # Keep only bounded summary fields; curves remain in the source packet.
    summary = {
        name: {
            key: scenario[key]
            for key in (
                "ending_equity", "return_percent", "completed_trades",
                "realized_pnl", "data_quality",
            )
        }
        for name, scenario in summary.items()
    }
    return {
        "packet_directory": str(directory),
        "report_sha256": hashes["report.json"],
        "artifacts_sha256": {
            **hashes, "result.json": digest(raw["result.json"]),
        },
        "designation": "development",
        "promotion_authorized": False,
        "association": "retrospective",
        "availability_verified": False,
        "symbol": report["symbol"],
        "provider": report["provider"],
        "start": report["start"],
        "end_exclusive": report["end_exclusive"],
        "summary": summary,
    }


def add_attachment(candidate, packet, version, attached_by, relevance):
    require(type(version) is int and version > 0,
            "Hypothesis version must be a positive integer.")
    require(candidate.hypothesis_version == version,
            "Candidate hypothesis version differs from requested version.")
    require(bool(attached_by.strip()) and bool(relevance.strip()),
            "Attached-by and relevance must not be empty.")
    require(packet["symbol"].upper() in {
        symbol.strip().upper() for symbol in candidate.asset_universe
    }, "Replay symbol is outside the candidate asset universe.")
    require(not any(
        item["report_sha256"] == packet["report_sha256"]
        for item in candidate.evaluation_attachments
    ), "This report is already attached.")
    record = deepcopy(packet)
    record.update({
        "research_id": candidate.research_id,
        "hypothesis_version": version,
        "hypothesis": candidate.hypothesis,
        "strategy_name": candidate.strategy_name,
        "strategy_match_verified": False,
        "attached_by": attached_by.strip(),
        "relevance": relevance.strip(),
        "attached_at": utc_now_iso(),
    })
    candidate.evaluation_attachments.append(record)
    return record


def attach_packet(directory, research_id, version, attached_by, relevance):
    # Recompute before obtaining the research write lock.
    packet = inspect_packet(directory)
    with locked_research_state(write=True) as state:
        research_id = research_id.strip()
        if research_id not in state["candidates"]:
            raise KeyError(f"Unknown research candidate: {research_id}")
        candidate = ResearchCandidate.from_dict(
            state["candidates"][research_id]
        )
        record = add_attachment(
            candidate, packet, version, attached_by, relevance
        )
        candidate.updated_at = record["attached_at"]
        state["candidates"][research_id] = candidate.to_dict()
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("--attach", action="store_true")
    parser.add_argument("--research-id")
    parser.add_argument("--hypothesis-version", type=int)
    parser.add_argument("--attached-by")
    parser.add_argument("--relevance")
    args = parser.parse_args()
    if args.attach:
        if not all((
            args.research_id, args.hypothesis_version,
            args.attached_by, args.relevance,
        )):
            parser.error("Attachment requires research ID, version, author and relevance.")
        result = attach_packet(
            args.directory, args.research_id, args.hypothesis_version,
            args.attached_by, args.relevance,
        )
    else:
        result = inspect_packet(args.directory)
    print(json.dumps(result, indent=2))
    print("ATTACHED" if args.attach else "VERIFIED: research state unchanged.")


if __name__ == "__main__":
    main()
