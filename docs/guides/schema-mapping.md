# Schema mapping

Deciding which canonical field a source column represents, and who decides
when the machine won't.

Part of the [People & Company Data Foundation](../../README.md).

## How a column gets mapped

On the record-at-a-time path (dropping a file, or `pipeline process run`),
mapping happens inline, per batch, as part of loading — no separate step.
On the reprocessing path, it's its own command:

```powershell
docker compose run --rm mapping map-schema --batch-id <id> --show
```

Each mapping records how it was decided (`exact_alias`, `similarity`,
`value_analysis`, `corroborated`, `manual`, and — when `AI_MAPPING_ENABLED` is
on, see [AI assistance](ai-assistance.md) — `ai_suggestion`), a confidence,
and evidence. Confidence ≥0.90 auto-accepts; 0.60–0.90 goes to `needs_review`;
below that the column is recorded as `unmapped` rather than force-fit into a
plausible-looking field. An AI suggestion is scored exactly like value
analysis: it can only ever land in `needs_review`, never auto-accept.

Two rules keep it honest: **value analysis alone never auto-accepts** (a
column of valid emails could be `work_email` or `personal_email` — the
values prove the type, not the field), and **when two columns claim the same
field, both go to review** instead of the higher score silently winning.

```powershell
# See what needs a human decision
docker compose run --rm mapping review-mappings list --status needs_review

# Correct one — reused on every future file with this column layout
docker compose run --rm mapping review-mappings set --mapping-id <id> `
  --canonical-field person_external_id --reviewed-by you

# Valid canonical fields
docker compose run --rm mapping review-mappings fields --entity-type person
```

Mappings are stored against a **source schema** (a column layout), not a
single batch — a human correction is reused the next time that source sends
the same columns.

## Two failure modes worth knowing before you drop a file

**No header row.** If row 1 of the file is already data rather than column
labels, the mapper has no vocabulary to work from — it maps off literal data
values (`"Steve"`, `"Canton"`) as if they were column names, and everything
ends up `needs_review` or `unmapped`. Nothing downstream can use a mapping
stuck at `needs_review`, so the batch quarantines wholesale. Fix: open the
file, confirm row 1 is actually headers, before dropping it in.

**Wrong `entity_type` on the feed.** A file of person rows (name, title,
company, email) dropped into a feed configured `entity_type: company` won't
necessarily fail — if a `Company` column maps cleanly to `company_name`, that
alone satisfies the company identifier rule, and the batch happily creates
company entities out of person data, with the actual person fields
(first/last name, title) left unmapped. This doesn't quarantine, doesn't
error — it just silently builds the wrong kind of entity. Check the file's
real columns before writing `feed.json`.

## When a column maps to nothing

An unmapped column is still captured in full — but never validated, never
resolved on, never reaches a golden record. Storing a value isn't the same
as being able to answer a question with it, and the gap is easy to miss
because nothing fails.

Two kinds of column end up here, and only one is a problem:

- **Vendor workflow state** — Apollo's `Email Sent`, `Replied`, `Stage`,
  `Contact Owner`, `Lists`. Describes the vendor's CRM, not the company.
  Correctly unmapped, and it should stay that way.
- **Real facts with no word for them in the vocabulary** — `Annual Revenue`,
  `Total Funding`, `Technologies`, `SEO Description`. This is fixed by adding
  the field to the canonical schema, not by changing the file.

The only way to tell them apart is to look at what the columns actually hold:

```powershell
docker compose run --rm pipeline python /tools/measure/mapping_coverage.py
```

```sql
SELECT source_column,
       count(*) FILTER (WHERE NOT is_null_token) AS populated,
       count(DISTINCT source_id)                 AS sources,
       min(left(raw_value, 40)) FILTER (WHERE NOT is_null_token) AS sample
FROM attribute_observation
WHERE mapping_status = 'unmapped'
GROUP BY 1
HAVING count(*) FILTER (WHERE NOT is_null_token) > 0
ORDER BY populated DESC
LIMIT 40;
```

Then backfill the affected batches — see "Backfilling batches after a schema
change" in [the runbook](../runbook.md).

## No AI in the automatic path, deliberately

Mapping is four deterministic strategies. The same file must always produce
the same mapping — layouts are stored and reused, so a non-deterministic
mapper would make the same vendor's data mean different things on different
days, and every decision has to stay auditable. The hard cases (`location` →
city or address? whose `ID` is this?) are exactly where a model guesses
confidently and wrongly, and those already route to a human by construction.

Where AI genuinely helps: *assisting the review queue* — proposing a field
with a rationale for a human to accept, never in the automatic path. See
[ADR 0011](../decisions/0011-increment-11-review-findings.md).
