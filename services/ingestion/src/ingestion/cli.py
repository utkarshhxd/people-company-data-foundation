import argparse
import logging
import sys
from pathlib import Path

from ingestion.pipeline import ReingestBlocked, ingest
from ingestion.readers import CSV_SUFFIXES, DEFAULT_BATCH_SIZE, UnsupportedFileType


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ingest", description="Ingest a CSV/Excel file as raw source records."
    )
    parser.add_argument("path", type=Path, help="path to the CSV/Excel file")
    parser.add_argument("--entity-type", required=True, choices=["person", "company"])
    parser.add_argument("--source-name", required=True, help="logical feed/vendor name")
    parser.add_argument(
        "--source-type",
        choices=["csv", "excel"],
        help="defaults to being inferred from the file extension",
    )
    parser.add_argument(
        "--record-id-column",
        help="column holding a natural key; defaults to the row number",
    )
    parser.add_argument("--reliability", type=float, default=0.5, help="source reliability, 0-1")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        metavar="N",
        help=(
            f"rows read and committed at a time (default {DEFAULT_BATCH_SIZE}). "
            "Lower it for very wide files, raise it for narrow ones."
        ),
    )
    parser.add_argument(
        "--allow-reingest",
        action="store_true",
        help="ingest again even if this exact file was already ingested for this source",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    if not args.path.is_file():
        print(f"error: {args.path} is not a file", file=sys.stderr)
        return 2
    if not 0 <= args.reliability <= 1:
        print("error: --reliability must be between 0 and 1", file=sys.stderr)
        return 2

    source_type = args.source_type or (
        "csv" if args.path.suffix.lower() in CSV_SUFFIXES else "excel"
    )

    try:
        result = ingest(
            path=args.path,
            entity_type=args.entity_type,
            source_name=args.source_name,
            source_type=source_type,
            reliability=args.reliability,
            record_id_column=args.record_id_column,
            allow_reingest=args.allow_reingest,
            batch_size=args.batch_size,
        )
    except ReingestBlocked as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except (UnsupportedFileType, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        f"batch {result.batch_id}: read {result.rows_read}, ingested {result.rows_ingested}, "
        f"events {'published' if result.events_published else 'NOT published'}"
    )
    return 0 if result.events_published else 4


if __name__ == "__main__":
    sys.exit(main())
