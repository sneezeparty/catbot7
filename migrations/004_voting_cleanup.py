"""Rename user.vote_streak/max_vote_streak to daily_catch_streak/max_daily_streak.

Voting is permanently retired on this self-hosted instance. The vote_streak
column had been repurposed as a daily catch streak, which collided with the
per-catch `profile.catch_streak` counter and made every label confusing.
This migration honestly renames the columns.

Idempotent: skips work if migrations/004.done already exists. Per-step gated
so partial completion is recoverable by re-running.

Bot MUST be stopped before running this. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/004_voting_cleanup.py
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

MARKER = REPO_ROOT / "migrations" / "004.done"
LOGFILE = REPO_ROOT / "migrations" / "004.log"


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
    log("starting migration 004_voting_cleanup")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        # Add new columns (idempotent).
        if not await column_exists(conn, "user", "daily_catch_streak"):
            log("adding user.daily_catch_streak")
            await conn.execute(
                'ALTER TABLE "user" ADD COLUMN daily_catch_streak integer DEFAULT 0 NOT NULL'
            )
        else:
            log("user.daily_catch_streak already exists, skipping ADD")

        if not await column_exists(conn, "user", "max_daily_streak"):
            log("adding user.max_daily_streak")
            await conn.execute(
                'ALTER TABLE "user" ADD COLUMN max_daily_streak integer DEFAULT 0 NOT NULL'
            )
        else:
            log("user.max_daily_streak already exists, skipping ADD")

        # Backfill from the legacy columns if they still exist.
        if await column_exists(conn, "user", "vote_streak"):
            log("copying vote_streak → daily_catch_streak")
            result = await conn.execute(
                'UPDATE "user" SET daily_catch_streak = vote_streak WHERE daily_catch_streak = 0 AND vote_streak <> 0'
            )
            log(f"  {result}")
        if await column_exists(conn, "user", "max_vote_streak"):
            log("copying max_vote_streak → max_daily_streak")
            result = await conn.execute(
                'UPDATE "user" SET max_daily_streak = max_vote_streak WHERE max_daily_streak = 0 AND max_vote_streak <> 0'
            )
            log(f"  {result}")

        # Drop the legacy columns.
        if await column_exists(conn, "user", "vote_streak"):
            log("dropping user.vote_streak")
            await conn.execute('ALTER TABLE "user" DROP COLUMN vote_streak')
        if await column_exists(conn, "user", "max_vote_streak"):
            log("dropping user.max_vote_streak")
            await conn.execute('ALTER TABLE "user" DROP COLUMN max_vote_streak')

        MARKER.write_text(
            json.dumps({"completed_at": time.time()}, indent=2),
            encoding="utf-8",
        )
        log(f"DONE. marker written to {MARKER}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    if not os.environ.get("psql_password"):
        print("ERROR: psql_password env var required (see bot.py)", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main()))
