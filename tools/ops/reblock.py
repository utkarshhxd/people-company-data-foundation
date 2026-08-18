"""Find entities that share an identity key and should be one entity.

Entities are created in file order, and a record can only match what already
exists when it arrives. A company first seen in a company file by name, and
later named as somebody's employer by domain, has no overlapping key at the
moment of the second sighting -- so it becomes a second entity. Nothing
afterwards revisits that decision.

The pipeline notices anyway. When a third record arrives carrying both keys it
matches both entities equally well, refuses to pick between them, and files a
match_candidate. Those ties are not weak matches; they are the system reporting
that two entities it already holds are probably the same thing. This walks that
evidence back to the entities themselves.

**This is triage, not automation.** It reports; it does not decide. Two attempts
at deciding automatically both produced wrong answers, and the second was wrong
in a way no amount of tuning fixes -- see duplicate_groups. Merging stays behind
--merge and --confirm, and is meant for a group somebody has looked at.

The reason automation fails here is not that the rules are too loose. It is that
the same evidence supports opposite answers depending on what the source was
describing:

  * 925 entities named "UnitedHealth Group" sharing unitedhealthgroup.com came
    from Apollo person rows naming an employer. They are one company, and its
    employees are in many cities.
  * 156 entities named "Subway Sandwiches & Salads" sharing subway.com came from
    a business directory listing premises. They are different franchises, and
    merging them would erase every one of them but one.

Name matches, domain matches, and the right answer differs. Nothing in the keys
distinguishes the cases -- only knowing whether a source describes organisations
or locations does, and that is a fact about the vendor, not about the row.

    docker compose run --rm pipeline python /tools/ops/reblock.py
    docker compose run --rm pipeline python /tools/ops/reblock.py --entity-type company
    docker compose run --rm pipeline python /tools/ops/reblock.py --merge --reviewed-by you

Take a backup first. tools/ops/backup.sh, and tools/ops/restore_check.sh to prove it.
"""

import argparse
import sys

from common.db import connect
from golden.pipeline import build_entity
from resolution import repository

# Only keys that identify on their own. Name and name+city are moderate -- three
# colleagues share an employer, a city and a switchboard -- and merging on them
# would collapse genuinely different companies that happen to share a name.
STRONG_KEYS = ("email", "website_domain", "linkedin", "external_id")


def duplicate_groups(conn, entity_type: str) -> list[list[str]]:
    """Entities that share a strong key AND agree on their name.

    Both halves are load-bearing, and the first version of this had only the
    first -- which was wrong in a way worth recording, because it looked
    reasonable and would have destroyed the database.

    A shared strong key is not sufficient. `unitedhealthgroup.com` is held by
    925 entities that really are all UnitedHealth Group, but `maine.gov` is held
    by 442 entities that are different agencies of the same state government,
    and `subway.com` by 156 separate franchises. A domain identifies an
    organisation's web presence, not a company.

    Transitive closure across keys is worse than insufficient. Grouping A with B
    because they share a domain, then B with C because they share an id, chains
    unrelated clusters through single spurious links: the earlier version
    produced one group of 6,769 entities that proposed absorbing Cigna, Anthem,
    Atrium Health and Asia Foundation into Elevance Health. Two entities being
    the same thing is not transitive when the evidence is this noisy.

    So: one key at a time, no chaining, and the names must match after folding
    case and punctuation. That merges the 925 UnitedHealth Group entities and
    leaves the Maine agencies and the Subway franchises alone.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH named AS (
                SELECT k.key_type, k.key_value, k.entity_id,
                       lower(regexp_replace(g.value, '[^a-zA-Z0-9]+', '', 'g')) AS folded
                FROM entity_identity_key k
                JOIN entity e ON e.entity_id = k.entity_id AND e.status = 'active'
                JOIN golden_attribute g
                  ON g.entity_id = k.entity_id AND g.valid_to IS NULL
                 AND g.canonical_field IN ('company_name', 'full_name')
                WHERE k.entity_type = %s AND k.key_type = ANY(%s)
            )
            SELECT array_agg(DISTINCT entity_id::text)
            FROM named
            WHERE folded <> ''
            GROUP BY key_type, key_value, folded
            HAVING count(DISTINCT entity_id) > 1
            """,
            (entity_type, list(STRONG_KEYS)),
        )
        groups = [sorted(row[0]) for row in cur]

    # The same set can arrive from two different keys. Deduplicate on the set
    # itself; still no chaining, because a group is only ever the entities that
    # shared one key and one name.
    seen: set[tuple[str, ...]] = set()
    unique = []
    for group in groups:
        signature = tuple(group)
        if signature not in seen:
            seen.add(signature)
            unique.append(group)
    return unique


