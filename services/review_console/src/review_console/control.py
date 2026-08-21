"""Reading and changing whether a stage is running, from the console.

Same functions the `validation-control` CLI calls, for the same reason every
other decision in this service delegates: a stage stopped from a browser and
one stopped from a terminal have to be the same thing, or the two will
eventually disagree about what "paused" means.
"""

from typing import Any

from common import control
from common.db import connect


def states() -> dict[str, Any]:
    """Every stage, and whether anything is holding the pipeline up."""
    with connect() as conn:
        found = [state.as_dict() for state in control.all_states(conn)]
    paused = [s for s in found if s["paused"]]
    return {
        "stages": found,
        "paused_count": len(paused),
        # The console branches on this to decide whether to shout. Computed
        # here rather than in the page so the two consoles cannot disagree.
        "running": not paused,
    }


def recent(stage: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    with connect() as conn:
        return control.history(conn, stage, limit)


def pause(stage: str, reason: str, reviewed_by: str) -> dict[str, Any]:
    with connect() as conn:
        return control.pause(
            conn, stage, reason, changed_by=reviewed_by,
            note="Paused from the review console.",
        ).as_dict()


def resume(stage: str, reviewed_by: str, note: str | None = None) -> dict[str, Any]:
    with connect() as conn:
        return control.resume(
            conn, stage, changed_by=reviewed_by, note=note
        ).as_dict()
