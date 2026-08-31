# Serving the data to an application

This pipeline is a system of record. It is shaped so that nothing a vendor said
is ever lost: a value is a row in `attribute_observation`, a trusted value is a
row in `golden_attribute`, and every displayed field can be traced back to the
cell it came from.

An application that shows leads to customers needs the opposite shape. "VPs of
Engineering in Berlin at companies over 500 people, sorted by confidence" is one
indexed scan over one wide table, and there is no index that makes an
entity-attribute-value model answer it in a page load.

Both shapes are correct for their job, and neither can be made to do the other's.
So there are two databases and a one-way road between them.

```
  pipeline database                       serving database
  ────────────────────                    ──────────────────
  attribute_observation   ─┐
  golden_attribute         ├─ project ──▶  companies
  entity_relationship     ─┘               leads
```

The pipeline writes. The application reads. Nothing flows back — an edit made in
the application is overwritten by the next run, which is what a derived copy is
supposed to do.

---

## Setting it up, once

**1. Apply the serving contract.** The application's schema needs a stable
identity column, types its own filters can use, and unique constraints on the
two natural keys. See [`db/serving/README.md`](../../db/serving/README.md).

**2. Point the pipeline at it.** The projection connects to a second database,
with its own credentials — it is the only thing that writes there and needs
access to nothing else the application owns.

```bash
SERVING_HOST=leadsnemo-db
SERVING_PORT=5432
SERVING_DB=leadsnemo
SERVING_USER=projector
SERVING_PASSWORD=...      # or SERVING_PASSWORD_FILE, or /run/secrets/serving_password
```

All five follow the same rules as every other credential here — a variable, a
`_FILE` pointing at one, or a file mounted at `/run/secrets`. See
[`libs/common/src/common/config.py`](../../libs/common/src/common/config.py).

There is no default host on purpose. Every other part of a Postgres DSN has a
plausible default, and the four of them together point at a real, reachable,
entirely wrong database.

## Running it

```bash
docker compose run --rm pipeline python /tools/ops/project_serving.py
```

```
company: since 2026-08-24 03:00:11+00:00
company: 1,284 entit(ies) to project
  written  1,284

person: since 2026-08-24 03:00:11+00:00
person: 9,610 entit(ies) to project
  5000/9610  1430 entities/s
  written  9,602
  skipped  8 (no name to display yet)
```

| Flag | What it does |
| --- | --- |
| `--dry-run` | Count what would be projected and stop. Writes nothing, moves nothing. |
| `--full` | Ignore the watermark and reconsider every active entity. |
| `--limit N` | Stop after N entities. For trying it on a handful first. |
| `--entity-type person\|company` | One kind only. The default does companies then people. |
| `--batch-size N` | Rows per commit. Default 500. |

Companies are always projected before people. A person's serving row carries the
serving key of their employer, and that key does not exist until the company has
been projected.

## What gets projected

**Single-valued fields** come from `golden_attribute` — the value survivorship
chose, with the losing values still recorded upstream. Name, email, phone, job
title, address, the commercial profile.

**Array columns** (`industry`, `technologies`, `keywords`, `sic_codes`,
`naics_codes`) do *not* come from the golden record. A golden value is one value
by definition, and a `VARCHAR[]` column asking for one value would be a lie about
what the sources said. They come from the observations instead: every distinct
value any source reported for that field.

**`leads.company_id`** comes from the `employed_at` relationship, not from the
person's own attributes — a person row's employer columns belong to the company
entity (see [ADR 0013](../decisions/0013-employer-as-an-entity.md)).

**`leads.confidence_score`** is the mean confidence across that person's golden
fields, the same number `golden_person.mean_confidence` reports.

**`leads.source`** is the vendor that won the most of that person's fields. It is
a summary; the per-field answer stays in `golden_attribute.winning_source_id`.

**Nothing else.** The serving schema has columns no source reports and no
canonical field exists for — `is_b2b`, `ownership_status`, `logo_url`,
`tier_quality`, `intent_signal_arrays`. They are left alone. Filling them would
be enrichment by invention, which is what
[the enrichment job](ai-assistance.md) exists to do properly and visibly.

## What it skips, and why

**An entity with no name.** `companies.company_name` and `leads.name` are NOT
NULL in the serving schema. A company entity resolved on domain alone, still
waiting for a name from the next file, is a real and temporary state. It is
counted as skipped and projects itself the moment it has a name.

**A row that collides.** The serving contract makes lead emails and company
domains unique. If two pipeline entities both claim one email address — meaning
resolution has not merged them yet — the second is rejected, reported by
entity_id, and the watermark is **not** moved:

```
  rejected 2
    0193f8c1-...: duplicate key value violates unique constraint "leads_email_address_key"
  watermark unmoved: rerun after resolving the above
```

Holding the watermark back means the next run reconsiders those entities instead
of leaving them permanently unprojected. Everything else in the run was written;
re-writing it is idempotent. The alternative — moving the watermark past work
that failed — is a gap nobody ever notices.

Resolve the collision in the [review queues](review-console.md), then run again.

## Incremental by default

`projection_watermark` in the serving database records how far each entity type
got. The next run only looks at entities whose golden values or employment
changed after that moment.

It lives in the serving database rather than the pipeline's on purpose: it
describes the state of *that copy*. Restore the serving database from an older
dump and the watermark comes back with it, so the next run correctly re-projects
everything the dump was missing.

The watermark is the pipeline's clock, read at the start of the run — not the
end. Reading it at the end would silently skip anything committed while the run
was in progress.

`--full` ignores it entirely, which is what to use after changing the field maps
in `tools/ops/project_serving.py`.

## Scheduling it

Nightly is usually right — the pipeline is a batch system, and a lead that
appears in the application eight hours after the file was dropped is normal.

```cron
15 3 * * * cd /srv/pcdf && docker compose run --rm pipeline \
    python /tools/ops/project_serving.py >> /var/log/pcdf-projection.log 2>&1
```

Run it more often if you want; it is idempotent and an unchanged entity costs one
index probe.

## Changing what is projected

The field maps are three dictionaries at the top of
[`tools/ops/project_serving.py`](../../tools/ops/project_serving.py):
`COMPANY_SINGLE`, `COMPANY_MULTI` and `PERSON_SINGLE`, each mapping a canonical
field name to a serving column name. Adding a field is one line, plus an entry in
`CASTS` if the serving column is not text.

If the canonical field does not exist yet, add it in
[`libs/common/src/common/canonical.py`](../../libs/common/src/common/canonical.py)
first — see [Schema mapping](schema-mapping.md). A projection cannot invent a
field the pipeline never captured.

After either change, run once with `--full`.
