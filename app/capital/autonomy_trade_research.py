"""Queue diagnostic-backed research work without changing candidates."""

from copy import deepcopy
from decimal import Decimal
import hashlib
import json

from app.capital.research_store import locked_research_state, utc_now_iso


STORE_KEY = "trade_research_requests"


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    )


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _authorize():
    from app.agents.capital_registry import RESEARCH_AGENT_ID
    from app.capital.autonomy_control import read_operating_policy
    from app.capital.autonomy_policy import authorize_capital_action

    authorize_capital_action(
        agent_id=RESEARCH_AGENT_ID,
        action="capital.propose_research",
        policy=read_operating_policy(),
        execution_mode="paper",
    )


def collect_diagnosis(experiment_id):
    from app.capital.trade_diagnosis_collection import collect_trade_diagnosis

    return collect_trade_diagnosis(experiment_id=experiment_id)


def current_configuration(experiment_id):
    from app.capital.trade_diagnosis_collection import _binding, _canonical

    return json.loads(_canonical(_binding(experiment_id)))


def _verify_request(request, request_id):
    if (
        not isinstance(request, dict)
        or request.get("request_id") != request_id
        or request.get("schema_version") != 1
        or request.get("status") not in {"queued", "running", "completed", "failed"}
    ):
        raise ValueError("Invalid saved trade research request.")
    diagnosis = request.get("diagnosis")
    if not isinstance(diagnosis, dict):
        raise ValueError("Saved diagnosis is missing.")
    if request.get("diagnosis_sha256") != _digest(diagnosis):
        raise ValueError("Saved diagnosis hash changed.")
    if request.get("identity") != {
        "experiment_id": diagnosis["experiment_id"],
        "configuration_sha256": diagnosis["configuration_sha256"],
        "trigger": "mature_profit_factor_failure",
    }:
        raise ValueError("Saved request identity mismatch.")
    expected_id = "trade-research:" + _digest(request["identity"])
    if request_id != expected_id:
        raise ValueError("Saved request ID mismatch.")


def queue_trade_research(*, experiment_id):
    """Queue once for a mature, untruncated realized-profit-factor failure.

    This is a separate diagnostic work queue. It does not attach evidence
    to an existing candidate, change its verdict, or grant trading authority.
    A new experiment configuration creates a distinct request identity.
    """
    _authorize()
    diagnosis = collect_diagnosis(experiment_id)
    if diagnosis["experiment_id"] != experiment_id:
        raise ValueError("Diagnosis experiment mismatch.")
    if (
        diagnosis.get("designation") != "retrospective_trade_diagnosis"
        or diagnosis.get("configuration_rechecked") is not True
        or diagnosis.get("validation_verified") is not False
        or diagnosis.get("live_capital_authorized") is not False
    ):
        raise ValueError("Unexpected diagnosis scope or authority.")
    if _digest(diagnosis["configuration"]) != diagnosis["configuration_sha256"]:
        raise ValueError("Diagnosis configuration hash mismatch.")

    summary = diagnosis["summary"]
    thresholds = diagnosis["thresholds"]
    if diagnosis["query_limit_reached"]:
        return {"status": "blocked_incomplete_history", "scheduled_count": 0}
    if summary["closed_trades"] < thresholds["minimum_closed_trades"]:
        return {"status": "waiting_for_sample", "scheduled_count": 0}
    factor = summary["profit_factor"]
    if factor is None or Decimal(factor) >= Decimal(
        thresholds["minimum_profit_factor"]
    ):
        return {"status": "no_profit_factor_failure", "scheduled_count": 0}

    identity = {
        "experiment_id": experiment_id,
        "configuration_sha256": diagnosis["configuration_sha256"],
        "trigger": "mature_profit_factor_failure",
    }
    request_id = "trade-research:" + _digest(identity)

    _authorize()
    if current_configuration(experiment_id) != diagnosis["configuration"]:
        raise ValueError("Configuration changed before request persistence.")

    with locked_research_state(write=True) as state:
        _authorize()
        requests = state.setdefault(STORE_KEY, {})
        if not isinstance(requests, dict):
            raise ValueError("Invalid trade research request store.")
        existing = requests.get(request_id)
        if existing is not None:
            _verify_request(existing, request_id)
            return {
                "status": "already_requested",
                "scheduled_count": 0,
                "request": deepcopy(existing),
            }

        request = {
            "schema_version": 1,
            "request_id": request_id,
            "identity": identity,
            "status": "queued",
            "created_at": utc_now_iso(),
            "diagnosis": deepcopy(diagnosis),
            "diagnosis_sha256": _digest(diagnosis),
            "objective": (
                "Investigate the experiment's realized profit-factor failure. "
                "Distinguish journal observations from causal explanations. "
                "Identify missing price, quote, cost and execution evidence. "
                "Propose a falsifiable prospective test without claiming that "
                "removing losing assets or shortening exits improves returns. "
                "Preserve existing acceptance thresholds and cost assumptions."
            ),
            "research_candidate_changed": False,
            "validation_verified": False,
            "strategy_change_authorized": False,
            "promotion_authorized": False,
            "live_capital_authorized": False,
        }
        requests[request_id] = deepcopy(request)

    return {
        "status": "queued",
        "scheduled_count": 1,
        "request": request,
    }
