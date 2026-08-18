# ADR 0015: Making the system operable by someone who did not build it

## Status

Accepted.

## Context

Every stage from "a file lands on disk" to "an API explains why a value says what
it says" was built, tested, and proven on 190,574 real records. The data system
worked. What did not work was anyone else running it.

Four things stood between the two, and they turned out to share a shape. Each was
a place where the system's behaviour depended on a person remembering something
that was written down in a README rather than encoded anywhere the machine could
enforce.

- **Three review queues, all CLI-only.** Ambiguous column mappings, proposed
  merges, and quarantined records each stop and ask for a human. Every one of
  those humans needed a terminal, a checked-out repository, and the willingness
  to run three commands and join the output by eye.
- **Files loaded by hand.** `process run` required `--entity-type`,
  `--source-name` and `--reliability` typed correctly, per file, forever.
- **Credentials only as environment variables.** Visible in `docker inspect`, in
  the environment of anything else in the container, and in shell history.
- **Alerts with nowhere to go.** Eleven rules fired correctly into Alertmanager
  and were visible at `/alerts`. The config claimed a webhook could be switched
  on by setting `ALERTMANAGER_WEBHOOK_URL`. It could not — Alertmanager does not
  expand environment variables in its config, so that was a documented variable
  that nothing read.

---

## 1. The review console

**Decision:** serve the three queues over HTTP and render them as one
self-contained page at `/review/page`.

The missing thing was never the ability to decide. It was the ability to *see the
thing being decided*. So the projections carry the evidence, not just the ids:

- an ambiguous column arrives with six real values from its own file and the
  alternatives the engine considered and rejected — because the right answer is
  usually the second one, and it was previously buried in a JSON evidence blob;
- a proposed merge arrives as two sides with the differing fields marked, rather
  than as two UUIDs and a confidence;
- a quarantined record arrives with the exact source cell each failing rule
  objected to.

**Every decision is delegated.** `set_mapping`, `accept_candidate` and
`quarantine.review` are the same functions the CLIs call. A decision made in a
browser and the same decision made in a terminal are one code path, and the CLIs
keep working. This is the same rule ADR 0012 applied to the two processing paths,
for the same reason: two implementations of one decision will eventually disagree,
and the disagreement will be found in the data rather than in a test.

### Decisions now finish themselves

Two things the CLIs left to the operator, both documented as "re-run X
afterwards":

- accepting a merge left the surviving entity's golden record computed from half
  its observations until somebody ran `golden build`;
- releasing a record from quarantine made it *resolvable* but did not resolve it,
  leaving it in a state no queue showed.

A documented manual step after an irreversible decision is a step that gets
missed, and both were missed silently. The API now carries them. Both are
batch-scoped and both skip work already done — resolution reads only records with
no `self` link, the golden build reads only the entities that batch touched — so
releasing one record out of a 190,000-row batch costs one record.

**Follow-through failures are reported, never swallowed.** The decision itself has
already committed. A caller told it succeeded when the rebuild did not would be
worse off than one told exactly what happened and given the command to fix it.

### What the API is now

The API's description used to say "read-only". That is no longer true, and the
qualification matters: nothing here writes a *value*. The only thing it writes is
a human's decision about a value the pipeline already stopped on. An endpoint that
could write a golden value would create records nothing can explain, and that
remains forbidden.

A refused decision is `409`, not `500` — a record already released or a field
outside the vocabulary is the reviewer asking for something the data does not
allow, not a server fault. No decision can be made without a name.

---

## 2. Feeds, not files

**Decision:** move the loading arguments from the command line to the feed, and
poll a directory per feed.

`--entity-type`, `--source-name` and `--reliability` are properties of the *feed*,
not of the file. Retyping them per file is how one vendor's data ends up loaded
under two source names at two different reliabilities — and once that has
happened, survivorship is choosing between what it thinks are two vendors.

```
data/inbox/watch/apollo/feed.json      the arguments, written once
data/inbox/watch/apollo/export.csv     drop files here
data/inbox/watch/apollo/_done/         processed, with a receipt
data/inbox/watch/apollo/_failed/       not processed, with the reason
```

Three properties, none about throughput:

- **A file being copied is not a file.** A large export is a valid, truncated CSV
  for as long as the copy takes, and loading one silently drops every row after
  the cut. Nothing is touched until its size and mtime are unchanged across a
  full poll. That is the cheapest check that is actually correct on every
  filesystem this runs on.
- **One bad file must not stop the feed.** A file that throws is moved aside with
  the error beside it and the next is processed. The loop exits only on a signal,
  and finishes the file in hand first.
- **Arriving twice must not load twice.** Handled files leave the drop folder,
  and ingestion's SHA-256 check refuses the same bytes under a new name
  regardless. The move is tidiness; the hash is the guarantee.

A blocked re-ingest is filed as **done**, not failed. Nothing went wrong — the
data is already loaded — and sending somebody to investigate a system working
correctly is its own cost.

A `feed.json` that would load data wrongly is refused before anything is read. A
mistyped `entity_type` means a company's rows resolved as people: recoverable only
by purging the source, and not obviously wrong until much later.

