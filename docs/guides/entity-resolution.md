# Entity resolution

Linking records from different vendors to the same real-world person or
company, and the employer named inside a person row.

Part of the [People & Company Data Foundation](../../README.md).

## Entity resolution

The point of everything upstream: records become observations *of* a stable
`person_id` / `company_id` that survives the attributes changing, the vendor
changing, and the record being superseded.

On the record-at-a-time path this happens inline as part of loading a file.
On the reprocessing path it's a separate command — for example after
releasing a record from quarantine, which does not itself re-trigger
resolution:

```powershell
docker compose run --rm resolution resolve run --batch-id <id>

docker compose run --rm resolution resolve show --entity-id <id>   # everything known, and which vendor said it

# Matches that were NOT acted on automatically
docker compose run --rm resolution resolve candidates
docker compose run --rm resolution resolve accept --candidate-id <id> `
  --reviewed-by you --note "same organisation"
docker compose run --rm resolution resolve reject --candidate-id <id> `
  --reviewed-by you --note "different companies, shared switchboard"
```

**Strong keys can link on their own; moderate keys never can.** Email,
website domain, LinkedIn handle, and the vendor's own id are strong. Name,
name+city, and phone are moderate — no amount of moderate agreement crosses
the auto-link line, because three colleagues share an employer, a city, and
a switchboard. Corroboration adds a small bonus per additional key *type*,
capped, so weak signals never impersonate a strong one.

Two more rules:

- A vendor's own id is scoped as `{source_id}:{external_id}` — authoritative
  inside one feed, meaningless across feeds that reuse the same integers.
- A role mailbox (`info@`) is demoted for person matching, reusing
  validation's stored `email.role_account` verdict rather than re-deciding
  what a role account is.

Below the auto-link threshold, the record still gets its own entity and a
`match_candidate` is filed — the pipeline never blocks on a human, and no
merge is invented. **Merging never deletes an id** — the absorbed entity
becomes a tombstone pointing at the survivor, so any id already handed out
still resolves.

Resolution reads the `resolvable_record` view, so quarantined records are
never offered for matching.

### Worked example

Loading `vendor_b_overlap.csv` linked 2 of 2 records to existing entities,
created none. The Asia Foundation is now one entity observed by three
vendors — matched on **website domain despite the names disagreeing**
("Asia Foundation" vs "The Asia Foundation"), which a name-based match alone
would have missed:

```
observed by 3 record(s):
  vendor_dc    real_company_sample.csv   first sighting
  vendor_b     vendor_b_overlap.csv      website_domain  0.960 (auto_linked)
  vendor_near  near_match_company.csv    name_city       0.840 (human accepted)
```

Every value stays attributed to the vendor that reported it. Nothing gets
overwritten.

## The employer named in a person row

A vendor person export describes two subjects: the person, and the company
they work for. `Company City` and `City` both answer the canonical field
`city`, and they aren't competing answers — they're answers about different
subjects.

Every canonical field carries a **subject**, `self` or `employer`. The row
stays one record; what gains a role is the link:

```
raw_record ──┬── observations subject='self'     → person entity   role='self'
             └── observations subject='employer' → company entity  role='employer'
                                                   + employed_at relationship
```

**An employer becomes an entity only when the row identifies it.** A name
alone doesn't — "Consulting" appears thousands of times meaning thousands of
companies — so a domain, LinkedIn page, vendor id, phone, or address has to
stand beside it. `Self Employed`, `Freelance`, and `Retired` are refused
outright. An employer that fails these tests keeps every observation; there
is simply no entity yet.

Readable from either end via `entity_relationship` — see the worked queries
at `tools/sql/queries.sql:34`.

See [ADR 0013](../decisions/0013-employer-as-an-entity.md).
