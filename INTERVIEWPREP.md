# PCDF Interview Prep — People & Company Data Foundation

A study guide for talking through this project in an interview: what it does, why it's built the way it is, and the hard questions an interviewer is likely to ask.

---

## 1. The 30-second pitch

PCDF takes messy vendor exports (CSV/Excel) of people and companies, and turns them into **one trusted, deduplicated record per real-world entity**, while keeping every original value traceable back to the exact source cell it came from — forever. Nothing is ever thrown away, even values that lose. It's a record-at-a-time pipeline (Kafka announces batches; it never carries values) with six stages: **ingest → map schema → normalize → validate/quarantine → resolve entities → build golden record**, plus a review console for the four places a human has to make a call, and an AI-assisted path that is only ever allowed to *propose*, never *assert*, a fact.

---

## 2. Architecture at a glance

```mermaid
flowchart TD
    F["CSV / Excel file\n(watched folder or manual)"] --> ING[ingestion]
    ING -->|raw_record, batch, source| MAP[mapping]
    MAP -->|column_mapping keyed to source_schema| NORM[normalization]
    NORM -->|attribute_observation\n(1 row per column per row, lossless)| VAL[validation]
    VAL -->|validation_result + record_validation| Q{quarantine gate}
    Q -->|invalid| QI[(quarantine_item / quarantine_event)]
    Q -->|valid / warning| RES[resolution]
    QI -->|human releases| RES
    RES -->|entity, entity_identity_key,\nrecord_entity_link, match_candidate| GOLD[golden build]
    GOLD -->|golden_attribute\ncurrent + full history| API[provenance API / CLI]
    RES -.open candidates.-> RC[review_console]
    MAP -.needs_review.-> RC
    QI -.open.-> RC
    ENR[enrichment batch job] -.pending proposals.-> RC
    RC -->|accept| GOLD
    ENR -->|accepted proposal becomes\na real source row| NORM
```

**Two entry points, same underlying functions, can never disagree:**
- **Record-at-a-time** (`pipeline process run` / `process watch`): the production path. One record goes through every stage inside one transaction before the next record starts. This is what the watcher uses when a file lands in `data/inbox/watch/<feed>/`.
- **Stage CLIs** (`map-schema`, `normalize`, `validate`, `resolve`, `golden build`), each run by hand against a `--batch-id` already sitting in Postgres. This is the *reprocessing* path — redo one stage after a rule change without re-touching the source file.

**The broker carries references, never values.** A record moving to the next stage is a function call or a row already in Postgres; Kafka announces that a *batch* is committed and ready, and the stage consumers work from that. Postgres is the source of truth, so an event cannot go stale against it. See ADR 0012 and ADR 0018.

---

## 3. Why so many tables? (the core mental model)

The single idea that explains almost every table in this schema: **every kind of judgement gets its own table, and none of them get collapsed into one score.** Mapping confidence, validation verdict, match confidence, and source reliability are four *different questions* — "is this the right field," "is this value usable," "is this the same entity," "how much do I trust this vendor" — and the system refuses to average them into one number, because that number would be unexplainable. Instead:

| Question | Table(s) | Answered at stage |
|---|---|---|
| What did the source literally say? | `raw_record` (whole row), `attribute_observation` (per column, incl. unmapped) | ingestion / normalization |
| Which canonical field does this column mean? | `column_mapping` (keyed to a schema fingerprint, not a batch) | mapping |
| Is this value usable? | `validation_result` (per observation, passes included), `record_validation` (rollup) | validation |
| Should this record be held back? | `quarantine_item` (current state), `quarantine_event` (append-only history) | quarantine |
| Is this the same real-world person/company as something already known? | `entity`, `entity_identity_key`, `record_entity_link`, `match_candidate`, `entity_merge` | resolution |
| What's the one trusted value per field, and why? | `golden_attribute` (current + history in one table, rejected alternatives kept in `evidence`) | golden |
| Did *our* code fail before a row even became data? | `record_error` (distinct from quarantine: quarantine is a verdict about the data, record_error is a bug/malformed-payload on our side) | any stage |
| Can AI touch this? | `enrichment_attempt` (dedup/cost gate), `enrichment_proposal` (a proposal, not a fact) | enrichment |