**Rejected:** a new service. The record-at-a-time path already had an image with
everything needed; `process watch` is a subcommand on it, and the `watcher` service
is the same image with a different command. A new Dockerfile and a new CI matrix
entry to run existing code was infrastructure the job did not need.

---

## 3. Credentials from files

**Decision:** every setting may arrive as `NAME`, as `NAME_FILE` pointing at a
file, or as a file at `/run/secrets/<name>` — read without being configured,
because that is where Compose and Kubernetes both mount one, and a convention
that has to be configured is one that gets skipped.

The variable always wins, so a stale file in a mounted volume cannot override
what an operator just typed.

`docker-compose.secrets.yml` wires `POSTGRES_PASSWORD` and `PCDF_API_KEYS`
through it. An **overlay** rather than the default, because the default has to
work on a fresh clone with nothing but `cp .env.example .env`, and a secrets file
that must exist before anything starts is not that.

This is not a secret store. It is the seam that lets one go in front of this
without any service knowing.

### The bug that only running it would find

An empty variable must not count as an answer. Setting one to `""` is the only
way Compose can clear a value inherited from `.env`, and the overlay does exactly
that to hand the credential over to the file. Treating `""` as supplied meant the
file was never read and the service started with no password at all. Two tests
hold that down.

---

## 4. Alerts with somewhere to go

**Decision:** make `alertmanager.yml` a template rendered at container start.

Unset, the route stays on a receiver with no destination and nothing leaves the
machine — the same default as before, but now true by construction rather than by
comment. Set, the route switches to the webhook receiver.

**The URL never enters the config.** The entrypoint writes it to a file with
`umask 077` and the receiver reads it via `url_file`, so the credential is not in
the config, not in the image, and not in `docker inspect` for anyone who can list
containers. Unsetting it removes the file rather than leaving a live credential
behind in the volume.

---

## 5. Re-blocking, corrected by a fact that now exists

`tools/ops/reblock.py` finds entities that share a strong identity key and a name.
Its own docstring records why it refuses to merge automatically: the same evidence
supports opposite answers, and only knowing whether a source catalogues
organisations or locations tells them apart.

That fact has existed since ADR 0014's neighbour, migration 0016 — `source.describes`.
Live resolution has used it since, demoting a premises directory's domain so it
cannot carry a link on its own. **reblock predates it** and was still reading every
stored key at whatever strength it happened to be written with, which is precisely
the population it exists to examine.

It now applies to stored keys the same rule resolution applies to new ones. On the
current database that is the difference between:

```
900 group(s) covering 6000 entities -> 5100 would be absorbed
  Bella Nails Spa #002
    survivor 01a0146c-26e2...  12 Pine Street, Portland
    absorb   01a0146c-4257...  912 Pine Street, Detroit
    absorb   01a0146c-5e13...  1812 Pine Street, Miami
```

and:

```
no entities share a strong identity key
```

Three nail salons in three cities, one brand, one domain. The old report proposed
merging them and 5,097 others like them into whichever entity had the most records
behind it. Running `--merge` on that report would have destroyed five thousand real
business locations and produced a plausible summary saying so.

The guard is not a threshold and cannot be tuned wrong. It reads what the vendor
was cataloguing, recorded once, by whoever loaded it.

---

## Consequences

- The API image now depends on `mapping`, `validation`, `resolution` and `golden`.
  That is the cost of not reimplementing their decisions, and it is the right
  trade: the alternative is four copies of a rule that must never diverge.
- A reviewer no longer needs a terminal, which means the people who know whether
  two companies are the same can now say so.
- `PCDF_ALLOW_UNAUTHENTICATED=true` in `.env.example` is now more dangerous than
  it was, because the endpoints behind it can merge entities. The secrets overlay
  sets it to `false` explicitly for that reason.
- Merging still stays behind `--merge --confirm` and a backup. Nothing in this
  increment made re-blocking automatic; it made its report trustworthy.

## Verification

Run against the live stack, not only stubs:

- a mapping approved through the API landed as `manual` / `1.000` with the
  reviewer recorded;
- a franchise premises row sharing a domain with its parent company was rejected,
  and both entities stayed separate;
- an accepted merge folded one entity into another, left the absorbed id resolving
  as a tombstone, and rebuilt the survivor — 1 value written, 4 refreshed, 9
  unchanged;
- a quarantined record released through the API was resolved (1 new entity) and
  its golden record built (3 values) in the same action;
- a file dropped into a watched feed was left alone on the first poll, processed
  on the next under its feed's settings, and filed into `_done/` with a receipt;
- a test alert was delivered to a webhook sink as
  `status=firing receiver=webhook alerts=['WebhookDeliveryProof']`, and unsetting
  the variable put it back to "receiver not referenced by any route";
- under the secrets overlay neither credential appeared in the container
  environment, the API reported `1 key(s) configured` from the mounted file, and
  requests with no key and with a wrong key were refused 401 while the file-only
  key was served 200.

526 tests across the eight suites.
