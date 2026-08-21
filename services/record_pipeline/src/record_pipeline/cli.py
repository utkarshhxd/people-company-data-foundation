import argparse
import sys
import time
from pathlib import Path

from common.db import connect
from common.logging import configure
from ingestion import repository as ingest_repo
from ingestion.pipeline import ReingestBlocked
from ingestion.readers import CSV_SUFFIXES, DEFAULT_BATCH_SIZE, UnsupportedFileType

from record_pipeline import repository, runner
from record_pipeline import watch as watcher
from record_pipeline.runner import EmptySource, run_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="process",
        description="Process a file one record at a time, all the way to golden values.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="process a file")
    run.add_argument("path", type=Path, help="path to the CSV/Excel file")
    run.add_argument("--entity-type", required=True, choices=["person", "company"])
    run.add_argument("--source-name", required=True, help="logical feed/vendor name")
    run.add_argument(
        "--source-type", choices=["csv", "excel"],
        help="defaults to being inferred from the file extension",
    )
    run.add_argument(
        "--record-id-column",
        help="column holding a natural key; defaults to the row number",
    )
    run.add_argument(
        "--reliability", type=float, default=0.5, help="source reliability, 0-1"
    )
    run.add_argument(
        "--describes", choices=["organisation", "location"],
        help="what this source catalogues: 'organisation' (rows name companies, so a shared domain means the same company) or 'location' (rows name premises, so a shared domain means only the same brand)",
    )
    run.add_argument(
        "--read-ahead", type=int, default=DEFAULT_BATCH_SIZE, metavar="N",
        help=(
            f"rows pulled off disk per read (default {DEFAULT_BATCH_SIZE}). This is "
            "buffering only — records are still processed and committed one at a time."
        ),
    )
    run.add_argument(
        "--no-golden", action="store_true",
        help=(
            "skip rebuilding each record's entity as it lands. Faster, but a "
            "record is not fully current when it finishes, so downstream must "
            "wait for a separate golden build."
        ),
    )
    run.add_argument(
        "--no-publish", action="store_true",
        help="process without emitting a record.processed event per record",
    )
    run.add_argument(
        "--fail-fast", action="store_true",
        help=(
            "stop at the first record that throws instead of recording it and "
            "carrying on. Useful while developing; wrong for a production load, "
            "where one bad row should cost one row."
        ),
    )
    run.add_argument(
        "--async-commit", action="store_true",
        help=(
            "defer the commit fsync for this run (synchronous_commit=off). Roughly "
            "1.7x faster. A crash can lose recently committed records, which leaves "
            "the batch un-completed and therefore ignored downstream; recovery is "
            "re-running the file."
        ),
    )
    run.add_argument(
        "--allow-reingest", action="store_true",
        help="process again even if this exact file was already processed for this source",
    )

    watch = sub.add_parser(
        "watch",
        help="process files as they are dropped into a watched feed directory",
    )
    watch.add_argument(
        "--root", type=Path, default=None,
        help="directory of feed directories (default: $PCDF_WATCH_DIR)",
    )
    watch.add_argument(
        "--poll", type=float, default=watcher.DEFAULT_POLL_SECONDS, metavar="SECONDS",
        help=(
            f"how often to look (default {watcher.DEFAULT_POLL_SECONDS:.0f}s). Also "
            "how long a file must be unchanged before it is considered finished "
            "arriving."
        ),
    )
    watch.add_argument(
        "--once", action="store_true",
        help="do one pass and exit, instead of watching. For cron, and for tests.",
    )

    errors = sub.add_parser("errors", help="list records that could not be processed")
    errors.add_argument("--batch-id", help="limit to one batch")
    errors.add_argument("--limit", type=int, default=50)

    reprocess = sub.add_parser(
        "reprocess",
        help="run rows from record_error through the pipeline again",
    )
    reprocess.add_argument("--batch-id", help="limit to one batch")
    reprocess.add_argument("--limit", type=int, default=1000)
    reprocess.add_argument("--reviewed-by", default="reprocess",
                           help="who is replaying these, recorded on each row")
    reprocess.add_argument("--no-golden", action="store_true",
                           help="skip rebuilding each record's entity as it lands")

    abandon = sub.add_parser(
        "abandon",
        help="mark a stuck 'running' batch as failed so the file can be processed again",
    )
    abandon.add_argument("batch_id")
    abandon.add_argument(
        "--reason", default="abandoned by operator",
        help="why, recorded on the batch",
    )

    return parser


