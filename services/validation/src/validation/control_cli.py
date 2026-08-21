"""Stop validation, start it again, and see why it stopped.

Deliberately its own command rather than a flag on `validate`: this is an
operational decision about the pipeline, not an argument to a run. Both
directions are recorded with who did it, because "why is nothing loading"
answered by "somebody paused it last Tuesday and said why" is the whole reason
the table has a history.
"""

import argparse
import json
import logging
import sys

from common import control
from common.db import connect
from common.logging import configure

logger = logging.getLogger(__name__)

STAGE = "validation"


def _print_state(state: control.ControlState) -> None:
    if not state.paused:
        print(f"{state.stage}: running")
        return
    print(f"{state.stage}: PAUSED")
    print(f"  reason:     {state.reason or '(none recorded)'}")
    print(f"  paused by:  {state.changed_by}")
    print(f"  paused at:  {state.changed_at:%Y-%m-%d %H:%M:%SZ}")
    if state.evidence:
        print("  evidence:")
        for line in json.dumps(state.evidence, indent=2, default=str).split("\n"):
            print(f"    {line}")
    print()
    print("  Nothing has been lost. Records already validated kept their")
    print("  verdicts and their quarantine reasons; nothing further is being")
    print("  validated, and queued work is waiting in its topic.")
    print()
    print("  Resume with:")
    print("    docker compose run --rm validation \\")
    print("      validation-control resume --reviewed-by <you>")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validation-control",
        description="Stop and start the validation stage.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="is validation running, and if not, why not")

    pause = sub.add_parser("pause", help="stop validating until somebody resumes")
    pause.add_argument("--reason", required=True,
                       help="why, in a sentence the next person can act on")
    pause.add_argument("--reviewed-by", required=True, help="who is stopping it")

    resume = sub.add_parser("resume", help="start validating again")
    resume.add_argument("--reviewed-by", required=True, help="who is resuming it")
    resume.add_argument("--note", help="what was found, or what was changed")

    log = sub.add_parser("history", help="every stop and start, most recent first")
    log.add_argument("--limit", type=int, default=20)

    args = parser.parse_args(argv)
    configure("validation")

    with connect() as conn:
        if args.command == "status":
            _print_state(control.get(conn, STAGE))
            return 0

        if args.command == "pause":
            state = control.pause(
                conn, STAGE, args.reason, changed_by=args.reviewed_by
            )
            _print_state(state)
            return 0

        if args.command == "resume":
            before = control.get(conn, STAGE)
            if not before.paused:
                print("validation is already running; nothing to resume")
                return 0
            control.resume(conn, STAGE, changed_by=args.reviewed_by, note=args.note)
            print(f"validation resumed (was: {before.reason})")
            print("Queued work starts moving again on its own; files left in a")
            print("watched feed load on the next sweep.")
            return 0

        for event in control.history(conn, STAGE, args.limit):
            when = event["created_at"].strftime("%Y-%m-%d %H:%M:%SZ")
            print(f"{when}  {event['from_state']} -> {event['to_state']}  "
                  f"by {event['changed_by']}")
            if event["reason"]:
                print(f"    reason: {event['reason']}")
            if event["note"]:
                print(f"    note:   {event['note']}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