That's the whole schema, table by table, table 0001–0021 in `db/migrations/`. Nothing merges; everything stays queryable independently, which is what makes `explain_value()` in `libs/common/lineage.py` able to show five separate confidence dimensions side by side instead of one opaque score.

---

## 4. Stage-by-stage Q&A

### Ingestion
**Q: What happens when a file is dropped?**
A: `ingestion` reads it with type inference *disabled* everywhere — every cell becomes a string, verbatim. Rows become `raw_record` rows (`raw_payload` jsonb + `payload_hash`), grouped under a `batch` (tracks rows_read/ingested/skipped, status running/completed/failed). A `source` row (vendor identity + `reliability` score 0–1) is created once and reused.

**Q: Why disable type inference?**
A: The single most load-bearing rule in the whole system: never destroy source information before it's captured. A spreadsheet library silently turning `"007"` into `7`, or a date string into a different date format, is unrecoverable data loss that happens before anyone even decided the column meant anything.

**Q: What stops the same file being loaded twice?**
A: `payload_hash`/`file_hash` plus an explicit re-ingest guard (`--allow-reingest` to override); a race for two processes ingesting the same file concurrently is closed via `find_in_flight_batch` (ADR 0011).

### Schema mapping
**Q: Why is mapping keyed to a schema fingerprint, not a batch?**
A: `source_schema.column_fingerprint` is a sha256 of the ordered column list. The same vendor sends the same layout across many files; keying to the fingerprint means a human's correction on one file is reused forever on every future file with that exact layout — review work isn't repeated.

**Q: How does a column get mapped?**
A: A cascade of methods, each with a confidence: `exact_alias` (1.0) → `similarity` (difflib) → `value_analysis` (values match a pattern, capped at 0.75 — deliberately can never auto-accept, because values prove a type, not a field) → `corroborated` (+0.15 for multiple signals agreeing) → `ai_suggestion` (opt-in, same 0.75 cap) → `manual`. Status buckets: `auto_accepted` (≥0.90), `needs_review` (0.60–0.90), `unmapped` (<0.60).

**Q: What if two columns both look like they map to the same field?**
A: Both drop to `needs_review` — no silent winner is picked.

### Normalization
**Q: What's `attribute_observation` and why does it exist even for unmapped columns?**
A: It's the lossless substrate — literally one row per source column per source row, mapped or not. Even a column nobody has reviewed yet gets captured with `raw_value`, so nothing is lost while mapping review is still pending. `canonical_field` only gets set once a mapping is confirmed (`auto_accepted`/`approved`).

**Q: Why store raw_value and normalized_value side by side instead of overwriting?**
A: So validation can check *both* — normalization can accidentally mask a bad value (e.g. `"50-100"` normalizing into `"50100"`), and checking only the normalized side would hide that.

### Validation & quarantine
**Q: What does validation actually decide?**
A: Whether a value is *usable*, never whether it's *true*, and it never repairs anything. Every check — pass or fail — gets a `validation_result` row (so "no row" can't be confused with "passed"). Severities: `error`/`warning`/`info`; only a record-scope `error` invalidates the whole record.

**Q: Why is quarantine a separate table from validation, and why a VIEW (`resolvable_record`) instead of a filter in code?**
A: Quarantine is a *routing decision* ("should resolution even see this record"), not a copy of the data. Putting the gate in a view means every consumer (resolution, reprocessing, ad hoc queries) automatically respects it — there's exactly one place the rule lives, so it can't drift between code paths.

**Q: Tell me about a real bug in this stage.**
A: Originally *any* attribute-level error would invalidate the whole record. That meant a single broken `Phone` column wrongly quarantined 907 of 1000 otherwise-good rows. Fixed in ADR 0013: only record-scope errors invalidate now.

