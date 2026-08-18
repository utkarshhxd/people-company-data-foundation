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
from datetime import date, timedelta

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
_CURRENCY_SYMBOL = re.compile(r"[$£€¥₹]|\b(usd|eur|gbp|inr|jpy)\b")
# An optional suffix, so '1.2M' and '1200000' reduce to the same number.
_MONEY = re.compile(r"(?P<amount>-?\d+(?:\.\d+)?)\s*(?P<suffix>[kmb]|bn|mm)?")
_MONEY_SUFFIXES = {"k": 1_000, "m": 1_000_000, "mm": 1_000_000,
                   "b": 1_000_000_000, "bn": 1_000_000_000}
# Only unambiguous layouts. dd/mm/yyyy and mm/dd/yyyy are indistinguishable for
# the first twelve days of every month, so neither is accepted: a date that is
# wrong two thirds of the time is worse than no date.
# Excel counts days from 1900-01-01 as serial 1, but also counts a 29th of
# February in 1900 that did not exist -- so the epoch that makes real dates come
# out right is two days earlier, not one.
EXCEL_EPOCH = date(1899, 12, 30)
EXCEL_SERIAL_MIN = 20000   # 1954-10-03
EXCEL_SERIAL_MAX = 60000   # 2064-04-16
_DATE_FORMATS = (
    re.compile(r"(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?:[T ].*)?"),
    re.compile(r"(?P<y>\d{4})/(?P<m>\d{2})/(?P<d>\d{2})(?:[T ].*)?"),
)
# Two numbers with a range marker between them: '50-100', '10 to 50', '2019–20'.
# Matched after thousands separators are removed, so '1,200' is one number.
_RANGE = re.compile(r"\d\s*(?:-|–|—|\.\.|/|\bto\b)\s*\d", re.IGNORECASE)
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


def normalize_integer(value: str) -> str | Normalized | None:
    """A whole number, or nothing — never a number the source did not write.

    Stripping non-digits is right for formatting (`1,200`, a spreadsheet's
    leading `'`) and catastrophic for a range: `50-100` became `50100`, a
    headcount wrong by a factor of five hundred, which then won survivorship and
    reached the golden record with only a warning beside it. A warning next to a
    fabricated number is not the same as not storing one.

    So a range is refused. The source genuinely said something — it said it did
    not know the exact figure — and there is no integer field that can hold
    that. Refusing keeps the raw value visible and lets validation say why,
    which is the same treatment an ambiguous date gets.
    """
    text = value.strip()
    # Thousands separators and spreadsheet artefacts are formatting, not
    # meaning, and must go before the range test or `1,200` looks like one.
    for artefact in (",", "'", "’", "_"):
        text = text.replace(artefact, "")
    if _RANGE.search(text):
        return Normalized(None, "integer:range")
    digits = _NON_DIGIT.sub("", text)
    return digits.lstrip("0") or "0" if digits else None


def normalize_money(value: str) -> str | None:
    """A monetary amount, reduced to whole units of whatever currency it was in.

    Currency is deliberately NOT captured or converted. A vendor that ships an
    amount without a currency column has not told us the currency, and inventing
    one would be enrichment by guesswork — the exact thing this module refuses to
    do. Amounts are comparable within a source, which is what survivorship needs.

    Suffixes are read because real exports use them ('1.2M', '$3.4bn'). They
    multiply rather than truncate, so 1.2M and 1200000 normalize identically and
    two sources reporting the same figure differently still agree.
    """
    text = value.strip().lower().replace(",", "").replace("_", "")
    text = _CURRENCY_SYMBOL.sub("", text).strip()
    match = _MONEY.fullmatch(text)
    if match is None:
        return None

    amount = float(match.group("amount"))
    amount *= _MONEY_SUFFIXES.get(match.group("suffix") or "", 1)
    # Sub-unit precision in a reported revenue or funding total is noise, and
    # keeping it would make 3400000 and 3400000.0 two different values.
    return str(round(amount))


def _from_excel_serial(text: str) -> str | None:
    """Decode Excel's day-count, which is what a date column becomes via xlsx.

    Apollo's CSV export writes '2024-09-01T00:00:00+00:00'; the same field from
    the xlsx export arrives as '45047'. Both are the vendor stating a date, and
    reading only one of them threw away 221 dates per thousand rows.

    The epoch is 1899-12-30 rather than 1900-01-01 because Excel counts a
    29th of February in 1900 that never happened. Offsetting the epoch by the
    phantom day makes every date after 1900-03-01 come out right, and the range
    guard below keeps us well clear of the region where it does not.
    """
    if not text.isdigit():
        return None
    serial = int(text)
    # Roughly 1954 to 2064. A date column holding a number outside this is not
    # holding a date, and guessing would turn a wrong column into a wrong fact.
    if not EXCEL_SERIAL_MIN <= serial <= EXCEL_SERIAL_MAX:
        return None
    return (EXCEL_EPOCH + timedelta(days=serial)).isoformat()


def normalize_date(value: str) -> str | None:
    """A calendar date, as ISO yyyy-mm-dd.

    Time and timezone are dropped, not because they are worthless but because
    every field using this type reports a day ('last raised at', 'verified at')
    and keeping a timestamp would make two sources reporting the same day
    disagree. A field that genuinely needs the instant should not use this type.
    """
    text = value.strip()
    serial = _from_excel_serial(text)
    if serial is not None:
        return serial
    for pattern in _DATE_FORMATS:
        match = pattern.fullmatch(text)
        if match is None:
            continue
        parts = match.groupdict()
        try:
            return date(
                int(parts["y"]), int(parts["m"]), int(parts["d"])
            ).isoformat()
        except ValueError:
            # A well-shaped date that does not exist (2024-02-31). Not repairable
            # here; validation reports it and the raw value stays visible.
            return None
    return None


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
    "money": normalize_money,
    "date": normalize_date,
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
        # A normalizer may return a Normalized itself when it has a reason worth
        # naming. ':empty' says nothing usable was left, which is true of 'abc'
        # in a phone column and misleading for '50-100' — that value was
        # perfectly meaningful, just not as an integer.
        if isinstance(result, Normalized):
            return result
    except Exception:
        # Normalization must never lose a record. Keep the raw value visible
        # and let validation decide what to do with it.
        return Normalized(None, f"{value_type}:failed")

    if result is None or result == "":
        return Normalized(None, f"{value_type}:empty")
    return Normalized(result, value_type)