def _run(args) -> int:
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
        result = run_file(
            path=args.path,
            entity_type=args.entity_type,
            source_name=args.source_name,
            source_type=source_type,
            reliability=args.reliability,
            record_id_column=args.record_id_column,
            allow_reingest=args.allow_reingest,
            read_ahead=args.read_ahead,
            build_golden=not args.no_golden,
            publish=not args.no_publish,
            fail_fast=args.fail_fast,
            async_commit=args.async_commit,
            describes=args.describes,
        )
    except ReingestBlocked as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except (UnsupportedFileType, EmptySource, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        f"batch {result.batch_id}\n"
        f"  read       {result.rows_read}\n"
        f"  processed  {result.processed}\n"
        f"  failed     {result.failed}\n"
        f"  invalid    {result.invalid} (quarantined {result.quarantined})\n"
        f"  linked     {result.linked}\n"
        f"  new        {result.new_entities}\n"
        f"  review     {result.review}\n"
        f"  mapping    {'reused' if result.mapping_reused else 'derived'}"
    )
    # A run with failed records succeeded at its job — it processed what it
    # could and kept the rest. It exits non-zero anyway, because a scheduled
    # load that quietly drops rows is how data goes missing unnoticed.
    if result.failed:
        return 5
    return 0


def _watch(args) -> int:
    """Watch, or do one pass and stop.

    `--once` exists because the same code should serve both a long-running
    container and a cron entry, and because a loop that only terminates on a
    signal is otherwise untestable.
    """
    root = args.root or watcher.default_root()
    feeds, problems = watcher.discover_feeds(root)
    for problem in problems:
        print(f"warning: {problem}", file=sys.stderr)

    if not args.once:
        return watcher.Watcher(root, poll_seconds=args.poll).run()

    if not feeds:
        print(f"no feeds under {root}", file=sys.stderr)
        return 2

    # One Watcher across both sweeps, because "unchanged since last seen" is
    # state it carries. The first sweep records what is there, the second acts
    # on whatever has not moved since — the same settle check the loop uses,
    # not a weaker one that happens to be easier to run once.
    once = watcher.Watcher(root, poll_seconds=args.poll)
    once.sweep()
    time.sleep(min(args.poll, watcher.DEFAULT_POLL_SECONDS))
    handled = once.sweep()

    for item in handled:
        status = "ok  " if item.ok else "FAIL"
        print(f"{status} {item.path.name}: {item.detail.splitlines()[0]}")
    if not handled:
        print("nothing to process")
    return 1 if any(not item.ok for item in handled) else 0


def _errors(args) -> int:
    with connect() as conn:
        rows = repository.list_errors(conn, args.batch_id, args.limit)
    if not rows:
        print("no unprocessed records")
        return 0
    for row in rows:
        note = " (payload sanitized for display)" if row["payload_sanitized"] else ""
        print(
            f"{row['source_name']}/{row['file_name']} row {row['row_number']} "
            f"[{row['stage']}] {row['error_type']}: {row['error_message'][:120]}{note}"
        )
    print(f"\n{len(rows)} record(s) awaiting attention")
    return 0


def _abandon(args) -> int:
    """Release a batch left 'running' by a process that died.

    Only ever marks it failed. The rows it already wrote stay exactly where they
    are: they are what the source said, every downstream stage requires
    'completed', and deleting them would destroy the only record of a partial
    load. A re-run creates a new batch alongside it.
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM batch WHERE batch_id = %s", (args.batch_id,)
            )
            row = cur.fetchone()
        if row is None:
            print(f"error: no batch {args.batch_id}", file=sys.stderr)
            return 2
        if row[0] != "running":
            print(
                f"error: batch {args.batch_id} is {row[0]!r}, not 'running'; "
                "nothing to abandon",
                file=sys.stderr,
            )
            return 2
        ingest_repo.fail_batch(conn, args.batch_id, args.reason)
    print(f"batch {args.batch_id} marked failed; its rows were left in place")
    return 0


def _reprocess(args) -> int:
    """Replay failed rows. Exit non-zero if any are still failing.

    A partially successful replay is still a failure as far as the exit code is
    concerned, for the same reason a partially successful load is: a scheduled
    job that quietly leaves rows unprocessed is how data goes missing unnoticed.
    """
    result = runner.reprocess_errors(
        batch_id=args.batch_id, limit=args.limit, actor=args.reviewed_by,
        build_golden=not args.no_golden,
    )
    if result.attempted == 0 and result.skipped == 0:
        print("nothing to reprocess")
        return 0

    print(f"attempted      {result.attempted}")
    print(f"  succeeded    {result.succeeded}")
    print(f"  still failing{result.still_failing:>4}")
    print(f"  skipped      {result.skipped}")
    if result.skipped:
        print()
        print("skipped rows have no byte-exact payload (written before migration")
        print("0010) or belong to a batch whose column mapping is gone.")
    return 1 if result.still_failing else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure("record_pipeline")
    if args.command == "run":
        return _run(args)
    if args.command == "watch":
        return _watch(args)
    if args.command == "reprocess":
        return _reprocess(args)
    if args.command == "abandon":
        return _abandon(args)
    return _errors(args)


if __name__ == "__main__":
    sys.exit(main())
