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

"""Piñatas 🪅 (Season 6): seven profile columns, all plain counters.

  - pinatas              how many this player has crafted/owns on this server
                         (piñatas aren't tradeable, so the two are the same)
  - pinata_cats_won      lifetime cats received from bursts, own + others'
                         (/inventory's "Pinata Cats")
  - pinata_cats_given    lifetime cats this player's opens spilled to others
  - pinata_packs_won     lifetime packs that fell out of piñatas for them
  - pinata_day           UTC day number the two daily counters below belong to
  - pinata_recv_today    cats received from other people's bursts that day
  - pinata_bonus_today   owner-bonus cats from their own opens that day

Nothing to backfill: everyone starts at zero. Idempotent (column-gated). Bot
MUST be stopped before running. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/042_pinatas.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import asyncpg  # noqa: E402

import config  # noqa: E402

MARKER = REPO_ROOT / "migrations" / "042.done"
LOGFILE = REPO_ROOT / "migrations" / "042.log"

COLUMNS = [
    "pinatas",
    "pinata_cats_won",
    "pinata_cats_given",
    "pinata_packs_won",
    "pinata_day",
    "pinata_recv_today",
    "pinata_bonus_today",
]


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def check_number_is_ours() -> None:
    """Refuse to run if another migration already owns this number (see 038)."""
    number = os.path.basename(__file__).split("_")[0]
    siblings = sorted(
        p for p in os.listdir(REPO_ROOT / "migrations")
        if p.startswith(f"{number}_") and p.endswith(".py") and p != os.path.basename(__file__)
    )
    if siblings:
        raise SystemExit(f"migration number {number} is already used by {', '.join(siblings)} — renumber this script")


async def column_exists(conn: asyncpg.Connection, column: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'profile' AND column_name = $1",
        column,
    )
    return row is not None


async def main() -> int:
    check_number_is_ours()

    if MARKER.exists():
        log(f"marker {MARKER} exists — migration already applied. Delete it to re-run.")
        return 0

    LOGFILE.write_text("", encoding="utf-8")
    log("starting migration 042_pinatas")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        async with conn.transaction():
            for col in COLUMNS:
                if await column_exists(conn, col):
                    log(f"profile.{col} already exists, skipping")
                    continue
                log(f"adding profile.{col}")
                await conn.execute(f"ALTER TABLE profile ADD COLUMN {col} integer DEFAULT 0 NOT NULL")
    finally:
        await conn.close()

    MARKER.write_text(time.strftime("%Y-%m-%d %H:%M:%S") + "\n", encoding="utf-8")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
