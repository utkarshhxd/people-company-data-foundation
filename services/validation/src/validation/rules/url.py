"""URL and web-address rules."""

import re

from validation.rules.base import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    AttributeContext,
    Judgement,
    failed,
    passed,
)

URL_HOST = re.compile(r"^(?:[a-z][a-z0-9+.-]*://)?([^/\s?#]+)", re.IGNORECASE)
HOSTNAME = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$",
    re.IGNORECASE,
)


def rule_host(value: str, ctx: AttributeContext) -> Judgement | None:
    match = URL_HOST.match(value)
    host = match.group(1) if match else ""
    # Strip credentials and port before judging the hostname itself.
    host = host.rsplit("@", 1)[-1].split(":", 1)[0]
    if HOSTNAME.match(host):
        return passed("url.host", SEVERITY_ERROR, host=host)
    return failed(
        "url.host", SEVERITY_ERROR,
        "no resolvable hostname (expected something like example.com)",
        host=host, value=value,
    )


def rule_linkedin_host(value: str, ctx: AttributeContext) -> Judgement | None:
    if ctx.canonical_field != "linkedin_url":
        return None
    if "linkedin." in value.lower():
        return passed("linkedin.host", SEVERITY_WARNING)
    return failed(
        "linkedin.host", SEVERITY_WARNING,
        "mapped as a LinkedIn URL but the host is not linkedin.*",
        value=value,
    )


def rule_credentials_in_url(value: str, ctx: AttributeContext) -> Judgement | None:
    """A URL carrying a password should never have been in a vendor file."""
    match = URL_HOST.match(value)
    authority = match.group(1) if match else ""
    if "@" in authority and ":" in authority.rsplit("@", 1)[0]:
        return failed(
            "url.embedded_credentials", SEVERITY_WARNING,
            "the URL contains embedded credentials and should be treated as a "
            "secret rather than published",
        )
    return passed("url.embedded_credentials", SEVERITY_WARNING)


RULES = (rule_host, rule_linkedin_host, rule_credentials_in_url)
