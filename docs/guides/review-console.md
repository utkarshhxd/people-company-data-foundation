# Review console

Working the three queues that stop and ask for a human, in a browser or a terminal.

Part of the [People & Company Data Foundation](../../README.md).

Three stages refuse to guess and file the question instead:

| Queue | Stopped because | Deciding it means |
| --- | --- | --- |
| **Schema mappings** | A column could be one of several canonical fields, or none | Saying which field it is, once, for every future file of that layout |
| **Possible duplicates** | A match was plausible but not certain | Saying whether two entities are one real thing |
| **Quarantined records** | A record failed validation with an error | Saying whether to use it anyway or confirm it is unusable |

Nothing ages out of these queues on its own. A record can sit in quarantine
indefinitely without anything being lost — and without anyone noticing, which is
why the [alerts](operations.md) watch their *age* rather than their depth.

## The console

```
http://localhost:8000/review/page
```

Type your name once; every decision is recorded against it, permanently. If the
API has keys configured, a key box appears — the page is a shell and the requests
it makes carry the key, which is why the shell itself is not behind one.

Each queue shows the evidence rather than the identifiers:

- **A mapping** shows six real values from the column's own file and the
  alternatives the engine considered and rejected. The right answer is often the
  second one. Click an alternative to fill it in, or type any canonical field —
  the box is backed by the full vocabulary with descriptions.
- **A possible duplicate** shows the two entities side by side with the differing
  fields marked. "Already in the database" is the survivor; "this record" is what
  would be folded into it.
- **A quarantined record** shows every failing rule with the exact source cell it
  objected to, so "is this usable" is answerable without opening the file.

## What a decision sets in motion

Two of them do more than close the queue item, because leaving these to be
remembered meant they were forgotten:

**Accepting a merge rebuilds the survivor's golden record.** Without it the
trusted values stay computed from half the observations — the absorbed entity's
sources are attached but never consulted.

**Releasing a record from quarantine resolves it and builds it.** A released
record is *resolvable*; nothing resolves it. Before this it sat in a state no
queue showed.

Both are batch-scoped and skip work already done, so releasing one record out of
a 190,000-row batch costs one record. If the follow-through fails, the response
says so and gives the command to finish it by hand — the decision itself has
already committed, and reporting success would be a lie.

## The same decisions from a terminal

The console delegates to these; they are not a second implementation.

```bash
# schema mappings
docker compose run --rm mapping review-mappings list --status needs_review
docker compose run --rm mapping review-mappings set --mapping-id <id> \
    --canonical-field work_phone --reviewed-by you

# possible duplicates
docker compose run --rm resolution resolve candidates --status open
docker compose run --rm resolution resolve accept --candidate-id <id> --reviewed-by you
docker compose run --rm resolution resolve reject --candidate-id <id> --reviewed-by you

# quarantine
docker compose run --rm validation quarantine list --status open
docker compose run --rm validation quarantine show --record-id <id>
docker compose run --rm validation quarantine release --record-id <id> --reviewed-by you \
    --note "checked against the source file"
```

The CLIs do **not** carry the follow-through the API does. After a terminal
`accept`, run `golden build --entity-id <survivor>`; after a terminal `release`,
run `resolve run --batch-id <id>` and then `golden build --batch-id <id>`.

## The endpoints

Everything the console shows is served as JSON, and everything it does is a POST.

```bash
KEY=your-api-key

# how much is waiting, and how long the oldest has waited
curl -H "X-API-Key: $KEY" localhost:8000/review/summary

curl -H "X-API-Key: $KEY" 'localhost:8000/review/mappings?status=needs_review'
curl -H "X-API-Key: $KEY" 'localhost:8000/review/candidates?status=open'
curl -H "X-API-Key: $KEY" 'localhost:8000/review/quarantine?status=open'
curl -H "X-API-Key: $KEY" "localhost:8000/review/quarantine/$RECORD"

# the vocabulary a column may be mapped to
curl -H "X-API-Key: $KEY" localhost:8000/review/fields/person

# decisions
curl -X POST -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"canonical_field":"work_phone","reviewed_by":"you"}' \
  "localhost:8000/review/mappings/$MAPPING"

curl -X POST -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"accept":true,"reviewed_by":"you","note":"same company"}' \
  "localhost:8000/review/candidates/$CANDIDATE"

curl -X POST -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"action":"release","reviewed_by":"you","note":"checked by hand"}' \
  "localhost:8000/review/quarantine/$RECORD"
```

`reviewed_by` is required and cannot be empty. A decision the data does not allow
— a record already released, a candidate already closed, a field outside the
vocabulary — is a `409` with the reason, not a `500`.

## What this API will never do

It writes a human's decision about a value the pipeline already stopped on. It
does not write a value. An endpoint that could set a golden value directly would
create records nothing can explain, which is the one thing this system exists to
prevent.
