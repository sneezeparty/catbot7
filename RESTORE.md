# Restoring a PostgreSQL backup

The daily backup script (`scripts/backup-to-gdrive.sh`, scheduled via
`~/Library/LaunchAgents/com.catbot.dbbackup.plist`) writes a custom-format
`pg_dump` archive each day to
`~/Library/CloudStorage/GoogleDrive-mdgilford@gmail.com/My Drive/cat-bot-backups/`
with a 30-day retention.

This document is the operator runbook for going the other way — turning one
of those `.dump` files back into a working database. **Stop the bot before
running any of the in-place commands.** The dry-run path is safe to run
anytime.

## Quick paths

| Situation | Section |
|---|---|
| Yesterday's data was fine, today's is broken — roll back. | A. In-place rollback |
| Lost the laptop / starting fresh on a new machine. | B. Fresh-machine rebuild |
| About to do either, want to confirm the dump is intact first. | C. Dry-run into a scratch DB |

The dump is custom format (`-Fc`), so all restores go through `pg_restore`
running **inside the `cat-bot-pg` podman container** — that way you don't
need a host-side `pg_restore` whose version matches the server's Postgres
17.

## A. In-place rollback

The common case. The container is healthy, but the data isn't, and you want
to overwrite the `cat_bot` database with a known-good dump.

1. **Stop the bot.**

   ```sh
   pkill -f "python bot.py"
   ```

2. **Pick a dump from the Drive folder.**

   ```sh
   ls -lt "/Users/matthewgilford/Library/CloudStorage/GoogleDrive-mdgilford@gmail.com/My Drive/cat-bot-backups/"
   ```

3. **Restore over the existing database.** `--clean --if-exists` drops
   existing objects before recreating them — safe whether the target is
   populated or empty.

   ```sh
   DUMP="/Users/matthewgilford/Library/CloudStorage/GoogleDrive-mdgilford@gmail.com/My Drive/cat-bot-backups/cat_bot_YYYY-MM-DD_HHMMSS.dump"
   PGPASS="$(podman inspect cat-bot-pg --format '{{range .Config.Env}}{{println .}}{{end}}' | grep ^POSTGRES_PASSWORD= | cut -d= -f2-)"

   podman exec -i -e PGPASSWORD="$PGPASS" cat-bot-pg \
     pg_restore --clean --if-exists --no-owner --no-acl \
                -U cat_bot -d cat_bot < "$DUMP"
   ```

4. **Sanity-check** row counts before letting traffic back in:

   ```sh
   podman exec -e PGPASSWORD="$PGPASS" cat-bot-pg \
     psql -U cat_bot -d cat_bot -c \
     "SELECT (SELECT COUNT(*) FROM server) AS servers,
             (SELECT COUNT(*) FROM profile) AS profiles,
             (SELECT COUNT(*) FROM \"user\")  AS users;"
   ```

5. **Restart the bot** (`python bot.py` under whatever supervisor you
   normally use).

## B. Fresh-machine rebuild

You lost the laptop, the container, or both. The dump is the only thing
left.

1. **Install prereqs.** podman, Python 3.11+, and Google Drive for Desktop
   (sign in so `My Drive/cat-bot-backups/` syncs down). Clone this repo and
   `pip install -r requirements.txt`. Recreate `.env` with at least
   `TOKEN=...` and `psql_password=...`.

2. **Bring up the container.** Edit `PGPASS=` at the top of `setup-pg.sh`
   to the password the dump was taken with — i.e. the password the lost
   container had — then:

   ```sh
   bash setup-pg.sh
   ```

   This boots `cat-bot-pg` and applies `schema.sql` to give it the right
   shape. The restore in the next step will overwrite that shape with the
   dump's content.

3. **Restore the latest dump.** Same command as Section A step 3:

   ```sh
   DUMP="/Users/matthewgilford/Library/CloudStorage/GoogleDrive-mdgilford@gmail.com/My Drive/cat-bot-backups/$(ls -1t "/Users/matthewgilford/Library/CloudStorage/GoogleDrive-mdgilford@gmail.com/My Drive/cat-bot-backups/" | head -1)"
   PGPASS="$(podman inspect cat-bot-pg --format '{{range .Config.Env}}{{println .}}{{end}}' | grep ^POSTGRES_PASSWORD= | cut -d= -f2-)"

   podman exec -i -e PGPASSWORD="$PGPASS" cat-bot-pg \
     pg_restore --clean --if-exists --no-owner --no-acl \
                -U cat_bot -d cat_bot < "$DUMP"
   ```

4. **Migrations.** The dump captured post-migration schema, and
   `migrations/NNN.done` markers are committed in git, so a freshly cloned
   working tree usually already has a `.done` next to every migration the
   dump covers — meaning **zero replay is needed**. The only time you run
   anything from `migrations/` is when the codebase has migrations newer
   than the dump (so their `.done` is missing). In that case, run only
   those, in order, per the rules in `CLAUDE.md` (bot stopped, scripts
   idempotent).

5. **Start the bot.**

## C. Dry-run into a scratch database

Recommended before any real restore. Verifies the dump is intact and
restorable end-to-end without touching `cat_bot`.

```sh
DUMP="/Users/matthewgilford/Library/CloudStorage/GoogleDrive-mdgilford@gmail.com/My Drive/cat-bot-backups/cat_bot_YYYY-MM-DD_HHMMSS.dump"
PGPASS="$(podman inspect cat-bot-pg --format '{{range .Config.Env}}{{println .}}{{end}}' | grep ^POSTGRES_PASSWORD= | cut -d= -f2-)"

podman exec -e PGPASSWORD="$PGPASS" cat-bot-pg createdb -U cat_bot cat_bot_scratch
podman exec -i -e PGPASSWORD="$PGPASS" cat-bot-pg \
  pg_restore --no-owner --no-acl -U cat_bot -d cat_bot_scratch < "$DUMP"
podman exec -e PGPASSWORD="$PGPASS" cat-bot-pg psql -U cat_bot -d cat_bot_scratch -c "\dt"
podman exec -e PGPASSWORD="$PGPASS" cat-bot-pg psql -U cat_bot -d cat_bot_scratch -c \
  "SELECT (SELECT COUNT(*) FROM server) AS servers,
          (SELECT COUNT(*) FROM profile) AS profiles,
          (SELECT COUNT(*) FROM \"user\")  AS users;"
podman exec -e PGPASSWORD="$PGPASS" cat-bot-pg dropdb -U cat_bot cat_bot_scratch
```

If `\dt` lists every table you expect (the schema currently has 14 tables
in `public`) and the row counts look plausible, the dump is healthy. The
`cat_bot` database is never touched on this path.

## Flag reference

- `-Fc` — custom format. Produced by the backup script, required input for
  `pg_restore`.
- `--clean` — emit `DROP` for each object before recreating it.
- `--if-exists` — wraps those drops in `IF EXISTS`; safe on an empty target.
- `--no-owner` — don't try to reassign object owner. Avoids errors if the
  target role differs from the dump's.
- `--no-acl` — skip `GRANT` / `REVOKE` statements from the dump.

## When not to use this document

- **Per-table** restores (e.g. just rolling back `profile`). `pg_restore`
  supports `-t TABLE` for that, but a partial restore can leave foreign
  keys and the cat-spawn state inconsistent across tables. Prefer a full
  restore and accept the rollback window.
- **Point-in-time** recovery between dumps. Not supported — the daily
  cadence is the granularity; anything finer would need WAL archiving.
