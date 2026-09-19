"""Operator confirmation tests; simulated terminal, no database access."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.capital import experiment_factory_operator as operator
from app.capital.experiment_factory_packet import packet_digest


class FactoryOperatorTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "schema_version": 1,
            "action": "create_inactive_paper_experiment",
            "request": {"request_key": "agent:request-1"},
            "review": {
                "eligible_for_operator_review": True,
                "human_approval_required": True,
                "creation_authorized": False,
                "execution_authorized": False,
                "live_capital_authorized": False,
            },
        }
        self.packet = {
            "payload": self.payload,
            "sha256": packet_digest(self.payload),
        }
        self.stdin = self.mock("sys.stdin.isatty", return_value=True)
        self.stdout = self.mock("sys.stdout.isatty", return_value=True)
        self.mock("os.getuid", return_value=1234)
        self.mock(
            "pwd.getpwuid", return_value=SimpleNamespace(pw_name="test-operator")
        )
        handle = patch("builtins.input")
        self.enter = handle.start()
        self.addCleanup(handle.stop)
        self.enter.return_value = f"APPROVE {self.packet['sha256']}"
        handle = patch("builtins.print")
        self.printed = handle.start()
        self.addCleanup(handle.stop)

    def mock(self, name, **kwargs):
        handle = patch(
            f"app.capital.experiment_factory_operator.{name}", **kwargs
        )
        result = handle.start()
        self.addCleanup(handle.stop)
        return result

    def reseal(self):
        self.packet["sha256"] = packet_digest(self.payload)

    def test_exact_confirmation_records_operator_and_digest(self):
        result = operator.confirm_factory_packet(self.packet)
        self.assertEqual(result["request_key"], "agent:request-1")
        self.assertEqual(result["packet_sha256"], self.packet["sha256"])
        self.assertEqual(result["operator"], "test-operator")
        self.assertEqual(result["operator_uid"], 1234)
        self.assertEqual(result["action"], "create_inactive_paper_experiment")
        self.assertTrue(result["confirmed_at"])
        self.enter.assert_called_once()
        displayed = "\n".join(
            str(value)
            for call in self.printed.call_args_list
            for value in call.args
        )
        self.assertIn("agent:request-1", displayed)
        self.assertIn(self.packet["sha256"], displayed)

    def test_redirected_input_is_rejected(self):
        self.stdin.return_value = False
        with self.assertRaises(PermissionError):
            operator.confirm_factory_packet(self.packet)
        self.enter.assert_not_called()

    def test_redirected_output_is_rejected(self):
        self.stdout.return_value = False
        with self.assertRaises(PermissionError):
            operator.confirm_factory_packet(self.packet)
        self.enter.assert_not_called()

    def test_changed_packet_is_rejected_before_prompt(self):
        self.payload["request"]["request_key"] = "agent:other"
        with self.assertRaisesRegex(ValueError, "digest"):
            operator.confirm_factory_packet(self.packet)
        self.enter.assert_not_called()

    def test_malformed_digest_is_rejected(self):
        self.packet["sha256"] = "invalid"
        with self.assertRaisesRegex(ValueError, "digest"):
            operator.confirm_factory_packet(self.packet)
        self.enter.assert_not_called()

    def test_wrong_action_is_rejected(self):
        self.payload["action"] = "enable_live_trading"
        self.reseal()
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            operator.confirm_factory_packet(self.packet)
        self.enter.assert_not_called()

    def test_blocked_review_is_rejected(self):
        self.payload["review"]["eligible_for_operator_review"] = False
        self.reseal()
        with self.assertRaisesRegex(ValueError, "not eligible"):
            operator.confirm_factory_packet(self.packet)
        self.enter.assert_not_called()

    def test_authority_escalation_is_rejected(self):
        self.payload["review"]["live_capital_authorized"] = True
        self.reseal()
        with self.assertRaisesRegex(ValueError, "not eligible"):
            operator.confirm_factory_packet(self.packet)
        self.enter.assert_not_called()

    def test_removed_human_approval_is_rejected(self):
        self.payload["review"]["human_approval_required"] = False
        self.reseal()
        with self.assertRaisesRegex(ValueError, "not eligible"):
            operator.confirm_factory_packet(self.packet)
        self.enter.assert_not_called()

    def test_inexact_confirmation_is_rejected(self):
        for entered in ("", "yes", "APPROVE", f"APPROVE {'0' * 64}"):
            with self.subTest(entered=entered):
                self.enter.return_value = entered
                with self.assertRaises(PermissionError):
                    operator.confirm_factory_packet(self.packet)


if __name__ == "__main__":
    unittest.main()
