# ADR 0004: Increment 4 — normalization and lossless attribute capture

## Status

Accepted.

## Context

Increment 3 decided *which canonical field each source column represents*.
Nothing yet turned the values themselves into something comparable, and nothing
stored per-attribute facts that later increments (entity resolution, golden
record, history) can build on.

Partway through this increment Utkarsh supplied two real vendor exports and
restated the governing constraint: **no information may be lost**, and when the
same real-world entity turns up in two different datasets, every attribute from
both must be extracted and linked to that entity — not overwritten. That
requirement, more than the normalization rules, shaped the design.

The real files also disproved several assumptions the synthetic fixtures had
allowed:

| Reality in the files | What it broke |
| --- | --- |
| `,,,,,,,,,,,,` for thousands of trailing rows | nothing — readers already drop fully-empty rows |
| literal `null` / `NULL` strings | would have become the *string* "null" |
| `CRAZEEGIRL.COM^^QUALITYSPIRITS.COM` | one cell holding two values |
| `1722;19th Street;NW;#610 - Washington;DC` | naive delimiter splitting would shred an address |
| `1720WisconsinAveNW` | tempting to "fix"; we must not |
| `18003514494`, `12023289000` | tempting to assume +1 |
| `FAX_NUMBER`, `CAT`, `SIC_DESCRIPTION`, `email_status`, `profile_pic`, `ID` | no canonical field existed; would have been dropped |
| `WASHINGTON` vs `Washington` | same city, would never match |

## Decisions

- **`attribute_observation` is the lossless substrate.** One row per source
  column per source row — *including columns that mapped to nothing*. An
  unmapped column is still a fact about the source, and the real files prove
  vendors put useful data there (`FAX_NUMBER`, `SIC_DESCRIPTION`). Rows are only
  appended; nothing is ever updated in place. Which competing observation wins
  is the golden record's decision, not this table's.
  Verified arithmetically: 13 rows × 13 columns = 169 cells → **170
  observations**, the extra one being the `^^` split.

- **Only a confirmed mapping sets `canonical_field`.** Statuses `auto_accepted`
  and `approved` qualify; `needs_review` and `unmapped` leave it NULL while
  `source_column` and the value are still stored. Review gates *interpretation*,
  never *capture* — an unreviewed guess must not silently become truth.

- **Raw and normalized are stored side by side, always.** Normalization only
  makes a value comparable. It never repairs, enriches, or judges — that is
  validation's job next, and the golden record's after that.

- **Placeholders are recognised but preserved.** `null`, `NULL`, `N/A`, `-`,
  `none` → `normalized_value` NULL and `is_null_token` true, with the raw string
  kept verbatim. `NA` is deliberately *not* in the list: it is a real value in
  some columns (Namibia, North America).

- **Multi-value splitting is narrow by design.** `^^` (an explicit vendor
  marker) splits any type; `;` and `|` split only email/phone/url. Addresses and
  free text are never split on punctuation — the semicolon address above is
  exactly why. Each split value becomes its own observation with a `value_index`.

- **Phone normalization does not infer a country.** `+91 (98765)-43210` →
  `+919876543210`, but bare `18003514494` stays as its digits. Adding a `+1`
  would invent provenance the source never gave us. Country inference belongs to
  validation/enrichment, where it can be recorded as a derived assertion.

- **Addresses get whitespace collapsing only.** The spec is explicit about not
  blindly removing punctuation, and `#610` / `Ave NW` prove the point.
  `1720WisconsinAveNW` is passed through unchanged rather than "corrected".

- **Place names are cased for comparability.** `city` title-cases
  (`WASHINGTON` → `Washington`); `state_region`/`country` upper-case short codes
  (`dc` → `DC`, not `Dc`) and title-case longer names. Without this, entity
  resolution would fail to match records that differ only in case — which is
  precisely the failure normalization exists to prevent.

- **Canonical vocabulary expanded and versioned to `2`** with the fields the
  real files needed: `fax_phone`, `sic_description`, `email_status`,
  `profile_image_url`, `company_category`, person-level `industry`, and
  `person/company_external_id` (aliasing the bare `id` column — the source's own
  key is a strong signal for later entity resolution).

- **New `ambiguous_alias` mapping method.** Some names are plausible but not
  decisive: `location` usually means city, sometimes a full address. An
  ambiguous alias proposes the field at a confidence deliberately *below* the
  auto-accept threshold, so it always reaches a human. This closed a real gap —
  `location` had previously been mis-proposed as `country` by string similarity.

- **Trigger: a Kafka consumer on `schema.mapped`,** publishing
  `records.normalized`. Same at-least-once semantics as the mapping consumer;
  reprocessing is safe because `(record_id, source_column, value_index)` is
  unique and inserts are `ON CONFLICT DO NOTHING`.

## Consequences

- Expanding the vocabulary resolved three collisions the previous increment had
  correctly flagged (`ADDRESS`/`WEB_ADDRESS`, `PHONE_NUMBER`/`FAX_NUMBER`,
  `EMAIL`/`email_status`) — the fix was a richer vocabulary, not a weaker rule.
  The collision guard did its job: it surfaced a gap instead of hiding it.
- Bumping `CANONICAL_SCHEMA_VERSION` to `2` means existing `source_schema` rows
  (version `1`) are not reused; layouts are re-mapped against the new
  vocabulary. That is the intended effect of a versioned vocabulary change, and
  is why the version is stamped on every stored mapping.
- `attribute_observation` grows as columns × rows. The index on
  `(canonical_field, normalized_value)` is there for entity resolution's
  candidate lookups; volume is worth watching once real files are loaded.
