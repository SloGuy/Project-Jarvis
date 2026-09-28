"""Persist model revision drafts before changing research candidates."""

from copy import deepcopy
import json

from app.agents.capital_registry import RESEARCH_AGENT_ID
from app.capital.autonomy_control import read_operating_policy
from app.capital.autonomy_policy import authorize_capital_action
from app.capital.autonomy_research_model import propose_research
from app.capital.research_models import ResearchCandidate, ResearchStatus
from app.capital.research_store import locked_research_state, utc_now_iso


def _authorize():
    authorize_capital_action(
        agent_id=RESEARCH_AGENT_ID,
        action="capital.revise_research",
        policy=read_operating_policy(),
        execution_mode="paper",
    )


def _text(value, field, maximum):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
    ):
        raise ValueError(f"Invalid {field}.")
    return value.strip()


def _existing(state, task_id, request):
    drafts = state.get("autonomy_revision_drafts", {})
    if not isinstance(drafts, dict):
        raise RuntimeError("Invalid research draft store.")
    saved = drafts.get(task_id)
    if saved is not None:
        if saved["request"] != request:
            raise ValueError("Task ID is already bound to another request.")
        return deepcopy(saved)
    return None


def check_trade_count_consistency(hypothesis, success_criteria):
    """Reject explicit trade counts that conflict with the inherited minimum."""
    import re

    criteria = " ".join(success_criteria).lower()
    minimum = re.search(r"at least (\d+) completed trades", criteria)
    if minimum is None:
        return
    required = int(minimum.group(1))
    text = hypothesis.lower().replace("-", " ")
    for word, value in (
        ("fifteen", "15"), ("thirty", "30"),
        ("twenty", "20"), ("ten", "10"),
    ):
        text = re.sub(r"\b" + word + r"\b", value, text)
    counts = re.findall(
        r"\b(\d+)\s+(?:completed\s+)?trades\b", text
    )
    counts += re.findall(
        r"\b(?:completed\s+)?trade count\s*(?:of|:|=)\s*(\d+)\b",
        text,
    )
    if any(int(value) < required for value in counts):
        raise ValueError(
            "Proposed hypothesis conflicts with the inherited trade minimum."
        )


def prepare_revision_draft(*, task_id, parent_research_id, objective):
    """Generate at most one persisted draft per task identity.

    Concurrent callers can make redundant model requests, but only the
    first saved proposal is retained. Worker claiming prevents those
    redundant requests during normal operation.

    Saved assessments are context, not freshly verified evidence.
    No model proposal grants readiness or promotion authority.
    """
    task_id = _text(task_id, "task ID", 160)
    parent_id = _text(parent_research_id, "parent research ID", 160)
    objective = _text(objective, "objective", 2000)
    request = {
        "parent_research_id": parent_id,
        "objective": objective,
        "agent_id": RESEARCH_AGENT_ID,
    }

    _authorize()
    with locked_research_state() as state:
        existing = _existing(state, task_id, request)
        if existing is not None:
            return existing
        parent_row = deepcopy(state["candidates"][parent_id])

    parent = ResearchCandidate.from_dict(parent_row)
    if parent.status not in {
        ResearchStatus.REVISION_REQUIRED,
        ResearchStatus.REJECTED,
    }:
        raise ValueError("Parent must require revision or be rejected.")

    # References identify the exact saved context in this draft.
    # They are not claims of independent evidence verification.
    evidence = [{
        "id": "parent-research",
        "summary": json.dumps({
            "research_id": parent.research_id,
            "hypothesis": parent.hypothesis,
            "status": parent.status.value,
            "verdict": parent.verdict.value,
            "success_criteria": parent.success_criteria,
            "concerns": parent.concerns,
        }, sort_keys=True, allow_nan=False),
    }]
    for index, assessment in enumerate(parent.validation_assessments):
        evidence.append({
            "id": f"saved-assessment-{index + 1}",
            "summary": json.dumps(
                assessment, sort_keys=True, allow_nan=False
            ),
        })

    evidence.append({
        "id": "revision-constraints",
        "summary": (
            "The inherited success criteria are mandatory and unchanged. "
            "Do not lower the completed-trade minimum or weaken costs, "
            "return, benchmark, drawdown, or data-quality requirements. "
            "Keep historical sample counts in the rationale, not as a "
            "replacement acceptance threshold in the new hypothesis. "
            "A larger sample does not itself improve expected performance."
        ),
    })

    proposal = propose_research(
        objective=objective,
        strategies=[parent.strategy_name],
        evidence=evidence,
    )
    check_trade_count_consistency(
        proposal["hypothesis"], parent.success_criteria,
    )
    if proposal["hypothesis"].strip() == parent.hypothesis.strip():
        raise ValueError("Model did not propose a changed hypothesis.")

    # Recheck controls after the potentially long model request.
    _authorize()
    with locked_research_state(write=True) as state:
        existing = _existing(state, task_id, request)
        if existing is not None:
            return existing
        if state["candidates"].get(parent_id) != parent_row:
            raise ValueError("Parent changed while generating the draft.")

        draft = {
            "schema_version": 1,
            "task_id": task_id,
            "request": request,
            "parent_snapshot": parent_row,
            "model_inputs": evidence,
            "proposal": proposal,
            "created_at": utc_now_iso(),
            "status": "draft",
            "evidence_reverified": False,
            "promotion_authorized": False,
            "live_capital_authorized": False,
        }
        state.setdefault("autonomy_revision_drafts", {})[task_id] = deepcopy(
            draft
        )

    return draft


def apply_revision_draft(*, task_id):
    """Apply a saved draft once; return the original result on retry.

    The saved draft remains the original proposal. The revision request
    receipt records successful application in the same atomic write as
    candidate creation and parent archival.
    """
    from app.capital.research_revision import revise_research_candidate

    task_id = _text(task_id, "task ID", 160)
    _authorize()

    with locked_research_state() as state:
        drafts = state.get("autonomy_revision_drafts", {})
        if not isinstance(drafts, dict):
            raise RuntimeError("Invalid research draft store.")
        if task_id not in drafts:
            raise KeyError("Research draft not found.")
        draft = deepcopy(drafts[task_id])

    if (
        type(draft["schema_version"]) is not int
        or draft["schema_version"] != 1
        or draft["task_id"] != task_id
        or draft["status"] != "draft"
        or draft["request"]["agent_id"] != RESEARCH_AGENT_ID
    ):
        raise ValueError("Invalid saved research draft.")

    parent = draft["parent_snapshot"]
    proposal = draft["proposal"]
    parent_id = draft["request"]["parent_research_id"]
    if parent["research_id"] != parent_id:
        raise ValueError("Draft parent identity mismatch.")
    if proposal["strategy_name"] != parent["strategy_name"]:
        raise ValueError("Draft changed the selected strategy.")

    _authorize()
    candidate = revise_research_candidate(
        parent_research_id=parent_id,
        strategy_name=proposal["strategy_name"],
        hypothesis=proposal["hypothesis"],
        revision_reason=proposal["rationale"],
        request_key=f"capital-revision:{task_id}",
        proposed_by=RESEARCH_AGENT_ID,
        expected_parent_snapshot=parent,
    )

    return {
        "task_id": task_id,
        "status": "revision_created",
        "research_id": candidate.research_id,
        "candidate": candidate.to_dict(),
        "promotion_authorized": False,
        "live_capital_authorized": False,
    }
