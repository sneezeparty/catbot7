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

"""Bonus cats 🎁 (solo variant of upstream's june update).

Adds one column:

  public.profile.bonus_catches integer DEFAULT 0
    Count of successful bonus-cat minigames (each success = +3 cats of the
    bonus type). Shown in /profile stats and the webui profile browser.

Notes:
  - No server.legacy_catching column: this fork ships bonus cats WITHOUT
    late catching, so there is no per-server toggle — the kill switch is
    bonus_cat_chance_coef = 0 in config/tuning.json.
  - No math_jumpscare column: fork-era achievements live only in
    profile.unlocked_aches (JSONB); see database.Profile.unlock_ach.

Idempotent — ADD COLUMN IF NOT EXISTS; second run logs and exits. No backfill.

Bot MUST be stopped before running this. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/032_bonus_cats.py
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

MARKER = REPO_ROOT / "migrations" / "032.done"
LOGFILE = REPO_ROOT / "migrations" / "032.log"

COLUMN_DDL = """
ALTER TABLE public.profile ADD COLUMN IF NOT EXISTS bonus_catches integer DEFAULT 0;
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
    log("starting migration 032_bonus_cats")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        if await column_exists(conn, "profile", "bonus_catches"):
            log("profile.bonus_catches already exists, skipping")
        else:
            log("adding profile.bonus_catches")
        await conn.execute(COLUMN_DDL)

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
