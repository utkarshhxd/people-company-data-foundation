# ADR 0011: Increment 11 — review findings

## Status

Accepted.

## Context

Four points were raised for verification before further work: duplicate file
detection, whether AI is used for schema mapping, validation coverage and
module structure, and batch processing during ingestion.

Two were already implemented, two were genuine gaps. This ADR records what was
found and what changed, so the answers do not have to be re-derived later.

---

## 1. Duplicate file check — already implemented, two gaps closed

**Found:** ingestion already refused a file it had seen before. Identity is the
SHA-256 of the file's **contents**, read in 1 MB chunks so hashing never loads
the file, and matched against completed batches for the same source. A renamed
copy is caught; a same-named file with different contents correctly is not.
`--allow-reingest` is the deliberate override, and the CLI exits 3 rather than
silently succeeding.

**Two gaps, both now closed:**

- **Concurrent ingests of the same file.** Only `completed` batches were
  checked, so two simultaneous runs each saw no completed batch and both
  proceeded, duplicating every row. `find_in_flight_batch` now blocks on a
  `running` batch of the same content. `--allow-reingest` deliberately does
  *not* override this: re-ingesting later is a choice, but racing yourself never
  is.
- **The same file under a different source name.** Almost always a typo in
  `--source-name`, but not always — two vendors genuinely can ship identical
  content, and each is a separate claim the pipeline should record separately.
  So it warns and names the other batches rather than blocking.

## 2. AI schema mapping — not used, and should stay that way for now

**Found:** no AI anywhere. No `openai`, `anthropic`, embedding or LLM
dependency in any service; mapping is four deterministic strategies (exact
alias, curated ambiguous alias, name similarity, value analysis). AI was
explicitly deferred when mapping was built.

**Assessment — keep it deterministic, with one exception worth building later.**

The reasons are not squeamishness about AI; they are properties this particular
stage needs:

- **Reproducibility.** The same file must always produce the same mapping.
  Mappings are stored against a column layout and reused across every future
  file with those columns, so a non-deterministic mapper would make the same
  vendor's data mean different things on different days.
- **Explainability.** Every mapping records what matched and what was rejected.
  "The model said so" cannot be audited months later, and the spec requires that
  a mapping be defensible.
- **The hard cases are not language problems.** `location` → city or address?
  `ID` → whose id? An LLM guesses confidently at exactly the ambiguities where a
  wrong answer is expensive, and the existing design already routes those to a
  human by construction.
- **Cost and latency** on a stage that runs per column per layout, plus an
  external dependency and API key in a local-first system.

**Where AI would genuinely help, and the shape it should take:** as an
*assistant on the review queue*, never in the automatic path. When a column
reaches `needs_review`, an LLM could propose a canonical field with a rationale
for the human to accept or reject. That preserves the spec's rule — AI must not
silently make high-risk mappings — while attacking the one real cost of the
current design, which is that unfamiliar vendors generate review work. Not built
here; recorded as the shape it should take if it is.

## 3. Validation — restructured, and the address gap closed

**Found:** rules existed for email, phone, URL, names, integers and postal
codes, but all in one 362-line module, and **addresses had no rules at all**.

**Rules are now one module per concern:**

```
rules/base.py     shared vocabulary: severities, Judgement, AttributeContext
rules/email.py    syntax, length, role mailboxes, consumer domains
rules/phone.py    digit count, country code, filler, merged extensions
rules/url.py      hostname, LinkedIn, embedded credentials
rules/name.py     length, placeholders, drifted fields, completeness
rules/address.py  plausibility only — never format
rules/number.py   integer input fidelity, employee count, founding year
rules/postal.py   shape, lost leading zeros
```

`rules/__init__.py` is the only file that knows which module serves which value
type, so adding a rule means editing one small file about one subject. The
public surface is unchanged, so nothing downstream needed touching. Tests mirror
the layout under `tests/rules/`.

**Addresses now have rules, and the earlier reasoning still stands.** The
original argument — any string can be a street address, formats vary by country,
punctuation carries meaning — rules out *format* rules. It never ruled out
*plausibility* rules. Every address rule is a warning or info, never an error,
and none can reject an address for being foreign or oddly punctuated:

| Rule | Catches | Severity |
| --- | --- | --- |
| `address.length` | too short to locate anything | warning |
| `address.placeholder` | "same as above", "not provided" | warning |
| `address.spacing` | `1720WisconsinAveNW` — whitespace lost upstream | warning |
| `address.drifted_field` | an email or URL in the address column | warning |
| `address.street_number` | no digits at all | **info** |

