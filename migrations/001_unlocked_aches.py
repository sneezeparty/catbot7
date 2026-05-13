"""Backfill profile.unlocked_aches JSONB array from per-ach boolean columns.

Idempotent: skips work if migrations/001.done already exists.
Batched: works in chunks of BATCH_SIZE rows to avoid long-running locks.

Bot MUST be stopped before running this. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/001_unlocked_aches.py

The migration:
  1. Adds the `unlocked_aches` column if it doesn't already exist.
  2. Reads every ach ID from config/aches.json.
  3. For each ach, walks profile in batches of 10k and appends the ID to
     unlocked_aches for any row where the legacy boolean column is true and
     the ID isn't already in the array.
  4. Writes migrations/001.done with a per-ach count summary.

The old boolean columns are LEFT IN PLACE so the bot keeps working during a
rollout. A separate cleanup script can drop them once the new code is stable.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import asyncpg  # noqa: E402

import config  # noqa: E402

BATCH_SIZE = 10_000
MARKER = REPO_ROOT / "migrations" / "001.done"
LOGFILE = REPO_ROOT / "migrations" / "001.log"


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


async def column_exists(conn: asyncpg.Connection, table: str, column: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2",
        table,
        column,
    )
    return row is not None


async def main() -> int:
    if MARKER.exists():
        log(f"marker {MARKER} exists — migration already applied. Delete it to re-run.")
        return 0

    LOGFILE.write_text("", encoding="utf-8")  # truncate
    log("starting migration 001_unlocked_aches")

    aches_path = REPO_ROOT / "config" / "aches.json"
    ach_ids: list[str] = list(json.loads(aches_path.read_text(encoding="utf-8")).keys())
    log(f"loaded {len(ach_ids)} ach IDs from {aches_path.name}")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        # Step 1: add the column.
        if not await column_exists(conn, "profile", "unlocked_aches"):
            log("adding profile.unlocked_aches column")
            await conn.execute(
                "ALTER TABLE profile ADD COLUMN unlocked_aches jsonb DEFAULT '[]'::jsonb NOT NULL"
            )
        else:
            log("profile.unlocked_aches already exists, skipping ADD COLUMN")

        # Step 2: for each ach, append it to unlocked_aches where the legacy
        # boolean column is true and it's not already in the array.
        summary: dict[str, int] = {}
        for ach_id in ach_ids:
            if not await column_exists(conn, "profile", ach_id):
                log(f"skip {ach_id} (legacy column missing)")
                continue
            total_updated = 0
            while True:
                # NOTE: ach_id is interpolated as an identifier — comes from
                # aches.json, NOT user input. The webui never writes new
                # legacy columns, so this stays safe.
                quoted = '"' + ach_id.replace('"', '""') + '"'
                result = await conn.execute(
                    f"""
                    UPDATE profile p
                    SET unlocked_aches = unlocked_aches || to_jsonb($1::text)
                    WHERE p.id IN (
                        SELECT id FROM profile
                        WHERE {quoted} = TRUE
                          AND NOT (unlocked_aches @> to_jsonb($1::text))
                        LIMIT $2
                    )
                    """,
                    ach_id,
                    BATCH_SIZE,
                )
                # asyncpg returns "UPDATE n"
                n = int(result.split()[-1]) if result.startswith("UPDATE ") else 0
                total_updated += n
                if n < BATCH_SIZE:
                    break
                log(f"  {ach_id}: +{n} (running total {total_updated})")
            summary[ach_id] = total_updated
            if total_updated:
                log(f"{ach_id}: backfilled {total_updated} rows")

        MARKER.write_text(
            json.dumps({"completed_at": time.time(), "summary": summary}, indent=2),
            encoding="utf-8",
        )
        log(f"DONE. summary in {MARKER}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    if not os.environ.get("psql_password"):
        print("ERROR: psql_password env var required (see bot.py)", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main()))
