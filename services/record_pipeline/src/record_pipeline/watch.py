"""Process files as they arrive, instead of when somebody remembers to.

Until now a file entered this system because a person typed `process run` with
the right `--entity-type`, the right `--source-name` and the right
`--reliability`. That is fine for the first file from a vendor and wrong for the
hundredth: the arguments are properties of the *feed*, not of the file, and
retyping them per file is how a vendor's data ends up loaded under two source
names with two different reliabilities.

So the arguments move to the feed. A watched directory is one feed:

    data/inbox/watch/apollo/feed.json     <- the arguments, written once
    data/inbox/watch/apollo/export.csv    <- drop files here
    data/inbox/watch/apollo/_done/        <- processed, with a receipt
    data/inbox/watch/apollo/_failed/      <- not processed, with the reason

Three things this has to get right, none of which are about throughput:

**A file being copied is not a file.** A 200 MB export lands over several
seconds and is a valid, truncated CSV for most of them. Nothing is processed
until its size and mtime have been unchanged across a full poll, which is the
cheapest check that is actually correct on every filesystem this runs on.

**One bad file must not stop the feed.** A file that fails is moved aside with
the error written next to it, and the next one is processed. The watcher only
exits on a signal.

**Arriving twice must not load twice.** Files are moved out of the drop folder
once handled, and ingestion's own SHA-256 check refuses the same bytes under a
new name regardless — so the move is tidiness and the hash is the guarantee.
"""

import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ingestion.pipeline import ReingestBlocked
from ingestion.readers import CSV_SUFFIXES, UnsupportedFileType

from record_pipeline.runner import EmptySource, run_file

logger = logging.getLogger(__name__)

FEED_FILE = "feed.json"
DONE_DIR = "_done"
FAILED_DIR = "_failed"

DEFAULT_POLL_SECONDS = 10.0

# Anything else in a drop folder is not data: a receipt this wrote, an editor's
# swap file, a partial download from a browser or curl.
IGNORED_SUFFIXES = frozenset({".log", ".json", ".tmp", ".part", ".crdownload", ".swp"})
DATA_SUFFIXES = frozenset(CSV_SUFFIXES) | {".xlsx", ".xls"}


class FeedInvalid(Exception):
    """A feed.json that cannot be acted on. Named so the watcher can say which."""


@dataclass(frozen=True)
class Feed:
    """One watched directory and the arguments every file in it is loaded with."""

    name: str
    directory: Path
    entity_type: str
    source_name: str
    reliability: float
    record_id_column: str | None = None
    describes: str | None = None
    source_type: str | None = None
    build_golden: bool = True

    @property
    def done_dir(self) -> Path:
        return self.directory / DONE_DIR

    @property
    def failed_dir(self) -> Path:
        return self.directory / FAILED_DIR


def load_feed(directory: Path) -> Feed:
    """Read one feed.json, refusing anything that would load data wrongly.

    Validated here rather than at use, because the failure of a mistyped
    entity_type is a company's rows resolved as people — recoverable only by
    purging the source, and not obviously wrong until much later.
    """
    path = directory / FEED_FILE
    try:
        config: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FeedInvalid(f"{path} is not valid JSON: {exc}") from exc

    missing = [key for key in ("entity_type", "source_name") if not config.get(key)]
    if missing:
        raise FeedInvalid(f"{path} is missing {', '.join(missing)}")

    entity_type = config["entity_type"]
    if entity_type not in ("person", "company"):
        raise FeedInvalid(
            f"{path}: entity_type must be 'person' or 'company', not {entity_type!r}"
        )

    reliability = float(config.get("reliability", 0.5))
    if not 0 <= reliability <= 1:
        raise FeedInvalid(f"{path}: reliability must be between 0 and 1")

    describes = config.get("describes")
    if describes is not None and describes not in ("organisation", "location"):
        raise FeedInvalid(
            f"{path}: describes must be 'organisation' or 'location', not {describes!r}"
        )

    return Feed(
        name=directory.name,
        directory=directory,
        entity_type=entity_type,
        source_name=config["source_name"],
        reliability=reliability,
        record_id_column=config.get("record_id_column"),
        describes=describes,
        source_type=config.get("source_type"),
        build_golden=bool(config.get("build_golden", True)),
    )


def discover_feeds(root: Path) -> tuple[list[Feed], list[str]]:
    """Every readable feed under the watch root, and a complaint per unreadable one.

    Both are returned rather than one raising: a broken feed.json in one folder
    is not a reason to stop loading the other nine.
    """
    feeds, problems = [], []
    if not root.is_dir():
        return feeds, [f"{root} does not exist"]
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        if not (directory / FEED_FILE).is_file():
            problems.append(f"{directory} has no {FEED_FILE}; ignoring it")
            continue
        try:
            feeds.append(load_feed(directory))
        except FeedInvalid as exc:
            problems.append(str(exc))
    return feeds, problems


def _fingerprint(path: Path) -> tuple[int, int] | None:
    """Size and mtime, or None if the file vanished mid-scan."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def candidate_files(feed: Feed) -> list[Path]:
    """Data files sitting in the drop folder, oldest first.

    Oldest first so a backlog is worked in arrival order, which is the order a
    vendor's daily exports have to be applied in for 'most recent wins' to mean
    what it says.
    """
    files = [
        path
        for path in feed.directory.iterdir()
        if path.is_file()
        and path.name != FEED_FILE
        and not path.name.startswith(".")
        and path.suffix.lower() not in IGNORED_SUFFIXES
    ]
    return sorted(files, key=lambda p: (_fingerprint(p) or (0, 0))[1])


@dataclass
class Handled:
    """What became of one file."""

    path: Path
    ok: bool
    detail: str


def _move_aside(path: Path, target_dir: Path, note: str) -> Path:
    """Move a handled file out of the drop folder, with a receipt beside it.

    Never overwrites: a second file of the same name is a second delivery, and
    silently replacing the first would destroy the evidence of what was loaded.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"{stamp}__{path.name}"
    suffix = 1
    while target.exists():
        target = target_dir / f"{stamp}__{suffix}__{path.name}"
        suffix += 1
    path.replace(target)
    target.with_suffix(target.suffix + ".log").write_text(
        f"{datetime.now(UTC).isoformat()}\n{note}\n", encoding="utf-8"
    )
    return target


