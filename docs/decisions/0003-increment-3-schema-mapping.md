# ADR 0003: Increment 3 — schema mapping

## Status

Accepted.

## Context

`raw_record` holds source rows verbatim, keyed by whatever column names the
vendor used — `e_mail`, `mobile_no`, `org_name`, `designation`. Nothing knew
those meant email, phone, company, job title.

This increment implements step 9, which the spec requires to happen *before*
normalization and validation. It decides, per source column, which canonical
field it represents, and records **how** the decision was made, **how
confident** it is, and **whether a human must look at it**.

## Decisions

- **Scope: derive, persist, and review the mapping — not apply it.** Mapping
  is a decision about a *schema*; applying it to values belongs with
  normalization (increment 4), which already transforms values. Keeps the
  increments small and the concerns separate.

- **Mapping is keyed to a source SCHEMA, not a batch.** A `column_fingerprint`
  (sha256 of the ordered column list) identifies a layout; a later batch with
  the same fingerprint **reuses the existing mapping**. Without this, review
  would be pointless — every re-ingest would throw away the human's work.
  Verified: after correcting `customer_ref` → `person_external_id`, a
  re-ingest kept the correction (`manual` / `approved` / `reviewed_by`).

- **Four strategies, each recording its own method and confidence:**
  `exact_alias` (1.0) → `similarity` (difflib ratio) → `value_analysis`
  (capped) → `semantic` (reserved, unimplemented). Corroboration between name
  and value evidence adds +0.15, and is the only route by which a non-exact
  match reaches auto-accept.

- **Value analysis alone can never auto-accept (hard cap 0.75 →
  `needs_review`).** A column of valid emails could be `work_email` or
  `personal_email` — values prove the column's *type*, never *which field* it
  is. This is the spec's "do not allow AI/heuristics to silently make
  high-risk mappings" rule made concrete. Verified with `reachable_at`, which
  mapped to `phone` at exactly 0.750 and stayed in review.

- **Collisions force review.** When two columns claim the same canonical
  field, *all* claimants drop to `needs_review` with the conflict recorded in
  evidence, rather than the higher score silently winning. Verified: `email`
  and `e_mail` both matched an alias at 1.0 and both were demoted.

- **Thresholds**: ≥0.90 `auto_accepted`, 0.60–0.90 `needs_review`, <0.60
  `unmapped` (canonical_field NULL). Unmapped columns are still *recorded* —
  an unmapped column is a fact about the source, and the review CLI can map it
  by hand. Verified: `customer_ref`, `joined`, `notes`, `notes__2` were left
  unmapped rather than force-fit into a plausible-looking field.

- **`evidence jsonb` on every mapping** records the matched alias, similarity
  score, value-analysis ratio and sample size, rejected alternatives, and
  collision notes. Mapping confidence is deliberately its own column, never
  conflated with source reliability, validation result, or match confidence.

- **AI deferred.** The deterministic layers cover the common vendor
  vocabulary, stay reproducible, and need no API keys or per-run cost. The
  `semantic` method name is reserved in the CHECK constraint so adding it
  later needs no migration of existing rows.

- **Trigger: a Kafka consumer on `batch.ingested`, plus CLIs.** This is the
  first *consumer* in the system — until now the event backbone was only
  proven to produce. Batch-level events are low volume, so consumer-group
  complexity stays manageable. Verified: ingesting a file caused mapping to
  happen with no manual step.

- **Consumer semantics: manual offset commit after successful processing**
  (at-least-once). Reprocessing is safe because `map_batch` reuses an existing
  `source_schema`. A `BatchNotMappable` error is permanent, so the offset is
  committed to avoid an infinite retry loop; any other exception is treated as
  transient and the process exits non-zero so the container restarts and
  resumes from the last committed offset rather than dropping the message.

- **`EventProducer` moved from `services/ingestion` to `libs/common/kafka.py`**
  now that two services publish events — one implementation, not a copy.

## Consequences

- The canonical vocabulary in `libs/common/canonical.py` is now a shared
  dependency of every later increment. `CANONICAL_SCHEMA_VERSION` is stamped
  on every stored mapping, so changing field meanings later is a versioned
  migration rather than a silent reinterpretation.
- Alias lists are the accumulated knowledge of what vendors call things and
  are expected to grow. Adding an alias changes future mappings but not
  already-reviewed ones, since those are pinned by `source_schema`.