### Entity resolution
**Q: How does matching actually work?**
A: Blocking/identity keys are built only from *confirmed* mapped values (`entity_identity_key`: key_type, key_value, strength strong/moderate). Each candidate entity gets scored (`score_candidate`): start from the highest-weighted matched key, add `+0.04` per *additional distinct key type* that also matches (not per value — that would let one vendor's duplicate columns fake corroboration). If the best key is only "moderate" strength, confidence is hard-capped at 0.89 — no amount of moderate corroboration can cross the auto-link line.

**Q: What are the thresholds?**
A: `AUTO_LINK_THRESHOLD = 0.90`, `REVIEW_THRESHOLD = 0.60`. ≥0.90 → auto-link. A **tie** at ≥0.90 between the top two candidates is deliberately forced down to `review` instead of coin-flipped. 0.60–0.90 → files a `match_candidate` for human review, but the record still gets its *own* new entity in the meantime so the pipeline never blocks waiting on a human. <0.60 → new entity, no candidate filed.

**Q: What's `source.describes` and why does it matter?**
A: A vendor either catalogues organisations or catalogues locations/premises. Two Subway franchise locations can share a domain and address pattern but are NOT the same business. If `describes = 'location'`, domain/email keys get demoted from strong to moderate so they can't auto-link alone. A real bug: before this existed, `reblock.py --merge` would have collapsed 5,097 genuinely distinct locations into single entities.

**Q: Why does merge never delete an entity id?**
A: `entity_merge` is append-only; the losing entity becomes a tombstone (`status='merged'`, `merged_into_entity_id` set), never deleted or reused. Anything that referenced the old id (an API caller, a cached link) still resolves correctly by following the tombstone chain — `lineage.resolve_entity()` follows up to 16 hops.

### Golden record
**Q: What decides the "trusted" value for a field?**
A: A **per-field survivorship strategy**, not one global rule (`FIELD_STRATEGY` in `golden/strategies.py`): `most_recent` for things that legitimately change (job title, address, revenue), `most_frequent` for stable identity facts where disagreement means someone's wrong (name, legal_name — counts distinct *sources*, not rows, so one chatty vendor repeating itself isn't 5 votes), `earliest` for immutable facts (founded_year), and `most_reliable_source` as the default (identifiers, anything without temporal/consensus behavior).

**Q: How is confidence computed?**
A: `winner.reliability + 0.15 × (supporting_sources − 1) − 0.10 × dissenting_sources`, clamped to [0.05, 0.99]. `dissenting_sources` counts distinct *sources* with a different value, not distinct losing values — so one vendor being internally inconsistent isn't punished as if many independent vendors disagreed.

**Q: What happens to the values that lose?**
A: Never discarded — kept in `evidence.alternatives` on the winning row, with their sources. `golden_attribute` holds current + full history in one table (`valid_to IS NULL` = current, `superseded_by` chains history); rebuilding is idempotent — an unchanged winner keeps its original `valid_from` even if the builder reruns.

**Q: Can anything write directly to `golden_attribute`?**
A: No — never. It is always *derived* by the builder from `attribute_observation`. This is the enforcement point behind the AI boundary rule (next section).

### Provenance / API
**Q: How do you prove where a value came from?**
A: `explain_value()` in `libs/common/lineage.py` walks golden → observation → source cell and shows five confidence dimensions (mapping confidence, validation verdict, match confidence, source reliability, golden strategy) *side by side*, never summed into one number. This logic lives in the shared lib specifically so the API and the `golden explain` CLI can't drift into two different explanations for the same value.

