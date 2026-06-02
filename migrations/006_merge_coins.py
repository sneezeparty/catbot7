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

"""Merge profile.roulette_balance into profile.coins, then drop the column.

The original design segregated "cat dollars" (roulette only) from "coins"
(stocks + packs + catstore) to prevent arbitrage. The operator has decided
that on this self-hosted instance, a single shared wallet is preferable:
gambling losses now also cost stock/store buying power, and roulette
winnings can be spent anywhere.

Existing roulette_balance values are SUMMED into coins (not replaced), so no
player loses anything they earned. Negative balances (gambling debt) are
preserved — a player at -50 cat dollars and 100 coins ends up at 50 coins.

Idempotent: skips work if migrations/006.done already exists. Per-step gated
so partial completion is recoverable by re-running.

Bot MUST be stopped before running this. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/006_merge_coins.py
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

MARKER = REPO_ROOT / "migrations" / "006.done"
LOGFILE = REPO_ROOT / "migrations" / "006.log"
FLAG_COL = "_coins_merged_006"


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
    log("starting migration 006_merge_coins")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        has_roulette = await column_exists(conn, "profile", "roulette_balance")
        if not has_roulette:
            log("profile.roulette_balance not found — assuming migration already partially ran. Nothing to merge.")
        else:
            # Snapshot what we're about to merge so the log makes sense.
            stats = await conn.fetchrow(
                "SELECT COUNT(*) AS n, "
                "       COALESCE(SUM(roulette_balance), 0) AS total, "
                "       COUNT(*) FILTER (WHERE roulette_balance < 0) AS debt_count "
                "FROM profile WHERE roulette_balance <> 100"
            )
            log(f"snapshot: {stats['n']} profiles with non-default roulette_balance; "
                f"total = {stats['total']}; {stats['debt_count']} in debt")

            # Single-statement merge — atomic, no batching needed (we don't
            # expect 100k+ rows on a self-hosted instance, and a single UPDATE
            # with no WHERE clause runs as one tx in PG).
            log("merging roulette_balance into coins …")
            result = await conn.execute(
                "UPDATE profile SET coins = coins + roulette_balance"
            )
            log(f"  {result}")

            log("dropping profile.roulette_balance column")
            await conn.execute("ALTER TABLE profile DROP COLUMN roulette_balance")

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
