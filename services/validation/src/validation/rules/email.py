"""Email rules."""

import re

from validation.rules.base import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    AttributeContext,
    Judgement,
    failed,
    passed,
)

# Deliberately not RFC 5322. A full RFC parser accepts addresses no mail system
# in practice will deliver to, and the point here is usability, not pedantry.
EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$")

# Mailboxes answered by a function, not a person. Real and useful — for a
# company. For a person record they mean the row probably names no individual.
ROLE_LOCAL_PARTS = {
    "info", "admin", "support", "sales", "contact", "webmaster", "office",
    "hello", "help", "mail", "enquiries", "inquiries", "hr", "careers", "jobs",
    "billing", "accounts", "noreply", "no-reply", "donotreply", "postmaster",
    "marketing", "press", "media", "team", "general",
}

# Free mailbox providers. Legitimate for a person; for a *company* address it
# usually means the record has a personal contact where an organisation was
# expected, which matters when the value is used to identify the company.
CONSUMER_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "hotmail.com",
    "outlook.com", "live.com", "msn.com", "aol.com", "icloud.com", "me.com",
    "mail.com", "gmx.com", "yandex.com", "protonmail.com", "proton.me",
    "rediffmail.com", "qq.com", "163.com",
}

MAX_LOCAL_PART = 64   # RFC 5321 limit
MAX_LENGTH = 254      # RFC 5321 limit on the whole path


def rule_syntax(value: str, ctx: AttributeContext) -> Judgement | None:
    if EMAIL.match(value):
        return passed("email.syntax", SEVERITY_ERROR)
    return failed(
        "email.syntax", SEVERITY_ERROR,
        "not a deliverable email address shape (expected local@domain.tld)",
        value=value,
    )


def rule_length(value: str, ctx: AttributeContext) -> Judgement | None:
    """Over the RFC limits no mail system will accept it, whatever its shape."""
    local = value.split("@", 1)[0] if "@" in value else value
    if len(value) <= MAX_LENGTH and len(local) <= MAX_LOCAL_PART:
        return passed("email.length", SEVERITY_ERROR)
    return failed(
        "email.length", SEVERITY_ERROR,
        f"exceeds the deliverable limits ({MAX_LOCAL_PART} local, {MAX_LENGTH} total)",
        length=len(value), local_length=len(local),
    )


def rule_role_account(value: str, ctx: AttributeContext) -> Judgement | None:
    if ctx.entity_type != "person" or "@" not in value:
        return None
    local = value.split("@", 1)[0]
    if local in ROLE_LOCAL_PARTS:
        return failed(
            "email.role_account", SEVERITY_WARNING,
            f"'{local}@' is a role mailbox, so it may not identify this person",
            local_part=local,
        )
    return passed("email.role_account", SEVERITY_WARNING)


def rule_consumer_domain(value: str, ctx: AttributeContext) -> Judgement | None:
    """A free mailbox on a company record is a personal contact, not the company."""
    if ctx.entity_type != "company" or "@" not in value:
        return None
    domain = value.rsplit("@", 1)[1]
    if domain in CONSUMER_DOMAINS:
        return failed(
            "email.consumer_domain", SEVERITY_WARNING,
            f"'{domain}' is a consumer mailbox provider, so this is probably a "
            "person's address rather than the organisation's",
            domain=domain,
        )
    return passed("email.consumer_domain", SEVERITY_WARNING)


RULES = (rule_syntax, rule_length, rule_role_account, rule_consumer_domain)