### AI assistance & enrichment
**Q: Where is AI allowed to touch this system, and where not?**
A: "AI may propose, never assert." Allowed: suggesting a canonical field stuck in `needs_review` (capped at 0.75 confidence, same as value_analysis, gated behind `AI_MAPPING_ENABLED=false` by default), judging an ambiguous duplicate pair for a human to confirm, normalizing free text. **Not allowed:** producing a fact that gets written as a golden value. `enrichment enrich run` only ever files an `enrichment_proposal` (pending/accepted/rejected); only a human `accept` turns it into a real `source` row and observation, competing as evidence exactly like any vendor.

**Q: Why does that matter?**
A: A model asked for a company's revenue answers fluently and often wrongly — indistinguishable afterward from a value a real source actually stated. In a system whose entire value proposition is "every value traces back to who claimed it," that's contamination, not enrichment.

**Q: Was this always the design?**
A: No — ADR 0017 says the first draft of enrichment wrote straight to `attribute_observation` via a low-reliability synthetic AI source, and it was rejected mid-build for contradicting the AI-boundary rule from ADR 0014, even with the reliability dial tuned way down. It was replaced with the proposal queue. Also: live-testing `gemma3:4b` showed it would confidently hallucinate answers even for fields with zero grounding (a fabricated company name still got a "revenue" answer back) — which is why `ELIGIBLE_FIELDS` is a small explicit allowlist, not "ask about anything."

**Q: How does the enrichment job avoid re-asking the same question forever?**
A: `enrichment_attempt` has `UNIQUE(entity_id, canonical_field)` — each (entity, field) pair is asked about at most once, ever. A caught-up source costs nothing on the next scheduled run.

### Review console
**Q: What's the difference between the CLI review path and the browser console?**
A: They call the *exact same underlying functions* (`set_mapping`, `accept_candidate`, `quarantine.review`, etc.) — the console adds no logic of its own. The one real difference: the console makes decisions "finish themselves" — accepting a merge or releasing a quarantined record automatically triggers the batch-scoped follow-up rebuild (resolve + golden build) via the API, where the terminal path leaves that step manual.

---

## 5. Tricky / gotcha questions (real incidents from the ADRs — great "tell me about a bug" material)

