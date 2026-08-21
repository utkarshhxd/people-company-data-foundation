# Operations

Backup and restore, continuous integration, the test suites, and what lives
under `tools/`.

Part of the [People & Company Data Foundation](../../README.md).

## `tools/`

Run against a live stack rather than shipped in it. Grouped by what a script
is *for*, not by what it's written in — the whole directory is mounted
read-only at `/tools` in the `pipeline` and `ingestion` containers, so the
path inside a container is `/tools/<group>/<script>`.

**`tools/ops/`** — run against a live stack, by an operator.

| | |
| --- | --- |
| `backup.sh` · `restore_check.sh` | take a dump, and prove it restores |
| `rebuild_golden.py` | recompute golden values after a rule change |
| `reblock.py` | report entities that should have merged, and merge them on request |
| `purge_source.py` | remove a source's data — the only thing here that deletes |
| `load_samples.sh` · `load_testfiles.sh` | drive a directory of files through the pipeline, one at a time |
| `pgadmin-server.json` | pgAdmin server definition, so nobody types connection details |

**`tools/fixtures/`** — produce data to run the system against.

| | |
| --- | --- |
| `sample_file.py` | cut a bounded sample off a large export, streaming |
| `generate_fixture.py` | a synthetic file at a chosen size |
| `generate_test_corpus.py` | a multi-vendor corpus with known overlaps |
| `generate_edge_cases.py` | one file of everything that has ever broken a stage |
| `golden_sample.py` | pull a readable sample of golden records back out |

**`tools/sql/`** — hand verification, for psql or pgAdmin.

| | |
| --- | --- |
| `verify.sql` | twelve reconciliation checks; empty output is the pass |
| `queries.sql` | ten worked queries |
| `compare_runs.sql` | diff two batches of the same source |

## Credentials

Every setting can be supplied three ways, most explicit wins:

1. `POSTGRES_PASSWORD=...` — an environment variable
2. `POSTGRES_PASSWORD_FILE=/path/to/file` — a file to read it from
3. a file at `/run/secrets/postgres_password` — found without being
   configured, because that's where Compose and Kubernetes both mount one

The point is what an environment variable is visible to: `docker inspect`,
the environment of anything else in the container, and the shell history of
whoever exported it. A file is visible to whoever can read the file.

```bash
mkdir -p secrets
printf '%s' 'a-strong-password' > secrets/postgres_password

docker compose -f docker-compose.yml -f docker-compose.secrets.yml up -d
```

It's an overlay rather than the default because the default has to work on a
fresh clone with nothing but `cp .env.example .env`. This is not a secret
store — it's the seam that lets one go in front of this without any service
knowing.

## Continuous integration

`.github/workflows/ci.yml` runs three jobs: `ruff check` lint, every
migration applied twice against an empty database (a migration that isn't
idempotent can't be re-run), and all **seven** service images
(`ingestion`, `mapping`, `normalization`, `validation`, `resolution`,
`golden`, `record_pipeline`) built and their test suites run in-container,
plus `libs/common`'s suite riding along on the `ingestion` build.

CI needs nothing beyond Postgres: every suite tests functions that talk to the
database, and the consumers are a thin shell over those same functions, so
none of them needs a broker to be exercised.

## Running tests

Polars and psycopg's binary driver are both native extensions; this
environment's Application Control policy blocks them outside Docker, so
tests run in a container — matching CI exactly, so "works in CI" and "works
here" never drift apart:

```powershell
docker compose run --rm ingestion python -m pytest tests -v
docker compose run --rm mapping python -m pytest tests -v
docker compose run --rm normalization python -m pytest tests -v
docker compose run --rm validation python -m pytest tests -v
docker compose run --rm resolution python -m pytest tests -v
docker compose run --rm golden python -m pytest tests -v
docker compose run --rm pipeline python -m pytest tests -v

# common has no image of its own; any of the above carries it
docker compose run --rm ingestion python -m pytest ../../libs/common/tests -v
```

## Resetting for a clean test

Wipes all data, keeps the schema fresh via migrations:

```powershell
docker compose down -v          # stops everything, deletes the postgres volume
docker compose up -d postgres migrate watcher
```

Watch folders (`data/inbox/watch/<feed>/`) aren't touched by this — clear
`_done/`/`_failed/` by hand if you want a folder to look untouched again;
`feed.json` is config, not data, and is safe to leave alone.