def process_file(feed: Feed, path: Path) -> Handled:
    """Load one file under its feed's settings and file it away either way."""
    source_type = feed.source_type or (
        "csv" if path.suffix.lower() in CSV_SUFFIXES else "excel"
    )
    logger.info("feed %s: processing %s", feed.name, path.name)

    try:
        result = run_file(
            path=path,
            entity_type=feed.entity_type,
            source_name=feed.source_name,
            source_type=source_type,
            reliability=feed.reliability,
            record_id_column=feed.record_id_column,
            describes=feed.describes,
            build_golden=feed.build_golden,
        )
    except ReingestBlocked as exc:
        # Not a failure of the file. The same bytes are already loaded, so the
        # right outcome is to file it as done and say why.
        note = f"already ingested, not loaded again: {exc}"
        logger.info("feed %s: %s — %s", feed.name, path.name, note)
        _move_aside(path, feed.done_dir, note)
        return Handled(path, True, note)
    except (UnsupportedFileType, EmptySource, KeyError) as exc:
        note = f"{type(exc).__name__}: {exc}"
        logger.warning("feed %s: %s could not be read — %s", feed.name, path.name, note)
        _move_aside(path, feed.failed_dir, note)
        return Handled(path, False, note)
    except Exception as exc:
        # One file must not stop the feed. The watcher is a long-running process
        # whose whole job is to still be running tomorrow.
        note = f"{type(exc).__name__}: {exc}"
        logger.exception("feed %s: %s failed", feed.name, path.name)
        _move_aside(path, feed.failed_dir, note)
        return Handled(path, False, note)

    note = (
        f"batch {result.batch_id}\n"
        f"read {result.rows_read}, processed {result.processed}, "
        f"failed {result.failed}, quarantined {result.quarantined}, "
        f"linked {result.linked}, new {result.new_entities}, review {result.review}"
    )
    logger.info("feed %s: %s -> %s", feed.name, path.name, note.replace("\n", " "))

    # Rows that could not be processed are recorded in record_error and the file
    # is still done — it was read, and re-running it would re-read the rows that
    # already succeeded. `process reprocess` is the way back for those.
    _move_aside(path, feed.done_dir, note)
    return Handled(path, True, note)


class Watcher:
    """Poll the watch root, process what has settled, and keep going."""

    def __init__(self, root: Path, poll_seconds: float = DEFAULT_POLL_SECONDS):
        self.root = root
        self.poll_seconds = poll_seconds
        self._stop = False
        # Fingerprint of each file as last seen, so "unchanged since last poll"
        # is answerable without holding the file open.
        self._seen: dict[Path, tuple[int, int]] = {}
        self._complained: set[str] = set()

    def request_stop(self, *_args) -> None:
        """Finish the file in hand, then exit. Wired to SIGTERM and SIGINT."""
        logger.info("stop requested; finishing the current file first")
        self._stop = True

    def _settled(self, path: Path) -> bool:
        """True once a file has stopped changing.

        A file still being copied is a valid truncated CSV for as long as the
        copy takes, and loading one silently drops every row after the cut.
        """
        now = _fingerprint(path)
        if now is None:
            self._seen.pop(path, None)
            return False
        before = self._seen.get(path)
        self._seen[path] = now
        if before is None:
            logger.debug("waiting for %s to settle", path.name)
            return False
        return before == now

    def _complain_once(self, problems: list[str]) -> None:
        """Say what is wrong with a feed the first time, not every ten seconds."""
        for problem in problems:
            if problem not in self._complained:
                logger.warning("%s", problem)
                self._complained.add(problem)
        self._complained &= set(problems)

    def sweep(self) -> list[Handled]:
        """One pass: every feed, every file that has settled."""
        feeds, problems = discover_feeds(self.root)
        self._complain_once(problems)

        handled = []
        for feed in feeds:
            for path in candidate_files(feed):
                if self._stop:
                    return handled
                if not self._settled(path):
                    continue
                self._seen.pop(path, None)
                handled.append(process_file(feed, path))
        return handled

    def run(self) -> int:
        """Poll until told to stop. Returns the process exit code."""
        logger.info(
            "watching %s every %.0fs — drop files into a feed directory",
            self.root, self.poll_seconds,
        )
        for received in (signal.SIGTERM, signal.SIGINT):
            signal.signal(received, self.request_stop)

        while not self._stop:
            try:
                self.sweep()
            except Exception:
                # A failure in the sweep itself — an unreadable directory, a
                # permission change — must not end the process either.
                logger.exception("sweep failed; continuing")
            # Slept in slices so a stop is acted on in a second, not a poll.
            waited = 0.0
            while waited < self.poll_seconds and not self._stop:
                time.sleep(min(1.0, self.poll_seconds - waited))
                waited += 1.0

        logger.info("stopped")
        return 0


def default_root() -> Path:
    """Where to watch, overridable so the container and a laptop can differ."""
    return Path(os.environ.get("PCDF_WATCH_DIR", "/data/inbox/watch"))
