# ADR 0020: Two databases, and a one-way road between them

## Status

Accepted.

## Context

The data this pipeline produces is displayed by an application with its own
Postgres database and its own schema — flat `leads` and `companies` tables
alongside users, credits, billing, saved searches and API keys.

The question was whether that application's schema should replace this one, or
this one should replace that.

Neither. They are shaped for different questions, and both shapes are right.

**Here, a fact is a row.** `attribute_observation` holds every cell of every
ingested row, including the columns that mapped to nothing.
`golden_attribute` holds the one value survivorship chose, with the ones it beat
kept beside it. That is what makes "where did this phone number come from"
answerable three years later, and it is the entire premise of the system
(ADR 0003, ADR 0008).

**There, a lead is a row.** "VPs of Engineering in Berlin at companies over 500
people, sorted by confidence" is one indexed scan over one wide table. There is
no index that makes an entity-attribute-value model answer it in a page load, and
no amount of caching that hides fourteen joins per result row.

Restructuring the pipeline into the flat shape would destroy provenance, which is
the thing it exists to protect. Restructuring the application into this shape
would make its only feature unusably slow.

## Decision

**Both databases exist. The pipeline is the system of record. The application's
tables are a read model, derived by projection, and nothing ever flows back.**

`tools/ops/project_serving.py` reads `golden_attribute`,
`attribute_observation` and `entity_relationship`, and upserts into the
application's `leads` and `companies`. It is idempotent, incremental, and runs on
a schedule.

An edit made in the application is overwritten by the next run. That is the
correct behaviour for a derived copy, and it is why the application must not
offer one.

### Identity is the pipeline's entity_id

The serving contract adds a nullable, unique `entity_id` column to both tables.
It is the entity resolution decided is one real person or company, and it is what
makes the projection an upsert rather than an ever-growing pile of near
duplicates.

It is added *alongside* the existing primary key rather than replacing it.
`lead_id` is referenced by `list_leads`, `audit_ledger` and `data_flags`, and
rewriting a key three tables point at is a migration in its own right with no
benefit to this one. A NULL `entity_id` means a row with no upstream, which rows
predating the pipeline legitimately are.

### Array columns come from the observations, not the golden record

`industry`, `technologies`, `keywords`, `sic_codes` and `naics_codes` are
`VARCHAR[]` in the serving schema. A golden value is one value by definition —
survivorship exists to pick it — so filling an array column from the golden
record would misrepresent what the sources said.

They are read from `attribute_observation` instead: every distinct value any
source reported for the field. That is what a multi-valued column means, and it
is the one place the projection deliberately does not go through survivorship.

### `leads_staging` is removed

The serving schema carried a staging table with a `validation_status` and an
`error_notes` string. That is `raw_record`, `record_validation` and
`quarantine_item` flattened into one table which cannot say which source said
what, cannot hold two sources disagreeing, and loses the original value on
promotion.

Two staging paths into one serving table is exactly the failure this pipeline
exists to prevent. There is one, and it is the one with provenance.

### The application's schema is fixed where it could not do its job

Four defects in the serving schema block the projection or the queries it feeds,
and are corrected by `db/serving/0001_serving_contract.sql`:

* **Text columns the application filters on numerically.** `employees_count`,
  `founded_year`, `annual_revenue`, `linkedin_followers`, `latest_funding_date`
  and `confidence_score` were VARCHAR. `WHERE employees_count > 500` cannot work
  against text and no index makes it work. Narrowing them loses nothing: every
  value arrives already normalized, and what the source literally wrote is kept
  in `attribute_observation.raw_value`.
* **Natural keys indexed but not constrained.** `companies.domain` and
  `leads.email_address` had btree and trigram indexes and no uniqueness, so the
  same company could arrive as many times as it was imported.
* **A foreign key with no delete rule.** `leads.company_id` defaulted to
  NO ACTION, so deleting a company raised an error instead of doing anything.
  A lead whose company is gone is still a lead.
* **`updated_at` columns that never updated.** `DEFAULT CURRENT_TIMESTAMP` sets
  a column once, at insert. Every one of them had been recording `created_at`
  under another name.

### What is knowingly left alone

**`users.credit_balance`.** It duplicates
`sum(user_credit_batches.amount_remaining)`, and two homes for one number will
drift. The batches are the ledger — only they can express expiry — so the column
is a cache. The contract adds a `user_credit_balance` view so the two can be
compared, and stops there: application code reads that column, and breaking it is
the application repository's change to make.

**Serving columns no source reports.** `is_b2b`, `ownership_status`, `logo_url`,
`tier_quality`, `intent_signal_arrays` have no canonical field behind them. The
projection leaves them untouched rather than guessing. Inventing values is what
the enrichment job does deliberately and visibly (ADR 0017); doing it silently in
a copy job is not the same thing.

## Consequences

**The application gains typed, filterable, deduplicated data** and loses the
ability to write to it. Any correction has to be made where the evidence is,
which is the pipeline — and that is a feature: a fix applied in the read model
would be undone on the next run and would explain nothing.

**Displayed data lags by one projection run.** Nightly is normal for a batch
system. Anything needing to be immediate would need the projection triggered by
the golden consumer, which is a change to this decision rather than a gap in it.

**A collision holds the watermark back.** Two entities claiming one email address
means resolution has not merged them yet. The projection rejects the second,
names it, and refuses to advance — so the next run reconsiders it instead of
leaving it permanently unprojected. The repeated work is idempotent; a watermark
moved past failed work is a gap nobody notices.

**The serving schema is now a contract.** A column the application team renames
breaks the projection loudly rather than quietly writing to nothing, because the
upsert names every column it writes instead of generating them.
