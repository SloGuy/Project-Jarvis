"""Operator command orchestration tests; no database or real approval."""
import unittest
from unittest.mock import patch

from app.capital.experiment_factory_operator import main


class FactoryOperatorCommandTests(unittest.TestCase):
    def setUp(self):
        self.prepare = self.mock(
            "app.capital.experiment_factory_packet.prepare_factory_packet"
        )
        self.confirm = self.mock(
            "app.capital.experiment_factory_operator.confirm_factory_packet"
        )
        self.create = self.mock(
            "app.capital.experiment_factory_creation.create_confirmed_experiment"
        )
        self.printed = self.mock("builtins.print")
        self.packet = {"payload": {"test": True}, "sha256": "a" * 64}
        self.confirmation = {"test_confirmation": True}
        self.prepare.return_value = self.packet
        self.confirm.return_value = self.confirmation
        self.create.return_value = {"status": "created"}
        self.order = []

    def mock(self, target):
        handle = patch(target)
        result = handle.start()
        self.addCleanup(handle.stop)
        return result

    def run_command(self, *options):
        with patch("sys.argv", ["factory", "agent:request-1", *options]):
            main()

    def test_default_is_read_only(self):
        self.run_command()
        self.prepare.assert_called_once_with("agent:request-1")
        self.confirm.assert_not_called()
        self.create.assert_not_called()

    def test_creation_requires_review_then_confirmation(self):
        def prepare(key):
            self.order.append("review")
            return self.packet

        def confirm(packet):
            self.assertIs(packet, self.packet)
            self.order.append("confirm")
            return self.confirmation

        def create(confirmation):
            self.assertIs(confirmation, self.confirmation)
            self.order.append("create")
            return {"status": "created"}

        self.prepare.side_effect = prepare
        self.confirm.side_effect = confirm
        self.create.side_effect = create
        self.run_command("--create")
        self.assertEqual(self.order, ["review", "confirm", "create"])

    def test_blocked_review_never_prompts_or_creates(self):
        self.prepare.side_effect = ValueError("Validation blocked")
        with self.assertRaisesRegex(ValueError, "Validation blocked"):
            self.run_command("--create")
        self.confirm.assert_not_called()
        self.create.assert_not_called()

    def test_cancelled_confirmation_never_creates(self):
        self.confirm.side_effect = PermissionError("Cancelled")
        with self.assertRaises(PermissionError):
            self.run_command("--create")
        self.create.assert_not_called()

    def test_creation_failure_does_not_report_success(self):
        self.create.side_effect = ValueError("Evidence changed")
        with self.assertRaisesRegex(ValueError, "Evidence changed"):
            self.run_command("--create")
        messages = [
            str(value)
            for call in self.printed.call_args_list
            for value in call.args
        ]
        self.assertFalse(any(
            "Created planned experiment" in message for message in messages
        ))


if __name__ == "__main__":
    unittest.main()
