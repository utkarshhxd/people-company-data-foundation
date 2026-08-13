import argparse
import json
import logging
import sys

from common.db import connect

from golden import repository
from golden.pipeline import NothingToBuild, build_batch, build_one


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="golden",
        description="Build and inspect the trusted canonical record per entity.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="recompute the golden record")
    target = build.add_mutually_exclusive_group(required=True)
    target.add_argument("--batch-id")
    target.add_argument("--entity-id")

    show = sub.add_parser("show", help="the trusted values, and what each one beat")
    show.add_argument("--entity-id", required=True)

    history = sub.add_parser("history", help="every value a field has ever held")
    history.add_argument("--entity-id", required=True)
    history.add_argument("--field")

    sub.add_parser("stats", help="counts across all entities")
    return parser


def cmd_build(args) -> int:
    try:
        result = (
            build_batch(args.batch_id) if args.batch_id else build_one(args.entity_id)
        )
    except NothingToBuild as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"{result.entities} entity(ies): {result.written} value(s) written, "
        f"{result.unchanged} unchanged, {result.retired} retired "
        f"{json.dumps(result.counts)}"
    )
    return 0


def cmd_show(args) -> int:
    with connect() as conn:
        rows = repository.golden_for_entity(conn, args.entity_id)
    if not rows:
        print("no golden record; run `golden build --entity-id <id>` first")
        return 0

    print(f"entity {args.entity_id}\n")
    print(f"{'FIELD'.ljust(20)} {'VALUE'.ljust(34)} {'CONF'.ljust(6)} "
          f"{'WON BY'.ljust(20)} SOURCE")
    for row in rows:
        print(f"{row['canonical_field'].ljust(20)} {row['value'][:33].ljust(34)} "
              f"{str(row['confidence']).ljust(6)} {row['strategy'].ljust(20)} "
              f"{row['source_name']}")
        for alt in row["evidence"].get("alternatives", []):
            print(f"{''.ljust(20)}   rejected: {alt['value'][:30]} "
                  f"({', '.join(alt['sources'])})")
    return 0


def cmd_history(args) -> int:
    with connect() as conn:
        rows = repository.history_for_entity(conn, args.entity_id, args.field)
    if not rows:
        print("no history")
        return 0
    print(f"{'FIELD'.ljust(20)} {'VALUE'.ljust(30)} {'FROM'.ljust(17)} "
          f"{'TO'.ljust(17)} SOURCE")
    for row in rows:
        until = "current" if row["is_current"] else f"{row['valid_to']:%Y-%m-%d %H:%M}"
        print(f"{row['canonical_field'].ljust(20)} {row['value'][:29].ljust(30)} "
              f"{row['valid_from']:%Y-%m-%d %H:%M} {until.ljust(17)} "
              f"{row['source_name']}")
    return 0


def cmd_stats(_args) -> int:
    with connect() as conn:
        print(json.dumps(repository.golden_counts(conn), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    if args.command == "build":
        return cmd_build(args)
    if args.command == "show":
        return cmd_show(args)
    if args.command == "history":
        return cmd_history(args)
    return cmd_stats(args)


if __name__ == "__main__":
    sys.exit(main())
