# ADR 0008: Increment 8 — golden record

## Status

Accepted.

## Context

Increment 7 gave every record a stable `person_id` / `company_id`, but entities
deliberately hold no attributes. Asking "what is this company's address" still
meant reading every observation and deciding by hand — three vendors reported
three things and nothing arbitrated.

This increment answers that, and answers it *auditably*: which source won, which
rule decided it, what it beat, and how much to believe it.

## Decisions

- **The golden value is derived, never authored.** Nothing writes here except
  the builder, and it can be recomputed at any time from
  `attribute_observation`, which remains the only place source facts live. A
  golden record that could be hand-edited would become a source of its own, and
  an unprovenanced one.

- **One rule per field, not one rule globally.** Fields differ in kind, and a
  single policy would be wrong for most of the schema:
  - an address *changes*, so the newest report is the best one (`most_recent`);
  - a company name does not, so disagreement means someone is wrong and votes
    are counted (`most_frequent`);
  - a founding year is immutable, so a later vendor cannot know better
    (`earliest`);
  - everything else trusts the source we trust most (`most_reliable_source`).

  Only the fields that differ from the default are listed. A short table is
  easier to defend than an exhaustive one.

- **The deciding rule is stored on every value.** A surprising golden value must
  trace back to a policy rather than to a mystery, months later.

- **One vendor repeating itself is one opinion.** `most_frequent` counts
  distinct *sources*, not rows. Otherwise a chatty low-quality feed could
  outvote a careful one simply by sending more files.

- **Confidence is built from source reliability**, which is the dimension that
  exists precisely to say how much a vendor is believed: start at the winning
  source's reliability, add 0.15 per additional agreeing source, subtract 0.10
  per rejected competing value. A contested field *should* read as less certain,
  and it does — the Asia Foundation's phone number, with three variants in play,
  lands at 0.300 while its twice-confirmed email reaches 0.850.

  This is a fifth distinct number, and the system now keeps all five apart:
  mapping confidence, validation result, source reliability, match confidence,
  and golden-value confidence.

- **Every rejected value is kept, with who said it.** Being beaten is not a
  reason to disappear. `evidence.alternatives` records each losing value and its
  sources, so a disputed field can be reviewed without re-deriving anything.

- **Current state and history are one table**, with `valid_to IS NULL` meaning
  current and a partial unique index enforcing exactly one current value per
  (entity, field). Splitting them would let the two drift, and the history of a
  trusted value is the thing that must never be wrong.

- **Rebuilding is idempotent.** An unchanged value keeps its existing
  `valid_from`, so `valid_from` means "since when has this been true" rather
  than "when did we last run the builder". Re-running writes nothing —
  demonstrated by replayed batches reporting `0 written, 31 unchanged`.

- **Ties break on a fixed chain** (reliability, recency, source name, value), so
  the same observations always produce the same golden record. Without
  determinism nothing downstream is reproducible.

- **Only linked records contribute.** A quarantined record stays fully stored
  but backs no trusted value, which is the last link in the chain that makes
  quarantine meaningful.

## A bug integration testing caught

The first build ordered the work as *insert new value, then close the old one*.
The partial unique index rejected it immediately — both rows were momentarily
current. The fix is to close first, insert, then link the closed row to its
replacement. The unit tests could not have found this: it only exists at the
point where the model meets the constraint that enforces it, which is a fair
argument for the index existing rather than the rule living only in code.

## Verification

The Asia Foundation, assembled from four vendors:

| Field | Value | Won by | Confidence |
| --- | --- | --- | --- |
| `company_name` | Asia Foundation | `most_frequent` (2 sources vs 1) | 0.750 |
| `email` | taf@pk.asiafound.org | `most_reliable_source`, 2 agreeing | 0.850 |
| `employee_count` | 1400 | `most_recent` (vendor_c) | — |
| `founded_year` | 1954 | `earliest` | 0.600 |
| `phone` | +12025889420 | `most_recent`, 3 values competing | 0.300 |

`company_name` rejected "The Asia Foundation" and recorded it. The phone number
is correctly the least trusted field on the record.

History was proven by ingesting a genuine update from a higher-reliability
vendor:

```
address_line1  1779 Massachusetts Ave NW #815  06:08 -> 06:10  vendor_dc
address_line1  465 California St 9th Floor     06:10 -> current vendor_c
employee_count 1200                           06:08 -> 06:10  vendor_b
employee_count 1400                           06:10 -> current vendor_c
```

System-wide: 255 current values across 25 entities, 9 contested, 5 superseded.

## Consequences

- `golden_person` and `golden_company` are flat, named-column views — the
  "trusted canonical data" deliverable. They are deliberately not a dynamic
  pivot, because a fixed column list is what consumers can depend on.
- The golden record is single-valued per field. A person with two genuine email
  addresses gets one golden email, with the other kept in `evidence`; the full
  multi-valued truth is always in `attribute_observation`. Promoting alternates
  to first-class would mean the flat views stop being flat.
- `website` currently keeps its scheme, so `www.asiafoundation.org` and
  `https://www.asiafoundation.org` compete as different values even though
  entity matching correctly treats them as one host. Stripping the scheme during
  normalization would fix the golden value but discard whether the source said
  https — worth deciding deliberately rather than by accident.
- Accepting a match candidate merges two entities but does not itself rebuild
  the survivor's golden record; `golden build --entity-id <id>` does. The
  alternative is coupling the resolution service to this one's topic.
