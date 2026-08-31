"""Project the golden record onto the application database that displays it.

The pipeline database and the application database answer different questions
and are shaped for those questions. Here, a fact is a row in
`attribute_observation` and a trusted value is a row in `golden_attribute`, so
that nothing a vendor said is ever lost and every displayed value can be traced
to the cell it came from. There, a lead is one wide row with typed columns and
trigram indexes, because the question is "show me VPs of Engineering in Berlin
at companies over 500 people" and no amount of indexing makes an entity-
attribute-value table answer it in a page load.

Neither shape can be made to do the other's job, so both exist and this is the
one-way road between them: the pipeline is the system of record, the app is a
read model, and nothing ever flows back. An edit made in the app would be
overwritten by the next run, which is the correct behaviour for a derived copy
and worth stating out loud.

    docker compose run --rm pipeline python /tools/ops/project_serving.py
    docker compose run --rm pipeline python /tools/ops/project_serving.py --dry-run
    docker compose run --rm pipeline python /tools/ops/project_serving.py --full

Requires SERVING_HOST (see common.config) and `db/serving/0001_serving_contract.sql`
already applied to the serving database.
"""

import argparse
import logging
import time
from typing import Any

import psycopg
from common.config import settings
from common.db import connect

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

BATCH_SIZE = 500
PROGRESS_EVERY = 5_000

# ---------------------------------------------------------------------------
# What maps to what
# ---------------------------------------------------------------------------
# Canonical field -> serving column, for the fields where exactly one value is
# meant. Anything absent from these maps is deliberately not projected: the
# serving schema has columns (is_b2b, ownership_status, logo_url, tier_quality)
# that no source reports and no canonical field exists for, and filling them
# with a guess would be enrichment by invention.

COMPANY_SINGLE = {
    "company_name": "company_name",
    "website": "domain",
    "phone": "company_phone",
    "email": "company_email_id",
    "address_line1": "company_street",
    "city": "company_city",
    "state_region": "company_state",
    "country": "company_country",
    "postal_code": "company_postal_code",
    "employee_count": "employees_count",
    "founded_year": "founded_year",
    "linkedin_url": "linkedin_url",
    "facebook_url": "facebook_url",
    "twitter_url": "twitter_url",
    "instagram_url": "instagram_url",
    "annual_revenue": "annual_revenue",
    "total_funding": "total_funding",
    "latest_funding_amount": "latest_funding_amount",
    "last_funding_date": "latest_funding_date",
    "seo_description": "seo_description",
}

# Columns the serving schema declares as VARCHAR[]. A golden value is one value
# by definition -- survivorship exists to pick it -- so an array column cannot
# be filled from the golden record without misrepresenting it. These come from
# the observations instead: every distinct value any source reported for the
# field, which is what a multi-valued column actually means.
COMPANY_MULTI = {
    "industry": "industry",
    "technologies": "technologies",
    "keywords": "keywords",
    "sic_code": "sic_codes",
    "naics_code": "naics_codes",
}

PERSON_SINGLE = {
    "first_name": "first_name",
    "last_name": "last_name",
    "email": "email_address",
    "email_status": "email_status_enum",
    "phone": "phone_number",
    "mobile_phone": "mobile_phone",
    "home_phone": "home_phone",
    "work_phone": "corporate_phone",
    "job_title": "designation",
    "department": "department",
    "seniority": "seniority",
    "city": "city",
    "state_region": "state",
    "country": "country",
    "address_line1": "address1",
    "linkedin_url": "linkedin_url",
}

# The serving columns whose declared type is not text. Casting in the statement
# rather than in Python keeps the conversion rules in one place -- Postgres's --
# and means a value that somehow is not a number fails one row rather than
# silently becoming something else.
CASTS = {
    # Entity ids travel as strings and land in uuid columns; without the cast
    # Postgres is asked to compare uuid with text and refuses.
    "entity_id": "uuid",
    "employees_count": "integer",
    "founded_year": "smallint",
    "annual_revenue": "numeric",
    "total_funding": "numeric",
    "latest_funding_amount": "numeric",
    "latest_funding_date": "date",
    "confidence_score": "numeric",
    "industry": "varchar[]",
    "technologies": "varchar[]",
    "keywords": "varchar[]",
    "sic_codes": "varchar[]",
    "naics_codes": "varchar[]",
}

