# Cat Bot - A Discord bot about catching cats.
# Copyright (C) 2026 Lia Milenakos & Cat Bot Contributors
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

"""Respect (mafia decay) meter and prism craft counter.

Adds three columns:

  - profile.respect            INT      DEFAULT 50   — current Respect with the
        Cat Mafia (0..100). Ticks down passively (-1/hr) and gets refilled by
        completing jobs. When it hits 0, catnip XP drains; if XP runs out the
        catnip_level drops one (floored at Lv4 so Tier-2 jobs always remain a
        recovery path). Discount continues to track current catnip_level, so
        losing a level also loses the discount that came with it.

  - profile.respect_last_tick  BIGINT   DEFAULT 0    — UNIX timestamp of the
        last time _respect_settle ran on this profile. 0 means "never ticked"
        and the helper skips decay until the first real interaction (so newly
        migrated profiles aren't punished retroactively).

  - profile.prisms_crafted     INT      DEFAULT 0   — lifetime count of prism
        crafts attributable to this profile, used to scale the per-player
        prism coin tax: cost = 5_000 * 2^prisms_crafted, capped at 320_000.
        Backfilled below from the prism table (counts rows where this profile
        is the creator).

Idempotent (per-column gated, and the backfill SQL is idempotent at the
profile level because it overwrites with the same query). Bot MUST be
stopped before running. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/018_respect_and_prism_count.py
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

MARKER = REPO_ROOT / "migrations" / "018.done"
LOGFILE = REPO_ROOT / "migrations" / "018.log"


# (table, column, type, default, not_null)
COLUMNS: list[tuple[str, str, str, str, bool]] = [
    ("profile", "respect", "integer", "50", True),
    ("profile", "respect_last_tick", "bigint", "0", True),
    ("profile", "prisms_crafted", "integer", "0", True),
]

# Backfill: set prisms_crafted to the count of prism rows whose creator
# matches this profile's (user_id, guild_id). The current craft path stamps
# Prism.creator = the player who paid the cat cost, so this is the right
# attribution for the new coin tax (which should price the NEXT craft based
# on how many the player has already paid for on this server).
BACKFILL_PRISMS = """
    UPDATE profile p
       SET prisms_crafted = sub.cnt
      FROM (
          SELECT creator AS user_id, guild_id, COUNT(*) AS cnt
            FROM prism
           GROUP BY creator, guild_id
      ) sub
     WHERE sub.user_id = p.user_id
       AND sub.guild_id = p.guild_id
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
    log("starting migration 018_respect_and_prism_count")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        added_prisms_crafted = False
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
            if col == "prisms_crafted":
                added_prisms_crafted = True

        # Backfill is safe to run unconditionally (idempotent), but only do
        # the work when the column was actually just added — re-running on an
        # already-populated column would clobber later in-flight craft counts.
        if added_prisms_crafted:
            log("backfilling prisms_crafted from existing prism rows")
            result = await conn.execute(BACKFILL_PRISMS)
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
