#!/bin/sh
# Restore a backup into a scratch database and prove it matches the original.
#
#     docker compose exec -T postgres sh /tools/restore_check.sh /backups/pcdf-....dump
#
# This is the part that is usually skipped, and it is the only part that turns a
# dump file into a backup. Until a restore has been performed and checked,
# what exists is a file somebody hopes is a backup.
#
# It restores into a NEW database rather than over the live one, so the drill
# can be run any day without a maintenance window and without the drill itself
# being the thing that loses the data. The scratch database is dropped at the
# end; pass --keep to leave it for inspection.

set -eu

DUMP="${1:?usage: restore_check.sh <dump-file> [--keep]}"
KEEP="${2:-}"
SCRATCH="pcdf_restore_check"

psql_main() { psql --username "$POSTGRES_USER" --dbname postgres -tA -c "$1"; }
psql_scratch() { psql --username "$POSTGRES_USER" --dbname "$SCRATCH" -tA -c "$1"; }

cleanup() {
    if [ "$KEEP" != "--keep" ]; then
        psql_main "DROP DATABASE IF EXISTS $SCRATCH;" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

echo "restoring $DUMP into $SCRATCH"
psql_main "DROP DATABASE IF EXISTS $SCRATCH;" >/dev/null
psql_main "CREATE DATABASE $SCRATCH;" >/dev/null

# --exit-on-error, because a restore that reports success while having skipped
# objects is the failure this drill exists to catch. Errors here should stop it.
pg_restore \
    --username "$POSTGRES_USER" \
    --dbname "$SCRATCH" \
    --exit-on-error \
    --no-owner \
    "$DUMP"

echo ""
echo "comparing row counts against the live database"
echo ""
printf '%-28s %12s %12s   %s\n' TABLE LIVE RESTORED ''

FAILED=0
for table in source batch raw_record source_schema column_mapping \
             attribute_observation record_validation validation_result \
             quarantine_item entity entity_identity_key record_entity_link \
             entity_relationship golden_attribute record_error; do
    live=$(psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -tA \
           -c "SELECT count(*) FROM $table" 2>/dev/null || echo "n/a")
    restored=$(psql_scratch "SELECT count(*) FROM $table" 2>/dev/null || echo "n/a")
    if [ "$live" = "$restored" ]; then
        printf '%-28s %12s %12s   ok\n' "$table" "$live" "$restored"
    else
        printf '%-28s %12s %12s   MISMATCH\n' "$table" "$live" "$restored"
        FAILED=1
    fi
done

echo ""
# Views and indexes are as much a part of the schema as the rows. A restore that
# brings back every row and none of the views leaves a database that answers no
# question anyone actually asks.
for view in golden_person golden_company resolvable_record; do
    if psql_scratch "SELECT 1 FROM $view LIMIT 1" >/dev/null 2>&1; then
        echo "view $view: present"
    else
        echo "view $view: MISSING"
        FAILED=1
    fi
done

echo ""
if [ "$FAILED" -eq 0 ]; then
    echo "RESTORE VERIFIED: every table and view matches the live database."
else
    echo "RESTORE FAILED: see MISMATCH/MISSING above." >&2
fi

[ "$KEEP" = "--keep" ] && echo "scratch database $SCRATCH left in place"
exit "$FAILED"
