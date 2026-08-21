# Normalization, validation and quarantine

Making values comparable, judging whether they're usable, and holding back
the ones that aren't — without losing them.

Part of the [People & Company Data Foundation](../../README.md).

On the record-at-a-time path these two happen inline, per record, as part of
loading a file — no separate step. The standalone commands below are for the
reprocessing path (`--batch-id` against a batch already in Postgres).

## Normalization

```powershell
docker compose run --rm normalization normalize --batch-id <id> --show 20
```

Writes one **attribute observation** per source column per row. The
governing rule: **nothing is lost**. Every column produces an observation —
including columns that mapped to nothing, because vendor files routinely put
real data there (`FAX_NUMBER`, `SIC_DESCRIPTION`, the source's own `ID`).
`raw_value` and `normalized_value` are always stored together; rows are only
ever appended, never overwritten.

Normalization only makes values *comparable* — it never repairs or judges:

- `WASHINGTON` and `Washington` → `Washington` (otherwise they'd never match)
- `+91 (98765)-43210` → `+919876543210`, but bare `18003514494` keeps its
  digits — inventing a `+1` would fabricate provenance the source never gave
- `NULL` / `N/A` / `-` → no value, raw string kept verbatim
- `A^^B` in one cell → two observations, not one mangled string
- addresses get whitespace collapsing only — punctuation carries meaning,
  nothing is "corrected"
- `$1.2M` and `1200000` → `1200000`, so two vendors reporting the same
  amount agree instead of looking like a disagreement. Currency is *not*
  captured or converted — a vendor that ships a bare amount hasn't told us
  the currency, and picking one would be a guess
- `45047` in a date column → `2023-05-01` (Excel's day-count decoded). `2023`
  alone does not decode — a bare year is a year, and the range guard is what
  stops a wrong column becoming a wrong fact

The last two are decodes, not repairs — but they're still arithmetic we did,
so validation records that separately (`money.input`, `date.input`) rather
than letting an inferred figure look identical to a stated one.

## Validation

```powershell
docker compose run --rm validation validate --batch-id <id> --show 20

docker compose exec postgres psql -U pcdf_dev -d pcdf `
  -c "SELECT status, count(*) FROM record_validation GROUP BY 1;"
docker compose exec postgres psql -U pcdf_dev -d pcdf `
  -c "SELECT rule_id, severity, count(*) FROM validation_result WHERE outcome='fail' GROUP BY 1,2 ORDER BY 3 DESC;"
```

One question per value: **is this usable as the field it was mapped to?**
Never repairs a value, never deletes a record. Three severities:

- `error` — the value can't serve as this field at all (`not-an-email`), OR
  the row has no identifying attribute and could never resolve to an entity.
  **Only the second invalidates the whole record.** A record with one
  unusable value stays `warning`, not condemned — a bad `Phone` column
  shouldn't withhold an otherwise-identifiable row.
- `warning` — plausible but suspect: a phone with no country code, a role
  mailbox (`info@`) on a *person* record.
- `info` — recorded, no judgement implied.

Which fields a record must carry, in three tiers:

- **identifying** (error) — without one of these, the record can never
  resolve to an entity. Enforced as a *set*: an email alone is enough, so is
  a name.
- **core** (warning) — makes a record legible. A company known only by a
  vendor id is resolvable but useless to read.
- **expected** (info) — commonly present, legitimately absent sometimes.

**Addresses are checked for plausibility, never format** — any string can be
a street address, formats vary by country, so address rules are warning/info
only and never reject for being foreign or oddly punctuated.

**Passes are stored too** — otherwise "no row" is ambiguous between *passed*
and *never ran*, and the second is common by design (free text has no
rules). **The raw value is checked where normalization can hide a
problem** — `employees = 50-100` normalizes to `50100`, passing a headcount
check while meaning something the source never said; `integer.input` flags
the distortion, both judgements kept.

## Quarantine

A record validated `invalid` routes to quarantine **in the same
transaction** as the verdict — never a moment where a record is known-bad
but still visible downstream. Only `error`-severity failures quarantine
anything; warnings never do.

The control is the `resolvable_record` view — **entity resolution reads
that, not `raw_record`**. Quarantined records stay fully intact in
`raw_record` and `attribute_observation`; they're simply absent from the
view resolution sees.

```powershell
docker compose run --rm validation quarantine list --status open
docker compose run --rm validation quarantine show --record-id <id>

docker compose run --rm validation quarantine release --record-id <id> `
  --reviewed-by you --note "vendor confirmed by phone"
docker compose run --rm validation quarantine reject --record-id <id> `
  --reviewed-by you --note "row is entirely NULL placeholders"

docker compose run --rm validation quarantine stats
```

Four dispositions:

- `open` → awaiting a human.
- `released` → usable despite the failures. Survives the *same* failure
  recurring, but a **new** failure reopens it — they signed off on one
  problem, not any future one.
- `rejected` → confirmed unusable. Terminal, outranks a later clean
  validation — a ruleset change must not quietly overturn a human judgement.
- `resolved` → re-validation passed, closed automatically, no human needed.
  This is what makes fixing a mapping cheap instead of generating review
  work — approve the mapping, re-run normalize/validate, and rows that only
  failed because of the bad mapping close themselves.

Every transition is appended to `quarantine_event` with actor and reason, so
a release followed by a re-quarantine never erases who signed off.

**Releasing does not re-trigger resolution or golden-build** — both are
separate, explicit steps. See [Review queues](review-console.md).
