# ADR 0014: The commercial profile, and where enrichment is allowed to live

## Status

Accepted.

## Context

Two questions were asked of the system: is the "make bad data structured" part
actually working, and is anything being enriched.

The first was working. 287,105 records had become 315,619 entities and 3.8M
current golden values, with 31 record-level errors in total. Coverage on the
fields that matter was high — `email` and `first_name` on 100% of people,
`company_name` on 100% of companies, `website` on 93%.

The second answer was no, and deliberately so: every stage to that point was
*preserving*. Normalization makes values comparable and explicitly "does not
repair, enrich, or judge"; validation reports; survivorship chooses between
values that a source actually stated. No field was ever better than what a
vendor typed.

Looking closely at the first question surfaced three defects, and the second
turned out to be less absent than it looked.

---

## 1. 41% of observations mapped to nothing — and not all of it was noise

4.88M of 11.8M observations sat in `mapping_status = 'unmapped'`. That state is
lossless by design: the value is captured, the raw string is stored, nothing is
discarded. But an unmapped observation is never validated, never resolved on and
never reaches a golden record, so it cannot answer a question.

Splitting the unmapped columns by what they hold gave two very different groups:

- **Vendor workflow state** — `Email Sent`, `Replied`, `Stage`, `Demoed`,
  `Contact Owner`, `Lists`, `Last Contacted`. These describe Apollo's CRM, not
  the company. Correctly unmapped, and they stay unmapped.
- **Real facts with no word for them** — `Annual Revenue`, `Total Funding`,
  `Latest Funding`, `Latest Funding Amount`, `Last Raised At`, `Technologies`,
  `Keywords`, `SEO Description`, `Number of Retail Locations`. 191,574
  observations each: roughly 1.7M values captured and ignored for the life of
  the project, because the canonical vocabulary had no field to put them in.

**Decision:** add the nine fields, as company fields *and* as employer fields on
a person row (see ADR 0013), plus two new value types — `money` and `date`.

The employer twins are what make this worth doing. Every one of these columns
arrives on a **person** row; none of them appears on a company file we hold. The
projection machinery from ADR 0013 already carries employer-subject observations
onto the company entity, so declaring the fields was enough to make Apollo
contact rows populate company golden records. No new stage, no new service.

### Breaking the employer-qualification convention, on purpose

ADR 0013 requires every employer alias to be employer-qualified, because an
unqualified `city` on a person row is the *person's* city and an employer field
answering to it would cost the person their own address.

These fields answer to unqualified names anyway. The convention exists to
prevent a collision, and none of these can collide: a person does not have an
annual revenue, so `Annual Revenue` on a contact row is not ambiguous about
whose revenue it is. Demanding `Company Annual Revenue` would reject the only
spelling anyone ships.

That safety is now a test rather than a comment — `test_person_fields_are_not_
shadowed_by_the_unqualified_aliases` fails the moment a person field claims one
of those names.

---

## 2. Three defects found while doing it

### `record.core_fields` demanded a field nobody sends

200,574 records — 70% of everything loaded — carried a warning saying they were
missing `full_name`. All of them had `first_name` and `last_name`, both at 100%
coverage.

`rule_has_identifier` had always treated the two name parts as equivalent to a
full name. `rule_core_fields` did not. The record was legible; the rule was
wrong. Fixed in `required_fields.py` so the policy lives in one place, and
`RULESET_VERSION` bumped to 3 because this *reverses* an existing verdict rather
than adding a new one.

### Re-validation left retracted verdicts standing

`insert_results` upserts on `(record_id, observation_id, rule_id,
ruleset_version)`, documented as "within one ruleset version the latest run
wins". That is only true for rules that fire again. A rule that **stops** firing
writes nothing, so its old verdict survives.

Re-normalizing the Apollo sample made this visible: 221 dates that had failed
`value.empty_after_normalization` decoded successfully on the second run, and
the failures sat in the table beside the new passes — one record carrying two
contradictory verdicts from one ruleset.

Fixed by clearing a record's results for the current ruleset version before
writing the new ones. Scoped to the version, so cross-version history — the
entire reason the version is on the row — still survives.

