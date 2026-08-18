# Watched feeds

Each directory here is one **feed** — one vendor, one kind of file, one set of
loading arguments. The `watcher` service polls them and processes whatever
appears, so nobody has to remember whether last month's Apollo export was loaded
as `apollo` or `apollo_io`, at reliability 0.8 or 0.5.

```
data/inbox/watch/
  apollo/
    feed.json          the arguments, written once
    export_2026_08.csv drop files here
    _done/             processed, each with a .log receipt beside it
    _failed/           not processed, each with the reason beside it
```

## Adding a feed

Make a directory, put a `feed.json` in it, drop files in. Nothing else.

```json
{
  "entity_type": "person",
  "source_name": "apollo",
  "reliability": 0.8,
  "describes": "organisation"
}
```

| Key | Required | What it means |
| --- | --- | --- |
| `entity_type` | yes | `person` or `company` — what a row in this file *is* |
| `source_name` | yes | The vendor/feed name every file here is loaded under |
| `reliability` | no (0.5) | 0–1. How much this vendor is trusted when sources disagree |
| `describes` | no | `organisation` (rows name companies, so a shared domain means the same company) or `location` (rows name premises, so a shared domain means only the same brand) |
| `record_id_column` | no | A column holding the vendor's own key; defaults to the row number |
| `source_type` | no | `csv` or `excel`; inferred from the extension otherwise |
| `build_golden` | no (true) | Set false to load faster and build golden records separately |

A `feed.json` that would load data wrongly is refused before anything is read —
a mistyped `entity_type` means a company's rows resolved as people, which is
recoverable only by purging the source. One broken feed never stops the others.

## What happens to a file

- **Nothing, at first.** A file is left alone until its size and mtime have been
  unchanged across a full poll. A large export is a valid, truncated CSV for as
  long as the copy takes, and loading one silently drops every row after the cut.
- **Processed, then moved to `_done/`** with a timestamped name and a `.log`
  saying what the batch did.
- **Moved to `_failed/`** if it could not be read, with the reason beside it.
  The next file is processed regardless.
- **Moved to `_done/` if it was already loaded.** Ingestion hashes file contents,
  so the same bytes under a new name are refused — that is not a failure and
  nobody should be sent to investigate it.

Rows that individually failed are in `record_error`, not in `_failed/`: the file
was read successfully. `process reprocess` is the way back for those.

## Running it

```bash
docker compose up -d watcher          # long-running
docker compose logs -f watcher

docker compose run --rm pipeline process watch --once    # one pass, for cron
```
