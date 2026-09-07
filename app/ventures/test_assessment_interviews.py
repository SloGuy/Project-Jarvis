from copy import deepcopy
from unittest.mock import patch

from app.ventures.assessment_interviews import build_interview_input
from app.ventures import assessment_store as store


def run_tests():
    record = {
        "source_url": "https://www.youtube.com/watch?v=1yR4C9kFG0I",
        "collected_at": "2026-09-07T00:00:00+00:00",
        "language": "en",
        "auto_generated": True,
        "segments": [
            {
                "text": f"Caption {index}. " * 20,
                "start": float(index * 5),
                "duration": 5.0,
            }
            for index in range(100)
        ],
    }
    original = deepcopy(record)
    selected = build_interview_input(record)

    assert record == original
    assert 1 <= len(selected["claims"]) <= 6
    assert sum(
        len(claim["claim"]) for claim in selected["claims"]
    ) <= 3000
    assert all(
        claim["evidence_status"] == "unverified"
        and claim["evidence_quality"] == "low"
        and "&t=" in claim["source"]
        for claim in selected["claims"]
    )
    assert selected == build_interview_input(record)
    print("bounded_excerpts_provenance_and_preservation: PASS")

    research = {"opportunity_id": "test"}
    configuration = {"model": "test"}
    first = store.assessment_key(
        research_record=research,
        configuration=configuration,
        interview_inputs=[selected],
    )
    record["segments"][50]["text"] = "Changed caption."
    changed = build_interview_input(record)
    second = store.assessment_key(
        research_record=research,
        configuration=configuration,
        interview_inputs=[changed],
    )
    assert first != second
    print("transcript_change_invalidates_key: PASS")

    with patch.object(
        store, "current_interview_inputs", return_value=[selected]
    ):
        automatic = store.assessment_key(
            research_record=research,
            configuration=configuration,
        )
    assert automatic == first
    print("automatic_and_explicit_keys_match: PASS")

    record["segments"][0]["start"] = float("nan")
    try:
        build_interview_input(record)
    except ValueError:
        pass
    else:
        raise AssertionError("Nonfinite timestamp accepted.")
    print("invalid_timestamp_rejected: PASS")


if __name__ == "__main__":
    run_tests()
