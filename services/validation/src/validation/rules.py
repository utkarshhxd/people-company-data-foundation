"""Attribute-level validation rules.

A rule asks one question: *is this value usable as the canonical field it was
mapped to?* It never answers "is this value correct" (nothing here can know
that) and it never changes the value.

Three severities, and the difference matters because it decides whether a record
is merely suspicious or actually unusable:

  error   - the value cannot serve as this field at all. An email with no '@'
            is not a weak email, it is not an email.
  warning - plausible but suspect. A phone with no country code is usable; it
            is just ambiguous internationally.
  info    - an observation worth recording, no judgement implied.

Rules return None when they do not apply, which is different from passing. That
distinction is preserved all the way into the database, because "no rule ran"
and "every rule passed" are very different statements about a record.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

RULESET_VERSION = "1"

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

PASS = "pass"
FAIL = "fail"


@dataclass(frozen=True)
class Judgement:
    rule_id: str
    severity: str
    outcome: str
    message: str | None = None
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AttributeContext:
    canonical_field: str
    entity_type: str
    value_type: str
    raw_value: str
    normalization_method: str


Rule = Callable[[str, AttributeContext], Judgement | None]


def _passed(rule_id: str, severity: str, **details) -> Judgement:
    return Judgement(rule_id, severity, PASS, None, details)


def _failed(rule_id: str, severity: str, message: str, **details) -> Judgement:
    return Judgement(rule_id, severity, FAIL, message, details)


# --------------------------------------------------------------------------
# email
# --------------------------------------------------------------------------

# Deliberately not RFC 5322. A full RFC parser accepts addresses no mail system
# in practice will deliver to, and the point here is usability, not pedantry.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$")

# Mailboxes answered by a function, not a person. Real and useful — for a
# company. For a person record they mean the row probably names no individual.
ROLE_LOCAL_PARTS = {
    "info", "admin", "support", "sales", "contact", "webmaster", "office",
    "hello", "help", "mail", "enquiries", "inquiries", "hr", "careers", "jobs",
    "billing", "accounts", "noreply", "no-reply", "donotreply", "postmaster",
    "marketing", "press", "media", "team", "general",
}


def rule_email_syntax(value: str, ctx: AttributeContext) -> Judgement | None:
    if _EMAIL.match(value):
        return _passed("email.syntax", SEVERITY_ERROR)
    return _failed(
        "email.syntax", SEVERITY_ERROR,
        "not a deliverable email address shape (expected local@domain.tld)",
        value=value,
    )


def rule_email_role_account(value: str, ctx: AttributeContext) -> Judgement | None:
    if ctx.entity_type != "person" or "@" not in value:
        return None
    local = value.split("@", 1)[0]
    if local in ROLE_LOCAL_PARTS:
        return _failed(
            "email.role_account", SEVERITY_WARNING,
            f"'{local}@' is a role mailbox, so it may not identify this person",
            local_part=local,
        )
    return _passed("email.role_account", SEVERITY_WARNING)


# --------------------------------------------------------------------------
# phone
# --------------------------------------------------------------------------

# E.164 caps a full international number at 15 digits; 7 is about the shortest
# real subscriber number. Outside that range the value is not a phone number.
PHONE_MIN_DIGITS = 7
PHONE_MAX_DIGITS = 15

_DIGITS = re.compile(r"\d")


def rule_phone_digit_count(value: str, ctx: AttributeContext) -> Judgement | None:
    digits = len(_DIGITS.findall(value))
    if PHONE_MIN_DIGITS <= digits <= PHONE_MAX_DIGITS:
        return _passed("phone.digit_count", SEVERITY_ERROR, digits=digits)
    return _failed(
        "phone.digit_count", SEVERITY_ERROR,
        f"{digits} digits is outside the dialable range "
        f"{PHONE_MIN_DIGITS}-{PHONE_MAX_DIGITS}",
        digits=digits,
    )


def rule_phone_country_code(value: str, ctx: AttributeContext) -> Judgement | None:
    """A warning, never an error — normalization refuses to guess a country.

    Recording the ambiguity is the honest move: the number may well be fine,
    but nothing in the file says which country it belongs to.
    """
    if value.startswith("+"):
        return _passed("phone.country_code", SEVERITY_WARNING)
    return _failed(
        "phone.country_code", SEVERITY_WARNING,
        "no country code, so the number is only dialable if the country is known",
    )


# --------------------------------------------------------------------------
# url
# --------------------------------------------------------------------------

_URL_HOST = re.compile(r"^(?:[a-z][a-z0-9+.-]*://)?([^/\s?#]+)", re.IGNORECASE)
_HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$",
                       re.IGNORECASE)


def rule_url_host(value: str, ctx: AttributeContext) -> Judgement | None:
    match = _URL_HOST.match(value)
    host = match.group(1) if match else ""
    # Strip credentials and port before judging the hostname itself.
    host = host.rsplit("@", 1)[-1].split(":", 1)[0]
    if _HOSTNAME.match(host):
        return _passed("url.host", SEVERITY_ERROR, host=host)
    return _failed(
        "url.host", SEVERITY_ERROR,
        "no resolvable hostname (expected something like example.com)",
        host=host, value=value,
    )


def rule_linkedin_host(value: str, ctx: AttributeContext) -> Judgement | None:
    if ctx.canonical_field != "linkedin_url":
        return None
    if "linkedin." in value.lower():
        return _passed("linkedin.host", SEVERITY_WARNING)
    return _failed(
        "linkedin.host", SEVERITY_WARNING,
        "mapped as a LinkedIn URL but the host is not linkedin.*",
        value=value,
    )


# --------------------------------------------------------------------------
# names
# --------------------------------------------------------------------------

# Filler that survives normalization because it is not a null token.
PLACEHOLDER_NAMES = {
    "test", "tests", "testing", "asdf", "qwerty", "xxx", "xxxx", "abc", "aaa",
    "sample", "dummy", "example", "tbd", "todo", "no name", "noname", "name",
    "first last", "john doe", "jane doe",
}

MIN_NAME_LENGTH = 2


def rule_name_length(value: str, ctx: AttributeContext) -> Judgement | None:
    if len(value) >= MIN_NAME_LENGTH:
        return _passed("name.length", SEVERITY_ERROR, length=len(value))
    return _failed(
        "name.length", SEVERITY_ERROR,
        f"{len(value)} character(s) is too short to be a name",
        length=len(value),
    )


def rule_name_placeholder(value: str, ctx: AttributeContext) -> Judgement | None:
    if value.strip().lower() in PLACEHOLDER_NAMES:
        return _failed(
            "name.placeholder", SEVERITY_WARNING,
            "looks like filler text rather than a real name", value=value,
        )
    return _passed("name.placeholder", SEVERITY_WARNING)


def rule_name_shape(value: str, ctx: AttributeContext) -> Judgement | None:
    """Catches columns that drifted: an email or an ID sitting in a name field."""
    if "@" in value:
        return _failed("name.shape", SEVERITY_WARNING,
                       "contains '@', so this may be an email in a name column",
                       value=value)
    if len(_DIGITS.findall(value)) >= 3:
        return _failed("name.shape", SEVERITY_WARNING,
                       "contains 3+ digits, so this may be an identifier",
                       value=value)
    return _passed("name.shape", SEVERITY_WARNING)


# --------------------------------------------------------------------------
# numbers
# --------------------------------------------------------------------------

MAX_EMPLOYEES = 5_000_000  # comfortably above the largest employer on earth
MIN_FOUNDED_YEAR = 1600

_NUMERIC_INPUT = re.compile(r"^\d+$")


def rule_integer_input(value: str, ctx: AttributeContext) -> Judgement | None:
    """The RAW value is judged here, not the normalized one.

    '50-100' and '1,200+' normalize to digit strings that look clean but mean
    something the source never said. The distortion is only visible upstream.
    """
    raw = ctx.raw_value.strip()
    if _NUMERIC_INPUT.match(raw):
        return _passed("integer.input", SEVERITY_WARNING)
    return _failed(
        "integer.input", SEVERITY_WARNING,
        "raw value is not a plain integer, so the normalized number may distort it",
        raw_value=ctx.raw_value, normalized_value=value,
    )


def rule_employee_count_range(value: str, ctx: AttributeContext) -> Judgement | None:
    if ctx.canonical_field != "employee_count":
        return None
    count = int(value)
    if count > MAX_EMPLOYEES:
        return _failed("employee_count.range", SEVERITY_ERROR,
                       f"{count} exceeds any plausible headcount", count=count)
    if count == 0:
        return _failed("employee_count.range", SEVERITY_WARNING,
                       "zero employees is unusual for an operating company", count=count)
    return _passed("employee_count.range", SEVERITY_ERROR, count=count)


def rule_founded_year_range(value: str, ctx: AttributeContext) -> Judgement | None:
    if ctx.canonical_field != "founded_year":
        return None
    year = int(value)
    current = datetime.now(timezone.utc).year
    if year < MIN_FOUNDED_YEAR:
        return _failed("founded_year.range", SEVERITY_ERROR,
                       f"{year} predates {MIN_FOUNDED_YEAR}", year=year)
    if year > current:
        return _failed("founded_year.range", SEVERITY_ERROR,
                       f"{year} is in the future", year=year, current_year=current)
    return _passed("founded_year.range", SEVERITY_ERROR, year=year)


# --------------------------------------------------------------------------
# postal code
# --------------------------------------------------------------------------

POSTAL_MIN_LENGTH = 3
POSTAL_MAX_LENGTH = 10
_POSTAL_CHARS = re.compile(r"^[A-Z0-9][A-Z0-9 -]*$")


def rule_postal_code_shape(value: str, ctx: AttributeContext) -> Judgement | None:
    """A warning only — postal formats vary far too much between countries to
    reject a value without knowing which country it belongs to."""
    if (POSTAL_MIN_LENGTH <= len(value) <= POSTAL_MAX_LENGTH
            and _POSTAL_CHARS.match(value)):
        return _passed("postal_code.shape", SEVERITY_WARNING)
    return _failed("postal_code.shape", SEVERITY_WARNING,
                   "unusual shape for a postal code", value=value, length=len(value))


# --------------------------------------------------------------------------
# values that survived normalization but carry nothing
# --------------------------------------------------------------------------

def rule_normalization_outcome(method: str, raw_value: str) -> Judgement | None:
    """Runs when there is no normalized value but the source did write something.

    ':failed' means the normalizer raised; ':empty' means it consumed the whole
    value — 'abc' in a phone column leaves zero digits behind. Either way the
    source wrote a value that cannot serve as this field.
    """
    if method.endswith(":failed"):
        return _failed("value.normalization_failed", SEVERITY_ERROR,
                       "the value could not be normalized for this field type",
                       raw_value=raw_value, method=method)
    if method.endswith(":empty"):
        return _failed("value.empty_after_normalization", SEVERITY_ERROR,
                       "nothing usable remained once the value was normalized",
                       raw_value=raw_value, method=method)
    return None


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

RULES_BY_VALUE_TYPE: dict[str, tuple[Rule, ...]] = {
    "email": (rule_email_syntax, rule_email_role_account),
    "phone": (rule_phone_digit_count, rule_phone_country_code),
    "url": (rule_url_host, rule_linkedin_host),
    "person_name": (rule_name_length, rule_name_placeholder, rule_name_shape),
    "integer": (rule_integer_input, rule_employee_count_range, rule_founded_year_range),
    "postal_code": (rule_postal_code_shape,),
}

# Free text, addresses, identifiers, place names and region codes have no
# syntactic rules on purpose: any string can legitimately be a street address, a
# vendor's own key, or a town. Inventing rules for them would manufacture
# failures rather than find them.
UNRULED_VALUE_TYPES = frozenset(
    {"text", "address", "identifier", "place_name", "region_code", "lower_token"}
)


def judge_attribute(
    normalized_value: str | None, ctx: AttributeContext
) -> list[Judgement]:
    """All applicable judgements for one observation. Empty means no rule ran."""
    if normalized_value is None:
        outcome = rule_normalization_outcome(ctx.normalization_method, ctx.raw_value)
        return [outcome] if outcome else []

    judgements = []
    for rule in RULES_BY_VALUE_TYPE.get(ctx.value_type, ()):
        try:
            judgement = rule(normalized_value, ctx)
        except Exception as exc:
            # A broken rule must never take down a batch, and must never be
            # mistaken for a passing one.
            judgement = _failed(
                "rule.error", SEVERITY_INFO,
                f"rule {rule.__name__} raised: {exc}", rule=rule.__name__,
            )
        if judgement is not None:
            judgements.append(judgement)
    return judgements
