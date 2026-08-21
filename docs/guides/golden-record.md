# Golden record and provenance

Choosing the one trusted value per entity per field, and tracing it back to
the source cell it came from.

Part of the [People & Company Data Foundation](../../README.md).

## Golden record

The deliverable: one trusted value per entity per field, with the rule that
decided it, what it beat, and full history. It is **derived, never
authored** — recomputable at any time from `attribute_observation`.

On the record-at-a-time path this rebuilds automatically as each record
lands. To rebuild by hand (e.g. after accepting a merge candidate):

```powershell
docker compose run --rm golden golden build --entity-id <id>
docker compose run --rm golden golden build --batch-id <id>

docker compose run --rm golden golden show --entity-id <id>
docker compose run --rm golden golden history --entity-id <id> --field address_line1

# The flat "trusted canonical data" views — this is the actual deliverable
docker compose exec postgres psql -U pcdf_dev -d pcdf -c "SELECT * FROM golden_company;"
docker compose exec postgres psql -U pcdf_dev -d pcdf -c "SELECT * FROM golden_person;"
```

**One rule per field, because fields differ in kind.** An address *changes*,
so the newest report wins. A company name doesn't, so disagreement means
someone is wrong and votes are counted — one vendor repeating itself is one
opinion, not many. A founding year is immutable, so a later vendor can't
know better. Everything else trusts the most reliable source.

**Confidence comes from source reliability**: start at the winning source's
reliability, +0.15 per additional agreeing source, −0.10 per rejected
competing value:

```
company_name  Asia Foundation        0.750  most_frequent         vendor_dc
                rejected: The Asia Foundation (vendor_b)
email         taf@pk.asiafound.org   0.850  most_reliable_source  vendor_dc
phone         +12025889420           0.300  most_recent           vendor_near
                rejected: +14153928863 (vendor_b)
```

This is deliberately the **fifth** distinct confidence-like number in the
system, and they're never conflated: mapping confidence, validation result,
source reliability, match confidence, golden-value confidence.

**Current state and history are one table** (`valid_to IS NULL` means
current). Rebuilding is idempotent — an unchanged value keeps its
`valid_from`, so that column means "since when has this been true," not
"when did the builder last run."

Only records actually linked to an entity contribute — a quarantined record
stays fully stored while backing no trusted value.

## Provenance: why does a value say that?

```powershell
docker compose run --rm golden golden explain --entity-id <id> --field address_line1
```

```
trusted value : 465 California St 9th Floor
chosen by     : most_recent (confidence 0.750)
agreement     : 1 source(s) agreed, 2 distinct value(s) competed

    vendor_dc  real_company_sample.csv row 1
      column 'ADDRESS' held '1779 Massachusetts Ave NW #815'
      column interpreted : exact_alias @ 1.000 (auto_accepted)
      record validated   : warning
      linked to entity   : no_match @ 0.000 (new_entity)
```

Every stage recorded its reasoning in its own table; `explain` is the join
that answers the whole chain in one question — showing the five judgements
**side by side, never summed**.

## Rules never write facts

**Nothing may write directly into `golden_attribute`.** A value with no
observation behind it has no provenance, and would be indistinguishable from
an observed one. See [where AI is allowed](../../README.md#where-ai-is-and-isnt-allowed)
and [ADR 0014](../decisions/0014-commercial-profile-and-enrichment.md).
