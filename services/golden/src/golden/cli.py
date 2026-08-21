import argparse
import json
import sys

from common import lineage
from common.db import connect
from common.logging import configure

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

    explain = sub.add_parser(
        "explain", help="trace one field back to the source cells it came from"
    )
    explain.add_argument("--entity-id", required=True)
    explain.add_argument("--field", required=True)

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
        f"{result.refreshed} evidence-refreshed, {result.unchanged} unchanged, "
        f"{result.retired} retired {json.dumps(result.counts)}"
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


def cmd_explain(args) -> int:
    """The five dimensions side by side, never added together."""
    explanation = lineage.explain_value(args.entity_id, args.field)
    if explanation is None:
        print(f"error: nothing recorded for {args.field}", file=sys.stderr)
        return 2

    golden = explanation["golden"]
    print(f"entity {explanation['entity_id']}  field {args.field}")
    if golden:
        print(f"\ntrusted value : {golden['value']}")
        print(f"chosen by     : {golden['strategy']} "
              f"(confidence {golden['confidence']})")
        print(f"in force since: {golden['valid_from']:%Y-%m-%d %H:%M}")
        print(f"agreement     : {golden['supporting_sources']} source(s) agreed, "
              f"{golden['competing_values']} distinct value(s) competed")

    print("\nwhere it came from:")
    for row in explanation["contributions"]:
        marker = "->" if row["is_winning_record"] else ("~ " if row["agrees_with_golden"] else "  ")
        print(f"\n {marker} {row['source_name']}  {row['file_name']} row {row['row_number']}")
        print(f"      column {row['source_column']!r} held {row['raw_value']!r}")
        print(f"      normalized to {row['normalized_value']!r}")
        # Four separate judgements about four separate questions.
        print(f"      column interpreted : {row['mapping_method']} @ "
              f"{row['mapping_confidence']} ({row['mapping_status']})")
        print(f"      record validated   : {row['record_validation_status']}")
        print(f"      linked to entity   : {row['match_method']} @ "
              f"{row['match_confidence']} ({row['match_status']})")
        print(f"      vendor reliability : {row['source_reliability']}")
        for judgement in row["validation"]:
            if judgement["outcome"] == "fail":
                print(f"      ! {judgement['severity']} {judgement['rule_id']}: "
                      f"{judgement['message']}")

    if len(explanation["history"]) > 1:
        print("\npreviously:")
        for row in explanation["history"]:
            if not row["is_current"]:
                print(f"      {row['value']}  until {row['valid_to']:%Y-%m-%d %H:%M}")
    return 0


def cmd_stats(_args) -> int:
    with connect() as conn:
        print(json.dumps(repository.golden_counts(conn), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure("golden")

    if args.command == "build":
        return cmd_build(args)
    if args.command == "show":
        return cmd_show(args)
    if args.command == "history":
        return cmd_history(args)
    if args.command == "explain":
        return cmd_explain(args)
    return cmd_stats(args)


if __name__ == "__main__":
    sys.exit(main())
