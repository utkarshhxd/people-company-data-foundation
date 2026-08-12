# ADR 0005: Increment 5 — validation

## Status

Accepted.

## Context

Increment 4 made values comparable and stored every one of them losslessly.
Nothing yet said whether a value is *usable*. `not-an-email` normalizes cleanly
to `not-an-email`; a company founded in `2099` normalizes to the integer `2099`.
Normalization deliberately does not judge, so something else has to.

The spec is emphatic that **invalid records must not disappear**, and that
validation result is its own dimension — never to be conflated with mapping
confidence, source reliability, or match confidence. A perfectly-mapped column
from a trusted vendor can still hold a broken email; all four numbers must stay
separately answerable.

## Decisions

- **Validation asserts, it never repairs.** No rule modifies a value, and no
  rule deletes a record. Judgements are attached alongside the observation,
  which is untouched. `invalid` means *"not safe to resolve to an entity yet"*,
  not *"delete me"* — verified: the 5 invalid records in the broken fixture keep
  all 30 of their observations.

- **Validation runs on observations, not raw payloads.** The observation is
  where the normalized value and the confirmed canonical field meet, which is
  the only place a rule has enough context to ask "is this usable *as this
  field*".

- **Only a confirmed mapping is validated.** If `canonical_field` is NULL the
  column's meaning is still a guess, and validating a guess produces a judgement
  about our own inference rather than about the data. Those attributes are
  counted as unvalidated instead.

- **Passes are stored, not just failures.** Without a `pass` row, "no row" would
  be ambiguous between *the rule passed* and *the rule never ran* — and the
  second is the common case here, because most value types have no rules at all.
  The cost is volume; the alternative is a table that cannot answer what it was
  asked. `record_validation.unvalidated_attributes` makes the coverage gap
  countable at a glance, and every one of the 110 unvalidated attributes in the
  real company file resolves to a specific reason.

- **Three severities, and the split is the whole point.**
  `error` = the value cannot serve as this field at all (an email with no `@` is
  not a weak email); `warning` = plausible but suspect; `info` = recorded, no
  judgement. A record is `invalid` only on an error. Collapsing these would
  either quarantine most of a real file or quarantine none of it.

- **A missing country code is a warning, never an error.** Normalization refuses
  to invent a `+1`, so validation records the ambiguity rather than punishing the
  value for a gap the source left. This fired 22 times on the real company file —
  correctly, and without invalidating a single record.

- **The raw value is validated too, where normalization can hide a problem.**
  `employees = 50-100` normalizes to `50100`, which passes a headcount range
  check while meaning something the source never said. `integer.input` inspects
  the raw value and flags the distortion. Both judgements are stored side by
  side, which is exactly the sort of thing that is impossible to reconstruct once
  the raw value is gone.

- **Record-level rules answer a question no attribute rule can see.**
  `record.has_identifier` asks whether the row could ever be resolved to an
  entity. A phone number is deliberately not an identifier for either entity
  type: it identifies a line, not a person or a company, and two colleagues
  sharing a switchboard would otherwise collapse into one entity.

- **Rules are versioned (`RULESET_VERSION`).** A judgement is only meaningful
  relative to the rules that produced it — the same reasoning that versions the
  canonical schema. Within a version a re-run updates in place (idempotent:
  re-running the broken batch left the count at 60); a new version writes new
  rows beside the old ones rather than rewriting history.

- **A rule that raises is reported as `rule.error`, never as a pass.** A broken
  rule silently returning "fine" is the worst failure mode a validation layer
  has.

- **Value types with no rules have none on purpose.** Addresses, free text,
  identifiers, place names and region codes accept any string: a street address,
  a vendor's own key and a town name are all legitimately arbitrary. Rules there
  would manufacture failures rather than find them.

- **Trigger: a Kafka consumer on `records.normalized`,** publishing
  `records.validated` with counts only — an invalid record is read from Postgres
  by the next stage, never carried in the event.

## Verification

A deliberately broken fixture (`data/inbox/invalid_company.csv`) exercises every
path, and each row failed exactly the rule it was written to fail:

| Row | Content | Result |
| --- | --- | --- |
| 1 | clean company row | `valid` |
| 2 | `not-an-email` | `invalid` — `email.syntax` |
| 3 | no name, no email, no site | `invalid` — `record.has_identifier` |
| 4 | 99,000,000 staff, founded 2099 | `invalid` — `employee_count.range`, `founded_year.range` |
| 5 | every column the literal `NULL` | `invalid` — `record.all_values_missing` (the row survives ingestion; it is not blank) |
| 6 | 3-digit phone | `invalid` — `phone.digit_count` |
| 7 | `employees = 50-100` | `warning` — `integer.input` only |

Across the three real files: 22 warnings, all `phone.country_code`, and zero
errors.

## Consequences

- Storing passes makes `validation_result` roughly one row per rule per
  validated attribute. At current volumes this is small; if it becomes the
  largest table, the fallback is to store failures plus a per-record manifest of
  rules run — the ambiguity above is what any such change has to keep solving.
- `record_validation` holds current state and is upserted, while
  `validation_result` accumulates per ruleset version. This is the same
  current-state/history split the spec asks for at entity level, applied early.
- Nothing yet *acts* on `invalid`. Quarantine (next increment) is what routes
  these records for review and keeps them out of entity resolution; until then
  the status is recorded but inert.
