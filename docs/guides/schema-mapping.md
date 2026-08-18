# Schema mapping

Deciding which canonical field a source column represents, and who decides when the machine will not.

Part of the [People & Company Data Foundation](../../README.md).

## Schema mapping

Mapping runs **automatically**: the `mapping` service consumes
`batch.ingested` and maps each new batch's columns to canonical fields. No
manual step is needed after ingesting.

Each mapping records how it was decided (`exact_alias`, `similarity`,
`value_analysis`, `corroborated`, `manual`), a confidence, and evidence.
Confidence ≥0.90 auto-accepts; 0.60–0.90 needs review; below that the column
is recorded as unmapped rather than force-fit into a plausible-looking field.

Two rules keep it honest: **value analysis alone never auto-accepts** (a
column of valid emails could be `work_email` or `personal_email` — the values
prove the type, not the field), and **when two columns claim the same field,
both go to review** instead of the higher score silently winning.

```powershell
# See what needs a human decision
docker compose run --rm mapping review-mappings list --status needs_review

# Correct one (survives future re-ingests of the same column layout)
docker compose run --rm mapping review-mappings set --mapping-id <id> `
  --canonical-field person_external_id --reviewed-by you

# Valid canonical fields
docker compose run --rm mapping review-mappings fields --entity-type person

# Map a batch by hand (normally unnecessary)
docker compose run --rm mapping map-schema --batch-id <id> --show
```

Mappings are stored against a **source schema** (a column layout), not a
batch, so a human correction is reused the next time that source sends the
same columns.

### When a column maps to nothing

An unmapped column is still captured in full — but it is never validated, never
resolved on, and never reaches a golden record. Storing a value is not the same
as being able to answer a question with it, and the gap is easy to miss because
nothing fails.

Two kinds of column live in that state, and only one of them is a problem:

- **Vendor workflow state** — Apollo's `Email Sent`, `Replied`, `Stage`,
  `Contact Owner`, `Lists`. These describe the vendor's CRM, not the company.
  Correctly unmapped, and they should stay that way.
- **Real facts with no word for them in the vocabulary.** `Annual Revenue`,
  `Total Funding`, `Technologies`, `Keywords`, `SEO Description`,
  `Number of Retail Locations` sat here for the whole life of the project —
  1.7M observations captured and ignored, because the canonical schema had no
  field to put them in.

The second kind is fixed by adding the field, not by changing the file, and the
only way to tell them apart is to look at what the columns actually hold.

Before loading a vendor, dry-run the mapper over their sample files:

```powershell
docker compose run --rm pipeline python /tools/measure/mapping_coverage.py
```

For data already loaded, ask the database which unmapped columns are carrying
real values — a column that is 95% blank is not the one to worry about:

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

**No AI is used here, deliberately.** Mapping is four deterministic strategies.
The same file must always produce the same mapping — layouts are stored and
reused, so a non-deterministic mapper would make the same vendor's data mean
different things on different days — and every decision must stay auditable.
The hard cases (`location` → city or address? `ID` → whose id?) are exactly
where a model guesses confidently and wrongly, and those already route to a
human by construction. The place AI would genuinely help is *assisting the
review queue* — proposing a field with a rationale for a human to accept — never
in the automatic path. See `docs/decisions/0011-increment-11-review-findings.md`.