- **"Deleting 50K rows took 11+ minutes — why?"** Postgres never auto-indexes the *referencing* side of a foreign key. Four FKs pointing at `raw_record` had none. Fixed in migration 0008 (and 7 more in 0011, forced by a 20-minute cleanup on `golden_attribute.superseded_by`, a self-referencing FK).
- **"An index was silently rejecting inserts."** `attribute_observation_field_value_idx` (a btree on canonical_field+normalized_value) hit Postgres's 2704-byte tuple limit on long values (e.g. a giant `Technologies` field from Apollo), silently failing the insert and routing 20 otherwise-good rows to `record_error`. It also had zero scans, ever. Dropped in migration 0014.
- **"jsonb doesn't preserve column order — why does that matter here?"** Two code paths were deriving `column_fingerprint` from two different orderings of the same file's columns, silently creating two `source_schema` rows for what was really one layout — which meant a reviewer's mapping correction on one "layout" silently didn't apply to the other. Fixed by adding an explicit `batch.columns` jsonb array (migration 0015).
- **"Your `record_error` safety net had the same hole it was supposed to catch."** jsonb itself rejects NUL bytes. A malformed payload containing one couldn't even be safely logged. Fixed by adding `raw_payload_bytes` (bytea) alongside a `payload_sanitized` flag (migration 0010).
- **"907 out of 1000 rows got wrongly quarantined — what happened?"** Any attribute-level validation error invalidated the whole record. One broken `Phone` column took down almost the entire batch. Fixed in ADR 0013: only record-scope errors invalidate a record now.
- **"A merge tool almost destroyed 5,097 real, distinct records — how?"** `reblock.py --merge` wasn't honoring `source.describes`. A vendor cataloguing 5,097 individual retail locations that happened to share a parent domain would have all been merged into one entity. Fixed in ADR 0015 by checking `describes` before merging.
- **"Why record-at-a-time instead of the original batch design, if it's 6.6x slower naively?"** Per-record delivery mattered more than raw throughput, but the naive version really was 6.61x slower. Recovered to 2.14x by: client-side UUIDv7 generation (removes RETURNING round-trips), libpq pipeline mode (one sync per record instead of per statement), and `synchronous_commit=off` as an opt-in flag (defers fsync only — atomicity/isolation untouched).
- **"Why is `record_error` a different table from `quarantine_item`?"** Quarantine is a verdict *about the data* (it's invalid, hold it, a human can release it). `record_error` is a failure *of the system* (malformed payload, a bug, a constraint violation) that happened before the row could even become a proper `raw_record`.

---

## 6. System-design / "what would you change" questions

**Q: Kafka is in the stack — but a record never travels on it. Why?**
A: Two different questions got separated. *Moving a record between stages* is a correctness problem: a record's normalize/validate/resolve/golden work happens in one transaction so it lands whole or not at all (ADR 0012), and splitting that across a broker would reintroduce the window where a record exists but its entity's values haven't caught up. Resolution also depends on file order — record 900 has to match the entity record 12 created — and a record carries several identity keys (email, phone, linkedin, name+company), so there is no single partition key that keeps everything which might touch one entity on one partition. Serializing that is what the per-key advisory locks do; Kafka partitioning cannot express it. *Announcing that a batch is ready* is a decoupling problem, and that is what Kafka does here: `batch.ingested` carries a batch id, the stage consumers work from it, and a stopped consumer means work waits in the topic rather than being lost. Rows are committed before anything is published, so an event can never reference a row that doesn't exist; if the broker is down, `batch.events_published_at` stays NULL and the gap is queryable and replayable.

**Q: How would you shard/scale this?**
A: Resolution is the actual bottleneck to parallelizing — records are resolved strictly in file order, one at a time, because a later record can match an entity an earlier record in the *same file* just created, and that ordering guarantee is what's currently sacrificed for any parallelism. You'd need to partition by blocking key (email domain, name+city bucket) so cross-partition collisions become rare enough to reconcile after the fact, then explicitly design the "two partitions created the same entity independently" merge path — which the schema already supports (`entity_merge` never deletes an id).

**Q: How would you handle "delete this person's data" (GDPR)?**
A: The schema is deliberately append-heavy and provenance-first, which is in tension with hard deletes. You'd need a real design decision here — likely a tombstone/redaction pass that nulls `raw_payload`/`raw_value` in place (keeping the row and its foreign keys intact so joins don't break) rather than deleting rows, plus a rebuild of any `golden_attribute` that depended on the deleted evidence.

**Q: Single Postgres instance — what's the failure mode, and how would you fix it in Kubernetes?**
A: ADR 0016 explicitly kept Postgres single-instance and flagged that real HA needs Patroni or CloudNativePG — out of scope for that increment. What K8s bought was only a stable volume/name across a pod reschedule, not actual failover. That's a named, acknowledged gap, not an oversight — good to say so directly if asked.

**Q: Why does resolution favor "record still gets its own entity, file the candidate for later" over blocking on human review?**
A: Throughput and availability of the pipeline matter more than immediate certainty — an ambiguous match doesn't stall everything behind it. The tradeoff is temporary duplication (two entities that get merged later once a human accepts the candidate) in exchange for the pipeline never blocking on a human being awake.

---

## 7. One-liners worth having ready

- "Every value traces back to who claimed it — that's the whole point, and it's why AI is only ever allowed to propose, never write a fact directly."
- "Confidence dimensions are never summed. Mapping confidence, validation verdict, match confidence, source reliability — four different questions, four different tables, shown side by side, never averaged into one number nobody can explain."
- "Nothing ever loses data, only loses an argument — a rejected value stays in `evidence.alternatives`, a rejected quarantine stays in `quarantine_event`, a merged entity stays a tombstone."
- "Two entry points — record-at-a-time for loading, stage CLIs for reprocessing — call the identical underlying functions, so they can't disagree about what a record means."
