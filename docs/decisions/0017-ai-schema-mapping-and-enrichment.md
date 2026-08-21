# ADR 0017: AI schema mapping and enrichment, both against a local model

## Status

Accepted.

## Context

Two AI-assisted paths were requested, both against a locally hosted Ollama
model (`gemma3:4b` for initial testing) rather than a cloud API, and both
meant to run economically — a batch window, not a model kept warm around the
clock. Two ADRs already staked out where AI is allowed to sit in this
architecture, and this work has to land inside both lines rather than redraw
them:

- **ADR 0011** found no AI in the mapping engine and recorded, as future
  work, the one shape it thought AI belonged in: "an *assistant on the review
  queue*, never in the automatic path... propose a canonical field with a
  rationale for the human to accept or reject."
- **ADR 0014** drew the line for facts generally: "AI may propose, never
  assert... a model asked for a company's employee count or revenue will
  answer, fluently and often wrongly, and the answer would carry a confidence
  score computed as though a source had said it... that is not enrichment —
  it is contamination that cannot be told apart from the real thing
  afterward."

## Decision 1 — schema mapping gets the fallback ADR 0011 described

`mapping.engine.map_column` gains `_ai_candidates`, called only when the
deterministic candidates (alias, similarity, value analysis, corroboration)
leave a column below `AUTO_ACCEPT_THRESHOLD`. Its confidence is capped at
`AI_SUGGESTION_CAP` (0.75, the same cap value analysis uses, for the same
reason: a proposal, never a confirmation), so it can only ever land in
`needs_review` — the same queue `review-mappings` already works, method
`ai_suggestion` alongside `value_analysis` and the rest.

Gated behind `AI_MAPPING_ENABLED` (default off), since mapping runs inline on
every file load — turning it on unconditionally would make loading a file
depend on a reachable model. And the call only happens for a column already
below auto-accept, so a well-mapped file never pays for it: one model call at
most per unique column layout, not per column, not per row.

This is a straight implementation of ADR 0011's plan; it does not revisit
that ADR's reasoning.

## Decision 2 — enrichment gets a fourth review queue, not a write path

The first implementation of this ADR wrote an AI-guessed field straight into
`attribute_observation` through a low-reliability synthetic source
(`ai_enrichment`), reasoning that a low enough reliability would keep it from
ever outranking real vendor data in survivorship. That is true, and beside
the point: ADR 0014 already considered and rejected exactly this shape,
because the reliability tuning doesn't change what the row *is* — an
observation with a confidence-like number attached, indistinguishable
downstream from one a vendor's file produced, for a value nobody outside the
model actually reported.

**Decision:** a confident answer becomes a row in `enrichment_proposal`
(migration 0021) — `status = 'pending'` — not an observation. It is a fourth
review queue, the same shape [`review-console.md`](../guides/review-console.md)
already documents for schema mappings, duplicate candidates and quarantined
records: `enrich proposals list / accept / reject`. Only `accept` writes
through `raw_record` / `attribute_observation` / `record_entity_link`, via the
same `ai_enrichment` source and its configurable low reliability — at that
point a human has confirmed it, and it is evidence competing in survivorship
like any other source's, not an assertion wearing a confidence score.
Consistent with every other queue in this project, accepting does not rebuild
the golden record on its own; the operator runs `golden build` after.

**Scope, corrected by testing against the real model.** The first pass
computed `ELIGIBLE_FIELDS` by *excluding* contact/identity fields (email,
phone, url, external identifiers, the entity's own name) and allowing
everything else — which still let through addresses, revenue, funding,
employee counts, and free text (`seo_description`, `keywords`,
`technologies`). A smoke test against the actual target model, `gemma3:4b`,
showed that was wrong on two counts:

- Asked to fill `seo_description` for a company it had almost no data on, it
  invented a fluent, plausible paragraph at **stated confidence 0.9**.
  Open-ended text generation and hallucination are the same operation on a
  model this size.
- Asked to fill `industry` or `seniority` from nothing but a company or
  person **name** — including names made up for the test, describing nothing
  real — it still answered confidently. Stated confidence does not track
  whether the model actually has grounding.

`ELIGIBLE_FIELDS` is now a small, explicit allowlist per entity type, chosen
the way ADR 0014 chose its nine commercial-profile fields — named and
justified, not derived by exclusion. Nothing numeric, spatial, dated, or
open-ended free text.

A second pass narrowed it further, for a reason specific to three fields
rather than to AI risk generally: `sic_description`, `company_category` and
`seniority` are each defined in `common/canonical.py` as *the source's own*
classification — a vendor-specific code or band ("Seniority band the source
assigns", "Source's own category/segment code"), not a fact with one right
answer. There is nothing to infer; asking a model to invent a vendor's
internal segment code or banding scheme is a category error, not a narrower
version of enrichment. Dropping them leaves:

- **company**: `industry`
- **person**: `industry`, `department`

A third gate, `MIN_KNOWN_FIELDS`, refuses to ask with fewer than two known
facts in hand, since a bare name was enough for the model to fabricate an
answer anyway. None of these three are the actual safety backstop — that's
Decision 2's review queue — they only cut down how much of what a human sees
is obvious noise.

**What makes the batch approach viable on a small model.** Every `(entity,
field)` pair the job asks about — proposed, declined, or errored — is
recorded in `enrichment_attempt` (migration 0020) and never asked about
again automatically. A daily run converges to zero new model calls once it
has caught up with genuinely new gaps, which is what makes "collect all day,
enrich in a short window, shut the model down" ([`docker compose --profile
ai up -d ollama` / `... stop ollama`](../guides/ai-assistance.md)) cost
roughly the same regardless of how often it runs.

## Consequences

- `column_mapping.mapping_method` gains `ai_suggestion`; `source.source_type`
  gains `ai_enrichment` (migration 0019).
- Two new tables: `enrichment_attempt` (the dedup gate) and
  `enrichment_proposal` (the review queue) — migrations 0020 and 0021.
- A fourth queue in the review-console guide. `reviewed_by` is required on
  accept/reject, same as the other three.
- `AI_MAPPING_ENABLED` and the enrichment job are both off unless explicitly
  run; no deployment's behavior changes by this ADR landing.
