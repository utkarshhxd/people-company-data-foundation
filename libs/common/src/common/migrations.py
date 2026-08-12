"""Forward-only SQL migration runner.

Applies numbered .sql files from db/migrations/ in order, once each, tracking
what has been applied in a schema_migrations table.
"""

import hashlib
import logging
import os
import re
import sys
from pathlib import Path

import psycopg

from common.db import connect

logger = logging.getLogger(__name__)

# Repo root is 4 levels up from libs/common/src/common/; overridable so the
# container layout doesn't have to match the repo layout.
_DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "db" / "migrations"
MIGRATIONS_DIR = Path(os.environ.get("PCDF_MIGRATIONS_DIR", _DEFAULT_MIGRATIONS_DIR))
FILENAME_RE = re.compile(r"^(\d+)_.+\.sql$")

# A migration whose first line is this marker runs outside a transaction, for
# statements Postgres forbids inside one (e.g. CREATE INDEX CONCURRENTLY).
NO_TRANSACTION_MARKER = "-- pcdf:no-transaction"

TRACKING_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    filename   text        NOT NULL,
    checksum   text        NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def discover(migrations_dir: Path = MIGRATIONS_DIR) -> list[tuple[str, Path]]:
    found = []
    for path in migrations_dir.glob("*.sql"):
        match = FILENAME_RE.match(path.name)
        if not match:
            raise ValueError(
                f"Migration {path.name!r} does not match the NNNN_description.sql convention"
            )
        found.append((match.group(1), path))

    versions = [v for v, _ in found]
    duplicates = {v for v in versions if versions.count(v) > 1}
    if duplicates:
        raise ValueError(f"Duplicate migration version(s): {sorted(duplicates)}")

    return sorted(found, key=lambda item: int(item[0]))


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _applied(conn: psycopg.Connection) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version, checksum FROM schema_migrations")
        return dict(cur.fetchall())


def _apply(conn: psycopg.Connection, version: str, path: Path, sql: str) -> None:
    outside_transaction = sql.lstrip().startswith(NO_TRANSACTION_MARKER)
    record = (
        "INSERT INTO schema_migrations (version, filename, checksum) VALUES (%s, %s, %s)",
        (version, path.name, _checksum(sql)),
    )

    if outside_transaction:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(*record)
        conn.autocommit = False
    else:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(*record)


def migrate(migrations_dir: Path = MIGRATIONS_DIR) -> int:
    migrations = discover(migrations_dir)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(TRACKING_TABLE_DDL)
        conn.commit()

        applied = _applied(conn)
        pending = []

        for version, path in migrations:
            sql = path.read_text(encoding="utf-8")
            if version not in applied:
                pending.append((version, path, sql))
            elif applied[version] != _checksum(sql):
                raise RuntimeError(
                    f"Migration {path.name} was already applied but its content has changed. "
                    "Applied migrations are immutable — add a new migration instead."
                )

        for version, path, sql in pending:
            logger.info("applying migration %s", path.name)
            _apply(conn, version, path, sql)

    return len(pending)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    try:
        count = migrate()
    except (ValueError, RuntimeError, psycopg.Error) as exc:
        logger.error("migration failed: %s", exc)
        return 1
    logger.info("migrations up to date (%d applied this run)", count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
