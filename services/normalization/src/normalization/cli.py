import argparse
import json
import sys

from common.db import connect
from common.logging import configure
from psycopg.rows import dict_row

from normalization.pipeline import BatchNotNormalizable, normalize_batch


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="normalize",
        description="Normalize a batch's records into attribute observations.",
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument(
        "--show", type=int, metavar="N",
        help="print the first N observations of the batch",
    )
    return parser


def print_observations(batch_id: str, limit: int) -> None:
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT source_column, coalesce(canonical_field, '-') AS canonical_field,
                   value_index, raw_value, coalesce(normalized_value, '') AS normalized_value,
                   normalization_method
            FROM attribute_observation
            WHERE batch_id = %s
            ORDER BY record_id, source_column, value_index
            LIMIT %s
            """,
            (batch_id, limit),
        )
        rows = cur.fetchall()

    if not rows:
        return
    print(f"\n{'SOURCE COLUMN'.ljust(18)} {'CANONICAL'.ljust(20)} "
          f"{'RAW'.ljust(34)} {'NORMALIZED'.ljust(34)} METHOD")
    for row in rows:
        marker = f"[{row['value_index']}]" if row["value_index"] else ""
        print(
            f"{(row['source_column'] + marker).ljust(18)} "
            f"{row['canonical_field'].ljust(20)} "
            f"{row['raw_value'][:33].ljust(34)} "
            f"{row['normalized_value'][:33].ljust(34)} "
            f"{row['normalization_method']}"
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure("normalization")

    try:
        result = normalize_batch(args.batch_id)
    except BatchNotNormalizable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        f"batch {result.batch_id}: {result.records} record(s) -> "
        f"{result.observations} observation(s) {json.dumps(result.counts)}"
    )
    if args.show:
        print_observations(args.batch_id, args.show)
    return 0


if __name__ == "__main__":
    sys.exit(main())
