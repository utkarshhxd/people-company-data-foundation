import argparse
import json
import logging
import sys

from common.db import connect

from resolution import repository
from resolution.pipeline import (
    BatchNotResolvable,
    accept_candidate,
    reject_candidate,
    resolve_batch,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="resolve",
        description="Resolve records to real-world Person and Company entities.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="resolve a batch")
    run.add_argument("--batch-id", required=True)

    show = sub.add_parser("show", help="everything known about one entity, and who said it")
    show.add_argument("--entity-id", required=True)

    candidates = sub.add_parser("candidates", help="matches awaiting a human decision")
    candidates.add_argument("--status", default="open",
                            choices=["open", "accepted", "rejected"])
    candidates.add_argument("--limit", type=int, default=50)

    for name, help_text in (
        ("accept", "confirm the match and merge the two entities"),
        ("reject", "confirm they are different; both entities stay separate"),
    ):
        action = sub.add_parser(name, help=help_text)
        action.add_argument("--candidate-id", required=True)
        action.add_argument("--reviewed-by", required=True)
        action.add_argument("--note")

    return parser


def cmd_run(args) -> int:
    try:
        result = resolve_batch(args.batch_id)
    except BatchNotResolvable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"batch {result.batch_id}: {result.records} record(s) -> "
        f"{result.linked} linked to existing, {result.created} new entities, "
        f"{result.candidates} awaiting review {json.dumps(result.counts)}"
    )
    return 0


def cmd_show(args) -> int:
    with connect() as conn:
        entity_id = repository.resolve_entity_id(conn, args.entity_id)
        if entity_id is None:
            print(f"error: no entity {args.entity_id}", file=sys.stderr)
            return 2
        if entity_id != args.entity_id:
            print(f"note: {args.entity_id} was merged into {entity_id}\n")
        sources = repository.entity_sources(conn, entity_id)
        profile = repository.entity_profile(conn, entity_id)

    print(f"entity {entity_id}")
    print(f"\nobserved by {len(sources)} record(s):")
    for row in sources:
        print(f"  {row['source_name'].ljust(14)} {row['file_name'][:28].ljust(29)} "
              f"{row['match_method'].ljust(16)} {row['match_confidence']} "
              f"({row['match_status']})")

    print("\nattributes, by the source that reported them:")
    current = None
    for row in profile:
        if row["canonical_field"] != current:
            current = row["canonical_field"]
            print(f"  {current}")
        print(f"      {row['source_name'].ljust(14)} {row['normalized_value']}")
    return 0


def cmd_candidates(args) -> int:
    with connect() as conn:
        rows = repository.list_candidates(conn, args.status, args.limit)
    if not rows:
        print(f"no {args.status} match candidates")
        return 0
    print(f"{'CANDIDATE ID'.ljust(38)} {'CONF'.ljust(6)} {'METHOD'.ljust(16)} "
          f"{'SOURCE'.ljust(14)} ROW")
    for row in rows:
        print(f"{str(row['candidate_id']).ljust(38)} "
              f"{str(row['match_confidence']).ljust(6)} "
              f"{row['match_method'].ljust(16)} "
              f"{row['source_name'][:13].ljust(14)} {row['row_number']}")
    return 0


def cmd_decide(args, accept: bool) -> int:
    try:
        if accept:
            result = accept_candidate(args.candidate_id, args.reviewed_by, args.note)
            if result.get("already_merged"):
                print(f"already merged into {result['entity_id']}")
            else:
                print(f"merged {result['merged_entity_id']} into "
                      f"{result['surviving_entity_id']} "
                      f"({result['records_moved']} record(s), "
                      f"{result['keys_moved']} key(s) moved)")
                print("the absorbed id still resolves — it is a tombstone, not a delete")
        else:
            reject_candidate(args.candidate_id, args.reviewed_by, args.note)
            print("rejected; both entities remain separate")
    except BatchNotResolvable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    if args.command == "run":
        return cmd_run(args)
    if args.command == "show":
        return cmd_show(args)
    if args.command == "candidates":
        return cmd_candidates(args)
    return cmd_decide(args, accept=args.command == "accept")


if __name__ == "__main__":
    sys.exit(main())
