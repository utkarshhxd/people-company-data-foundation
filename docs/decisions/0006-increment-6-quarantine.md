# ADR 0006: Increment 6 — quarantine

## Status

Accepted.

## Context

Increment 5 could label a record `invalid`, but nothing acted on it. The label
was inert: an unusable record was just as visible to whatever came next as a
clean one. The spec's rule has two halves and only one was being served —
invalid records must not disappear, *and* they must not silently get through.

## Decisions

- **Quarantine is a routing decision, not a copy.** `quarantine_item` references
  the record and names the reasons. Nothing is duplicated, nothing is moved,
  nothing is deleted. All 317 attribute observations survived the run in which 5
  records were quarantined.

- **A view is the actual control.** `resolvable_record` is what entity
  resolution will read instead of `raw_record`. Without it quarantine would be a
  passive log that everyone downstream is free to ignore; with it, being
  quarantined has a mechanical consequence. Verified: 31 records, 27 resolvable,
  the difference being 3 open plus 1 rejected.

- **Routing happens in the same transaction as the validation verdict.** This is
  the one place a separate Kafka consumer would have been wrong. A separate
  stage leaves a window in which a record is known-invalid but not yet
  quarantined — and if that consumer were down, the window stays open and bad
  records flow straight into entity resolution. The pipeline's asynchronous
  boundaries are where a delay is harmless; this is not one of them.
  Consequently there is no new container: quarantine adds a table, a lifecycle,
  and a CLI to the validation service rather than a fifth consumer.

- **Only error-severity failures quarantine a record.** Warnings are kept
  strictly apart. The real company file produces 22 warnings and 0 errors; if
  warnings quarantined, the entire file would have been held back on a rule
  about missing country codes.

- **Four dispositions, and re-validation closes the easy ones by itself.**
  `open` → `released` | `rejected` | `resolved`. A record whose re-validation
  passes is auto-`resolved` with no human involved, which is what makes fixing a
  mapping or a rule cheap rather than generating review work.

- **A reviewer's release survives the same failure recurring, but not a new
  one.** Re-running validation must not undo a person's decision. But they
  signed off on *one* problem, not on any future problem, so a changed reason
  code set reopens the item. This is why `reason_codes` is stored sorted and
  deduplicated — "same problem" has to be cheap to compare.

- **A rejection outranks a later clean validation.** If a human judged a record
  unusable, a ruleset change must not quietly let it back in; reopening is
  itself a human decision. The `resolvable_record` view excludes `rejected`
  before it considers anything else.

- **`quarantine_event` is append-only.** A release followed by a re-quarantine
  must not erase the fact that someone once signed off, and who. The item row
  and its history entry are written in one statement, so a status can never
  change without a recorded reason.

## A defect this increment exposed

Proving auto-resolution end to end surfaced a real bug in increment 4:
`insert_observations` used `ON CONFLICT DO NOTHING`, so re-normalizing a batch
after a mapping was approved left `canonical_field` NULL. Human review therefore
had **no effect on any batch that had already been normalized** — it only
influenced future files, which is close to useless.

The fix distinguishes source facts from derived ones: `raw_value` and
`observed_at` are still never updated, while `canonical_field`,
`normalized_value`, `value_type` and `normalization_method` are refreshed on
conflict. Losslessness is a promise about what the source said, not a
prohibition on recomputing our own interpretation of it.

## Verification

A collision fixture (`data/inbox/ambiguous_company.csv`, columns `name` *and*
`org_name`) exercises the whole loop, and every stage behaved as designed:

1. Both columns claim `company_name`, so increment 3's collision guard sends
   both to review and neither confirms.
2. With no confirmed company name, and no email or website,
   `record.has_identifier` fails — both records are quarantined as `open`.
3. A human approves `org_name` → `company_name`.
4. Re-normalizing lifts canonical attributes from 4 to 6 (the fix above).
5. Re-validating returns `valid`, and both items close themselves as `resolved`
   with the transition recorded:
   `quarantined (- -> open)` then `resolved (open -> resolved)`.

Separately, a release and a rejection both survived a full re-validation of
their batch with reviewer and note intact.

## Consequences

- Entity resolution must read `resolvable_record`, not `raw_record`. That is a
  constraint on the next increment and the reason this one exists.
- Nothing ages an `open` item. A record can sit in review forever without
  anything being lost, but also without anyone being told. A staleness metric
  belongs with the Grafana work rather than here.
- `released` records reach entity resolution carrying `was_quarantined = true`,
  so a human override stays visible downstream instead of being laundered into
  looking like clean data.
