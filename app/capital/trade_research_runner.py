"""Generate constrained advisory proposals from trade diagnostics."""

from copy import deepcopy
from decimal import Decimal
import fcntl
from uuid import uuid4

from app.capital import autonomy_trade_research as queue
from app.capital import research_store
from app.capital.trade_research_design import (
    HYPOTHESIS,
    build_research_design,
    validate_design_hypothesis,
    verify_design,
)


def _evidence(request):
    diagnosis = request["diagnosis"]

    def compact_group(row, key):
        return {
            key: row[key],
            "closed_trades": row["closed_trades"],
            "net_realized_usd": row["net_realized_usd"],
            "profit_factor": row["profit_factor"],
        }

    assets = sorted(
        diagnosis["by_symbol"],
        key=lambda row: (
            Decimal(row["net_realized_usd"]), row["symbol"],
        ),
    )
    exits = diagnosis["by_exit_rule"]
    stops = diagnosis["stop_overruns"]
    payload = {
        "designation": "retrospective_journal_observations",
        "experiment_id": diagnosis["experiment_id"],
        "journal_sha256": diagnosis["journal_sha256"],
        "diagnosis_sha256": request["diagnosis_sha256"],
        "thresholds": diagnosis["thresholds"],
        "summary": diagnosis["summary"],
        "lowest_net_asset_groups": [
            compact_group(row, "symbol") for row in assets[:5]
        ],
        "exit_groups": [
            compact_group(row, "exit_rule") for row in exits[:6]
        ],
        "largest_stop_overruns": [
            {
                key: row[key]
                for key in (
                    "id", "symbol", "return_percent",
                    "threshold_overrun_percentage_points",
                )
            }
            for row in stops[:3]
        ],
        "omitted_group_counts": {
            "assets": max(0, len(assets) - 5),
            "exits": max(0, len(exits) - 6),
            "stop_overruns": max(0, len(stops) - 3),
        },
        "limitations": diagnosis["limitations"],
        "validation_verified": False,
    }
    design = build_research_design(diagnosis)
    design_context = {
        key: design[key]
        for key in (
            "design_id",
            "design_sha256",
            "hypothesis",
            "selection_basis",
            "baseline",
            "intervention",
            "comparison",
            "registration_blockers",
            "historical_benefit_established",
            "validation_ready",
        )
    }
    evidence = [
        {
            "id": "trade-diagnosis",
            "summary": queue._canonical(payload),
        },
        {
            "id": "research-design",
            "summary": queue._canonical(design_context),
        },
    ]
    if any(len(item["summary"]) > 4000 for item in evidence):
        raise ValueError("Research evidence exceeds the model evidence budget.")
    return evidence


def _model_objective(request):
    return (
        "Explain the supplied trade diagnosis and predefined research design. "
        "Copy this exact question into the hypothesis field: "
        + HYPOTHESIS
        + " Do not alter the comparison, cooldown duration, acceptance "
        "criteria, costs or asset universe. In the rationale, distinguish "
        "observations from speculation. The journals do not establish "
        "causation or demonstrate that cooldowns improve performance. "
        "Distinguish total stopped-trade losses from threshold overruns. "
        "Do not propose selection using eventual exits or future outcomes. "
        "In next_question, identify missing evidence or implementation "
        "needed for this design. Do not claim the comparison is executable, "
        "registered, validated or approved."
    )


def propose_for_request(request, evidence):
    from app.capital.autonomy_research_model import propose_research

    return propose_research(
        objective=_model_objective(request),
        strategies=[request["diagnosis"]["strategy_name"]],
        evidence=evidence,
    )


def _validate_proposal(proposal, strategy_name):
    fields = {
        "strategy_name", "hypothesis", "rationale",
        "evidence_ids", "next_question",
    }
    if not isinstance(proposal, dict) or set(proposal) != fields:
        raise ValueError("Invalid trade research proposal structure.")
    for field, maximum in (
        ("strategy_name", 120),
        ("hypothesis", 2000),
        ("rationale", 4000),
        ("next_question", 2000),
    ):
        value = proposal[field]
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > maximum
        ):
            raise ValueError(f"Invalid proposal {field}.")
    if proposal["strategy_name"] != strategy_name:
        raise ValueError("Proposal changed the experiment strategy.")

    validate_design_hypothesis(proposal["hypothesis"])
    citations = proposal["evidence_ids"]
    if (
        not isinstance(citations, list)
        or any(
            not isinstance(value, str)
            or value not in {"trade-diagnosis", "research-design"}
            for value in citations
        )
        or len(citations) != len(set(citations))
    ):
        raise ValueError("Invalid diagnostic evidence citation.")


