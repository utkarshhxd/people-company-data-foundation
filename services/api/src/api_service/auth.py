"""API key authentication.

Two properties matter more than the mechanism, which is deliberately boring.

**It fails closed on the thing that matters.** The API is read-only, but what it
reads is personal data with full provenance attached -- names, home addresses,
phone numbers, and which vendor sold them. `PCDF_API_KEYS` unset means no keys
are configured; the API then serves only on loopback and says so loudly at
startup. Anything else is refused rather than silently served, because an
unauthenticated deployment that *looks* fine is exactly how this leaks.

**Keys are compared in constant time.** A naive `==` leaks the key one byte at a
time to anyone who can measure a few thousand requests, and the fix costs
nothing.

Health endpoints stay open. A liveness probe that needs a credential is a
liveness probe that reports the credential's health, and orchestrators check it
before secrets are necessarily in place.
"""

import hmac
import logging
import os

from fastapi import Header, HTTPException, Request

logger = logging.getLogger(__name__)

ENV_KEYS = "PCDF_API_KEYS"
ENV_ALLOW_UNAUTH = "PCDF_ALLOW_UNAUTHENTICATED"

# Loopback only. A container's own address is not in here on purpose: if the
# API is reachable from another host, it is reachable from the network, and
# "it's only internal" is a claim about a network diagram rather than about
# this process.
_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def configured_keys() -> frozenset[str]:
    """Keys from the environment. Comma-separated so several can rotate."""
    raw = os.environ.get(ENV_KEYS, "")
    return frozenset(key.strip() for key in raw.split(",") if key.strip())


def unauthenticated_allowed() -> bool:
    """Explicitly running open. Deliberate, recorded, and never the default."""
    return os.environ.get(ENV_ALLOW_UNAUTH, "").lower() in ("1", "true", "yes")


def _matches(candidate: str, keys: frozenset[str]) -> bool:
    # compare_digest against every key rather than a set lookup: a hash lookup
    # is fast precisely because it stops early, which is the leak.
    return any(hmac.compare_digest(candidate, key) for key in keys)


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> None:
    """Reject anything without a valid key. A FastAPI dependency.

    Accepts `X-API-Key: <key>` or `Authorization: Bearer <key>`, because callers
    arrive with one habit or the other and there is no reason to have an opinion.
    """
    keys = configured_keys()

    if not keys:
        if unauthenticated_allowed():
            return
        client = request.client.host if request.client else ""
        if client in _LOCAL_HOSTS:
            return
        # Reachable from off-box with no keys configured. Refusing is the only
        # honest answer: serving would hand out personal data to anyone who
        # found the port.
        raise HTTPException(
            status_code=503,
            detail=(
                f"No API keys are configured. Set {ENV_KEYS} to a comma-separated "
                f"list, or set {ENV_ALLOW_UNAUTH}=true to serve without "
                f"authentication deliberately."
            ),
        )

    presented = x_api_key
    if not presented and authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()

    if not presented:
        raise HTTPException(
            status_code=401,
            detail="Provide a key via the X-API-Key header or Authorization: Bearer.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not _matches(presented, keys):
        # No detail about why. "Unknown key" and "expired key" are both useful
        # to an attacker and useless to a legitimate caller, who has one key.
        raise HTTPException(status_code=401, detail="Invalid API key.")


def describe_configuration() -> str:
    """One line for the startup log, so how it is running is never a guess."""
    keys = configured_keys()
    if keys:
        return f"API key authentication enabled ({len(keys)} key(s) configured)"
    if unauthenticated_allowed():
        return (
            f"API is UNAUTHENTICATED: {ENV_ALLOW_UNAUTH} is set. Anyone who can "
            "reach this port can read every record and its provenance."
        )
    return (
        f"No API keys configured ({ENV_KEYS} is empty): requests from anywhere "
        "but loopback will be refused with 503."
    )
