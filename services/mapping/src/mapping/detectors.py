"""Value-shape detectors.

These answer "do these values look like emails?" — the TYPE of a column, never
which canonical field it is. A column of valid emails could be work_email or
personal_email; that is exactly why value analysis alone cannot auto-accept.
"""

import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
URL_RE = re.compile(r"^(https?://|www\.)\S+$|^[a-z0-9-]+(\.[a-z0-9-]+)+$", re.IGNORECASE)
LINKEDIN_RE = re.compile(r"linkedin\.com/", re.IGNORECASE)
PHONE_ALLOWED_RE = re.compile(r"^[\d\s()+\-.]+$")
INTEGER_RE = re.compile(r"^\d{1,9}$")
YEAR_RE = re.compile(r"^(1[89]\d{2}|20\d{2}|21\d{2})$")
POSTAL_RES = (
    re.compile(r"^\d{5}(-\d{4})?$"),                       # US
    re.compile(r"^\d{6}$"),                                # IN
    re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$", re.IGNORECASE),   # UK
    re.compile(r"^[A-Z]\d[A-Z]\s*\d[A-Z]\d$", re.IGNORECASE),            # CA
)


def is_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value))


def is_url(value: str) -> bool:
    return bool(URL_RE.match(value))


def is_linkedin(value: str) -> bool:
    return bool(LINKEDIN_RE.search(value))


def is_phone(value: str) -> bool:
    if not PHONE_ALLOWED_RE.match(value):
        return False
    digits = re.sub(r"\D", "", value)
    return 7 <= len(digits) <= 15


def is_integer(value: str) -> bool:
    return bool(INTEGER_RE.match(value))


def is_year(value: str) -> bool:
    return bool(YEAR_RE.match(value))


def is_postal_code(value: str) -> bool:
    return any(pattern.match(value.strip()) for pattern in POSTAL_RES)


DETECTORS = {
    "email": is_email,
    "url": is_url,
    "linkedin": is_linkedin,
    "phone": is_phone,
    "integer": is_integer,
    "year": is_year,
    "postal_code": is_postal_code,
}

# Detectors whose match narrows *which family of field* a column belongs to,
# and those that only describe the shape of a value.
#
# The distinction decides whether values may propose a field on their own. A
# column of linkedin.com URLs belongs to a LinkedIn field; a column of email
# addresses belongs to an email field. But every count, score, rating,
# identifier and year is an integer, and a US ZIP is indistinguishable from any
# other five digits — so proposing a field from those is naming one
# arbitrarily. Real evidence: `instagram_followers`, `ads_adwords` and
# `googlereviewscount` each proposed `employee_count` purely for being numeric.
#
# Shape detectors still corroborate a name-based candidate, which is their
# proper use: the column is *called* phone and the values are phone-shaped.
DISCRIMINATING_DETECTORS = frozenset({"email", "linkedin", "url"})

# Some detectors are strictly narrower than others: every LinkedIn URL is a
# URL, so both match and both would propose a field at the same capped
# confidence, leaving the winner to sort order. The narrower match is the more
# informative one and takes precedence.
DETECTOR_SPECIFICITY = {
    "linkedin": 2,
    "email": 2,
    "url": 1,
}


def match_ratio(detector: str, values: list[str]) -> float:
    """Fraction of non-empty values matching the detector. 0.0 when nothing to judge."""
    check = DETECTORS[detector]
    candidates = [v.strip() for v in values if v and v.strip()]
    if not candidates:
        return 0.0
    return sum(1 for value in candidates if check(value)) / len(candidates)