def _inputs(request):
    return {
        "identity": request["identity"],
        "diagnosis": request["diagnosis"],
        "diagnosis_sha256": request["diagnosis_sha256"],
        "objective": request["objective"],
    }


def _owned(state, request_id, token, inputs):
    request = state[queue.STORE_KEY][request_id]
    queue._verify_request(request, request_id)
    if (
        request["status"] != "running"
        or request.get("run_token") != token
        or _inputs(request) != inputs
    ):
        raise ValueError("Trade research request ownership or inputs changed.")
    return request


def _verify_completed(request):
    proposal = request.get("proposal")
    _validate_proposal(proposal, request["diagnosis"]["strategy_name"])
    if request.get("proposal_sha256") != queue._digest(proposal):
        raise ValueError("Saved proposal hash changed.")
    verify_design(request.get("research_design"), request["diagnosis"])
    if (
        request.get("model_inputs") != _evidence(request)
        or request.get("model_objective") != _model_objective(request)
        or request.get("proposal_scientifically_validated") is not False
        or request.get("validation_ready") is not False
    ):
        raise ValueError("Saved proposal context or scope changed.")


def _cycle():
    queue._authorize()
    with research_store.locked_research_state() as state:
        requests = state.get(queue.STORE_KEY, {})
        if not isinstance(requests, dict):
            raise ValueError("Invalid trade research request store.")
        for request_id, request in requests.items():
            queue._verify_request(request, request_id)
            if request["status"] == "completed":
                _verify_completed(request)
        running = [
            key for key, value in requests.items()
            if value["status"] == "running"
        ]
        if running:
            return {
                "status": "blocked_running_request",
                "request_ids": sorted(running),
                "processed_count": 0,
            }
        pending = sorted(
            (
                deepcopy(value) for value in requests.values()
                if value["status"] == "queued"
            ),
            key=lambda value: (value["created_at"], value["request_id"]),
        )

    if not pending:
        return {"status": "idle", "processed_count": 0}

    request = pending[0]
    request_id = request["request_id"]
    inputs = deepcopy(_inputs(request))
    evidence = _evidence(request)
    design = build_research_design(request["diagnosis"])
    experiment_id = request["diagnosis"]["experiment_id"]
    configuration = request["diagnosis"]["configuration"]
    if queue.current_configuration(experiment_id) != configuration:
        raise ValueError("Configuration changed before proposal generation.")

    token = uuid4().hex
    with research_store.locked_research_state(write=True) as state:
        queue._authorize()
        current = state[queue.STORE_KEY][request_id]
        if current != request:
            raise ValueError("Request changed before claim.")
        current.update({
            "status": "running",
            "run_token": token,
            "started_at": research_store.utc_now_iso(),
        })

    try:
        proposal = propose_for_request(request, evidence)
        _validate_proposal(proposal, request["diagnosis"]["strategy_name"])
        queue._authorize()
        if queue.current_configuration(experiment_id) != configuration:
            raise ValueError("Configuration changed during proposal generation.")

        with research_store.locked_research_state(write=True) as state:
            queue._authorize()
            current = _owned(state, request_id, token, inputs)
            current.update({
                "status": "completed",
                "completed_at": research_store.utc_now_iso(),
                "model_inputs": deepcopy(evidence),
                "model_objective": _model_objective(request),
                "research_design": deepcopy(design),
                "proposal": deepcopy(proposal),
                "proposal_sha256": queue._digest(proposal),
                "proposal_scientifically_validated": False,
                "validation_ready": False,
            })
        return {
            "status": "advisory_proposal_saved",
            "request_id": request_id,
            "processed_count": 1,
            "research_candidate_changed": False,
            "validation_ready": False,
            "strategy_change_authorized": False,
            "live_capital_authorized": False,
        }
    except Exception as error:
        with research_store.locked_research_state(write=True) as state:
            current = _owned(state, request_id, token, inputs)
            current.update({
                "status": "failed",
                "completed_at": research_store.utc_now_iso(),
                "error_type": type(error).__name__,
            })
        raise


def process_trade_research_once(*, directory=None):
    """Serialize generation; completed and failed requests are not rerun.

    Interrupted running requests remain visibly blocked for inspection.
    Model commentary remains unvalidated even when the design matches.
    """
    from pathlib import Path

    root = (
        Path(directory) if directory is not None
        else research_store.STATE_DIRECTORY / "autonomy"
    )
    root.mkdir(parents=True, exist_ok=True)
    with (root / "trade-research-worker.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy", "processed_count": 0}
        try:
            return _cycle()
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
