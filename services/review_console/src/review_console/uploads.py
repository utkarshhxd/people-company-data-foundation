"""Take a file dropped in the browser, and ingest it -- same as the CLI.

`ingestion.pipeline.ingest()` is the exact function `docker compose run --rm
ingestion ingest <path> --entity-type ... --source-name ...` calls
(`services/ingestion/src/ingestion/cli.py:63-73`). This module does nothing an
operator couldn't already do from a terminal: save the uploaded bytes
somewhere `ingest()` can open as a `Path`, call it with the form fields as its
arguments, and let it create its own batch the same way it always has.

Ingesting a large file is not instant, so this follows the same background-
thread-plus-registry shape as `stages.py`: the upload endpoint returns as soon
as the file is saved and the run has started, not when ingestion finishes.
Unlike a stage run there is no `batch_id` to key the registry by until
`ingest()` returns one -- ingestion is what *creates* the batch -- so this
tracks by an upload id instead, and hands back the resulting `batch_id` once
known, for the dashboard to select.
"""

import logging
import re
import threading
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from ingestion.pipeline import ReingestBlocked, ingest
from ingestion.readers import CSV_SUFFIXES, MultipleSheets, sheet_names

logger = logging.getLogger(__name__)

# Bind-mounted the same way ingestion/pipeline/watcher already mount
# ./data:/data -- uploaded files land next to everything else dropped into
# the pipeline by hand, visible on the host, not hidden inside a container.
UPLOAD_DIR = Path("/data/inbox/uploads")

# A browser sends whatever the client claims the file is called, and a claimed
# name is not a path component until something checks it. `../` in it walks out
# of UPLOAD_DIR while still landing inside the ./data bind mount -- which is
# where the watcher's feed directories live. An upload named
# `../watch/apollo/feed.json` would rewrite the arguments every future file in
# that feed is loaded under, turning permission to upload a file into control
# over how other people's files are interpreted.
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")
MAX_NAME_LENGTH = 120

# Read in chunks against a cap rather than with .read(), which pulls the whole
# body into memory before anything can object. The docstring above promises
# this handles a ten-million-row file; doing that by materialising it in RAM
# first is how one upload takes the console down for everyone.
CHUNK_BYTES = 1024 * 1024
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024


class UploadTooLarge(Exception):
    """The body exceeded MAX_UPLOAD_BYTES and was refused mid-stream."""


def safe_name(file_name: str) -> str:
    """The client's filename reduced to one harmless path component.

    Kept recognisable rather than replaced outright: the operator who dropped
    `Q3 export (final).csv` should still see which upload it was, and the
    stored name is what the batch is identified by afterwards.
    """
    stem = PurePosixPath(file_name.replace("\\", "/")).name
    cleaned = _SAFE_NAME.sub("_", stem).lstrip(".")[:MAX_NAME_LENGTH]
    return cleaned or "upload"


_INGEST = ingest  # module-level so tests can monkeypatch this name directly


class _UploadState:
    __slots__ = (
        "batch_id", "detail", "error", "finished_at", "started_at", "status",
        "upload_id",
    )

    def __init__(self, upload_id: str) -> None:
        self.upload_id = upload_id
        self.status = "running"
        self.started_at = datetime.now(UTC)
        self.finished_at: datetime | None = None
        self.batch_id: str | None = None
        self.detail: Any = None
        self.error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "upload_id": self.upload_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "batch_id": self.batch_id,
            "detail": self.detail,
            "error": self.error,
        }


_lock = threading.Lock()
# Insertion-ordered and capped. The console is a long-running process and an
# upload's state is only ever read by the browser that started it, polling for
# a few seconds; keeping every one forever means an operator who loads files
# all year is holding a year of finished uploads in memory. Finished entries
# are evicted oldest-first, and a running one is never evicted -- losing the
# state of an in-flight upload would report it as "no such upload" while it is
# still writing rows.
MAX_REMEMBERED_UPLOADS = 200
_uploads: OrderedDict[str, _UploadState] = OrderedDict()


def _evict_locked() -> None:
    """Caller holds `_lock`."""
    while len(_uploads) > MAX_REMEMBERED_UPLOADS:
        for upload_id, state in _uploads.items():
            if state.status != "running":
                del _uploads[upload_id]
                break
        else:
            return  # every remembered upload is still running


def _save(source: BinaryIO, dest: Path) -> int:
    """Stream the body to disk, refusing it the moment it exceeds the cap.

    The partial file is removed on refusal: leaving it would let repeated
    over-size uploads fill the volume with data nothing will ever read.
    """
    written = 0
    try:
        with dest.open("wb") as out:
            while chunk := source.read(CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise UploadTooLarge(
                        f"upload exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit"
                    )
                out.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return written


def start(
    source: BinaryIO,
    file_name: str,
    *,
    entity_type: str,
    source_name: str,
    source_type: str | None = None,
    record_id_column: str | None = None,
    reliability: float = 0.5,
    describes: str | None = None,
    batch_size: int,
    allow_reingest: bool = False,
    sheet: str | None = None,
) -> _UploadState:
    upload_id = str(uuid.uuid4())
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # Prefixed with the upload id so two people dropping same-named files
    # (or the same person retrying one) never collide on disk.
    dest = UPLOAD_DIR / f"{upload_id}__{safe_name(file_name)}"
    _save(source, dest)

    resolved_type = source_type or (
        "csv" if dest.suffix.lower() in CSV_SUFFIXES else "excel"
    )

    state = _UploadState(upload_id)
    with _lock:
        _uploads[upload_id] = state
        _evict_locked()

    def _run() -> None:
        try:
            result = _INGEST(
                path=dest,
                entity_type=entity_type,
                source_name=source_name,
                source_type=resolved_type,
                reliability=reliability,
                record_id_column=record_id_column,
                allow_reingest=allow_reingest,
                batch_size=batch_size,
                describes=describes,
                sheet=sheet,
            )
            with _lock:
                state.status = "completed"
                state.finished_at = datetime.now(UTC)
                state.batch_id = result.batch_id
                state.detail = {
                    "rows_read": result.rows_read,
                    "rows_ingested": result.rows_ingested,
                    "rows_skipped": result.rows_skipped,
                }
        except ReingestBlocked as exc:
            with _lock:
                state.status = "failed"
                state.finished_at = datetime.now(UTC)
                state.error = str(exc)
        except MultipleSheets as exc:
            # The message already names the sheets; this also hands them back
            # structured, so the UI can offer them as picks instead of asking
            # someone to retype a name they just read out of an error string.
            with _lock:
                state.status = "failed"
                state.finished_at = datetime.now(UTC)
                state.error = str(exc)
                try:
                    state.detail = {"available_sheets": sheet_names(dest)}
                except Exception as list_exc:
                    logger.warning("could not list sheets in %s: %s", dest, list_exc)
        except Exception as exc:
            logger.warning("upload %s (%s) failed: %s", upload_id, file_name, exc)
            with _lock:
                state.status = "failed"
                state.finished_at = datetime.now(UTC)
                state.error = str(exc)

    threading.Thread(target=_run, name=f"upload-{upload_id}", daemon=True).start()
    return state


def status(upload_id: str) -> dict[str, Any] | None:
    with _lock:
        state = _uploads.get(upload_id)
        return state.as_dict() if state else None
