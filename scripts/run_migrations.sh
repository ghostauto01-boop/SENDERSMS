#!/usr/bin/env bash
# Run the Alembic migrations against the configured database.
#
#   DATABASE_URL=postgresql://... ./scripts/run_migrations.sh
#
# The same thing happens automatically at startup (create_all + schema
# repair); this script is the deliberate, reviewable upgrade path and the one
# to run when the startup log reports a column it could not add.
set -euo pipefail
cd "$(dirname "$0")/../backend"
exec python -m alembic upgrade head "$@"
