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

"""Weekly quests 🍀 + /scratch scratchcards (cattlepass v2.1 port).

Adds four columns:

  public.profile.weekly_quest character varying(10) DEFAULT ''
    Active weekly quest id ('' = the no-quest sentinel; days 28+ of a season).

  public.profile.weekly_progress smallint DEFAULT 0
    Progress toward the active weekly quest.

  public.profile.weekly_cattypes smallint[] DEFAULT '{}'
    Distinct cattype indices caught this window (the "different" quest).

  public.profile.scratchcards smallint DEFAULT 0
    Unspent /scratch cards. Earned only from weekly quest completion;
    wiped at season rollover along with packs.

Idempotent — ADD COLUMN IF NOT EXISTS; second run logs and exits. No backfill.

Bot MUST be stopped before running this. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/034_weekly_scratch.py
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

MARKER = REPO_ROOT / "migrations" / "034.done"
LOGFILE = REPO_ROOT / "migrations" / "034.log"

COLUMN_DDL = """
ALTER TABLE public.profile ADD COLUMN IF NOT EXISTS weekly_quest character varying(10) DEFAULT ''::character varying;
ALTER TABLE public.profile ADD COLUMN IF NOT EXISTS weekly_progress smallint DEFAULT 0;
ALTER TABLE public.profile ADD COLUMN IF NOT EXISTS weekly_cattypes smallint[] DEFAULT '{}'::smallint[];
ALTER TABLE public.profile ADD COLUMN IF NOT EXISTS scratchcards smallint DEFAULT 0;
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
    log("starting migration 034_weekly_scratch")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        for col in ("weekly_quest", "weekly_progress", "weekly_cattypes", "scratchcards"):
            if await column_exists(conn, "profile", col):
                log(f"profile.{col} already exists, skipping")
            else:
                log(f"adding profile.{col}")
        await conn.execute(COLUMN_DDL)

        # sanity: smallint[] round-trips as a Python list through asyncpg
        probe = await conn.fetchval("SELECT '{1,5,9}'::smallint[]")
        if list(probe) != [1, 5, 9]:
            log(f"WARNING: smallint[] round-trip returned {probe!r} — investigate before starting the bot")
        else:
            log("smallint[] round-trip OK")

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
