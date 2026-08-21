import argparse
import json
import sys

from common.db import connect
from common.logging import configure

from mapping import repository
from mapping.pipeline import BatchNotMappable, map_batch


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="map-schema", description="Map a batch's source columns to canonical fields."
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument(
        "--show", action="store_true", help="print the resulting mappings as a table"
    )
    return parser


def print_mappings(source_schema_id: str) -> None:
    with connect() as conn:
        rows = repository.list_mappings(conn, source_schema_id=source_schema_id)
    if not rows:
        return
    width = max(len(r["source_column"]) for r in rows)
    print(f"\n{'SOURCE COLUMN'.ljust(width)}  {'CANONICAL'.ljust(20)} "
          f"{'METHOD'.ljust(15)} {'CONF'.ljust(6)} STATUS")
    for row in rows:
        print(
            f"{row['source_column'].ljust(width)}  "
            f"{(row['canonical_field'] or '-').ljust(20)} "
            f"{row['mapping_method'].ljust(15)} "
            f"{str(row['mapping_confidence']).ljust(6)} "
            f"{row['mapping_status']}"
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure("mapping")

    try:
        result = map_batch(args.batch_id)
    except BatchNotMappable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        f"source_schema {result.source_schema_id} "
        f"({'reused' if result.reused else 'new'}): {json.dumps(result.counts)}"
    )
    if args.show:
        print_mappings(result.source_schema_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
