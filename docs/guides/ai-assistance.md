# AI assistance

Two narrow, opt-in uses of a local model — one filling gaps in schema mapping,
one filling gaps in the golden record — both running against
[Ollama](https://ollama.com), not a cloud API. Nothing about your data leaves
the machine running the model.

Part of the [People & Company Data Foundation](../../README.md).

## Why these two, and not more

Both slots exist because they fit a shape the rest of the pipeline already
enforces: a proposal that a human, or the existing confidence math, gets to
overrule. Neither one is allowed to become truth on its own.

- **Schema mapping** ([Schema mapping](schema-mapping.md)) already has a place
  for a guess that names a field without confirming it — `needs_review`. The
  model is only ever asked about a column that alias/similarity/value-analysis
  left there or in `unmapped`, and its answer is capped below the auto-accept
  threshold, so it can only ever land in the same queue a human already works.
  A well-mapped file never calls the model at all.
- **Enrichment** fills a missing *field* on an entity that already has other
  reported data — never an identifier, contact detail, or the entity's own
  name. A confident answer becomes a *proposal*, not an observation: per
  [ADR 0014](../decisions/0014-commercial-profile-and-enrichment.md) and
  [ADR 0017](../decisions/0017-ai-schema-mapping-and-enrichment.md), AI may
  propose, never assert. It is a fourth review queue, alongside the three
  [Review queues](review-console.md) already documents — nothing reaches the
  golden record until a human accepts it. Once accepted, it writes through a
  synthetic source (`ai_enrichment`) at a low, configurable reliability, so
  [golden-record survivorship](golden-record.md) only ever prefers it when
  nothing else was reported. One real vendor value always outranks it.

## Running the model

Not started by `docker compose up` — it has its own profile, because the point
of the batch approach below is to not pay for an idle model all day:

```powershell
docker compose --profile ai up -d ollama
docker compose exec ollama ollama pull gemma3:4b   # or whatever OLLAMA_MODEL is set to
```

`gemma3:4b` is the default in `.env.example` — small enough to run on a laptop
CPU, meant for testing the two paths below. Swap `OLLAMA_MODEL` for a larger
model later; nothing else changes, since every call here only ever asks for
JSON in a fixed shape and treats a bad or missing answer as "no opinion",
never as an error.

Already running Ollama natively on the host instead? Skip the `ollama`
service entirely and point the containers at it:

```powershell
# .env
OLLAMA_HOST=http://host.docker.internal:11434
```

`host.docker.internal` is what a container uses to reach the host on Docker
Desktop (Windows/Mac); the containerized `ollama` service above binds
`11434` on the host too, so the two are mutually exclusive on one machine.

## Schema mapping's fallback

Off by default (`AI_MAPPING_ENABLED=false`), because mapping runs inline on
every file load — turning this on unconditionally would make loading a file
depend on a reachable model. Turn it on only while `ollama` is up:

```powershell
# .env, or passed at run time
AI_MAPPING_ENABLED=true
```

From there it needs nothing else: `pipeline process run`, `pipeline process
watch`, and `mapping map-schema` all pick it up automatically. A column the
model placed shows up in `review-mappings list --status needs_review` exactly
like any other guess, with `mapping_method = ai_suggestion` and the model's
own stated confidence recorded in `evidence`.

## Enrichment, run as a batch

This is the one meant to run in a bounded window rather than continuously —
collect a day's files under the normal pipeline, then spend a few minutes of
model time asking about the golden record's gaps, rather than keeping a
model warm around the clock:

```powershell
docker compose --profile ai up -d ollama
docker compose run --rm enrichment enrich run --entity-type company
docker compose run --rm enrichment enrich run --entity-type person
docker compose stop ollama
```

Wire that sequence into whatever runs your daily batch (cron, a scheduled
task, `data/inbox/watch/`'s own cadence) — nothing here restarts it on its
own. `run` never writes a golden value; it only files proposals for a human
to work, same as the other three queues.

**Working the proposal queue** — the same shape as every other queue in
[Review queues](review-console.md):

```powershell
docker compose run --rm enrichment enrich proposals list --status pending
docker compose run --rm enrichment enrich proposals accept --proposal-id <id> --reviewed-by you
docker compose run --rm enrichment enrich proposals reject --proposal-id <id> --reviewed-by you
```

Accepting is what writes the value — through the same raw_record /
attribute_observation / record_entity_link path a vendor row would use — and,
consistent with every other queue here, doesn't rebuild the golden record on
its own:

```powershell
docker compose run --rm golden golden build --entity-id <id>
```

**What makes repeat `run`s cheap.** Every `(entity, field)` pair the model is
asked about is recorded in `enrichment_attempt`, whatever the outcome. A run
that has caught up costs zero model calls the next time — it only asks about
entities or fields that are genuinely new since the last run. This is
separate from a proposal's own status: a rejected proposal is not re-asked
either. To force a retry (a better model, a fixed prompt), delete the
`enrichment_attempt` row; nothing retries on a schedule by itself.

**What fields are ever in scope.** A small, explicit allowlist: `industry`
for companies; `industry` and `department` for people — see
`ELIGIBLE_FIELDS` in `enrichment/engine.py`. Two things narrowed it to this:

- Not derived by excluding the obviously bad fields (contact details,
  identifiers, the entity's own name); an earlier version was, and a live
  test against `gemma3:4b` showed it wasn't enough — asked to fill
  `seo_description` for a company it had almost no data on, it invented a
  fluent paragraph at stated confidence 0.9, and it answered confidently
  from nothing but a made-up name.
- `sic_description`, `company_category`, and `seniority` were dropped for a
  different reason: each is defined in the canonical schema as *the
  source's own* classification — a vendor-specific code or band, not a fact
  with one right answer. There's nothing to infer; asking the model to
  invent a vendor's internal segment code isn't narrower enrichment, it's a
  category error.

Also why the model is never asked with fewer than two known facts in hand
(`MIN_KNOWN_FIELDS`) — a bare name was enough for it to fabricate an answer
anyway. None of this is the safety backstop, though: that's the review queue
below. These just cut down how much of what a human sees is obvious noise.

**Tracing an accepted value.** It's an observation like any other:

```powershell
docker compose run --rm golden golden explain --entity-id <id> --field industry
```

shows `ai_enrichment` as the source, its reliability, and whatever it beat —
same command, same shape, as tracing a value a vendor reported.
