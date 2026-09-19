"""Interactive operator confirmation for the paper experiment factory.

Not exposed through the agent API. Terminal access is assumed trusted.
This is not protection against arbitrary code running as the operator.
"""
from datetime import datetime, timezone
import json
import os
import pwd
import re
import sys

from app.capital.experiment_factory_packet import packet_digest


def confirm_factory_packet(packet):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise PermissionError("Factory approval requires an operator terminal.")

    payload = packet["payload"]
    expected = packet["sha256"]
    if (
        not isinstance(expected, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected) is None
        or packet_digest(payload) != expected
    ):
        raise ValueError("Review packet digest does not match its contents.")

    if payload.get("action") != "create_inactive_paper_experiment":
        raise ValueError("Unsupported approval action.")

    review = payload["review"]
    if (
        review.get("eligible_for_operator_review") is not True
        or review.get("human_approval_required") is not True
        or any(
            review.get(field) is not False
            for field in (
                "creation_authorized",
                "execution_authorized",
                "live_capital_authorized",
            )
        )
    ):
        raise ValueError("Review is not eligible for operator confirmation.")

    operator = pwd.getpwuid(os.getuid()).pw_name
    print(json.dumps(payload, indent=2, sort_keys=True))
    print("\nOperator account:", operator)
    print("Packet SHA256:", expected)
    print("This approves creation of an inactive paper experiment only.")
    print("It does not approve activation, trading, or live capital.")

    entered = input(f"\nType APPROVE {expected}\n> ")
    if entered != f"APPROVE {expected}":
        raise PermissionError("Approval cancelled; no creation authorized.")

    return {
        "request_key": payload["request"]["request_key"],
        "packet_sha256": expected,
        "operator": operator,
        "operator_uid": os.getuid(),
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "action": payload["action"],
    }


def main():
    import argparse

    from app.capital.experiment_factory_packet import prepare_factory_packet
    from app.capital.experiment_factory_creation import create_confirmed_experiment

    parser = argparse.ArgumentParser(
        description="Review or create an inactive paper experiment."
    )
    parser.add_argument("request_key")
    parser.add_argument(
        "--create",
        action="store_true",
        help="Require interactive confirmation, then create atomically.",
    )
    args = parser.parse_args()

    packet = prepare_factory_packet(args.request_key)

    if not args.create:
        print(json.dumps(packet, indent=2, sort_keys=True))
        print("REVIEW ONLY: no approval or creation recorded.")
        return

    confirmation = confirm_factory_packet(packet)
    result = create_confirmed_experiment(confirmation)
    print(json.dumps(result, indent=2, sort_keys=True))
    print("Created planned experiment with an inactive paper portfolio.")
    print("Execution remains disabled. Live capital is not authorized.")


if __name__ == "__main__":
    main()
