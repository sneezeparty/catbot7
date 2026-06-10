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

"""Activity-dashboard snapshot infrastructure.

Adds two pieces of schema used by the webui's Activity page:

  1. server.name VARCHAR(100) DEFAULT '' NOT NULL
     Cached guild display name. Populated opportunistically by the bot's
     snapshot loop (and on_guild_join) so the dashboard can resolve names
     for guilds the bot is no longer in.

  2. public.metric_snapshot
     Hourly-bucketed aggregate counters (guild count, active catchers, total
     catches, coins in circulation, jobs lifetime, etc). PK on bucket_time
     gives us safe `ON CONFLICT DO NOTHING` upserts. Time-series deltas on
     the dashboard come from `LAG()` over rows in this table.

Both pieces are idempotent — second run logs "already exists" and exits.
No backfill: the bot fills server.name on next snapshot tick, and
metric_snapshot starts populating at the next hour boundary.

Bot MUST be stopped before running this. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/029_metrics_snapshot.py
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

MARKER = REPO_ROOT / "migrations" / "029.done"
LOGFILE = REPO_ROOT / "migrations" / "029.log"


METRIC_SNAPSHOT_DDL = """
CREATE TABLE public.metric_snapshot (
    bucket_time bigint NOT NULL,
    guild_count integer DEFAULT 0 NOT NULL,
    profile_count integer DEFAULT 0 NOT NULL,
    user_count integer DEFAULT 0 NOT NULL,
    active_24h integer DEFAULT 0 NOT NULL,
    active_7d integer DEFAULT 0 NOT NULL,
    active_30d integer DEFAULT 0 NOT NULL,
    total_catches bigint DEFAULT 0 NOT NULL,
    total_packs bigint DEFAULT 0 NOT NULL,
    total_prisms bigint DEFAULT 0 NOT NULL,
    coins_in_circulation bigint DEFAULT 0 NOT NULL,
    catnip_total bigint DEFAULT 0 NOT NULL,
    jobs_completed_lifetime bigint DEFAULT 0 NOT NULL,
    jobs_failed_lifetime bigint DEFAULT 0 NOT NULL,
    live_spawns integer DEFAULT 0 NOT NULL,
    active_rains integer DEFAULT 0 NOT NULL,
    pending_jobs integer DEFAULT 0 NOT NULL,
    PRIMARY KEY (bucket_time)
);
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


async def table_exists(conn: asyncpg.Connection, table: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = $1",
        table,
    )
    return row is not None


async def main() -> int:
    if MARKER.exists():
        log(f"marker {MARKER} exists — migration already applied. Delete it to re-run.")
        return 0

    LOGFILE.write_text("", encoding="utf-8")
    log("starting migration 029_metrics_snapshot")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        if await column_exists(conn, "server", "name"):
            log("server.name already exists, skipping ADD")
        else:
            log("adding server.name")
            await conn.execute(
                "ALTER TABLE server ADD COLUMN name varchar(100) "
                "DEFAULT '' NOT NULL"
            )

        if await table_exists(conn, "metric_snapshot"):
            log("metric_snapshot table already exists, skipping CREATE")
        else:
            log("creating metric_snapshot table")
            await conn.execute(METRIC_SNAPSHOT_DDL)

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