# The column order every row built below follows. Companies carry no `source`
# or `confidence_score` column in the serving schema, so neither is projected;
# both remain answerable from the pipeline.
COMPANY_COLUMNS = (
    ["entity_id"]
    + list(COMPANY_SINGLE.values())
    + list(COMPANY_MULTI.values())
)

PERSON_COLUMNS = (
    ["entity_id", "company_id", "name"]
    + list(PERSON_SINGLE.values())
    + ["confidence_score", "source"]
)


# ---------------------------------------------------------------------------
# Reading the pipeline
# ---------------------------------------------------------------------------


def entities_to_project(
    conn: psycopg.Connection, entity_type: str, since: Any, limit: int | None
) -> list[str]:
    """Entities whose projected form could have changed since the last run.

    A golden value gaining a new `valid_from` is the signal: values are only
    ever written when survivorship actually decided something different, so an
    unchanged entity does not reappear here run after run.

    Employment is a second signal for a person, and a separate one -- a person
    can be linked to a newly-resolved employer without any of their own values
    moving, and the serving row's company_id would then be stale forever.
    """
    sql = """
        SELECT DISTINCT e.entity_id
        FROM entity e
        WHERE e.entity_type = %(entity_type)s
          AND e.status = 'active'
          AND (
            EXISTS (
                SELECT 1 FROM golden_attribute g
                WHERE g.entity_id = e.entity_id
                  AND g.valid_to IS NULL
                  AND (%(since)s::timestamptz IS NULL OR g.valid_from > %(since)s)
            )
            OR EXISTS (
                SELECT 1 FROM entity_relationship r
                WHERE r.from_entity_id = e.entity_id
                  AND r.valid_to IS NULL
                  AND (%(since)s::timestamptz IS NULL OR r.valid_from > %(since)s)
            )
          )
        ORDER BY e.entity_id
    """
    if limit:
        sql += f"\n        LIMIT {int(limit)}"
    with conn.cursor() as cur:
        cur.execute(sql, {"entity_type": entity_type, "since": since})
        return [str(row[0]) for row in cur]


def golden_values(
    conn: psycopg.Connection, entity_ids: list[str]
) -> dict[str, dict[str, str]]:
    """The current trusted value of every field, for a chunk of entities."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT entity_id, canonical_field, value
            FROM golden_attribute
            WHERE valid_to IS NULL AND entity_id = ANY(%s::uuid[])
            """,
            (entity_ids,),
        )
        out: dict[str, dict[str, str]] = {}
        for entity_id, field, value in cur:
            out.setdefault(str(entity_id), {})[field] = value
        return out


def mean_confidence(
    conn: psycopg.Connection, entity_ids: list[str]
) -> dict[str, float]:
    """How well supported this entity's record is, averaged over its fields.

    The same number `golden_person.mean_confidence` reports. Recomputed here
    rather than read from the view because this job never reads the flat views:
    they expose a fixed column list, and the fields this projection needs are
    not all on it.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT entity_id, round(avg(confidence), 3)
            FROM golden_attribute
            WHERE valid_to IS NULL AND entity_id = ANY(%s::uuid[])
            GROUP BY entity_id
            """,
            (entity_ids,),
        )
        return {str(entity_id): value for entity_id, value in cur}


def dominant_source(
    conn: psycopg.Connection, entity_ids: list[str]
) -> dict[str, str]:
    """Which vendor won most of this entity's fields.

    The serving schema's `source` column wants one name and a merged entity has
    several. The one that won the most fields is the honest answer to "where did
    most of this come from"; the full answer stays in golden_attribute, where
    every field names its own winner.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT entity_id, source_name FROM (
                SELECT g.entity_id, s.source_name,
                       row_number() OVER (
                           PARTITION BY g.entity_id
                           ORDER BY count(*) DESC, s.source_name
                       ) AS rank
                FROM golden_attribute g
                JOIN source s ON s.source_id = g.winning_source_id
                WHERE g.valid_to IS NULL AND g.entity_id = ANY(%s::uuid[])
                GROUP BY g.entity_id, s.source_name
            ) ranked
            WHERE rank = 1
            """,
            (entity_ids,),
        )
        return {str(entity_id): name for entity_id, name in cur}


