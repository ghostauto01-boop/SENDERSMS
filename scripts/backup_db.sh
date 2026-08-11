#!/usr/bin/env bash
#
# Back up the live Postgres database (contacts, conversations, messages,
# campaigns, settings) to a timestamped file in ./backups/.
#
# The code lives on GitHub and can always be restored from there. This data
# cannot: if the Neon database is deleted, expires, or is wiped by a bad
# migration, everything your customers ever sent you is gone. Run this before
# any risky change, and on a schedule you can live with losing.
#
# Usage:
#   export DATABASE_URL='postgresql://user:pass@host/dbname'
#   ./scripts/backup_db.sh
#
# Restore with scripts/restore_db.sh (read its warning first).

set -euo pipefail

cd "$(dirname "$0")/.."
BACKUP_DIR="backups"
STAMP="$(date -u +%Y%m%d-%H%M%SZ)"
OUT="${BACKUP_DIR}/sendersms-${STAMP}.dump"

if [ -z "${DATABASE_URL:-}" ]; then
  cat >&2 <<'EOF'
ERROR: DATABASE_URL is not set.

Copy it from Render (sendsms-api -> Environment -> DATABASE_URL) or from the
Neon dashboard, then run:

  export DATABASE_URL='postgresql://user:pass@host/dbname'
  ./scripts/backup_db.sh
EOF
  exit 1
fi

if ! command -v pg_dump >/dev/null 2>&1; then
  cat >&2 <<'EOF'
ERROR: pg_dump is not installed.

  macOS:          brew install libpq && brew link --force libpq
  Ubuntu/Debian:  sudo apt install postgresql-client
  Windows:        install "PostgreSQL" and use the SQL Shell, or use WSL

Alternatively, Neon can take a snapshot for you from its dashboard
(Branches -> create a branch = an instant point-in-time copy).
EOF
  exit 1
fi

# The app rewrites postgres:// -> postgresql+asyncpg:// for SQLAlchemy, and
# appends asyncpg-incompatible query args. pg_dump wants neither: normalise the
# scheme and strip everything from '?' onward (sslmode, channel_binding, ...).
CLEAN_URL="${DATABASE_URL%%\?*}"
CLEAN_URL="${CLEAN_URL/postgresql+asyncpg:\/\//postgresql://}"
CLEAN_URL="${CLEAN_URL/postgres:\/\//postgresql://}"

mkdir -p "$BACKUP_DIR"

echo "Backing up database -> ${OUT}"
# -Fc = compressed custom format, restorable with pg_restore and far smaller
# than plain SQL. --no-owner/--no-acl so it restores cleanly into a fresh
# database owned by a different role (e.g. a new Neon project).
pg_dump "$CLEAN_URL" -Fc --no-owner --no-acl -f "$OUT"

SIZE="$(du -h "$OUT" | cut -f1)"
echo "OK  ${OUT}  (${SIZE})"

# Keep the 10 most recent backups; prune the rest so the folder cannot grow
# without bound. Adjust KEEP if you want a longer history.
KEEP=10
COUNT="$(ls -1t "${BACKUP_DIR}"/sendersms-*.dump 2>/dev/null | wc -l | tr -d ' ')"
if [ "$COUNT" -gt "$KEEP" ]; then
  ls -1t "${BACKUP_DIR}"/sendersms-*.dump | tail -n +$((KEEP + 1)) | while read -r old; do
    echo "pruning old backup: ${old}"
    rm -f "$old"
  done
fi

cat <<EOF

Done.
IMPORTANT: backups/ is gitignored, so this file exists only on this machine.
A backup that lives on the same disk as nothing else is still one accident away
from gone. Copy it somewhere else -- Google Drive, Dropbox, an external disk:

  ${OUT}
EOF
