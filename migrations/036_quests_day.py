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

"""Pure-daily quest reset 🗓️ — day tracker + daily variety-type dedup.

Adds two columns:

  public.profile.quests_day integer DEFAULT 0
    Last day-index (int((time.time() + 4*3600) // 86400), the same +4h clock as
    the season/weekly boundaries) on which this profile's daily quest slots were
    rolled over. refresh_quests compares it to today; on a mismatch every daily
    slot (catch/misc/extra/challenge + the vote substitute) is force-rerolled
    regardless of completion so incomplete quests don't carry over day to day.
    Weekly is exempt. Default 0 means every existing profile gets one fresh set
    on its first post-migration refresh.

  public.profile.quests_variety_types smallint[] DEFAULT '{}'
    Distinct cat-type rarity indices caught since the last daily reset, backing
    the `variety5` challenge quest ("Catch 5 different cat types"). Mirrors the
    storage of weekly_cattypes; cleared by the daily reset.

Idempotent — ADD COLUMN IF NOT EXISTS; second run logs and exits. No backfill.

Bot MUST be stopped before running this. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/036_quests_day.py
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

MARKER = REPO_ROOT / "migrations" / "036.done"
LOGFILE = REPO_ROOT / "migrations" / "036.log"

COLUMN_DDL = """
ALTER TABLE public.profile ADD COLUMN IF NOT EXISTS quests_day integer DEFAULT 0;
ALTER TABLE public.profile ADD COLUMN IF NOT EXISTS quests_variety_types smallint[] DEFAULT '{}'::smallint[];
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
    log("starting migration 036_quests_day")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        for col in ("quests_day", "quests_variety_types"):
            if await column_exists(conn, "profile", col):
                log(f"profile.{col} already exists, skipping")
            else:
                log(f"adding profile.{col}")
        await conn.execute(COLUMN_DDL)

        # sanity: the array column round-trips (asyncpg returns smallint[] as a
        # list[int], same as weekly_cattypes which main.py .copy()s)
        probe = await conn.fetchval("SELECT '{7,8}'::smallint[]")
        if not (isinstance(probe, list) and probe == [7, 8]):
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
