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

"""Backing column for the "Discreet" (clean_record) achievement.

Adds one column:

  - profile.clean_record_broken boolean DEFAULT false NOT NULL — sticky flag,
    set by main._jobs_apply_commit_heat the first time a commit would push
    heat above 30. clean_record ("complete 20 jobs without heat ever exceeding
    30") awards on a successful job when jobs_completed >= 20 and this is
    still false.

The flag exists because heat DECAYS (main._jobs_apply_heat_decay). There is no
running peak anywhere, so by the time a player finishes job 20 their worst
moment is unrecoverable from the current row — it has to be recorded when it
happens.

BACKFILL AND ITS LIMIT: existing rows get `heat > 30` — the only evidence of
past heat the database still holds. That is deliberately imperfect and
generous: a veteran who spiked to 90 last week and has since decayed back to 4
starts this migration with a clean record. There is no history table to do
better with, and the alternative (marking everyone broken) would make the ach
unobtainable for every current player until they burn through 20 more jobs.
Erring toward "clean" costs nothing but a few early unlocks.

Idempotent (column-gated; the backfill only touches rows still at the default,
so re-running never re-dirties a flag the bot has since set). Bot MUST be
stopped before running. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/039_clean_record.py
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

MARKER = REPO_ROOT / "migrations" / "039.done"
LOGFILE = REPO_ROOT / "migrations" / "039.log"

# Must match main.JOBS_CLEAN_RECORD_MAX_HEAT.
CLEAN_RECORD_MAX_HEAT = 30

# (table, column, type, default, not_null)
COLUMNS: list[tuple[str, str, str, str, bool]] = [
    ("profile", "clean_record_broken", "boolean", "false", True),
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
        table.strip('"'),
        column,
    )
    return row is not None


def check_number_is_ours() -> None:
    """Refuse to run if another migration already owns this number.

    Markers are named NNN.done, not <script>.done, so picking a number that is
    already taken makes the new migration silently no-op against the OLD
    migration's marker. Fail loudly instead. (See 038's note — this bit us.)
    """
    number = os.path.basename(__file__).split("_")[0]
    siblings = sorted(
        p for p in os.listdir(REPO_ROOT / "migrations")
        if p.startswith(f"{number}_") and p.endswith(".py") and p != os.path.basename(__file__)
    )
    if siblings:
        raise SystemExit(
            f"migration number {number} is already used by {', '.join(siblings)} — "
            f"renumber this script (and its MARKER/LOGFILE) before running it"
        )


async def main() -> int:
    check_number_is_ours()

    if MARKER.exists():
        log(f"marker {MARKER} exists — migration already applied. Delete it to re-run.")
        return 0

    LOGFILE.write_text("", encoding="utf-8")
    log("starting migration 039_clean_record")

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
            sql = f"ALTER TABLE {table} ADD COLUMN {col} {coltype} DEFAULT {default}{null_clause}"
            log(f"adding {table}.{col}")
            await conn.execute(sql)

        log(f"backfilling clean_record_broken for profiles with heat > {CLEAN_RECORD_MAX_HEAT}")
        status = await conn.execute(
            "UPDATE profile SET clean_record_broken = true "
            "WHERE clean_record_broken = false AND heat > $1",
            CLEAN_RECORD_MAX_HEAT,
        )
        log(f"backfill: {status}")

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
