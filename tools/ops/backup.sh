#!/bin/sh
# Take a backup of the database, and verify it can be read back.
#
#     docker compose exec -T postgres sh /tools/ops/backup.sh            (into /backups)
#     docker compose exec -T postgres sh /tools/ops/backup.sh /some/dir
#
# Custom format (-Fc), not plain SQL, for three reasons: it compresses, it can
# be restored selectively with pg_restore, and pg_restore --list can read its
# table of contents without restoring anything -- which is what makes the
# verification below possible at all.
#
# A backup nobody has read back is not a backup. This script therefore refuses
# to report success until pg_restore has parsed the file it just wrote, because
# the failure mode that matters is not "the backup did not run", which is
# noisy, but "the backup ran and produced something unrestorable", which is
# silent until the day it is needed.

set -eu

DIR="${1:-/backups}"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
FILE="$DIR/pcdf-$STAMP.dump"

mkdir -p "$DIR"

echo "dumping $POSTGRES_DB -> $FILE"
pg_dump \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --format=custom \
    --compress=6 \
    --file "$FILE"

SIZE=$(du -h "$FILE" | cut -f1)

# Read it back. Not a checksum -- a checksum proves the bytes survived the disk,
# which was never the doubt. This proves pg_restore can parse the archive and
# find the objects it claims to contain.
echo "verifying archive is readable"
TABLES=$(pg_restore --list "$FILE" | grep -c 'TABLE DATA' || true)
if [ "$TABLES" -lt 1 ]; then
    echo "FAILED: archive contains no table data" >&2
    exit 1
fi

echo "ok: $FILE ($SIZE, $TABLES tables with data)"
echo ""
echo "Restore into a scratch database and compare before trusting it:"
echo "    sh /tools/ops/restore_check.sh $FILE"
