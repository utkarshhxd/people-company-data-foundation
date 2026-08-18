# Golden record and provenance

Choosing the one trusted value per entity per field, and tracing it back to the source cell it came from.

Part of the [People & Company Data Foundation](../../README.md).

## Golden record

The deliverable: one trusted value per entity per field, with the rule that
decided it, what it beat, and full history. It is **derived, never authored** —
recomputable at any time from `attribute_observation`.

```powershell
# Automatic on entities.resolved; re-run after accepting a merge candidate
docker compose run --rm golden golden build --entity-id <id>

docker compose run --rm golden golden show --entity-id <id>
docker compose run --rm golden golden history --entity-id <id> --field address_line1

# The flat "trusted canonical data" views
docker compose exec postgres psql -U pcdf_dev -d pcdf -c "SELECT * FROM golden_company;"
docker compose exec postgres psql -U pcdf_dev -d pcdf -c "SELECT * FROM golden_person;"
```

**One rule per field, because fields differ in kind.** An address *changes*, so
the newest report wins. A company name does not, so disagreement means someone
is wrong and votes are counted — and one vendor repeating itself is one opinion,
not many. A founding year is immutable, so a later vendor cannot know better.
Everything else trusts the most reliable source.

**Confidence comes from source reliability**: start at the winning source's
reliability, +0.15 per additional agreeing source, −0.10 per rejected competing
value. A contested field should read as less certain, and it does:

```
company_name  Asia Foundation        0.750  most_frequent         vendor_dc
                rejected: The Asia Foundation (vendor_b)
email         taf@pk.asiafound.org   0.850  most_reliable_source  vendor_dc
phone         +12025889420           0.300  most_recent           vendor_near
                rejected: +14153928863 (vendor_b)
                rejected: 4153928863 (vendor_dc)
```

That is now the **fifth** distinct confidence-like number in the system, and
they are never conflated: mapping confidence, validation result, source
reliability, match confidence, golden-value confidence.

**Current state and history are one table** (`valid_to IS NULL` means current, a
partial unique index enforces one current value per field). Rebuilding is
idempotent — an unchanged value keeps its `valid_from`, so that column means
"since when has this been true", not "when did the builder last run":

```
address_line1  1779 Massachusetts Ave NW #815  06:08 -> 06:10   vendor_dc
address_line1  465 California St 9th Floor     06:10 -> current vendor_c
```

Only records actually linked to an entity contribute, so a quarantined record
stays fully stored while backing no trusted value.

## Provenance: why does a value say that?

Every stage recorded its reasoning, but each in its own table. `explain` is the
join that makes the chain answerable in one question — and it shows the five
judgements **side by side, never summed**:

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
      vendor reliability : 0.70

 -> vendor_c  vendor_c_update.csv row 1
      column 'address' held '465 California St 9th Floor'
      linked to entity   : website_domain @ 0.960 (auto_linked)
      vendor reliability : 0.85

previously:
      1779 Massachusetts Ave NW #815  until 2026-08-13 06:10
```

| Question | Number |
| --- | --- |
| Did we read the column correctly? | mapping confidence |
| Is the value usable as that field? | validation result |
| Is this record the same entity? | match confidence |
| How much do we believe this vendor? | source reliability |
| Why did this value beat the others? | golden strategy + confidence |

A single blended score would also destroy the ability to say *"the value is
fine, we're just unsure it's the same company"*.
