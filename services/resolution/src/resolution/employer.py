"""Resolve the company named inside a person row.

A vendor person export describes two subjects. The person is what the record is
*about*; the employer is a company the record happens to know something about,
and it is a real entity with its own identity — the Gilbane in an Apollo person
row is the same Gilbane a company export describes.

Two decisions shape this module, and both are about restraint.

**A bare company name does not make an entity.** "Consulting", "Retired",
"Self Employed" and "Freelance" appear thousands of times across vendor files
meaning thousands of different things, and a name is a moderate key precisely
because it does not identify. So an employer entity is created only when the
row corroborates the name with something that does identify — a domain, a
LinkedIn page, the vendor's own company id, a phone or an address. Without
corroboration the employer's observations are still stored, in full, attributed
to the person's record; what does not happen is a company entity being invented
that will collide with every other vaguely-named employer.

**The relationship is asserted by a record, not by the system.** entity_relationship
carries the record that said so, which is what makes "why do we think Angela
works at Exac" answerable in the same way every other value is.
"""

import logging

from resolution import repository
from resolution.keys import keys_for
from resolution.scoring import DECISION_LINK

logger = logging.getLogger(__name__)

EMPLOYER_ROLE = "employer"
RELATIONSHIP_EMPLOYED_AT = "employed_at"

# An employer needs one of these beside its name before it becomes an entity.
# Each one identifies a company; a name on its own does not.
CORROBORATING_FIELDS = (
    "website",
    "linkedin_url",
    "company_external_id",
    "phone",
    "address_line1",
)

# Names that are not names. A row saying someone works at "Self Employed" is
# saying they do not have an employer, and turning that into a company entity
# would merge every self-employed person's employer into one organisation.
NON_EMPLOYERS = frozenset({
    "self employed", "selfemployed", "self-employed", "freelance", "freelancer",
    "unemployed", "retired", "none", "n/a", "na", "student", "independent",
    "self", "private", "not applicable", "unknown",
})


def _is_real_employer_name(name: str) -> bool:
    return name.strip().lower() not in NON_EMPLOYERS


def employer_identity(values: dict[str, list[str]]) -> tuple[str, bool]:
    """The employer's name, and whether the row identifies it well enough.

    Returns ('', False) when there is no usable employer in this row at all.
    """
    names = values.get("company_name") or []
    name = next((n for n in names if n and _is_real_employer_name(n)), "")
    if not name:
        return "", False
    corroborated = any(values.get(field) for field in CORROBORATING_FIELDS)
    return name, corroborated


def resolve_employer(
    conn, record_id: str, batch_id: str, source_id: str,
    employer_values: dict[str, list[str]], person_entity_id: str,
    new_entity_id: str | None = None,
) -> str | None:
    """Link this record's employer to a company entity. Returns the entity id.

    None means the row named no employer, or named one too vaguely to identify —
    both of which leave the observations stored and nothing invented.
    """
    name, corroborated = employer_identity(employer_values)
    if not name:
        return None

    keys = keys_for("company", employer_values, source_id)
    if not corroborated or not keys:
        logger.debug(
            "record %s names employer %r with nothing to identify it by; "
            "observations kept, no entity created", record_id, name,
        )
        return None

    # Deliberately the same lookup a company record gets. An employer named in a
    # person row and a company loaded from a company file must land on the same
    # entity, or the two halves of what is known about a company never meet.
    from resolution.pipeline import apply_decision, candidates_for

    decision = candidates_for(conn, "company", keys)
    outcome, entity_id = apply_decision(
        conn, record_id, "company", batch_id, source_id, keys, decision,
        new_entity_id=new_entity_id, role=EMPLOYER_ROLE,
    )

    repository.assert_relationship(
        conn, person_entity_id, entity_id, RELATIONSHIP_EMPLOYED_AT,
        record_id, batch_id, source_id,
        {"employer_name": name, "match_decision": outcome,
         "keys_built": [k.key_type for k in keys]},
    )
    if outcome == DECISION_LINK:
        logger.debug("record %s employer %r linked to existing %s",
                     record_id, name, entity_id)
    return entity_id