def survivor(conn, entity_ids: list[str]) -> str:
    """The entity with the most records behind it wins.

    Not the oldest: an entity created first but observed once holds less than
    one created later and observed forty times, and merging into the thinner one
    moves more rows for no reason. Ties break on the id, so a rerun picks the
    same survivor.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.entity_id::text, count(l.link_id) AS links
            FROM entity e
            LEFT JOIN record_entity_link l ON l.entity_id = e.entity_id
            WHERE e.entity_id = ANY(%s::uuid[])
            GROUP BY 1
            ORDER BY links DESC, 1
            """,
            (entity_ids,),
        )
        return cur.fetchone()[0]


def label(conn, entity_id: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT value FROM golden_attribute
            WHERE entity_id = %s AND valid_to IS NULL
              AND canonical_field IN ('company_name', 'full_name')
            LIMIT 1
            """,
            (entity_id,),
        )
        row = cur.fetchone()
        return row[0] if row else "(no name)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entity-type", default="company",
                        choices=["company", "person"])
    parser.add_argument("--merge", action="store_true",
                        help="actually merge. Without this, nothing is written.")
    parser.add_argument("--confirm", action="store_true",
                        help="required with --merge, having read the report")
    parser.add_argument("--reviewed-by", default="reblock")
    parser.add_argument("--limit", type=int, help="stop after this many groups")
    args = parser.parse_args()

    with connect() as conn:
        groups = duplicate_groups(conn, args.entity_type)
        if args.limit:
            groups = groups[: args.limit]

        if not groups:
            print("no entities share a strong identity key")
            return 0

        total = sum(len(g) for g in groups)
        print(f"{len(groups)} group(s) covering {total} entities "
              f"-> {total - len(groups)} would be absorbed")
        print()

        for group in groups[:20]:
            keep = survivor(conn, group)
            print(f"  {label(conn, keep)}")
            print(f"    survivor {keep}")
            for other in group:
                if other != keep:
                    print(f"    absorb   {other}  {label(conn, other)}")
        if len(groups) > 20:
            print(f"  ... and {len(groups) - 20} more group(s)")

        if not args.merge:
            print("\nreport only. Re-run with --merge to apply, after a backup.")
            return 0

        print("\nmerging")
        merged = rebuilt = 0
        for group in groups:
            keep = survivor(conn, group)
            for other in group:
                if other == keep:
                    continue
                try:
                    repository.merge_entities(
                        conn, other, keep, args.entity_type, args.reviewed_by,
                        "shares a strong identity key (reblock)", None,
                    )
                    merged += 1
                except Exception as exc:
                    conn.rollback()
                    print(f"  {other} -> {keep}: {type(exc).__name__}: {exc}")
                    continue
            # The survivor now has the absorbed entity's records behind it, so
            # its golden values are stale until rebuilt -- in the same
            # transaction, so an entity is never left merged but unrebuilt.
            try:
                build_entity(conn, keep, args.entity_type)
                rebuilt += 1
                conn.commit()
            except Exception as exc:
                conn.rollback()
                print(f"  rebuild {keep}: {type(exc).__name__}: {exc}")

        print(f"\nmerged {merged} entit(ies) into {rebuilt} survivor(s)")
        print("Re-run resolution for records still awaiting a decision:")
        print("  docker compose run --rm resolution resolve run --batch-id <id>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
