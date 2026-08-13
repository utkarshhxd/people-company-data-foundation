# ADR 0007: Increment 7 — entity resolution

## Status

Accepted.

## Context

Six increments stored, in escalating detail, what sources *said*. None of them
produced the thing the project exists for: a stable identifier for the
real-world person or company that many records are observations of.

Increment 4 demonstrated cross-vendor linking by querying on a shared normalized
email. That was a query, not a link — it worked only because a human knew to run
it, and it would have failed the moment two vendors spelled the address
differently or one of them omitted it. This increment turns that into a
persisted `person_id` / `company_id`.

## Decisions

- **An entity holds no attributes.** `entity` is an identity and nothing else.
  Which of three reported names is *the* name is the golden record's decision, in
  the next increment. Mixing the two would mean resolving and choosing at once,
  and a bad merge would then also corrupt the values.

- **One `entity` table, with `person` and `company` views.** The spec speaks of
  person_id and company_id, and the views give that vocabulary back
  (`SELECT person_id FROM person`). But merge, link and key logic is identical
  for both, and duplicating it across two tables would mean two chances to get
  the delicate part wrong. The trade-off is that a future person→company
  employment edge has to carry its own type checks.

- **Strong and moderate keys, and moderate can never auto-link.** This is the
  spec's "do not blindly merge records" made mechanical. A shared email or
  website domain can carry a link alone; a shared name, city or phone never can,
  no matter how many agree. Three colleagues share an employer, a city and a
  switchboard — without the ceiling they would eventually become one person.

- **Corroboration is capped at +0.04 per additional key *type*.** Enough to tip
  a borderline decision, never enough for weak signals to impersonate a strong
  one. Distinct types, not values: three matching phone numbers is one kind of
  evidence repeated, not three kinds.

- **A vendor's own id is scoped to that vendor.** `{source_id}:{external_id}` —
  it is the strongest evidence available inside one feed and meaningless across
  feeds, which reuse the same integers for entirely different entities.

- **A role mailbox is demoted for person matching, using validation's verdict.**
  Everyone sharing `info@company.com` would otherwise collapse into one human
  being. Rather than re-deriving what a role account is, resolution reads the
  stored `email.role_account` result. One definition, living with the rules that
  own it.

- **Below the threshold, the record still becomes its own entity.** Blocking the
  pipeline on a human would stall every downstream stage, and inventing the
  merge is what the spec forbids. So a `match_candidate` is filed and both
  entities exist until someone decides. Accepting merges them; rejecting leaves
  them apart, which is the outcome auto-linking would have denied.

- **A tie goes to review, never a coin flip.** Two entities matching equally
  well usually means those two are duplicates of *each other*, which is a
  different problem and one a human should see.

- **Merging never deletes an id.** The absorbed entity becomes a tombstone
  pointing at the survivor, so any id already handed out downstream still
  resolves — that is the entire promise of a stable entity id. Keys move with
  the records, or the survivor would be unfindable by the absorbed entity's
  identifiers and the next record would create a third entity.

- **Records are resolved in file order, committed one at a time**, so a record
  can match an entity created two rows above it. Determinism matters more than
  throughput here: the same file must always produce the same entities.

- **Resolution reads `resolvable_record`, not `raw_record`.** This is what makes
  increment 6 load-bearing rather than decorative — a quarantined record is
  simply not offered for resolution.

## Verification

`vendor_b_overlap.csv` linked **2 of 2 records to existing entities, creating
none**. The Asia Foundation entity is now observed by three vendors:

| Vendor | Method | Confidence |
| --- | --- | --- |
| vendor_dc | first sighting | — |
| vendor_b | `website_domain` | 0.960 |
| vendor_near | `name_city`, accepted by a human | 0.840 |

The vendor_b link is the interesting one: the two files disagree on the name
("Asia Foundation" vs "The Asia Foundation"), so a name-based match would have
failed. The website domain carried it, and the resulting entity holds
vendor_dc's address, SIC code and fax alongside vendor_b's employee count,
founding year and LinkedIn URL — nothing overwritten, every value still
attributed to the vendor that said it.

`near_match_company.csv` exercises the review path: name and city agree, nothing
strong does, so it scored 0.840 (`name_city` 0.80 + one corroborating key) and
was **not** linked. After a human accepted it, the merge moved 1 record and 1
key, and the absorbed id still resolves:

```
note: 019ff9ad-b974-... was merged into 019ff9ac-dc79-...
```

System-wide: 32 raw records, 28 resolvable (4 held in quarantine), 28 linked,
7 persons, 18 active companies, 1 merged tombstone, 124 identity keys.

## Consequences

- The golden record (next increment) reads `entity_observation`, which already
  exposes every value with its source and that source's reliability — the three
  inputs a survivorship rule needs.
- Match quality depends on the canonical vocabulary. A field that never gets
  mapped can never become a key, so mapping review is not cosmetic: it directly
  determines what can be resolved.
- Releasing a record from quarantine does not itself trigger resolution;
  `resolve run --batch-id <id>` picks it up. Making the release publish an event
  would couple the validation service to resolution's topic, and the explicit
  re-run is honest about what happened.
- Nothing yet re-examines old entities when new keys arrive. Two entities that
  should have merged stay separate until a third record matches both. A periodic
  re-blocking pass belongs with the golden record work.
