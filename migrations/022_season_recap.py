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

"""Per-season recap leaderboard support columns on the profile table.

Adds six columns to the profile table:

  - profile.coins_earned         BIGINT  DEFAULT 0  -- lifetime gross coins gained
  - profile.roulette_coins_won   BIGINT  DEFAULT 0  -- lifetime coins won at /roulette
  - profile.roulette_coins_bet   BIGINT  DEFAULT 0  -- lifetime coins bet at /roulette
  - profile.stock_coins_earned   BIGINT  DEFAULT 0  -- lifetime coins received from stock sells/dividends
  - profile.stock_coins_spent    BIGINT  DEFAULT 0  -- lifetime coins spent on stock buys (net of cancel refunds)
  - profile.season_stat_baseline JSONB   DEFAULT '{}'  -- lifetime-counter snapshot taken at the player's current-season start

The first five are lifetime accumulators (never reset). The season recap turns
them into "this season" totals by subtracting season_stat_baseline (the values
captured the last time that player rolled into a new season). For a player who
has never rolled over under this code the baseline is '{}' (== 0 for every key),
so the season total equals the full lifetime — correct for Season 1, which began
when this instance launched.

The bot reads every one of these columns defensively (a probe-read guarded by
try/except KeyError, so a missing column is a silent no-op), so the bot will run
without this migration — but the recap's coins-earned / gambling / stock
categories and future-season baselining won't work until it's applied.

No backfill needed — the defaults (0 / '{}') are correct for every existing row.

Idempotent (per-column gated). Bot MUST be stopped before running. Run with
the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/022_season_recap.py
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

MARKER = REPO_ROOT / "migrations" / "022.done"
LOGFILE = REPO_ROOT / "migrations" / "022.log"


# (table, column, type, default, not_null)
COLUMNS: list[tuple[str, str, str, str, bool]] = [
    ("profile", "coins_earned", "bigint", "0", True),
    ("profile", "roulette_coins_won", "bigint", "0", True),
    ("profile", "roulette_coins_bet", "bigint", "0", True),
    ("profile", "stock_coins_earned", "bigint", "0", True),
    ("profile", "stock_coins_spent", "bigint", "0", True),
    ("profile", "season_stat_baseline", "jsonb", "'{}'::jsonb", True),
]


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
    log("starting migration 022_season_recap")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
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
