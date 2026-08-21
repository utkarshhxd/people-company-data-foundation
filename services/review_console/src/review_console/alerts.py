"""Reading alerts, kept separate from where alerts are delivered.

The alert *engine* is Prometheus and Alertmanager: rules evaluate, fire, group,
inhibit and resolve there. This module only reads the result. That separation is
the point -- adding Slack, email or a webhook later is a change to Alertmanager's
routing and nothing else, because no notification channel is encoded here.

Alertmanager is reached over the internal Docker network. It is deliberately not
proxied wholesale: this exposes a read-only projection shaped for a page, so the
UI never depends on Alertmanager's own API and swapping the engine would not
break the frontend.
"""

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ALERTMANAGER_URL = os.environ.get("ALERTMANAGER_URL", "http://alertmanager:9093")
TIMEOUT_SECONDS = 5.0

# Ordered worst-first, so a page can sort without knowing the vocabulary.
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


class AlertsUnavailable(Exception):
    """Alertmanager could not be reached.

    Raised rather than returning an empty list, because "no alerts" and "cannot
    tell whether there are alerts" are opposite states and a page that renders
    them identically is worse than one that shows an error.
    """


def _shape(raw: dict[str, Any]) -> dict[str, Any]:
    labels = raw.get("labels") or {}
    annotations = raw.get("annotations") or {}
    status = raw.get("status") or {}
    return {
        "fingerprint": raw.get("fingerprint"),
        "name": labels.get("alertname", "unknown"),
        "severity": labels.get("severity", "info"),
        "state": status.get("state", "unknown"),
        "silenced": bool(status.get("silencedBy")),
        "inhibited": bool(status.get("inhibitedBy")),
        "summary": annotations.get("summary", ""),
        "description": annotations.get("description", ""),
        "started_at": raw.get("startsAt"),
        "ends_at": raw.get("endsAt"),
        "labels": labels,
    }


async def active_alerts() -> list[dict[str, Any]]:
    """Alerts Alertmanager currently holds, worst first.

    Inhibited alerts are included and flagged rather than hidden: when the
    metrics collector is down its symptoms are suppressed from notification, and
    someone looking at a page should still be able to see that they exist.
    """
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(f"{ALERTMANAGER_URL}/api/v2/alerts")
            response.raise_for_status()
            raw = response.json()
    except Exception as exc:
        logger.warning("alertmanager unreachable: %s", exc)
        raise AlertsUnavailable(str(exc)) from exc

    alerts = [_shape(item) for item in raw]
    alerts.sort(key=lambda a: (SEVERITY_ORDER.get(a["severity"], 99), a["name"]))
    return alerts


def summarize(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts a page can show without walking the list itself."""
    counts: dict[str, int] = {}
    for alert in alerts:
        counts[alert["severity"]] = counts.get(alert["severity"], 0) + 1
    return {
        "total": len(alerts),
        "by_severity": counts,
        "worst": alerts[0]["severity"] if alerts else None,
    }