def multi_values(
    conn: psycopg.Connection, entity_ids: list[str], fields: list[str]
) -> dict[str, dict[str, list[str]]]:
    """Every distinct value any source reported, for the multi-valued fields.

    `o.subject = l.role` is the same filter the golden builder uses and is here
    for the same reason: a person row carries its employer's columns too, and
    without it a company would collect the industries of everyone who works
    there.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT l.entity_id, o.canonical_field,
                   array_agg(DISTINCT o.normalized_value ORDER BY o.normalized_value)
            FROM record_entity_link l
            JOIN attribute_observation o
              ON o.record_id = l.record_id AND o.subject = l.role
            WHERE l.entity_id = ANY(%s::uuid[])
              AND o.canonical_field = ANY(%s)
              AND o.normalized_value IS NOT NULL
            GROUP BY l.entity_id, o.canonical_field
            """,
            (entity_ids, fields),
        )
        out: dict[str, dict[str, list[str]]] = {}
        for entity_id, field, values in cur:
            out.setdefault(str(entity_id), {})[field] = values
        return out


def employers(conn: psycopg.Connection, entity_ids: list[str]) -> dict[str, str]:
    """The company entity each person currently works for, where one is known."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (from_entity_id) from_entity_id, to_entity_id
            FROM entity_relationship
            WHERE relationship_type = 'employed_at'
              AND valid_to IS NULL
              AND from_entity_id = ANY(%s::uuid[])
            ORDER BY from_entity_id, valid_from DESC, to_entity_id
            """,
            (entity_ids,),
        )
        return {str(person): str(company) for person, company in cur}


# ---------------------------------------------------------------------------
# Shaping a pipeline value for a serving column
# ---------------------------------------------------------------------------
# Two transforms, both applied only on the way out. The pipeline's stored value
# is not changed by either: what the source wrote is still in
# `attribute_observation.raw_value`, and the normalized form is still in
# `golden_attribute.value`. These exist because a read model's column has a
# meaning the pipeline's canonical field does not quite share, and getting a
# derived copy to fit its consumer is what a projection is for.


def hostname(url: str | None) -> str | None:
    """A website, as the bare host the serving `domain` column is named for.

    The canonical field is `website` and normalization keeps it a whole URL,
    scheme and all, because that is what the source wrote and shortening it
    upstream would lose a path some vendor meant. The serving column is a
    domain: it carries a unique index and a trigram index, and neither does
    anything useful if 'http://www.acme.com' and 'acme.com' are two companies.
    """
    if not url:
        return None
    text = url.strip().lower()
    _, _, text = text.rpartition("://")
    text = text.partition("/")[0].partition("?")[0]
    text = text.removeprefix("www.")
    return text or None


def split_list(values: list[str] | None) -> list[str] | None:
    """The elements of a list-shaped value, for an array column.

    Vendors ship these fields as one cell holding 'React, Apache, Typekit', and
    normalization deliberately does not split on a comma -- for `text` it cannot
    know whether the comma is a separator or part of the value, and inventing
    elements upstream would put values in the record that no source wrote.

    A `VARCHAR[]` column is a different promise. `'React' = ANY(technologies)`
    is the query it exists to serve, and against a single element reading
    'React, Apache, Typekit' that query returns nothing at all. So the split
    happens here, where it is reversible by re-reading the observation, and
    where being wrong about one comma costs a search result rather than a fact.
    """
    if not values:
        return None
    out: list[str] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out or None


# ---------------------------------------------------------------------------
# Writing the serving database
# ---------------------------------------------------------------------------