`address.street_number` is info precisely because PO boxes, rural addresses and
named buildings legitimately have none — it is recorded, never judged.

**Required fields are now explicit**, in `required_fields.py`, in three tiers
because "required" means different things:

- **identifying** (error) — without one of these the record can never be
  resolved to an entity. Enforced as a *set*: an email alone is enough, so is a
  name. Demanding every field would reject usable records.
- **core** (warning) — the fields that make a record legible. A company known
  only by a vendor id is resolvable but useless to read.
- **expected** (info) — commonly present, routinely and legitimately absent.
  Countable without ever affecting a record's status.

Deliberately *not* a per-field required flag: source files vary enormously in
which columns they carry, and a hard requirement would quarantine most of a real
vendor export over a missing postcode.

`RULESET_VERSION` moves to `2`. Judgements from the old ruleset stay queryable
beside the new ones rather than being silently reinterpreted.

## 4. Batch processing — the real gap

**Found:** partially done and misleading. Database inserts were already chunked
at 1,000 rows, but:

- `read_file()` loaded the **entire file into memory** as a list of dicts, then
  the pipeline built a second full list beside it;
- the whole insert ran in **one transaction**, so a 20,000-row file was one
  transaction and a 1M-row file would be a very long one;
- `_publish` emitted **one Kafka event per record** — and nothing subscribed to
  it. Every stage works batch-wise and reads rows from Postgres, so a 20,000-row
  file was producing 20,000 messages no consumer ever read.

**Changes:**

- `iter_file(path, batch_size)` streams via `scan_csv().collect_batches()`,
  regrouped to the exact requested size. `--batch-size` defaults to 5,000.
- The pipeline commits per batch. A failure part-way marks the batch `failed`
  and **keeps the rows already written** — they are what the source said, and
  every downstream stage requires `completed`, so partial rows are inert rather
  than dangerous. Deleting them would destroy the only evidence of a partial
  load.
- Per-record events removed. Kafka carries references; the batch id is the
  reference that matters.
- Excel is batched but still resident: the format is a zip archive whose rows
  cannot be read without decompressing the sheet. That limit is real and
  documented rather than an oversight.

**Measured:**

| Rows | Peak memory, whole file | Peak memory, batched |
| --- | --- | --- |
| 20,000 | 15.4 MB | **7.8 MB** |
| 100,000 | 77.0 MB | **7.8 MB** |
| 250,000 | 193.5 MB | **7.8 MB** |

Batched memory is flat because it is bounded by the batch, not the file. At 1M
rows the old path needed roughly 775 MB for the row list alone, before the
second copy. Read time is unchanged (0.30s vs 0.33s at 20k), and the rows
produced are byte-identical either way — verified by test, since a memory
strategy that changed what was read would be worse than the problem.

## Verification

A generated 20,000-record file with deliberately planted defects ran the full
chain unattended:

```
ingested   20,000 rows          (4.4s wall, including container start)
normalized 20,000 -> 200,000 observations
validated  20,000 records, 499,382 judgements, 0 invalid, 0 quarantined
resolved   20,000 entities
```

Counts reconcile exactly across `raw_record`, `attribute_observation`,
`record_validation` and `record_entity_link`. The new rules fired precisely on
the planted defects — `phone.filler` 645 times against a defect planted every
31st row (20000/31 ≈ 645), `address.spacing` 377 times against every 53rd
(20000/53 ≈ 377) — and produced **no false quarantines**, which was the risk of
adding rules to a stage that gates entity resolution.

## Consequences

- This is the first run above 19 rows, so it also partially answers the largest
  risk in the delivery plan. 20,000 rows is comfortable; 1M is still unproven,
  and entity resolution remains the stage to watch — it commits per record.
- `RULESET_VERSION = 2` means existing records carry ruleset-1 judgements until
  re-validated. That is the versioning behaving as designed, not drift.
- `record.expected_fields` fires on most records by construction. It is info
  severity and excluded from `failed_rules` so it cannot drown the real
  problems, but it does add one `validation_result` row per record.
- Removing the per-record event is a contract change. Nothing consumed it, but
  any future consumer wanting per-record granularity must read Postgres rather
  than expecting Kafka to carry it.