### A btree index that would have crashed the golden build

`golden_attribute_lookup_idx` indexed `value` directly. The longest
`Technologies` cell in the Apollo export is 2,960 characters, and a btree tuple
cannot exceed 2,704 bytes. The first golden build to choose a Technologies value
would have failed with `index row size ... exceeds btree version 4 maximum 2704`
and taken the whole entity build with it.

This is the same failure shape as migration 0014, which dropped an observation
index for exactly this reason. The difference is that this index is used, so
migration 0017 rebuilds it on `left(value, 255)` instead of dropping it. A
caller matching an exact long value must still compare `value` itself; the
prefix narrows the scan, it does not decide the answer.

---

## 3. Excel serial dates

`Last Raised At` arrives as `2024-09-01T00:00:00+00:00` from Apollo's CSV export
and as `45047` from the xlsx one. Both are the vendor stating a date. Reading
only ISO discarded 221 stated dates per thousand rows.

**Decision:** decode Excel's day count, with two guards.

The epoch is 1899-12-30, not 1900-01-01, because Excel counts a 29th of February
in 1900 that never happened. And only serials between 20,000 and 60,000 (roughly
1954–2064) decode: a date column holding `2023` is holding a year, and a column
of integers that is not really dates would decode just as willingly. The range
guard is what stops a wrong column becoming a wrong fact.

This is a decode, not a repair — the same category as parsing ISO. But it is
still arithmetic we performed rather than something the vendor wrote, so
`date.input` records it, exactly as `integer.input` has always recorded a raw
`50-100` that normalized to a clean-looking number.

---

## 4. Where enrichment is allowed to live

The commercial-profile work is enrichment in the only form the architecture
currently permits: **projection of facts a source already stated**, across a
relationship the system already models. Nothing was invented.

Anything further is ordered by how much it can fabricate.

**Derivation** — `full_name` from its parts, `country` from a phone country
code, a domain from a website. No external data, deterministic, reversible,
explainable from the inputs alone.

**Cross-source projection** — what this ADR did. Facts stated by one source
about a subject it named, attached to the entity that subject resolved to.

**External lookup** — company registries, WHOIS, commercial providers. Produces
genuine new facts, and each provider must enter as **a source like any other**:
its own `source` row, its own reliability, its own observations. Survivorship
then treats a derived value as evidence competing with vendor evidence rather
than as truth, provenance stays intact, and a bad enrichment run is purgeable by
source like any other load.

**Nothing may write directly into `golden_attribute`.** A value with no
observation behind it is a value with no provenance, and it would be
indistinguishable from an observed one.

### AI

The position from ADR 0011 stands and extends: **AI may propose, never assert.**

Useful, and allowed: suggesting a canonical field for an unmapped column with a
rationale a human accepts; judging ambiguous duplicate pairs in the
match-candidate queue; normalizing job titles into a taxonomy; parsing an
address a deterministic parser could not.

Not allowed: producing facts. A model asked for a company's employee count or
revenue will answer, fluently and often wrongly, and the answer would carry a
confidence score computed as though a source had said it. In a master data
system whose entire value is that every value is traceable to who claimed it,
that is not enrichment — it is contamination that cannot be told apart from the
real thing afterwards.

The line is the same one that keeps the mapping engine deterministic: a model
may narrow a human's work, never replace the evidence.

---

## Consequences

- Canonical schema at version 4. Existing batches keep their version-3 mappings
  until explicitly backfilled; the runbook documents the stage order.
- `map-schema` derives a **new** source schema after a version bump rather than
  editing the old one, so human mapping corrections do not carry across
  versions. Reviewers must re-check `needs_review` after a backfill.
- Two new value types mean two new rule modules. The registry now has a test
  asserting every value type in the schema is either ruled or deliberately
  unruled, because silence in validation is indistinguishable from passing.
- `golden_company` gains nine columns. It is an explicit column list rather than
  a dynamic pivot, so a new canonical field stays invisible to consumers until
  the view names it — that is a feature, and it is also a step that is easy to
  forget.
