# Getting started

Prerequisites, first boot, and how to tell the stack is actually up.

Part of the [People & Company Data Foundation](../../README.md).

## Prerequisites

- Docker Desktop (or engine) with Compose v2
- [uv](https://docs.astral.sh/uv/) — only needed for local (non-Docker) Python work

## Quickstart

```powershell
Copy-Item .env.example .env
# edit .env if you want non-default credentials/ports

docker compose build
docker compose up -d
docker compose ps    # postgres, migrate (Exited 0), watcher — all up
```

Three things start by default: `postgres`, the `migrate` job (applies every
migration, then exits — `Exited 0` is success, not a crash), and `watcher`
(polls `data/inbox/watch/` every 10s for files to load). Everything else —
`ingestion`, `pipeline`, `mapping`, `normalization`, `validation`,
`resolution`, `golden` — is CLI-only and only runs when you call it:

```powershell
docker compose run --rm pipeline process --help
```

> **Postgres is on host port `5433`, not 5432** (`POSTGRES_PORT` in `.env`) —
> deliberately, to avoid clashing with a native Postgres already on this
> machine. Connect pgAdmin or `psql` to `localhost:5433`. Data lives in the
> `pcdf_postgres-data` Docker volume, not on the host filesystem.

## Verifying the stack

```powershell
docker compose ps
docker compose exec postgres pg_isready -U pcdf_dev -d pcdf
docker compose logs watcher --tail 20
```

## The fastest real test

Make a feed, drop a file, watch it load — no flags to remember, no command to
type per file:

```powershell
mkdir data/inbox/watch/my_first_feed
@'
{"entity_type": "company", "source_name": "my_first_feed", "reliability": 0.5, "describes": "organisation"}
'@ | Set-Content -Encoding utf8 data/inbox/watch/my_first_feed/feed.json

Copy-Item your_file.csv data/inbox/watch/my_first_feed/
docker compose logs -f watcher
```

Two things decide whether this works cleanly the first time:

1. **The file needs a real header row.** If row 1 is already data (no column
   labels), the mapper has nothing to go on and every row ends up
   `needs_review` or `unmapped` — which cascades into every row failing
   `record.has_identifier` and landing in quarantine. Open the file and check
   row 1 before dropping it in.
2. **`entity_type` in `feed.json` has to match what the rows actually are.**
   Person data (name, title, email) dropped into a feed declared
   `entity_type: company` won't always fail loudly — a `Company` column can
   still pass as a company identifier, silently creating company entities out
   of person data instead of quarantining. Check the file's actual columns
   before writing `feed.json`, not after.

See [Watched feeds](../../data/inbox/watch/README.md) for the full mechanism,
and [Processing files](processing-files.md) for loading one file by hand
instead.
