"""Take a file dropped in the browser, put it somewhere safe, and queue it.

This module is now only the *receiving* half. It writes the bytes to disk
under a name that cannot be anything other than a filename, and hands the
request to `ingest_queue`, which is where it is written down and where a
single worker eventually runs `ingestion.pipeline.ingest()` on it -- the exact
function `docker compose run --rm ingestion ingest <path> ...` calls.

It used to also *do* the ingest, in a thread, tracked in a dictionary. That
was fine for one file and wrong for ten, and it is the reason the queue
exists: see `ingest_queue`.
"""

import logging
import re
from pathlib import Path, PurePosixPath
from typing import BinaryIO

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
# body into memory before anything can object. This has to handle a
# ten-million-row file; doing that by materialising it in RAM first is how one
# upload takes the console down for everyone.
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


def save(source: BinaryIO, dest: Path) -> int:
    """Stream the body to disk, refusing it the moment it exceeds the cap.

    The partial file is removed on refusal: leaving it would let repeated
    over-size uploads fill the volume with data nothing will ever read.
    """
    written = 0
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
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


def store(source: BinaryIO, file_name: str, queue_id_hint: str) -> tuple[Path, int]:
    """Write the upload to disk and say where it went and how big it was.

    Prefixed with a unique hint so two people dropping same-named files -- or
    the same person retrying one -- never collide on disk.
    """
    dest = UPLOAD_DIR / f"{queue_id_hint}__{safe_name(file_name)}"
    return dest, save(source, dest)
