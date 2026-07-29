#!/usr/bin/env bash
# Daily PostgreSQL backup -> Google Drive (cat-bot-pg podman container).
# Mirrors main.py's embedded pg_dump invocation (-Fc -Z 9) so dumps are
# pg_restore-compatible. 30-day retention. Independent of the dormant
# in-bot backup mechanism gated on config.BACKUP_ID.
set -uo pipefail

PROJECT_DIR="/Users/matthewgilford/catbot/cat-bot"
DEST_DIR="/Users/matthewgilford/Library/CloudStorage/GoogleDrive-mdgilford@gmail.com/My Drive/cat-bot-backups"
LOG_FILE="$PROJECT_DIR/scripts/backup-to-gdrive.log"
CONTAINER="cat-bot-pg"
RETENTION_DAYS=30

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"
}

die() {
  log "ERROR: $*"
  exit 1
}

# Resolve container password. Precedence: env var -> .env -> the live
# container's POSTGRES_PASSWORD env (the value the container was started
# with). The container env is the canonical truth: setup-pg.sh's PGPASS
# is just the seed that was passed at `podman run` time, and the file is
# often left on the CHANGEME placeholder after the container is created.
resolve_password() {
  if [[ -n "${psql_password:-}" ]]; then
    printf '%s' "$psql_password"
    return
  fi
  if [[ -f "$PROJECT_DIR/.env" ]]; then
    local v
    v="$(grep -E '^psql_password=' "$PROJECT_DIR/.env" | head -1 | cut -d= -f2- | sed -e 's/^["'"'"']//' -e 's/["'"'"']$//')"
    if [[ -n "$v" ]]; then
      printf '%s' "$v"
      return
    fi
  fi
  local v
  v="$(podman inspect "$CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
        | grep -E '^POSTGRES_PASSWORD=' | head -1 | cut -d= -f2-)"
  if [[ -n "$v" ]]; then
    printf '%s' "$v"
    return
  fi
  return 1
}

mkdir -p "$DEST_DIR" || die "could not create $DEST_DIR"
mkdir -p "$(dirname "$LOG_FILE")"

log "=== backup start ==="

# Preflight: container must exist and be running.
if ! podman container exists "$CONTAINER" 2>/dev/null; then
  die "container $CONTAINER does not exist"
fi
RUNNING="$(podman container inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo false)"
if [[ "$RUNNING" != "true" ]]; then
  die "container $CONTAINER is not running (State.Running=$RUNNING)"
fi

PGPASS="$(resolve_password)" || die "could not resolve postgres password from env/.env/setup-pg.sh"

TS="$(date '+%Y-%m-%d_%H%M%S')"
DUMP_PATH="$DEST_DIR/cat_bot_${TS}.dump"
TMP_PATH="${DUMP_PATH}.partial"

# pg_dump runs inside the container, streams custom-format dump to stdout.
# Write to .partial first so an interrupted run never leaves a corrupt file
# that looks like a complete dump to retention or pg_restore.
if ! podman exec -e PGPASSWORD="$PGPASS" "$CONTAINER" \
       pg_dump -U cat_bot -Fc -Z 9 cat_bot > "$TMP_PATH" 2>>"$LOG_FILE"; then
  rm -f "$TMP_PATH"
  die "pg_dump failed"
fi

if [[ ! -s "$TMP_PATH" ]]; then
  rm -f "$TMP_PATH"
  die "pg_dump produced empty file"
fi

mv "$TMP_PATH" "$DUMP_PATH" || die "rename to $DUMP_PATH failed"

SIZE="$(stat -f '%z' "$DUMP_PATH" 2>/dev/null || echo '?')"
log "wrote $DUMP_PATH ($SIZE bytes)"

# 30-day retention by file mtime.
DELETED=0
while IFS= read -r f; do
  log "pruned $f"
  DELETED=$((DELETED + 1))
done < <(find "$DEST_DIR" -name 'cat_bot_*.dump' -type f -mtime +${RETENTION_DAYS} -print -delete 2>/dev/null)
log "retention: pruned $DELETED file(s) older than ${RETENTION_DAYS}d"

log "=== backup ok ==="
