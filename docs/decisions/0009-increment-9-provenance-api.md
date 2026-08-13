# ADR 0009: Increment 9 — provenance and the read API

## Status

Accepted.

## Context

Every stage recorded why it did what it did, but each recorded it in its own
table. The data needed to answer *"why does this company's address say that?"*
was complete and completely unusable: it required six joins across
`golden_attribute`, `attribute_observation`, `column_mapping`,
`validation_result`, `record_entity_link` and `source`, written by hand, by
someone who knew the schema.

The `api` service, meanwhile, still served only health checks. The trusted
canonical data the pipeline exists to produce was reachable by `psql` alone.

This increment completes spec step 16 (history/provenance) by making the chain
answerable, and gives the API something to serve.

## Decisions

- **One question, one answer: `explain_value`.** It walks golden value →
  contributing observations → source cell, and attaches, per contribution, the
  four other judgements already on record. The output shows, side by side and
  never summed:

  | Question | Number |
  | --- | --- |
  | Did we read the column correctly? | `mapping_confidence` |
  | Is the value usable as that field? | `record_validation_status` + rule results |
  | Is this record the same entity? | `match_confidence` |
  | How much do we believe this vendor? | `source_reliability` |
  | Why did this value beat the others? | golden `strategy` + `confidence` |

  This is the spec's "never conflate these" requirement made visible rather than
  merely obeyed. A single blended score would also destroy the ability to say
  *"the value is fine, we're just unsure it's the same company"*.

- **Lineage lives in `libs/common`, not in a service.** Both the read API and
  `golden explain` need exactly these joins, and two copies would eventually
  disagree about provenance — the one thing that must never happen. The
  trade-off is that `common` now holds domain queries as well as
  infrastructure; the alternative was a service depending on another service.

- **Everything here is read-only.** Data enters through the pipeline, where it
  acquires provenance. An endpoint that could write a golden value would create
  records nothing can explain, which is the failure this whole design exists to
  prevent.

- **Sync queries in `def` handlers.** FastAPI runs non-async handlers in a
  threadpool, so the existing synchronous psycopg code is reused as-is. Making
  the queries async would have meant either blocking the event loop or
  maintaining a second, divergent set of them.

- **Merged ids keep answering.** Every endpoint resolves tombstones and reports
  both `requested_entity_id` and the surviving `entity_id`. An id that stops
  working is not a stable id.

- **Search hits identity keys, not golden values.** An entity stays findable by
  every identifier any vendor ever gave it, including ones that lost the
  survivorship contest — which is exactly when you most need to find it.

- **The timeline is a union, not an event table.** Each stage owns its own
  history; forcing them into a shared log would make every stage depend on a
  format none of them controls. The cost is a wide `UNION ALL`, which is
  acceptable for a per-entity query.

## Two bugs the end-to-end run exposed

Neither was reachable by unit tests, because both were about data that had
accumulated across several batches:

1. **Stale supporting evidence.** The idempotency rule ("unchanged value keeps
   its `valid_from`") also skipped updating *why* the value was believed. After
   a fourth vendor agreed on the company name, the record still read
   `supporting_sources: 2, confidence: 0.750`. The fix separates the two ideas:
   a new vendor agreeing does not make the value newly true, so `valid_from`
   must not move and no history row is written — but the confidence and
   agreeing-source list are refreshed in place. Rebuilding now reports
   `1 refreshed, 15 unchanged`, and the value reads `3 sources, 0.990` with its
   original `valid_from` intact.

2. **A flag conflating two questions.** `won` was true for every observation
   whose value matched the golden one, which reads as "this record produced the
   trusted value" but actually meant "this record agreed with it". Split into
   `agrees_with_golden` and `is_winning_record`.

## Verification

Explaining a field with history, against the live database:

```
trusted value : 465 California St 9th Floor
chosen by     : most_recent (confidence 0.750)
agreement     : 1 source(s) agreed, 2 distinct value(s) competed

    vendor_dc  real_company_sample.csv row 1
      column 'ADDRESS' held '1779 Massachusetts Ave NW #815'
      column interpreted : exact_alias @ 1.000 (auto_accepted)
      record validated   : warning
      linked to entity   : no_match @ 0.000 (new_entity)
      vendor reliability : 0.70

 -> vendor_c  vendor_c_update.csv row 1
      column 'address' held '465 California St 9th Floor'
      linked to entity   : website_domain @ 0.960 (auto_linked)
      vendor reliability : 0.85

previously:
      1779 Massachusetts Ave NW #815  until 2026-08-13 06:10
```

The timeline for that entity returns **35 events** spanning four vendors: six
ingestions and links, a human-accepted merge, and every golden value set and
superseded.

`GET /entities?q=asiafoundation.org` finds the entity by a domain key, reporting
`record_count: 4`.

## Consequences

- The API is still unauthenticated. It is local-first and read-only, but
  anything beyond a laptop needs auth before exposure — worth stating plainly
  rather than discovering later.
- `explain_value` runs several queries per call and is not paginated. Fine for
  one entity; a bulk export would need a different shape.
- The `api` image now installs dev dependencies so its suite runs in-container
  like every other service, matching the existing pattern. It is a larger image
  than a pure runtime build would be.
- Grafana is still provisioned with only infrastructure metrics. The pipeline
  emits no business metrics — quarantine depth, open match candidates, contested
  golden fields, mapping review backlog — and those are the numbers that would
  tell an operator the data foundation is drifting.
