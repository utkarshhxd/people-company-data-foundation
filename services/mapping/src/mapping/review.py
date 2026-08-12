"""Human review of ambiguous mappings.

Corrections are stored against the source_schema, so re-ingesting the same
layout reuses the decision instead of discarding it.
"""

import argparse
import json
import sys

from common.canonical import fields_for
from common.db import connect

from mapping import repository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review-mappings", description="List and correct column mappings."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list", help="list mappings")
    list_cmd.add_argument("--status", help="filter by mapping_status, e.g. needs_review")
    list_cmd.add_argument("--source-schema-id")
    list_cmd.add_argument("--json", action="store_true")

    set_cmd = sub.add_parser("set", help="record a human decision for one mapping")
    set_cmd.add_argument("--mapping-id", required=True)
    set_cmd.add_argument(
        "--canonical-field",
        help="canonical field to map to; omit together with --reject to unmap",
    )
    set_cmd.add_argument("--reject", action="store_true", help="mark as rejected/unmapped")
    set_cmd.add_argument("--reviewed-by", default="cli")

    fields_cmd = sub.add_parser("fields", help="list valid canonical fields")
    fields_cmd.add_argument("--entity-type", required=True, choices=["person", "company"])

    return parser


def _cmd_list(args) -> int:
    with connect() as conn:
        rows = repository.list_mappings(
            conn, source_schema_id=args.source_schema_id, status=args.status
        )
    if args.json:
        print(json.dumps([{**r, "mapping_id": str(r["mapping_id"]),
                           "mapping_confidence": float(r["mapping_confidence"])}
                          for r in rows], indent=2, default=str))
        return 0
    if not rows:
        print("no mappings match")
        return 0
    for row in rows:
        print(
            f"{row['mapping_id']}  {row['source_name']}/{row['source_column']} -> "
            f"{row['canonical_field'] or '-'}  [{row['mapping_method']} "
            f"{row['mapping_confidence']} {row['mapping_status']}]"
        )
        evidence = row["evidence"] or {}
        if "collision" in evidence:
            competing = ", ".join(evidence["collision"]["competing_columns"])
            print(f"    collision on {evidence['collision']['canonical_field']}: {competing}")
    return 0


def _cmd_set(args) -> int:
    if args.reject:
        canonical_field, status = None, "rejected"
    elif args.canonical_field:
        canonical_field, status = args.canonical_field, "approved"
    else:
        print("error: pass --canonical-field or --reject", file=sys.stderr)
        return 2

    with connect() as conn:
        if canonical_field is not None:
            valid = {f.name for f in fields_for("person")} | {
                f.name for f in fields_for("company")
            }
            if canonical_field not in valid:
                print(f"error: {canonical_field!r} is not a canonical field", file=sys.stderr)
                return 2
        updated = repository.set_mapping(
            conn, args.mapping_id, canonical_field, status, args.reviewed_by
        )
        conn.commit()

    if not updated:
        print(f"error: no mapping {args.mapping_id}", file=sys.stderr)
        return 2
    print(f"{args.mapping_id} -> {canonical_field or '-'} ({status})")
    return 0


def _cmd_fields(args) -> int:
    for spec in fields_for(args.entity_type):
        print(f"{spec.name.ljust(22)} {spec.description}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return {"list": _cmd_list, "set": _cmd_set, "fields": _cmd_fields}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
