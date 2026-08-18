# Entity resolution

Linking records from different vendors to the same real-world person or company, and the employer named inside a person row.

Part of the [People & Company Data Foundation](../../README.md).

## Entity resolution

The point of everything above: records become observations *of* a stable
`person_id` / `company_id` that survives the attributes changing, the vendor
changing, and the record being superseded.

```powershell
# Automatic on records.validated; this re-runs a batch (e.g. after releasing
# a record from quarantine, which does not itself re-trigger resolution)
docker compose run --rm resolution resolve run --batch-id <id>

# Everything known about an entity, and which vendor said it
docker compose run --rm resolution resolve show --entity-id <id>

# Matches that were NOT acted on automatically
docker compose run --rm resolution resolve candidates
docker compose run --rm resolution resolve accept --candidate-id <id> `
  --reviewed-by you --note "same organisation"
docker compose run --rm resolution resolve reject --candidate-id <id> `
  --reviewed-by you --note "different companies, shared switchboard"
```

**Strong keys can link on their own; moderate keys never can.** Email, website
domain, LinkedIn handle and the vendor's own id are strong. Name, name+city and
phone are moderate — and no amount of moderate agreement crosses the auto-link
line, because three colleagues share an employer, a city and a switchboard.
Corroboration adds +0.04 per additional key *type*, capped, so weak signals
never impersonate a strong one.

Two more rules worth knowing:

- A vendor's own id is scoped as `{source_id}:{external_id}` — authoritative
  inside one feed, meaningless across feeds that reuse the same integers.
- A role mailbox (`info@`) is demoted for person matching, reusing validation's
  stored `email.role_account` verdict rather than re-deciding what a role
  account is.

Below the threshold the record still gets its own entity and a `match_candidate`
is filed, so the pipeline never blocks on a human and no merge is invented.
**Merging never deletes an id** — the absorbed entity becomes a tombstone
pointing at the survivor, so any id already handed out still resolves.

Resolution reads the `resolvable_record` view, so quarantined records are never
offered for matching.

### Worked example

`vendor_b_overlap.csv` linked 2 of 2 records to existing entities and created
none. The Asia Foundation is now one entity observed by three vendors — matched
on **website domain despite the names disagreeing** ("Asia Foundation" vs "The
Asia Foundation"), which a name-based match would have missed:

```
observed by 3 record(s):
  vendor_dc    real_company_sample.csv   first sighting
  vendor_b     vendor_b_overlap.csv      website_domain  0.960 (auto_linked)
  vendor_near  near_match_company.csv    name_city       0.840 (human accepted)

company_name   vendor_b  The Asia Foundation
               vendor_dc Asia Foundation
employee_count vendor_b  1200
sic_description vendor_dc Associations
...
```

Every value is still attributed to the vendor that reported it, and nothing was
overwritten.

## The employer named in a person row

A vendor person export describes two subjects: the person, and the company they
work for. `Company City` and `City` both answer the canonical field `city`, and
they are not competing answers — they are answers about different subjects.

Every canonical field carries a **subject**, `self` or `employer`. The row stays
one record; what gains a role is the link:

```
raw_record ──┬── observations subject='self'     → person entity   role='self'
             └── observations subject='employer' → company entity  role='employer'
                                                   + employed_at relationship
```

The link's role selects the observations it is a link to, so a person's trusted
record is never built from their employer's address.

**An employer becomes an entity only when the row identifies it.** A name alone
does not — "Consulting" appears thousands of times meaning thousands of
companies — so a domain, LinkedIn page, vendor id, phone or address must stand
beside it. `Self Employed`, `Freelance` and `Retired` are refused outright.
An employer that fails these tests keeps every observation; there is simply no
entity yet.

```powershell
curl.exe "http://localhost:8000/entities/<id>/relationships"
```

Answers from either end: where a person works, and who works at a company. On
1,000 Apollo rows: 1,000 people, 999 employments, **868 distinct companies** —
131 employers recognised as somewhere a colleague already worked.

See [ADR 0013](../decisions/0013-employer-as-an-entity.md).
