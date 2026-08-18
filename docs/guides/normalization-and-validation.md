# Normalization, validation and quarantine

Making values comparable, judging whether they are usable, and holding back the ones that are not without losing them.

Part of the [People & Company Data Foundation](../../README.md).

## Normalization

Also automatic: the `normalization` service consumes `schema.mapped` and writes
one **attribute observation** per source column per row.

The governing rule is that **nothing is lost**. Every column produces an
observation — including columns that mapped to nothing, because vendor files
routinely put real data there (`FAX_NUMBER`, `SIC_DESCRIPTION`, the source's own
`ID`). `raw_value` and `normalized_value` are always stored together, and rows
are only ever appended, never overwritten. When the same entity arrives from two
vendors, both sets of attributes survive and the normalized values are what let
them be linked.

Normalization only makes values *comparable*; it never repairs or judges:

- `WASHINGTON` and `Washington` → `Washington` (otherwise they'd never match)
- `+91 (98765)-43210` → `+919876543210`, but bare `18003514494` keeps its digits
  — inventing a `+1` would fabricate provenance the source never gave
- `NULL` / `N/A` / `-` → no value, but the raw string is kept verbatim
- `A^^B` in one cell → two observations, not one mangled string
- addresses get whitespace collapsing only — `#610` and `Ave NW` carry meaning,
  and `1720WisconsinAveNW` is passed through rather than "corrected"
- `$1.2M` and `1200000` → `1200000`, so two vendors reporting the same amount
  differently agree instead of looking like a disagreement. The currency is
  *not* captured or converted: a vendor that ships an amount without a currency
  column has not told us the currency, and picking one would be a guess
- `45047` in a date column → `2023-05-01`. An xlsx export turns dates into
  Excel's day count, and reading only ISO threw away 221 stated dates per
  thousand Apollo rows. `2023` does not decode — a bare year in a date column is
  a year, and the range guard is what stops a wrong column becoming a wrong fact

The last two are decodes, not repairs, but they are still arithmetic we did
rather than something the vendor wrote — so validation records that separately
(`money.input`, `date.input`) instead of letting an inferred figure look
identical to a stated one.

```powershell
# Normally unnecessary; the consumer does this automatically
docker compose run --rm normalization normalize --batch-id <id> --show 20
```

## Validation

Also automatic: the `validation` service consumes `records.normalized` and asks
one question per value — **is this usable as the field it was mapped to?**

It never repairs a value and never deletes a record. Judgements are attached
alongside the observation, which stays untouched. Three severities, and the
difference decides what happens next:

- `error` — either the value cannot serve as this field at all (`not-an-email`),
  or the row has no identifying attribute and could never be resolved. **Only
  the second invalidates the record.** An unusable value is a verdict on the
  value; it is recorded as an error and excluded from resolution, but it leaves
  the record `warning` rather than condemning everything beside it. A c_suite
  export shipped the literal string `[object Object]` in `Phone` for 907 rows
  out of 1,000 — every one still had an email, a name and a LinkedIn URL, and
  quarantining them would have withheld 90% of an identifiable file over a
  column nobody needed.
  `invalid` means *not safe to resolve yet* — never *delete me*. Every raw
  record and observation survives either way.
- `warning` — plausible but suspect: a phone with no country code, `employees`
  written as `50-100`, a role mailbox (`info@`) on a *person* record.
- `info` — recorded, no judgement implied.

Rules live one module per concern under `services/validation/src/validation/rules/`
— `email.py`, `phone.py`, `url.py`, `name.py`, `address.py`, `number.py`,
`postal.py` — so adding a rule means editing one small file about one subject.
Which fields a record must carry lives separately again, in
`required_fields.py`, in three tiers:

- **identifying** (error) — without one of these the record can never be resolved
  to an entity. Enforced as a *set*: an email alone is enough, and so is a name.
- **core** (warning) — the fields that make a record legible. A company known
  only by a vendor id is resolvable but useless to read.
- **expected** (info) — commonly present, routinely and legitimately absent.

**Addresses are checked for plausibility, never format.** Any string can be a
street address and formats vary by country, so every address rule is a warning
or info and none can reject a value for being foreign or oddly punctuated. They
catch placeholder text (`same as above`), whitespace lost upstream
(`1720WisconsinAveNW`), values too short to locate anything, and other fields
that drifted into the address column. A missing street number is recorded as
*info* only — PO boxes and named buildings legitimately have none.

Two details worth knowing:

- **Passes are stored too.** Otherwise "no row" would be ambiguous between *the
  rule passed* and *the rule never ran* — and the second is common, since
  addresses, identifiers, place names and free text have no rules on purpose.
  `record_validation.unvalidated_attributes` keeps that gap countable.
- **The raw value is checked where normalization can hide a problem.**
  `employees = 50-100` normalizes to `50100`, which passes a headcount range
  check while meaning something the source never said; `integer.input` flags the
  distortion, and both judgements are kept.

```powershell
# Normally unnecessary; the consumer does this automatically
docker compose run --rm validation validate --batch-id <id> --show 20

# What is currently not safe to resolve, and why
docker compose exec postgres psql -U pcdf_dev -d pcdf `
  -c "SELECT status, count(*) FROM record_validation GROUP BY 1;"
docker compose exec postgres psql -U pcdf_dev -d pcdf `
  -c "SELECT rule_id, severity, count(*) FROM validation_result
      WHERE outcome='fail' GROUP BY 1,2 ORDER BY 3 DESC;"
```

`data/inbox/fixtures/invalid_company.csv` is a deliberately broken fixture: each row
fails exactly one rule path (bad email, no identifier, impossible headcount,
future founding year, an all-`NULL` row, a 3-digit phone, a `50-100` range).

## Quarantine

A record validated as `invalid` is routed to quarantine **in the same
transaction** as the verdict, so there is never a moment when a record is
known-bad but still visible downstream. Only `error`-severity failures
quarantine anything; warnings never do.

The control is the `resolvable_record` view — **entity resolution reads that,
not `raw_record`**. Quarantined records stay fully intact in `raw_record` and
`attribute_observation`; they are simply absent from the view.

```powershell
docker compose run --rm validation quarantine list --status open
docker compose run --rm validation quarantine show --record-id <id>

# Use it anyway / confirm it is unusable. Both are recorded permanently.
docker compose run --rm validation quarantine release --record-id <id> `
  --reviewed-by you --note "vendor confirmed by phone"
docker compose run --rm validation quarantine reject --record-id <id> `
  --reviewed-by you --note "row is entirely NULL placeholders"

docker compose run --rm validation quarantine stats
```

Four dispositions, and the lifecycle rules are the interesting part:

- `open` → awaiting a human.
- `released` → usable despite the failures. It survives the *same* failure
  recurring (re-running validation must not undo a person), but a **new** failure
  reopens it — they signed off on one problem, not on any future one.
- `rejected` → confirmed unusable. Terminal, and it outranks a later clean
  validation: a ruleset change must not quietly overturn a human judgement.
- `resolved` → re-validation passed, closed automatically with no human. This is
  what makes fixing a mapping cheap instead of generating review work.

Every transition is appended to `quarantine_event` with actor and reason, so a
release followed by a re-quarantine never erases who signed off.

`data/inbox/fixtures/ambiguous_company.csv` demonstrates the full loop: a file with both
`name` and `org_name` collides on `company_name`, neither mapping confirms, so
the records have no identifier and are quarantined — approve one mapping,
re-normalize, re-validate, and they close themselves as `resolved`.
