"""Review CLI for quarantined records.

Routing into quarantine is automatic; getting out of it is not. Every exit is a
named person's decision, recorded with a reason — which is the point of the
stage. A record can sit here indefinitely without anything being lost.
"""

import argparse
import json
import logging
import sys

from common.db import connect

from validation import repository
from validation.quarantine import (
    ACTION_REJECTED,
    ACTION_RELEASED,
    ACTION_REOPENED,
    review,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quarantine",
        description="Review records held back from entity resolution.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="list quarantined records")
    listing.add_argument("--status", choices=["open", "released", "rejected", "resolved"])
    listing.add_argument("--entity-type", choices=["person", "company"])
    listing.add_argument("--limit", type=int, default=50)

    show = sub.add_parser("show", help="why one record is quarantined, and its history")
    show.add_argument("--record-id", required=True)

    for name, help_text in (
        ("release", "use this record despite its failures"),
        ("reject", "confirm this record is unusable (it is kept, never resolved)"),
        ("reopen", "put a decided record back in the review queue"),
    ):
        action = sub.add_parser(name, help=help_text)
        action.add_argument("--record-id", required=True)
        action.add_argument("--reviewed-by", required=True)
        action.add_argument("--note", help="why — recorded permanently")

    sub.add_parser("stats", help="counts by disposition")
    return parser


def cmd_list(args) -> int:
    with connect() as conn:
        rows = repository.list_quarantine(conn, args.status, args.entity_type, args.limit)
    if not rows:
        print("no quarantined records match")
        return 0
    print(f"{'RECORD ID'.ljust(38)} {'STATUS'.ljust(9)} {'SOURCE'.ljust(14)} "
          f"{'ROW'.ljust(5)} REASONS")
    for row in rows:
        print(
            f"{str(row['record_id']).ljust(38)} {row['status'].ljust(9)} "
            f"{row['source_name'][:13].ljust(14)} {str(row['row_number']).ljust(5)} "
            f"{', '.join(row['reason_codes'])}"
        )
    return 0


def cmd_show(args) -> int:
    with connect() as conn:
        item = repository.get_quarantine_item(conn, args.record_id)
        if item is None:
            print(f"error: {args.record_id} is not quarantined", file=sys.stderr)
            return 2
        failures = repository.record_failures(conn, args.record_id)
        history = repository.quarantine_history(conn, args.record_id)

    print(f"record   {item['record_id']}")
    print(f"status   {item['status']}")
    print(f"source   {item['source_name']} ({item['file_name']})")
    print(f"reasons  {', '.join(item['reason_codes']) or '-'}")
    if item["reviewed_by"]:
        print(f"reviewed {item['reviewed_by']} — {item['review_note'] or 'no note'}")

    print("\nfailures:")
    for row in failures:
        value = f" [{row['raw_value'][:30]}]" if row["raw_value"] else ""
        print(f"  {row['severity'].ljust(8)} {row['rule_id'].ljust(30)}"
              f"{value} {row['message']}")

    print("\nhistory:")
    for row in history:
        arrow = f"{row['from_status'] or '-'} -> {row['to_status']}"
        print(f"  {row['created_at']:%Y-%m-%d %H:%M} {row['action'].ljust(16)} "
              f"{arrow.ljust(22)} {row['actor']}  {row['note'] or ''}")
    return 0


def cmd_decide(args, action: str) -> int:
    with connect() as conn:
        item = repository.get_quarantine_item(conn, args.record_id)
        if item is None:
            print(f"error: {args.record_id} is not quarantined", file=sys.stderr)
            return 2
        try:
            transition = review(action, item, args.reviewed_by, args.note)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

        repository.record_review(
            conn, str(item["quarantine_id"]), args.record_id, transition.to_status,
            transition.action, transition.from_status, transition.reason_codes,
            args.reviewed_by, args.note,
        )
        conn.commit()

    print(f"{args.record_id}: {transition.from_status} -> {transition.to_status} "
          f"by {args.reviewed_by}")
    if transition.to_status == "released":
        print("this record is now visible to entity resolution")
    return 0


def cmd_stats(_args) -> int:
    with connect() as conn:
        counts = repository.quarantine_counts(conn, None)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM resolvable_record")
            resolvable = cur.fetchone()[0]
    print(json.dumps(counts))
    print(f"records visible to entity resolution: {resolvable}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    if args.command == "list":
        return cmd_list(args)
    if args.command == "show":
        return cmd_show(args)
    if args.command == "release":
        return cmd_decide(args, ACTION_RELEASED)
    if args.command == "reject":
        return cmd_decide(args, ACTION_REJECTED)
    if args.command == "reopen":
        return cmd_decide(args, ACTION_REOPENED)
    return cmd_stats(args)


if __name__ == "__main__":
    sys.exit(main())
