#!/usr/bin/env bash
set -euo pipefail

# ---- edit this line, then run: bash setup-pg.sh ----
PGPASS='CHANGEME'
# ----------------------------------------------------

if [[ "$PGPASS" == "CHANGEME" ]]; then
  echo "edit PGPASS in this script first" >&2
  exit 1
fi

podman network create cat-bot-net 2>/dev/null || true
podman volume create cat-bot-pgdata 2>/dev/null || true

podman run -d \
  --name cat-bot-pg \
  --network cat-bot-net \
  --restart unless-stopped \
  -p 127.0.0.1:5433:5432 \
  -v cat-bot-pgdata:/var/lib/postgresql/data \
  -e POSTGRES_USER=cat_bot \
  -e POSTGRES_DB=cat_bot \
  -e POSTGRES_PASSWORD="$PGPASS" \
  docker.io/library/postgres:17

echo "waiting for postgres to be ready..."
for i in {1..30}; do
  if podman exec cat-bot-pg pg_isready -U cat_bot -d cat_bot >/dev/null 2>&1; then
    echo "postgres is up"
    break
  fi
  sleep 1
done

podman run --rm -i \
  --network cat-bot-net \
  -e PGPASSWORD="$PGPASS" \
  -v "$(pwd)/schema.sql:/schema.sql:ro,Z" \
  docker.io/library/postgres:17 \
  psql -h cat-bot-pg -U cat_bot -d cat_bot -v ON_ERROR_STOP=1 -f /schema.sql

echo
echo "tables:"
podman run --rm -i \
  --network cat-bot-net \
  -e PGPASSWORD="$PGPASS" \
  docker.io/library/postgres:17 \
  psql -h cat-bot-pg -U cat_bot -d cat_bot -c '\dt'
