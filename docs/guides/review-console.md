# Review queues

Working the queues that stop and ask for a human.

Part of the [People & Company Data Foundation](../../README.md).

Four stages refuse to guess and file the question instead:

| Queue | Stopped because | Deciding it means |
| --- | --- | --- |
| **Schema mappings** | A column could be one of several canonical fields, or none | Saying which field it is, once, for every future file of that layout |
| **Possible duplicates** | A match was plausible but not certain | Saying whether two entities are one real thing |
| **Quarantined records** | A record failed validation with an error | Saying whether to use it anyway or confirm it's unusable |
| **Enrichment proposals** | A local model guessed a field no vendor reported | Saying whether the guess becomes a real observation — see [AI assistance](ai-assistance.md) |

Nothing ages out of these queues on its own. A record can sit in quarantine
indefinitely without anything being lost — and without anyone noticing,
which is why it's worth checking periodically rather than assuming silence
means empty.

## In a browser

`services/review_console` is a small standalone service — one page, no build
step — that starts with the stack:

```bash
docker compose up -d
open http://localhost:8000/review/page
```

Want this alongside the [pipeline dashboard](dashboard.md) in one page,
tabbed, instead of two? `http://localhost:8000/admin/page` is the same
queues and the same decision endpoints, combined; both pages keep working
on their own too.

It writes nothing of its own. Every button posts to an endpoint that calls
the exact same function the terminal commands below call
(`mapping.repository.set_mapping`, `resolution.pipeline.accept_candidate`,
`validation.quarantine.review`, `enrichment.pipeline.accept`), so a decision
made in the browser and the same decision made in a terminal are the same
code path — and, unlike the terminal path below, the browser path also runs
the follow-through automatically (see "What a decision sets in motion").

It shows personal data with full provenance attached, so it is gated the same
way as everything else that does: unset `PCDF_API_KEYS` and
`PCDF_ALLOW_UNAUTHENTICATED` and it serves only to loopback; set one to open
it up. See `.env.example` and `services/review_console/src/review_console/auth.py`.

## Working a queue

```bash
# schema mappings
docker compose run --rm mapping review-mappings list --status needs_review
docker compose run --rm mapping review-mappings set --mapping-id <id> \
    --canonical-field work_phone --reviewed-by you

# possible duplicates
docker compose run --rm resolution resolve candidates --status open
docker compose run --rm resolution resolve accept --candidate-id <id> --reviewed-by you
docker compose run --rm resolution resolve reject --candidate-id <id> --reviewed-by you

# quarantine
docker compose run --rm validation quarantine list --status open
docker compose run --rm validation quarantine show --record-id <id>
docker compose run --rm validation quarantine release --record-id <id> --reviewed-by you \
    --note "checked against the source file"

# enrichment proposals
docker compose run --rm enrichment enrich proposals list --status pending
docker compose run --rm enrichment enrich proposals accept --proposal-id <id> --reviewed-by you
docker compose run --rm enrichment enrich proposals reject --proposal-id <id> --reviewed-by you
```

`reviewed_by` is required and cannot be empty on any of these.

## What a decision sets in motion

None of these CLIs carry follow-through — deciding closes the queue item and
nothing else. Run the next step by hand:

**After accepting a merge**, rebuild the survivor's golden record:

```bash
docker compose run --rm golden golden build --entity-id <survivor>
```

Without it the trusted values stay computed from half the observations — the
absorbed entity's sources are attached but never consulted.

**After releasing a record from quarantine**, resolve and build it:

```bash
docker compose run --rm resolution resolve run --batch-id <id>
docker compose run --rm golden golden build --batch-id <id>
```

A released record is *resolvable*; nothing resolves it on its own. Both are
batch-scoped and skip work already done, so releasing one record out of a
huge batch costs one record's worth of work, not the batch.

**After accepting an enrichment proposal**, rebuild that entity's golden
record:

```bash
docker compose run --rm golden golden build --entity-id <id>
```

Accepting writes the observation; it does not fold it into the trusted
value on its own.

## What lands in quarantine for a mapping reason vs. a data reason

Worth telling apart when triaging: `record.has_identifier` failing because a
row has genuinely no email/name/website is a **data** problem — the row is
what it is. The same failure because the mapping is stuck at `needs_review`
(so a real `company_name` column isn't being read as one yet) is a
**mapping** problem — approve the mapping, re-run normalize/validate, and
the quarantined rows that were only blocked by the mapping close themselves
as `resolved`, no human needed per row.
