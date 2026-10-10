"""Atomic registration of prospective cooldown comparison terms.

This module registers work. Collection and evaluation need separate handlers.
"""

from copy import deepcopy
from uuid import uuid4

from app.agents.capital_registry import VALIDATION_AGENT_ID
from app.capital import autonomy_trade_research as queue
from app.capital import validation_registry as registry
from app.capital.autonomy_control import read_operating_policy
from app.capital.autonomy_policy import authorize_capital_action
from app.capital.cooldown_contract import (
    build_cooldown_contract,
    verify_cooldown_contract,
)
from app.capital.cooldown_verification import canonical
from app.capital.research_store import locked_research_state
from app.capital.validation_plan import seal_plan, timestamp


PREFIX = "capital.cooldown/request/"


def _authorize():
    authorize_capital_action(
        agent_id=VALIDATION_AGENT_ID,
        action="capital.register_validation",
        policy=read_operating_policy(),
        execution_mode="paper",
    )


def _request_id(value):
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 160
    ):
        raise ValueError("Invalid research request ID.")
    return value


def _marker(request_id):
    return PREFIX + queue._digest({"request_id": request_id})


def _current_binding(plan):
    from app.capital.run_validation import current_binding

    return current_binding(plan)


def _retained_request(state, request_id):
    requests = state.get(queue.STORE_KEY, {})
    if not isinstance(requests, dict):
        raise ValueError("Invalid trade research request store.")
    request = requests.get(request_id)
    if request is None:
        raise KeyError("Completed trade research request not found.")
    queue._verify_request(request, request_id)
    return deepcopy(request)


def _check_configuration(request):
    diagnosis = request["diagnosis"]
    current = queue.current_configuration(diagnosis["experiment_id"])
    if canonical(current) != canonical(diagnosis["configuration"]):
        raise ValueError("Origin experiment configuration changed.")


def _verify_row(row, request, policy):
    if row["envelope"]["plan"]["created_by"] != _marker(
        request["request_id"]
    ):
        raise ValueError("Comparison registration owner changed.")
    if "cooldown_contract" not in row:
        raise ValueError("Registered comparison contract is missing.")
    if "cooldown_contract_sha256" not in row:
        raise ValueError("Retained comparison contract hash is missing.")

    body = verify_cooldown_contract(
        row["cooldown_contract"],
        expected_sha256=row["cooldown_contract_sha256"],
        source_row=row,
        request=request,
        policy=policy,
    )
    if body["origin_request_id"] != request["request_id"]:
        raise ValueError("Comparison origin request changed.")
    return body


def _check_reservations(state, plan):
    start = timestamp(plan["start"])
    end = timestamp(plan["end_exclusive"])

    for row in state["plans"].values():
        existing = row["envelope"]["plan"]
        same_series = (
            existing["asset_id"] == plan["asset_id"]
            and existing["provider"] == plan["provider"]
        )
        overlaps = (
            start < timestamp(existing["end_exclusive"])
            and timestamp(existing["start"]) < end
        )
        if same_series and overlaps:
            raise ValueError(
                "This asset/provider period is already reserved. "
                "Failed and completed plans retain their reservations."
            )


def register_cooldown_once(draft, *, request_id, policy):
    """Register one comparison per retained completed research request.

    The caller supplies a future baseline draft with created_by set to
    capital.cooldown. No research candidate is changed by this operation.
    """
    request_id = _request_id(request_id)
    if not isinstance(draft, dict):
        raise ValueError("A baseline draft is required.")
    if draft.get("created_by") != "capital.cooldown":
        raise ValueError("Comparison draft must belong to capital.cooldown.")
    if "created_at" in draft:
        raise ValueError("Registration assigns creation time.")

    prepared = deepcopy(draft)
    prepared["created_by"] = _marker(request_id)
    _authorize()

    # Registry ownership and the origin are checked under their locks.
    # The registry context commits only if every check succeeds.
    with registry.locked_state(write=True) as state:
        with locked_research_state() as research:
            request = _retained_request(research, request_id)
            _check_configuration(request)

            matches = [
                row for row in state["plans"].values()
                if row["envelope"]["plan"]["created_by"]
                == prepared["created_by"]
            ]
            if len(matches) > 1:
                raise ValueError("Duplicate comparison registrations.")

            if matches:
                row = matches[0]
                original = deepcopy(row["envelope"]["plan"])
                original.pop("created_at")
                if canonical(original) != canonical(prepared):
                    raise ValueError(
                        "Request is already registered with different terms."
                    )
                _verify_row(row, request, policy)
                _check_configuration(request)
                _authorize()
                result = deepcopy(row)
            else:
                measured = registry.now_utc()
                envelope = seal_plan(prepared, now=measured)
                plan = envelope["plan"]
                start = timestamp(plan["start"])
                end = timestamp(plan["end_exclusive"])
                if (
                    start.second or start.microsecond
                    or end.second or end.microsecond
                ):
                    raise ValueError(
                        "Comparison period must use whole-minute boundaries."
                    )

                _current_binding(plan)
                _check_reservations(state, plan)

                plan_id = f"validation_{uuid4().hex}"
                row = {
                    "plan_id": plan_id,
                    "envelope": envelope,
                    "registered_sha256": envelope["sha256"],
                    "status": "registered",
                    "history": [{
                        "status": "registered",
                        "at": plan["created_at"],
                    }],
                }
                saved = build_cooldown_contract(
                    source_row=row,
                    request=request,
                    policy=policy,
                    now=measured,
                )
                row["cooldown_contract"] = saved
                row["cooldown_contract_sha256"] = saved["sha256"]
                _verify_row(row, request, policy)

                _current_binding(plan)
                _check_configuration(request)
                _authorize()
                state["plans"][plan_id] = row
                result = deepcopy(row)

    return result


def read_registered_cooldown(plan_id, *, policy):
    """Verify a retained registration without changing its lifecycle."""
    from app.capital.cooldown_provider_comparison import authorize_read

    authorize_read()
    with registry.locked_state() as state:
        row = deepcopy(state["plans"][plan_id])
        saved = row.get("cooldown_contract")
        if not isinstance(saved, dict):
            raise ValueError("Registered comparison contract is missing.")
        request_id = _request_id(
            saved["contract"]["origin_request_id"]
        )
        with locked_research_state() as research:
            request = _retained_request(research, request_id)
            _check_configuration(request)
            _verify_row(row, request, policy)
            authorize_read()
    return row


def verify_collection_contract(row):
    """Check comparison terms without acquiring another registry lock.

    Collection callers enforce collection authority. This check also works
    while paused, when an already registered collection may continue.
    """
    owner = row["envelope"]["plan"]["created_by"]
    marked = owner.startswith(PREFIX)
    has_terms = (
        "cooldown_contract" in row
        or "cooldown_contract_sha256" in row
    )
    if not marked:
        if has_terms:
            raise ValueError("Comparison contract owner changed.")
        return

    from app.capital.policies import (
        MEAN_REVERSION_V2_1000_POLICY as comparison_policy,
    )

    saved = row.get("cooldown_contract")
    if not isinstance(saved, dict):
        raise ValueError("Registered comparison contract is missing.")
    body = saved.get("contract")
    if not isinstance(body, dict):
        raise ValueError("Invalid registered comparison terms.")
    request_id = _request_id(body.get("origin_request_id"))

    with locked_research_state() as research:
        request = _retained_request(research, request_id)
        _check_configuration(request)
        _verify_row(row, request, comparison_policy)
