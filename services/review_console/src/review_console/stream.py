"""Push pipeline state to connected browsers instead of making them poll.

Postgres has no change-notification wired up anywhere in this stack -- doing
that properly (LISTEN/NOTIFY, or triggers) would mean touching the write path
of every stage service, not just this one, and was deliberately not chosen
here (see the plan this shipped from). Instead: one background task in this
process re-runs the same aggregate queries the dashboard already polled every
5s from the browser -- `pipeline.global_summary`, `pipeline.recent_batches`,
`review.queue_summary`, and a selected batch's `pipeline.batch_detail` -- on a
tighter interval, and broadcasts the result over one SSE stream per connected
browser. The browser stops driving a poll loop; it opens one connection and
renders whatever arrives.

Not true database-level push -- still polling, just moved server-side and
shared across however many tabs are open, instead of each one asking
separately. Good enough for an operator's own dashboard; would not be the
right call for a lot of concurrent viewers, at which point LISTEN/NOTIFY
stops being overkill.

Every route elsewhere in this service is a plain `def`, run in FastAPI's
threadpool, because the queries are synchronous psycopg (`router.py`'s own
docstring). This module is the one place that breaks that rule on purpose: a
stream has to be `async def` to hold a connection open without blocking a
worker thread for it, so the sync DB calls inside the poll loop are pushed
onto a thread explicitly (`asyncio.to_thread`) instead.
"""

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi.encoders import jsonable_encoder

from review_console import pipeline, review

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2.0


@dataclass(eq=False)
class _Subscriber:
    """`eq=False` keeps identity-based equality/hashing -- two subscribers on
    the same batch_id must stay distinct entries in `_subscribers`."""

    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=1))
    batch_id: str | None = None


_subscribers: set[_Subscriber] = set()
_lock = asyncio.Lock()
_poller_task: asyncio.Task | None = None


async def _snapshot(batch_id: str | None) -> dict[str, Any]:
    summary, batches, queues = await asyncio.gather(
        asyncio.to_thread(pipeline.global_summary),
        asyncio.to_thread(pipeline.recent_batches),
        asyncio.to_thread(review.queue_summary),
    )
    payload: dict[str, Any] = {"summary": summary, "batches": batches, "queues": queues}
    if batch_id:
        payload["batch"] = await asyncio.to_thread(pipeline.batch_detail, batch_id)
    return payload


def _publish(queue: asyncio.Queue, payload: dict[str, Any]) -> None:
    # Latest-wins: a slow client should see where things stand NOW on its next
    # read, not work through a backlog of ticks it fell behind on.
    if queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
    queue.put_nowait(payload)


async def _poll_forever() -> None:
    while True:
        try:
            async with _lock:
                subs = list(_subscribers)
            if subs:
                # Distinct batch_ids only: two tabs open on the same batch
                # share one query instead of paying for it twice.
                cache: dict[str | None, dict[str, Any]] = {}
                for sub in subs:
                    if sub.batch_id not in cache:
                        cache[sub.batch_id] = await _snapshot(sub.batch_id)
                    _publish(sub.queue, cache[sub.batch_id])
        except Exception:
            logger.exception("stream poller tick failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def start() -> None:
    global _poller_task
    if _poller_task is None:
        _poller_task = asyncio.create_task(_poll_forever())


async def stop() -> None:
    global _poller_task
    if _poller_task is not None:
        _poller_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _poller_task
        _poller_task = None


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(jsonable_encoder(payload))}\n\n"


async def events(batch_id: str | None):
    """SSE body for one browser connection: an immediate snapshot, then
    whatever the poller publishes for this batch_id, forever."""
    sub = _Subscriber(batch_id=batch_id)
    async with _lock:
        _subscribers.add(sub)
    try:
        yield _sse(await _snapshot(batch_id))
        while True:
            payload = await sub.queue.get()
            yield _sse(payload)
    finally:
        async with _lock:
            _subscribers.discard(sub)
