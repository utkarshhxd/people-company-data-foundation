# Read API

Serving the trusted data and its lineage over HTTP, and the authentication in front of it.

Part of the [People & Company Data Foundation](../../README.md).

## Read API

`http://localhost:8000` — read-only by design. Data enters through the pipeline,
where it acquires the provenance these endpoints report; an endpoint that could
write a golden value would create records nothing can explain.

```powershell
# Find an entity by ANY identifier a vendor ever gave it — including ones
# that lost the survivorship contest
curl.exe "http://localhost:8000/entities?q=asiafoundation.org"

curl.exe "http://localhost:8000/entities/<id>"                          # trusted record + sources
curl.exe "http://localhost:8000/entities/<id>/explain/company_name"     # full lineage
curl.exe "http://localhost:8000/entities/<id>/timeline"                 # everything that happened
curl.exe "http://localhost:8000/entities/<id>/relationships"            # where a person works / who works here
curl.exe "http://localhost:8000/records/<id>"                           # what became of one source row
```

Merged ids keep answering: every endpoint resolves tombstones and reports both
`requested_entity_id` and the surviving `entity_id`. An id that stops working is
not a stable id.

Interactive docs at http://localhost:8000/docs.

### Authentication

Set `PCDF_API_KEYS` to a comma-separated list — several so one can be rotated
out without downtime — and every `/entities` and `/records` route requires one:

```powershell
curl.exe -H "X-API-Key: <key>" "http://localhost:8000/entities?q=acme.com"
curl.exe -H "Authorization: Bearer <key>" "http://localhost:8000/entities/<id>"
```

**With no keys set the API serves loopback and refuses everything else with
503.** It fails closed on the case that actually leaks: an unauthenticated
instance that looks healthy is how this gets exposed without anyone deciding to
expose it. Running open stays possible but must be chosen —
`PCDF_ALLOW_UNAUTHENTICATED=true` — and whichever way it is configured is logged
as a warning on every start.

Local development sets that flag, because requests from the host arrive through
the Docker bridge rather than 127.0.0.1 and would otherwise be refused. **Remove
it from `.env` before this is reachable by anything else.**

Health checks and `/metrics` never require a key: a liveness probe that needs a
credential reports the credential's health rather than the service's.

> The API is read-only, which limits the damage but not the disclosure. What it
> serves is personal data with provenance attached.
