# Cat Bot - A Discord bot about catching cats.
# Copyright (C) 2026 Lia Milenakos & Cat Bot Contributors
# Copyright (C) 2026 sneezeparty
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Job-grace timestamp for the mafia-level decay shield.

Adds one column:

  - profile.last_job_time  BIGINT  DEFAULT 0  — UNIX timestamp of the player's
        most recent committed /jobs (any outcome). While a job was committed
        within CATNIP_JOB_GRACE_SECONDS (24h), the mafia (catnip) level is
        shielded from BOTH decay systems: the catnip bounty deadline won't drop
        it, and respect decay won't strip a level. Lets a player who does /jobs
        daily engage catnip less often. 0 = "no job on record" (no shield).

Backfilled from the most recent resolved job per (user, guild) so existing
active players aren't instantly unprotected the moment this ships.

Idempotent (column add is gated on information_schema, the backfill only runs
when the column was just added and overwrites with the same query). Bot MUST be
stopped before running. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/027_last_job_time.py
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

MARKER = REPO_ROOT / "migrations" / "027.done"
LOGFILE = REPO_ROOT / "migrations" / "027.log"


# (table, column, type, default, not_null)
COLUMNS: list[tuple[str, str, str, str, bool]] = [
    ("profile", "last_job_time", "bigint", "0", True),
]

# Backfill: seed last_job_time from each profile's most recent resolved job, so
# players mid-streak keep their shield across the deploy instead of resetting to
# "no job on record". Idempotent at the profile level (same query overwrites).
BACKFILL_LAST_JOB = """
    UPDATE profile p
       SET last_job_time = sub.maxr
      FROM (
          SELECT user_id, guild_id, MAX(resolved_at) AS maxr
            FROM jobinstance
           WHERE state = 'resolved'
           GROUP BY user_id, guild_id
      ) sub
     WHERE sub.user_id = p.user_id
       AND sub.guild_id = p.guild_id
       AND sub.maxr IS NOT NULL
"""


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

    LOGFILE.write_text("", encoding="utf-8")
    log("starting migration 027_last_job_time")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        added_last_job_time = False
        for table, col, coltype, default, not_null in COLUMNS:
            if await column_exists(conn, table, col):
                log(f"{table}.{col} already exists, skipping ADD")
                continue
            null_clause = " NOT NULL" if not_null else ""
            sql = (
                f"ALTER TABLE {table} ADD COLUMN {col} {coltype} "
                f"DEFAULT {default}{null_clause}"
            )
            log(f"adding {table}.{col}")
            await conn.execute(sql)
            if col == "last_job_time":
                added_last_job_time = True

        # Only backfill when the column was just added — re-running on an
        # already-populated column would clobber in-flight job timestamps.
        if added_last_job_time:
            log("backfilling last_job_time from most recent resolved jobs")
            result = await conn.execute(BACKFILL_LAST_JOB)
            log(f"backfill result: {result}")

        MARKER.write_text(
            json.dumps({"completed_at": time.time()}, indent=2),
            encoding="utf-8",
        )
        log(f"DONE. marker at {MARKER}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    if not os.environ.get("psql_password"):
        print("ERROR: psql_password env var required (see bot.py)", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main()))
