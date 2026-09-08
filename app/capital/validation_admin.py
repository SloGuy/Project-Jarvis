"""Operator commands for validation registration and status."""
import argparse
import json
from pathlib import Path

from app.capital.validation_registry import register_plan, get_plan, locked_state


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register")
    register.add_argument("--draft", type=Path, required=True)
    status = commands.add_parser("status")
    status.add_argument("--plan-id")
    args = parser.parse_args()
    if args.command == "register":
        row = register_plan(json.loads(args.draft.read_text()))
        result = {
            "plan_id": row["plan_id"], "status": row["status"],
            "registered_sha256": row["registered_sha256"],
        }
    elif args.plan_id:
        row = get_plan(args.plan_id)
        result = {
            "plan_id": row["plan_id"], "status": row["status"],
            "registered_sha256": row["registered_sha256"],
            "research": row["envelope"]["plan"]["research"],
            "history": row["history"],
        }
    else:
        with locked_state() as state:
            result = [
                {
                    "plan_id": row["plan_id"], "status": row["status"],
                    "research_id": row["envelope"]["plan"]["research"]["research_id"],
                    "start": row["envelope"]["plan"]["start"],
                    "end_exclusive": row["envelope"]["plan"]["end_exclusive"],
                }
                for row in state["plans"].values()
            ]
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
