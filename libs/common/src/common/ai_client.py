"""A thin client for a local Ollama server.

Stdlib-only (urllib), on purpose: every other dependency in this workspace is
psycopg or pydantic-settings, and pulling in an HTTP library for one optional,
locally-hosted call is not worth carrying into every service that imports
`common`.

Every caller treats a missing or malformed answer the same way: fall back to
whatever the deterministic logic would have done anyway. An unreachable model,
a timeout, or a model that ignores the requested JSON shape must never stop a
pipeline that worked fine before AI was added to it -- so this raises nothing
and returns `None` instead.
"""

import json
import logging
import urllib.error
import urllib.request

from common.config import settings

logger = logging.getLogger(__name__)


def generate_json(prompt: str, *, system: str | None = None) -> dict | None:
    """Ask the model for a JSON object. `None` if it didn't give one back.

    `format: "json"` asks Ollama to constrain generation to valid JSON; the
    surrounding try/except is what actually protects callers, since a small
    model can still emit JSON that doesn't parse the way requested, or refuse
    to answer, or simply not be there.
    """
    body = {
        "model": settings.ollama_model,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    request = urllib.request.Request(
        f"{settings.ollama_host.rstrip('/')}/api/generate",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=settings.ollama_timeout_seconds
        ) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.warning("ollama request failed: %s", exc)
        return None

    text = payload.get("response", "")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("ollama returned non-JSON response: %.200s", text)
        return None

    return parsed if isinstance(parsed, dict) else None
