"""Trigger a stage's standalone batch function, and track whether it's running.

Every stage already has a whole-batch entry point built for exactly this --
`map_batch`, `normalize_batch`, `validate_batch`, `resolve_batch`,
`build_batch` -- normally reached through a CLI (`docker compose run --rm
normalization normalize --batch-id <id>`), one at a time, by hand
(`docs/guides/processing-files.md`: "a stage runs because you ran it").
Nothing here reimplements a stage; this only gives a browser button a way to
start one and a way to ask "is it still going."

There is no job queue in this codebase (no Celery, no Redis -- consistent
with `docs/decisions/0012-record-at-a-time-processing.md`'s "no broker"
stance) and every FastAPI route here is a plain `def` running in a
threadpool (`router.py`), so calling a batch-sized function directly in a
request would hold that thread for the run's whole duration. Instead: start
a daemon thread, return immediately, and let the caller poll `status_for()`
-- which is exactly what `pipeline.batch_detail()`'s funnel counts already
get polled for.

Each of the five functions opens its own connection via `common.db.connect()`
internally, so calling one from a background thread never shares a
connection across threads.

Not addressed here, on purpose (see the plan this shipped from): resolution
has no lock against a stage run racing the live watcher over the same
identity keys. Running two stages for the same batch concurrently is guarded
against below; running a stage while unrelated ingestion is live on
overlapping data is not, and isn't a new risk -- the CLI has always carried it.
"""

import logging
import threading
from collections import OrderedDict
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any

from golden.pipeline import build_batch
from mapping.pipeline import map_batch
from normalization.pipeline import normalize_batch
from resolution.pipeline import resolve_batch
from validation.pipeline import validate_batch

logger = logging.getLogger(__name__)

STAGES = ("mapping", "normalization", "validation", "resolution", "golden")

_RUNNERS = {
    "mapping": map_batch,
    "normalization": normalize_batch,
    "validation": validate_batch,
    "resolution": resolve_batch,
    "golden": build_batch,
}


class AlreadyRunning(Exception):
    pass


class _RunState:
    __slots__ = (
        "batch_id", "detail", "error", "finished_at", "stage", "started_at", "status",
    )

    def __init__(self, stage: str, batch_id: str) -> None:
        self.stage = stage
        self.batch_id = batch_id
        self.status = "running"
        self.started_at = datetime.now(UTC)
        self.finished_at: datetime | None = None
        self.detail: Any = None
        self.error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "detail": self.detail,
            "error": self.error,
        }


_lock = threading.Lock()
# Capped for the same reason `uploads.py` caps its own registry: this process
# is meant to still be running in six months, and one entry per (stage, batch)
# ever started is unbounded in the number of batches, which is unbounded in
# time. Finished runs are evicted oldest-first; a running one is never evicted,
# because the entry is also what `start()` checks to refuse a concurrent run of
# the same stage on the same batch.
MAX_REMEMBERED_RUNS = 500
_runs: OrderedDict[tuple[str, str], _RunState] = OrderedDict()


def _evict_locked() -> None:
    """Caller holds `_lock`."""
    while len(_runs) > MAX_REMEMBERED_RUNS:
        for key, state in _runs.items():
            if state.status != "running":
                del _runs[key]
                break
        else:
            return  # every remembered run is still going


def start(stage: str, batch_id: str) -> _RunState:
    """Start a stage's batch function in a background thread.

    Refuses a second start while one is already running for the same
    (stage, batch) -- not because a re-run would corrupt anything (mapping,
    normalization and golden are safe to re-run; see each module's own
    docstring), but because two threads racing the same batch through the
    same stage is never useful and always confusing to watch.
    """
    runner = _RUNNERS[stage]
    with _lock:
        existing = _runs.get((stage, batch_id))
        if existing is not None and existing.status == "running":
            raise AlreadyRunning(f"{stage} is already running for batch {batch_id}")
        state = _RunState(stage, batch_id)
        _runs[(stage, batch_id)] = state
        _runs.move_to_end((stage, batch_id))
        _evict_locked()

    def _run() -> None:
        try:
            result = runner(batch_id)
            detail = asdict(result) if is_dataclass(result) else result
            with _lock:
                state.status = "completed"
                state.finished_at = datetime.now(UTC)
                state.detail = detail
        except Exception as exc:
            logger.warning("%s failed for batch %s: %s", stage, batch_id, exc)
            with _lock:
                state.status = "failed"
                state.finished_at = datetime.now(UTC)
                state.error = str(exc)

    threading.Thread(target=_run, name=f"stage-{stage}-{batch_id}", daemon=True).start()
    return state


def status_for(batch_id: str) -> dict[str, dict[str, Any] | None]:
    """Every stage's last-known run state for one batch, idle stages as None."""
    with _lock:
        return {
            stage: (state.as_dict() if (state := _runs.get((stage, batch_id))) else None)
            for stage in STAGES
        }
