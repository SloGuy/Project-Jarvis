"""Research model tests; no real model or network calls."""

import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from app.capital.autonomy_research_model import (
    MAX_RESPONSE_BYTES,
    ResearchModelError,
    propose_research,
)


class CapitalResearchModelTests(unittest.TestCase):
    def setUp(self):
        self.proposal = {
            "strategy_name": "mean_reversion_v2",
            "hypothesis": "A longer observation window improves estimation.",
            "rationale": "The previous attempt had too few completed trades.",
            "evidence_ids": ["attempt-1"],
            "next_question": "Does the effect persist after costs?",
        }
        self.network = patch(
            "app.capital.autonomy_research_model.urlopen"
        )
        self.urlopen = self.network.start()
        self.addCleanup(self.network.stop)
        self.response = MagicMock()
        self.urlopen.return_value.__enter__.return_value = self.response
        self.respond()

    def respond(self, proposal=None, **fields):
        envelope = {
            "done": True,
            "done_reason": "stop",
            "message": {
                "content": json.dumps(
                    self.proposal if proposal is None else proposal
                ),
            },
        }
        envelope.update(fields)
        self.response.read.return_value = json.dumps(envelope).encode()

    def run_model(self, **overrides):
        arguments = {
            "objective": "Choose the next research question.",
            "strategies": ["mean_reversion_v2"],
            "evidence": [{
                "id": "attempt-1",
                "summary": "Insufficient evidence: 15 completed trades.",
            }],
        }
        arguments.update(overrides)
        return propose_research(**arguments)

    def test_valid_proposal_is_returned(self):
        self.assertEqual(self.run_model(), self.proposal)

    def test_request_has_bounded_generation(self):
        self.run_model()
        args, kwargs = self.urlopen.call_args
        payload = json.loads(args[0].data)
        self.assertEqual(kwargs["timeout"], 120)
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["options"]["num_predict"], 2048)
        self.assertEqual(payload["options"]["num_ctx"], 8192)
        self.response.read.assert_called_once_with(MAX_RESPONSE_BYTES + 1)

    def test_unknown_evidence_is_rejected(self):
        self.proposal["evidence_ids"] = ["invented"]
        self.respond()
        with self.assertRaises(ResearchModelError):
            self.run_model()

    def test_duplicate_citations_are_rejected(self):
        self.proposal["evidence_ids"] = ["attempt-1", "attempt-1"]
        self.respond()
        with self.assertRaises(ResearchModelError):
            self.run_model()

    def test_unknown_strategy_is_rejected(self):
        self.proposal["strategy_name"] = "invented_strategy"
        self.respond()
        with self.assertRaises(ResearchModelError):
            self.run_model()

    def test_approval_field_is_rejected(self):
        self.proposal["promotion_authorized"] = True
        self.respond()
        with self.assertRaises(ResearchModelError):
            self.run_model()

    def test_empty_hypothesis_is_rejected(self):
        self.proposal["hypothesis"] = " "
        self.respond()
        with self.assertRaises(ResearchModelError):
            self.run_model()

    def test_empty_citations_are_allowed(self):
        self.proposal["evidence_ids"] = []
        self.respond()
        self.assertEqual(self.run_model()["evidence_ids"], [])

    def test_incomplete_response_is_rejected(self):
        self.respond(done=False)
        with self.assertRaises(ResearchModelError):
            self.run_model()

    def test_output_budget_exhaustion_is_rejected(self):
        self.respond(done_reason="length")
        with self.assertRaises(ResearchModelError):
            self.run_model()

    def test_oversized_response_is_rejected(self):
        self.response.read.return_value = b"x" * (MAX_RESPONSE_BYTES + 1)
        with self.assertRaises(ResearchModelError):
            self.run_model()

    def test_duplicate_json_fields_are_rejected(self):
        self.response.read.return_value = (
            b'{"done":true,"done":false,"message":{}}'
        )
        with self.assertRaises(ResearchModelError):
            self.run_model()

    def test_nonfinite_json_is_rejected(self):
        self.response.read.return_value = (
            b'{"done":true,"message":{"content":NaN}}'
        )
        with self.assertRaises(ResearchModelError):
            self.run_model()

    def test_network_failure_has_no_automatic_retry(self):
        self.urlopen.side_effect = URLError("Unavailable")
        with self.assertRaises(ResearchModelError):
            self.run_model()
        self.urlopen.assert_called_once()

    def test_invalid_inputs_never_call_model(self):
        invalid_inputs = (
            {"objective": ""},
            {"strategies": []},
            {"strategies": ["duplicate", "duplicate"]},
            {"evidence": [{"id": "missing-summary"}]},
            {"evidence": [
                {"id": "same", "summary": "One"},
                {"id": "same", "summary": "Two"},
            ]},
        )
        for arguments in invalid_inputs:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    self.run_model(**arguments)
        self.urlopen.assert_not_called()

    def test_prompt_budget_is_checked_before_request(self):
        evidence = [
            {"id": str(index), "summary": "x" * 4000}
            for index in range(5)
        ]
        with self.assertRaises(ValueError):
            self.run_model(evidence=evidence)
        self.urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
