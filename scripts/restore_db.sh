#!/usr/bin/env bash
#
# Restore a backup created by scripts/backup_db.sh.
#
# !! THIS OVERWRITES DATA IN THE TARGET DATABASE !!
#
# Point it at a NEW / empty database first and confirm the row counts look
# right. Only restore over a live database when you have already accepted that
# its current contents are lost.
#
# Usage:
#   export DATABASE_URL='postgresql://user:pass@host/dbname'   # the TARGET
#   ./scripts/restore_db.sh backups/sendersms-20260811-120000Z.dump

set -euo pipefail

cd "$(dirname "$0")/.."

DUMP="${1:-}"
if [ -z "$DUMP" ]; then
  echo "Usage: ./scripts/restore_db.sh <backup-file.dump>" >&2
  echo >&2
  echo "Available backups:" >&2
  ls -1t backups/sendersms-*.dump 2>/dev/null >&2 || echo "  (none in backups/)" >&2
  exit 1
fi

[ -f "$DUMP" ] || { echo "ERROR: no such file: $DUMP" >&2; exit 1; }

if [ -z "${DATABASE_URL:-}" ]; then
  echo "ERROR: DATABASE_URL is not set. It must point at the database you want" >&2
  echo "to restore INTO (not necessarily the one you backed up from)." >&2
  exit 1
fi

command -v pg_restore >/dev/null 2>&1 || {
  echo "ERROR: pg_restore is not installed (see scripts/backup_db.sh for install hints)." >&2
  exit 1
}

CLEAN_URL="${DATABASE_URL%%\?*}"
CLEAN_URL="${CLEAN_URL/postgresql+asyncpg:\/\//postgresql://}"
CLEAN_URL="${CLEAN_URL/postgres:\/\//postgresql://}"

# Show the operator which host they are about to overwrite, with the password
# redacted, so a copy-pasted URL for the wrong environment is caught here.
SAFE_TARGET="$(printf '%s' "$CLEAN_URL" | sed -E 's#//[^:]+:[^@]+@#//***:***@#')"

cat <<EOF
About to restore
  from : ${DUMP}
  into : ${SAFE_TARGET}

This overwrites matching tables in the target database.
EOF

read -r -p "Type 'restore' to continue: " CONFIRM
[ "$CONFIRM" = "restore" ] || { echo "Aborted."; exit 1; }

# --clean --if-exists drops existing objects before recreating them, so a
# restore over a populated database replaces rather than collides.
pg_restore --no-owner --no-acl --clean --if-exists -d "$CLEAN_URL" "$DUMP"

echo
echo "Restore complete. Sanity-check before trusting it:"
echo "  psql '<your-url>' -c 'select count(*) from contacts;'"
echo "  psql '<your-url>' -c 'select count(*) from messages;'"