def upsert_sql(table: str, columns: list[str], company_lookup: bool) -> str:
    """One idempotent statement per row, keyed on the pipeline's entity_id.

    Written out per column rather than generated from a `SELECT *` so that a
    column added to the serving schema by the application team is ignored here
    until someone decides what should fill it -- which is the safe direction for
    a table this job does not own.
    """
    placeholders = []
    for column in columns:
        if column == "company_id" and company_lookup:
            # The serving row's own key for the employer, found by the pipeline
            # entity_id it was projected from. NULL when the employer has not
            # been projected yet, which the next run fixes.
            placeholders.append(
                "(SELECT c.company_id FROM companies c "
                "WHERE c.entity_id = %s::uuid)"
            )
        elif column in CASTS:
            placeholders.append(f"%s::{CASTS[column]}")
        else:
            placeholders.append("%s")

    updates = ", ".join(
        f"{column} = EXCLUDED.{column}" for column in columns if column != "entity_id"
    )
    return (
        f"INSERT INTO {table} ({', '.join(columns)}, updated_at)\n"
        f"VALUES ({', '.join(placeholders)}, now())\n"
        f"ON CONFLICT (entity_id) DO UPDATE SET {updates}, updated_at = now()"
    )


def company_row(entity_id: str, golden: dict, multi: dict) -> list[Any] | None:
    """One serving `companies` row, or None if there is nothing to call it.

    `company_name` is NOT NULL there, and a company entity with no agreed name
    is a real state -- a row resolved only on domain, awaiting a name from the
    next file. Skipping it is right: it will project the moment it has one.
    """
    name = golden.get("company_name")
    if not name:
        return None
    values: list[Any] = [entity_id]
    values += [
        hostname(golden.get(field)) if field == "website" else golden.get(field)
        for field in COMPANY_SINGLE
    ]
    values += [split_list(multi.get(field)) for field in COMPANY_MULTI]
    return values


def person_row(
    entity_id: str,
    golden: dict,
    employer_id: str | None,
    confidence: float | None,
    source_name: str | None,
) -> list[Any] | None:
    """One serving `leads` row, or None if there is no name to display."""
    name = golden.get("full_name")
    if not name:
        parts = [golden.get("first_name"), golden.get("last_name")]
        name = " ".join(part for part in parts if part).strip() or None
    if not name:
        return None

    values: list[Any] = [entity_id, employer_id, name]
    values += [golden.get(field) for field in PERSON_SINGLE]
    values += [confidence, source_name]
    return values


def write_batch(
    serving: psycopg.Connection, sql: str, rows: list[list[Any]]
) -> tuple[int, list[str]]:
    """Write a batch, isolating the rows that cannot be written.

    A savepoint per row rather than one per batch, because the failures this
    protects against are single-row facts: two entities that resolution has not
    yet merged both claiming one email address will collide on the unique index
    the serving contract adds, and losing 499 good rows to it would make the
    whole job untrustworthy. The collision is reported, and the entity stays
    unprojected until resolution settles it.
    """
    written = 0
    rejected: list[str] = []
    with serving.cursor() as cur:
        for row in rows:
            try:
                with serving.transaction():
                    cur.execute(sql, row)
                written += 1
            except psycopg.Error as exc:
                message = str(exc).strip().splitlines()[0]
                rejected.append(f"{row[0]}: {message}")
    return written, rejected


