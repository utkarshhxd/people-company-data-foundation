"""Field-type normalizers.

Two rules govern everything here:

1. The raw value is never modified — callers store it alongside the result.
2. Normalization only ever makes a value *comparable*. It does not repair,
   enrich, or judge. "is this a valid email" is validation's job (next
   increment); "which of two emails is correct" is the golden record's.

Addresses in particular are only whitespace-collapsed. Stripping their
punctuation would destroy meaning ('#610', 'Ave NW') for no gain.
"""

import re
from dataclasses import dataclass

# Placeholders that mean "no value". Deliberately conservative: 'na' is absent
# because it is a real value in some columns (Namibia, North America).
NULL_TOKENS = {
    "", "null", "n/a", "n\\a", "none", "nil", "-", "--", "---", "\\n", "(null)",
    "not available", "unknown",
}

# An explicit multi-value marker seen in real vendor exports:
#   CRAZEEGIRL.COM^^QUALITYSPIRITS.COM
EXPLICIT_MULTIVALUE = "^^"
# Only these types are split on ordinary punctuation. Never addresses or free
# text, where a comma or semicolon is part of the value.
SPLITTABLE_TYPES = {"email", "phone", "url"}
SPLIT_PATTERN = re.compile(r"[;|]")

_WHITESPACE = re.compile(r"\s+")
_NON_PHONE = re.compile(r"[^\d+]")
_NON_DIGIT = re.compile(r"\D")
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


@dataclass
class Normalized:
    normalized_value: str | None
    method: str
    is_null_token: bool = False


def is_null_token(value: str) -> bool:
    return value.strip().lower() in NULL_TOKENS


def split_values(raw: str, value_type: str) -> list[str]:
    """Split a cell into the values it actually contains, preserving order."""
    parts = [raw]
    if EXPLICIT_MULTIVALUE in raw:
        parts = raw.split(EXPLICIT_MULTIVALUE)
    elif value_type in SPLITTABLE_TYPES and SPLIT_PATTERN.search(raw):
        parts = SPLIT_PATTERN.split(raw)
    trimmed = [p for p in (part.strip() for part in parts) if p]
    # A cell that was only delimiters still counts as one (empty) observation.
    return trimmed or [raw.strip()]


def _collapse(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


def _smart_title(token: str) -> str:
    """Title-case a name token without mangling McKenna, O'Brien, or Jean-Luc."""
    def cap(part: str) -> str:
        if not part:
            return part
        if part.lower().startswith("mc") and len(part) > 2:
            return "Mc" + part[2:3].upper() + part[3:].lower()
        if part.lower().startswith("mac") and len(part) > 3:
            return "Mac" + part[3:4].upper() + part[4:].lower()
        return part[0].upper() + part[1:].lower()

    for sep in ("-", "'", "."):
        if sep in token:
            return sep.join(cap(p) if p else p for p in token.split(sep))
    return cap(token)


def normalize_person_name(value: str) -> str:
    collapsed = _collapse(value)
    tokens = []
    for token in collapsed.split(" "):
        # Leave deliberately-cased tokens alone; only fix ALL CAPS / all lower.
        tokens.append(_smart_title(token) if token.isupper() or token.islower() else token)
    return " ".join(tokens)


def normalize_email(value: str) -> str:
    return _collapse(value).lower()


def normalize_phone(value: str) -> str:
    """Keep digits and a leading '+'. No country is inferred.

    '+91 (98765)-43210' -> '+919876543210'. A bare '4153928863' stays as its
    digits: guessing a country code here would invent provenance the source
    never gave us. Country inference belongs to validation/enrichment.
    """
    cleaned = _NON_PHONE.sub("", value)
    if cleaned.startswith("+"):
        return "+" + _NON_DIGIT.sub("", cleaned)
    return _NON_DIGIT.sub("", cleaned)


def normalize_postal_code(value: str) -> str:
    # Upper-cased for UK/CA formats; spacing collapsed. Leading zeros survive
    # because every value is handled as a string end to end.
    return _collapse(value).upper()


def normalize_url(value: str) -> str:
    collapsed = _collapse(value).rstrip("/")
    match = _SCHEME.match(collapsed)
    if match:
        scheme = match.group(0).lower()
        rest = collapsed[len(match.group(0)):]
        host, sep, path = rest.partition("/")
        return scheme + host.lower() + sep + path
    host, sep, path = collapsed.partition("/")
    return host.lower() + sep + path


def normalize_integer(value: str) -> str | None:
    digits = _NON_DIGIT.sub("", value)
    return digits.lstrip("0") or "0" if digits else None


def normalize_identifier(value: str) -> str:
    # Case and leading zeros are significant in identifiers; only trim.
    return value.strip()


def normalize_address(value: str) -> str:
    # Whitespace only. Punctuation carries meaning in addresses.
    return _collapse(value)


def normalize_text(value: str) -> str:
    return _collapse(value)


def normalize_lower_token(value: str) -> str:
    return _collapse(value).lower()


def normalize_place_name(value: str) -> str:
    """'WASHINGTON' and 'Washington' must compare equal, so settle on one form."""
    collapsed = _collapse(value)
    return " ".join(
        _smart_title(t) if t.isupper() or t.islower() else t for t in collapsed.split(" ")
    )


def normalize_region_code(value: str) -> str:
    """Region names title-case; short codes upper-case ('dc' -> 'DC', not 'Dc')."""
    collapsed = _collapse(value)
    if len(collapsed) <= 3 and collapsed.isalpha():
        return collapsed.upper()
    return normalize_place_name(collapsed)


NORMALIZERS = {
    "person_name": normalize_person_name,
    "email": normalize_email,
    "phone": normalize_phone,
    "postal_code": normalize_postal_code,
    "url": normalize_url,
    "integer": normalize_integer,
    "identifier": normalize_identifier,
    "address": normalize_address,
    "text": normalize_text,
    "lower_token": normalize_lower_token,
    "place_name": normalize_place_name,
    "region_code": normalize_region_code,
}


def normalize(raw: str, value_type: str) -> Normalized:
    """Normalize one already-split value. Never raises on odd input."""
    if is_null_token(raw):
        return Normalized(None, f"{value_type}:null_token", is_null_token=True)

    func = NORMALIZERS.get(value_type, normalize_text)
    try:
        result = func(raw)
    except Exception:
        # Normalization must never lose a record. Keep the raw value visible
        # and let validation decide what to do with it.
        return Normalized(None, f"{value_type}:failed")

    if result is None or result == "":
        return Normalized(None, f"{value_type}:empty")
    return Normalized(result, value_type)
