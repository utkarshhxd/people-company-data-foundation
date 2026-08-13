"""Identity keys: the values that make an entity findable, and how much each one
is worth as evidence.

Two jobs, deliberately kept apart:

  blocking  - a key narrows millions of entities down to a handful of
              candidates. Cheap and recall-oriented; a bad blocking key costs
              time, not correctness.
  evidence  - a *strong* key can carry a link on its own; a *moderate* one never
              can, whatever it is combined with. That asymmetry is what stops
              two different people at the same company from becoming one person.

A key is only ever built from a CONFIRMED canonical field with a normalized
value. An unreviewed mapping must not decide who someone is.
"""

import re
from dataclasses import dataclass

STRONG = "strong"
MODERATE = "moderate"

# How much a shared key of this type implies "same real-world entity".
# Only strong keys reach the auto-link threshold; this table is the single place
# that judgement lives.
KEY_WEIGHTS: dict[str, dict[str, float]] = {
    "person": {
        "external_id": 0.98,
        "linkedin": 0.95,
        "email": 0.95,
        "name_company": 0.80,
        "name_city": 0.70,
        "phone": 0.40,
    },
    "company": {
        "external_id": 0.98,
        "website_domain": 0.92,
        "email": 0.90,
        "name_city": 0.80,
        "name": 0.65,
        "phone": 0.45,
    },
}

STRONG_KEY_TYPES = {
    "person": {"external_id", "linkedin", "email"},
    "company": {"external_id", "website_domain", "email"},
}

_WWW = re.compile(r"^www\d*\.", re.IGNORECASE)
_TRACKING = re.compile(r"[?#].*$")


@dataclass(frozen=True)
class IdentityKey:
    key_type: str
    key_value: str
    strength: str


def _first(values: dict[str, list[str]], field: str) -> str | None:
    got = values.get(field)
    return got[0] if got else None


def website_domain(url: str) -> str | None:
    """Reduce a URL to the host that identifies the organisation.

    asiafoundation.org, www.asiafoundation.org and https://www.asiafoundation.org/
    are one company; the scheme and the www prefix are noise.
    """
    cleaned = _TRACKING.sub("", url.strip())
    cleaned = re.sub(r"^[a-z][a-z0-9+.-]*://", "", cleaned, flags=re.IGNORECASE)
    host = cleaned.split("/", 1)[0].rsplit("@", 1)[-1].split(":", 1)[0].lower()
    host = _WWW.sub("", host)
    return host if "." in host else None


def linkedin_handle(url: str) -> str | None:
    """The profile path, not the URL. Query strings and trailing slashes differ
    between vendors for what is plainly the same profile."""
    cleaned = _TRACKING.sub("", url.strip().lower()).rstrip("/")
    if "linkedin." not in cleaned:
        return None
    _, sep, path = cleaned.partition("linkedin.com")
    if not sep or not path:
        return None
    return path.strip("/") or None


def _strength(entity_type: str, key_type: str) -> str:
    return STRONG if key_type in STRONG_KEY_TYPES[entity_type] else MODERATE


def _key(entity_type: str, key_type: str, value: str | None) -> IdentityKey | None:
    if not value:
        return None
    return IdentityKey(key_type, value, _strength(entity_type, key_type))


def person_keys(
    values: dict[str, list[str]], source_id: str, role_email: bool = False
) -> list[IdentityKey]:
    keys: list[IdentityKey] = []

    external = _first(values, "person_external_id")
    if external:
        # Scoped to the source: a vendor's key is authoritative within that
        # vendor's data and meaningless across vendors, who reuse the same
        # integers for entirely different people.
        keys.append(IdentityKey("external_id", f"{source_id}:{external}", STRONG))

    for url in values.get("linkedin_url", []):
        handle = linkedin_handle(url)
        if handle:
            keys.append(IdentityKey("linkedin", handle, STRONG))

    for email in values.get("email", []):
        # A role mailbox belongs to a function, not a person — validation already
        # said so for this record, and honouring that verdict here stops everyone
        # who shares info@ from collapsing into one human being.
        keys.append(IdentityKey("email", email, MODERATE if role_email else STRONG))

    name = _first(values, "full_name") or _joined_name(values)
    if name:
        employer = _first(values, "company_name")
        if employer:
            keys.append(IdentityKey("name_company", f"{name}|{employer}", MODERATE))
        city = _first(values, "city")
        if city:
            keys.append(IdentityKey("name_city", f"{name}|{city}", MODERATE))

    for phone in values.get("phone", []):
        keys.append(IdentityKey("phone", phone, MODERATE))

    return _dedupe(keys)


def _joined_name(values: dict[str, list[str]]) -> str | None:
    first, last = _first(values, "first_name"), _first(values, "last_name")
    return f"{first} {last}" if first and last else None


def company_keys(values: dict[str, list[str]], source_id: str) -> list[IdentityKey]:
    keys: list[IdentityKey] = []

    external = _first(values, "company_external_id")
    if external:
        keys.append(IdentityKey("external_id", f"{source_id}:{external}", STRONG))

    for url in values.get("website", []):
        domain = website_domain(url)
        if domain:
            keys.append(IdentityKey("website_domain", domain, STRONG))

    for email in values.get("email", []):
        keys.append(IdentityKey("email", email, STRONG))

    name = _first(values, "company_name") or _first(values, "legal_name")
    if name:
        keys.append(IdentityKey("name", name, MODERATE))
        city = _first(values, "city")
        if city:
            keys.append(IdentityKey("name_city", f"{name}|{city}", MODERATE))

    for phone in values.get("phone", []):
        keys.append(IdentityKey("phone", phone, MODERATE))

    return _dedupe(keys)


def _dedupe(keys: list[IdentityKey]) -> list[IdentityKey]:
    seen, out = set(), []
    for key in keys:
        marker = (key.key_type, key.key_value)
        if marker not in seen:
            seen.add(marker)
            out.append(key)
    return out


def keys_for(
    entity_type: str, values: dict[str, list[str]], source_id: str,
    role_email: bool = False,
) -> list[IdentityKey]:
    if entity_type == "person":
        return person_keys(values, source_id, role_email)
    if entity_type == "company":
        return company_keys(values, source_id)
    raise ValueError(f"unknown entity_type {entity_type!r}")
