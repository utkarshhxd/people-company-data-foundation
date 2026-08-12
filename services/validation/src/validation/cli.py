import argparse
import json
import logging
import sys

from common.db import connect
from psycopg.rows import dict_row

from validation.pipeline import BatchNotValidatable, validate_batch


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate",
        description="Validate a batch's attribute observations and summarize each record.",
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument(
        "--show", type=int, metavar="N",
        help="print the first N failing judgements",
    )
    return parser


def print_failures(batch_id: str, limit: int) -> None:
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT v.scope, v.severity, v.rule_id,
                   coalesce(v.canonical_field, '-') AS canonical_field,
                   coalesce(o.raw_value, '')        AS raw_value,
                   coalesce(v.message, '')          AS message
            FROM validation_result v
            LEFT JOIN attribute_observation o ON o.observation_id = v.observation_id
            WHERE v.batch_id = %s AND v.outcome = 'fail'
            ORDER BY (v.severity = 'error') DESC, v.rule_id, v.record_id
            LIMIT %s
            """,
            (batch_id, limit),
        )
        rows = cur.fetchall()

    if not rows:
        print("\nno failing judgements")
        return
    print(f"\n{'SEVERITY'.ljust(9)} {'RULE'.ljust(30)} {'FIELD'.ljust(20)} "
          f"{'VALUE'.ljust(28)} MESSAGE")
    for row in rows:
        print(
            f"{row['severity'].ljust(9)} {row['rule_id'].ljust(30)} "
            f"{row['canonical_field'].ljust(20)} "
            f"{row['raw_value'][:27].ljust(28)} {row['message']}"
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    try:
        result = validate_batch(args.batch_id)
    except BatchNotValidatable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        f"batch {result.batch_id}: {result.records} record(s), "
        f"{result.judgements} judgement(s) {json.dumps(result.counts)}"
    )
    if result.failures:
        print("\nfailing rules:")
        for row in result.failures:
            print(f"  {row['severity'].ljust(8)} {row['rule_id'].ljust(32)} "
                  f"{row['failures']}")
    if args.show:
        print_failures(args.batch_id, args.show)
    return 0


if __name__ == "__main__":
    sys.exit(main())
