"""Resolve a batch's records to real-world entities.

Records are processed in file order on one connection, so a record can match an
entity created by the record two rows above it -- each later record's read sees
every earlier record's write, because they share one open transaction until it
commits. That determinism is what must hold; the commit boundary is free to
move. Commits are batched (COMMIT_BATCH_SIZE at a time) rather than one per
record: a crash mid-batch loses only the still-uncommitted tail, and
`resolvable_records` already excludes anything already linked, so resuming
picks back up exactly where the batch left off, whatever the batch size was.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass

from common.db import connect

from resolution import repository
from resolution.employer import resolve_employer
from resolution.keys import (
    DESCRIBES_ORGANISATION,
    MODERATE,
    STRONG,
    IdentityKey,
    keys_for,
)
from resolution.scoring import (
    DECISION_LINK,
    DECISION_NEW,
    DECISION_REVIEW,
    decide,
)

logger = logging.getLogger(__name__)

# Records per commit. See the module docstring for why batching this is safe:
# resumability comes from resolvable_records excluding already-linked records,
# not from the commit granularity.
COMMIT_BATCH_SIZE = 500


class BatchNotResolvable(Exception):
    pass


@dataclass
class ResolveResult:
    batch_id: str
    records: int
    linked: int
    created: int
    candidates: int
    skipped: int
    counts: dict[str, int]


def candidates_for(conn, entity_type: str, keys: list[IdentityKey]):
    """Look up the entities these keys already point at, grouped per entity.

    A key's stored strength is whatever the source that first wrote it believed.
    The record being resolved now may come from a source that believes something
    different -- a directory listing premises holds a domain that a company file
    stored as decisive -- so the two are reconciled by taking the weaker.

    If either side says this key cannot identify on its own, it cannot. Erring
    the other way would let one organisation-source entity pull in every branch
    that happens to share its domain.
    """
    # Locked before the read: this is the one place every resolution path
    # funnels through (a person or company record's own resolution, and an
    # employer's), so a lock here closes the create-entity race for all of
    # them. See repository.lock_keys for what it prevents.
    repository.lock_keys(conn, entity_type, keys)
    rows = repository.find_candidates(
        conn, entity_type, [(k.key_type, k.key_value) for k in keys]
    )
    as_built = {(k.key_type, k.key_value): k.strength for k in keys}

    overlaps: dict[str, list[IdentityKey]] = defaultdict(list)
    for row in rows:
        stored = row["strength"]
        mine = as_built.get((row["key_type"], row["key_value"]), stored)
        strength = STRONG if stored == STRONG and mine == STRONG else MODERATE
        overlaps[str(row["entity_id"])].append(
            IdentityKey(row["key_type"], row["key_value"], strength)
        )
    return decide(entity_type, dict(overlaps))


def apply_decision(
    conn, record_id: str, entity_type: str, batch_id: str, source_id: str,
    keys: list[IdentityKey], decision, new_entity_id: str | None = None,
    role: str = "self",
) -> tuple[str, str]:
    """Write the consequences of a match decision. Returns (decision, entity_id).

    Separated from the lookups above so the record-at-a-time pipeline can supply
    values it already holds in memory without either path reimplementing what a
    decision *means*. `decision` is None when no identity key could be built.

    `new_entity_id` lets a caller supply the id instead of taking it from a
    RETURNING round-trip; the id is a UUIDv7 either way.
    """
    if decision is None:
        # Validation guarantees a resolvable record has an identifying
        # attribute, so this means the identifier is in a field we cannot build
        # a key from. Recording it as its own entity is honest; silently
        # dropping it would not be.
        entity_id = repository.create_entity(
            conn, entity_type, record_id, entity_id=new_entity_id
        )
        repository.link_record(
            conn, record_id, entity_id, entity_type, batch_id, source_id,
            "no_identity_keys", 0.0, "new_entity",
            {"reason": "no identity key could be built from the confirmed fields"},
            role=role,
        )
        return DECISION_NEW, entity_id

    evidence = {
        "keys_built": [{"key_type": k.key_type, "strength": k.strength} for k in keys],
        "candidates_considered": [
            {"entity_id": m.entity_id, "confidence": m.confidence, "method": m.method}
            for m in decision.considered
        ],
    }

    if decision.decision == DECISION_LINK:
        match = decision.match
        repository.link_record(
            conn, record_id, match.entity_id, entity_type, batch_id, source_id,
            match.method, match.confidence, "auto_linked",
            {**evidence, **match.evidence}, role=role,
        )
        # The record's own keys join the entity, so the next vendor's spelling
        # of the same organisation still finds it.
        repository.add_keys(conn, match.entity_id, entity_type, record_id, keys)
        return DECISION_LINK, match.entity_id

    # Below the auto-link line the record still becomes an entity of its own.
    # Blocking the pipeline on a human would stall every downstream stage, and
    # inventing the merge is exactly what the spec forbids.
    entity_id = repository.create_entity(
        conn, entity_type, record_id, entity_id=new_entity_id
    )
    repository.link_record(
        conn, record_id, entity_id, entity_type, batch_id, source_id,
        decision.match.method if decision.match else "no_match",
        decision.match.confidence if decision.match else 0.0,
        "new_entity", evidence, role=role,
    )
    repository.add_keys(conn, entity_id, entity_type, record_id, keys)

    if decision.decision == DECISION_REVIEW:
        match = decision.match
        repository.record_candidate(
            conn, record_id, match.entity_id, entity_type,
            match.method, match.confidence, {**evidence, **match.evidence},
        )
        return DECISION_REVIEW, entity_id
    return DECISION_NEW, entity_id


def resolve_record(conn, record, batch, batch_id: str) -> str:
    """Resolve one record. Returns the decision taken for the record itself."""
    record_id = str(record["record_id"])
    entity_type = batch["entity_type"]
    source_id = str(batch["source_id"])

    describes = batch.get("describes", DESCRIBES_ORGANISATION)

    values = repository.record_values(conn, record_id)
    keys = keys_for(
        entity_type, values, source_id,
        role_email=repository.has_role_email(conn, record_id),
        describes=describes,
    )
    decision = candidates_for(conn, entity_type, keys) if keys else None
    outcome, entity_id = apply_decision(
        conn, record_id, entity_type, batch_id, source_id, keys, decision
    )

    # The employer named inside a person row is a company in its own right. It
    # is resolved after the person because the relationship needs both ids, and
    # its outcome deliberately does not change the record's own — whether we
    # could identify someone's employer says nothing about whether we
    # identified them.
    if entity_type == "person":
        employer_values = repository.record_values(conn, record_id, "employer")
        if employer_values:
            # The employer is an organisation however the source is
            # catalogued: a directory of premises still names a company in its
            # owner field, and that company is not a place.
            resolve_employer(
                conn, record_id, batch_id, source_id, employer_values, entity_id
            )
    return outcome


def resolve_batch(batch_id: str) -> ResolveResult:
    with connect() as conn:
        batch = repository.get_batch(conn, batch_id)
        if batch is None:
            raise BatchNotResolvable(f"batch {batch_id} not found")
        if batch["status"] != "completed":
            raise BatchNotResolvable(
                f"batch {batch_id} has status {batch['status']!r}, expected 'completed'"
            )

        records = repository.resolvable_records(conn, batch_id)
        if not records:
            raise BatchNotResolvable(
                f"batch {batch_id} has no unresolved records that passed validation; "
                "validate it first, or release its quarantined records"
            )

        linked = created = candidates = 0
        for i, record in enumerate(records, start=1):
            outcome = resolve_record(conn, record, batch, batch_id)
            if outcome == DECISION_LINK:
                linked += 1
            else:
                created += 1
                if outcome == DECISION_REVIEW:
                    candidates += 1
            # Batched, not per record -- see the module docstring. The next
            # record still matches what this one just created, because reads
            # within the same open transaction see it regardless of commit.
            if i % COMMIT_BATCH_SIZE == 0:
                conn.commit()
        conn.commit()

        counts = repository.resolution_counts(conn, batch_id)
        logger.info(
            "batch %s resolved: %d record(s), %d linked, %d new entities, "
            "%d awaiting review %s",
            batch_id, len(records), linked, created, candidates, counts,
        )
        return ResolveResult(
            batch_id, len(records), linked, created, candidates, 0, counts
        )


def accept_candidate(candidate_id: str, actor: str, note: str | None) -> dict:
    """Accept a proposed match: merge the record's entity into the candidate's."""
    with connect() as conn:
        # Locked: accept is a read-check-merge-close sequence, and two of
        # them running at once would both pass the check and both merge.
        candidate = repository.get_candidate(conn, candidate_id, for_update=True)
        if candidate is None:
            raise BatchNotResolvable(f"candidate {candidate_id} not found")
        if candidate["status"] != "open":
            raise BatchNotResolvable(
                f"candidate is already {candidate['status']}"
            )

        surviving = repository.resolve_entity_id(conn, str(candidate["entity_id"]))
        merged = repository.resolve_entity_id(conn, str(candidate["record_entity_id"]))
        if surviving is None or merged is None:
            raise BatchNotResolvable("one of the entities no longer exists")
        if surviving == merged:
            # Another accepted candidate already merged them. Closing the
            # candidate is still right; merging again would not be.
            repository.close_candidate(conn, candidate_id, "accepted", actor, note)
            conn.commit()
            return {"already_merged": True, "entity_id": surviving}

        moved = repository.merge_entities(
            conn, merged, surviving, candidate["entity_type"], actor, note,
            float(candidate["match_confidence"]),
        )
        repository.close_candidate(conn, candidate_id, "accepted", actor, note)
        conn.commit()
        return {"merged_entity_id": merged, "surviving_entity_id": surviving, **moved}


def reject_candidate(candidate_id: str, actor: str, note: str | None) -> None:
    """Reject a proposed match. Both entities stay separate, which is the whole
    point of not having merged them automatically."""
    with connect() as conn:
        candidate = repository.get_candidate(conn, candidate_id, for_update=True)
        if candidate is None:
            raise BatchNotResolvable(f"candidate {candidate_id} not found")
        if candidate["status"] != "open":
            raise BatchNotResolvable(f"candidate is already {candidate['status']}")
        repository.close_candidate(conn, candidate_id, "rejected", actor, note)
        conn.commit()
