# ADR 0013: A person row describes two subjects

## Status

Accepted.

## Context

Every vendor person export in `testfiles/` — Apollo, c_suite, LeadsNemo —
describes a person *and* the company they work for, in one row. Apollo ships 52
columns of which 13 are the employer's: `Company`, `Company Address`,
`Company City`, `Company Phone`, `# Employees`, `Company Linkedin Url`.

Two things were wrong before this.

**`Company City` and `City` competed for one field.** Both answer the canonical
field `city`, so the collision rule sent both to review and one of them had to
lose. Whichever lost, the result was wrong: either the person lost their own
address, or the employer's was recorded as the person's.

**The employer was a string, repeated once per employee.** UnitedHealth Group
appearing in eleven rows was eleven unrelated strings. "Who works at
UnitedHealth Group" had no answer, and the company's own attributes — its
domain, headcount, LinkedIn page — sat in observations belonging to people.

## Decision

A canonical field gains a **subject**: whose attribute it is on the record
carrying it. `self` is the entity the record is fundamentally about; `employer`
is the company named inside a person row.

An employer field carries a name from the **company** vocabulary, because that
is where the value ends up. `Company City` maps to the company field `city`,
subject `employer`. This means an employer captured from a person row and a
company loaded from a company file are the same shape and resolve against each
other — the Gilbane in an Apollo row is the Gilbane in a company export.

### The row stays one record

A source row is one row. Splitting it into a person record and a company record
would fork its provenance, make `row_number` ambiguous, and double what
`record_error` means. What gains a role is the **link**:

```
raw_record ──┬── observations subject='self'     → person entity   link role='self'
             └── observations subject='employer' → company entity  link role='employer'
                                                   + entity_relationship 'employed_at'
```

**The link's role selects the observations it is a link to.** That single
predicate — `o.subject = l.role` — is what keeps the two apart everywhere
downstream. It was added to eighteen join sites across golden, lineage,
resolution and the metrics. Without it a person's trusted record would be built
from their employer's address as well as their own, and `explain` would offer
both as competing answers to the same question.

### Two rules stop the employer stealing the person's data

- **An employer field never matches on its bare field name**, only on
  explicitly employer-qualified aliases (`company_city`, `employer_city`). An
  unqualified `city` on a person row is the person's, and a vendor meaning
  otherwise has to say so. Guessing costs the person their own address.
- **Values may never originate an employer mapping.** A phone number looks
  identical whether it is the person's or their employer's, so whose it is can
  only come from the column name.

### A name alone does not make a company

"Consulting", "Services" and "Group" appear thousands of times across vendor
files meaning thousands of different organisations. A name is a *moderate* key
precisely because it does not identify. So an employer becomes an entity only
when the row corroborates the name with something that does: a domain, a
LinkedIn page, the vendor's own company id, a phone, or an address.

Placeholders that mean the *opposite* of an employer — `Self Employed`,
`Freelance`, `Retired`, `Unemployed` — are refused outright. Making an entity
of one would merge every self-employed person's employer into a single
organisation.

An employer that fails these tests still has every one of its observations
stored, attributed to the person's record. Nothing is invented and nothing is
lost; there is simply no company entity yet.

## Result

1,000 Apollo rows produced 1,000 people, 999 employments and **868 distinct
companies** — 131 employers recognised as somewhere a colleague already worked.
UnitedHealth Group has eleven employees: the first created the entity, the other
ten linked to it.

Across all eight real vendor layouts: 8,000 records, 0 failed, 0 quarantined.

## An attribute error no longer condemns its record

Running real files found this within minutes, and it is the more important
change.

A c_suite export ships the literal string `[object Object]` in its `Phone`
column — a JavaScript bug in the vendor's own tooling — for **907 rows out of
1,000**. Every one of those rows carried an email, a full name and a LinkedIn
URL. Every one was quarantined, because any error-severity failure invalidated
the whole record.

That conflated two different judgements:

- a **record**-scope error says the row could never identify anybody. There is
  nothing to resolve on, and `invalid` is right.
- an **attribute**-scope error says one value cannot serve as the field it was
  mapped to. That is a verdict on the value.

Only the first invalidates now. The second leaves the record `warning`, with
the bad value still recorded as an error and still excluded from resolution.

Withholding 90% of an identifiable file over a column nobody needed was not
caution. The review queue it created could only have been answered "yes, the
vendor's phone column is broken" nine hundred times, and a queue like that is
how real review queues stop being read.

## Consequences

- **Golden is built for the employer too**, in the same transaction as the
  record, so a company that exists always says something.
- **A person changing employer is not modelled.** `entity_relationship` has
  `valid_from`/`valid_to` and the current row is unique per (from, to, type),
  but nothing closes a relationship yet — telling a *change of job* apart from
  two vendors *disagreeing* needs a rule this data cannot yet supply.
- **Employer resolution cannot fail a record.** Whether we could identify
  someone's employer says nothing about whether we identified them, so its
  outcome deliberately does not change the record's own.
- **Excel must be told which sheet to load.** `LeadsNemo_Test1.xlsx` has two
  with different column orders and the reader silently took the first, dropping
  19,926 records with nothing recording the gap.

## Follow-ups

- Close a relationship when a later, more reliable source disagrees.
- `full_name` is absent from Apollo person entities (it ships First/Last only),
  so related-entity listings show a null display name for people.
- Re-block employers: two companies that should have merged stay separate until
  a third record matches both.