def read_watermark(serving: psycopg.Connection, entity_type: str) -> Any:
    with serving.cursor() as cur:
        cur.execute(
            "SELECT projected_to FROM projection_watermark WHERE entity_type = %s",
            (entity_type,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def write_watermark(serving: psycopg.Connection, entity_type: str, moment: Any) -> None:
    with serving.cursor() as cur:
        cur.execute(
            """
            INSERT INTO projection_watermark (entity_type, projected_to)
            VALUES (%s, %s)
            ON CONFLICT (entity_type)
            DO UPDATE SET projected_to = EXCLUDED.projected_to, updated_at = now()
            """,
            (entity_type, moment),
        )


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def project(
    pipeline: psycopg.Connection,
    serving: psycopg.Connection,
    entity_type: str,
    since: Any,
    limit: int | None,
    batch_size: int,
    dry_run: bool,
) -> tuple[int, int, list[str]]:
    is_person = entity_type == "person"
    table = "leads" if is_person else "companies"
    columns = PERSON_COLUMNS if is_person else COMPANY_COLUMNS
    sql = upsert_sql(table, columns, company_lookup=is_person)

    entity_ids = entities_to_project(pipeline, entity_type, since, limit)
    print(f"{entity_type}: {len(entity_ids)} entit(ies) to project")
    if dry_run or not entity_ids:
        return 0, 0, []

    written = skipped = 0
    rejected: list[str] = []
    start = time.time()

    for offset in range(0, len(entity_ids), batch_size):
        chunk = entity_ids[offset : offset + batch_size]
        golden = golden_values(pipeline, chunk)

        rows: list[list[Any]] = []
        if is_person:
            links = employers(pipeline, chunk)
            confidence = mean_confidence(pipeline, chunk)
            sources = dominant_source(pipeline, chunk)
            for entity_id in chunk:
                row = person_row(
                    entity_id,
                    golden.get(entity_id, {}),
                    links.get(entity_id),
                    confidence.get(entity_id),
                    sources.get(entity_id),
                )
                if row is None:
                    skipped += 1
                else:
                    rows.append(row)
        else:
            multi = multi_values(pipeline, chunk, list(COMPANY_MULTI))
            for entity_id in chunk:
                row = company_row(
                    entity_id, golden.get(entity_id, {}), multi.get(entity_id, {})
                )
                if row is None:
                    skipped += 1
                else:
                    rows.append(row)

        batch_written, batch_rejected = write_batch(serving, sql, rows)
        written += batch_written
        rejected += batch_rejected
        # Per batch, so an interrupted run leaves finished batches finished.
        # The watermark is only moved at the end, so an interruption costs a
        # repeat of the work, never a gap in it.
        serving.commit()

        done = offset + len(chunk)
        if done % PROGRESS_EVERY < batch_size:
            rate = done / max(time.time() - start, 0.001)
            print(f"  {done}/{len(entity_ids)}  {rate:.0f} entities/s")

    return written, skipped, rejected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be projected and stop")
    parser.add_argument("--full", action="store_true",
                        help="ignore the watermark and project every entity")
    parser.add_argument("--limit", type=int, help="stop after this many entities")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--entity-type", choices=["person", "company", "both"],
                        default="both")
    args = parser.parse_args()

    # Companies before people, always. A person's serving row carries the
    # serving key of their employer, and that key does not exist until the
    # company has been projected. The other order costs every new person a
    # NULL company_id until the following run.
    types = ["company", "person"] if args.entity_type == "both" else [args.entity_type]

    # Resolved before anything is opened, so a stack with no serving database
    # configured says so instead of connecting to the pipeline first and
    # failing afterwards.
    serving_dsn = settings.serving_dsn()

    with connect() as pipeline, psycopg.connect(serving_dsn) as serving:
        # The pipeline's clock, read before anything else, is the next
        # watermark. Taking it after the run would silently skip anything
        # committed while the run was in progress; taking the serving
        # database's would compare two clocks that are allowed to differ.
        with pipeline.cursor() as cur:
            cur.execute("SELECT now()")
            run_started = cur.fetchone()[0]

        for entity_type in types:
            since = None if args.full else read_watermark(serving, entity_type)
            print(f"\n{entity_type}: since {since or 'the beginning'}")

            written, skipped, rejected = project(
                pipeline, serving, entity_type, since,
                args.limit, args.batch_size, args.dry_run,
            )
            if args.dry_run:
                continue

            print(f"  written  {written}")
            if skipped:
                print(f"  skipped  {skipped} (no name to display yet)")
            if rejected:
                print(f"  rejected {len(rejected)}")
                for line in rejected[:10]:
                    print(f"    {line}")
                if len(rejected) > 10:
                    print(f"    ... and {len(rejected) - 10} more")

            # Held back when anything was rejected, so the next run reconsiders
            # those entities instead of leaving them permanently unprojected. A
            # rejection is a duplicate the pipeline has not merged yet, and the
            # repeated work is idempotent -- the alternative is a silent gap.
            if rejected:
                print("  watermark unmoved: rerun after resolving the above")
            else:
                write_watermark(serving, entity_type, run_started)
                serving.commit()

        if args.dry_run:
            print("\ndry run: nothing written, watermark unmoved")


if __name__ == "__main__":
    main()
