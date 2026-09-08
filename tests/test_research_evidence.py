import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.capital import research_evidence as evidence
from app.capital import research_store as store
from app.capital.research_models import (
    ResearchCandidate, ResearchStatus, ResearchVerdict,
)
from app.capital.research_revision import revise_research_candidate


def candidate():
    return ResearchCandidate(
        research_id="test", strategy_name="test_strategy",
        display_name="Test", hypothesis="Original hypothesis",
        description="Test", market_regime="any", asset_universe=["SPY"],
        data_requirements=["prices"], risk_thesis="Test",
        success_criteria=["Test"], status=ResearchStatus.REJECTED,
        verdict=ResearchVerdict.UNPROMISING, proposed_by="test",
        created_at="test", updated_at="test",
    )


class EvidenceTests(unittest.TestCase):
    def test_roundtrip_duplicate_and_version(self):
        item = candidate()
        packet = {"symbol": "SPY", "report_sha256": "abc"}
        before = item.to_dict()
        with self.assertRaises(ValueError):
            evidence.add_attachment(item, packet, 2, "test", "comparison")
        self.assertEqual(item.to_dict(), before)
        evidence.add_attachment(item, packet, 1, "test", "comparison")
        restored = ResearchCandidate.from_dict(item.to_dict())
        self.assertEqual(len(restored.evaluation_attachments), 1)
        self.assertEqual(restored.status, item.status)
        self.assertEqual(restored.verdict, item.verdict)
        with self.assertRaises(ValueError):
            evidence.add_attachment(restored, packet, 1, "test", "comparison")
        with self.assertRaises(ValueError):
            evidence.add_attachment(
                restored, {"symbol": "BTC", "report_sha256": "def"},
                1, "test", "comparison",
            )

    def test_legacy_and_revision(self):
        item = candidate()
        legacy = item.to_dict()
        legacy.pop("evaluation_attachments")
        self.assertEqual(
            ResearchCandidate.from_dict(legacy).evaluation_attachments, []
        )
        item.evaluation_attachments = [{"report_sha256": "abc"}]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with (
                patch.object(store, "STATE_DIRECTORY", directory),
                patch.object(store, "RESEARCH_STATE_FILE", directory / "state.json"),
                patch.object(store, "RESEARCH_LOCK_FILE", directory / "state.lock"),
            ):
                with store.locked_research_state(write=True) as state:
                    state["candidates"]["test"] = item.to_dict()
                child = revise_research_candidate(
                    parent_research_id="test", strategy_name="new_strategy",
                    hypothesis="Different hypothesis", revision_reason="Test",
                )
                self.assertEqual(child.evaluation_attachments, [])
                with store.locked_research_state() as state:
                    self.assertEqual(
                        state["candidates"]["test"], item.to_dict()
                    )

    def test_incomplete_and_tampered_packets(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name in evidence.ARTIFACTS:
                (directory / name).write_text("{}")
            result = {
                "status": "completed", "designation": "development",
                "promotion_authorized": False,
                "artifacts_sha256": {},
            }
            (directory / "result.json").write_text(json.dumps(result))
            with self.assertRaisesRegex(ValueError, "hashes"):
                evidence.inspect_packet(directory)
            result["status"] = "failed"
            (directory / "result.json").write_text(json.dumps(result))
            with self.assertRaisesRegex(ValueError, "incomplete"):
                evidence.inspect_packet(directory)


if __name__ == "__main__":
    unittest.main()
