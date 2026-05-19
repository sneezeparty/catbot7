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

"""Phase 0 foundation for the Jobs / Mafia Killings feature.

Adds the profile columns needed by the jobs system (heat, faction_rep, lifetime
job counters, Big Score state, Whiskers's Favor state, first-time UI flags),
plus the jobinstance table and its two indexes.

Idempotent: skips work if migrations/007.done already exists. Each ALTER and
the CREATE TABLE are per-column / per-object gated so partial completion is
recoverable by re-running.

`heat_last_decay` is backfilled to NOW() epoch on existing profiles so the
first heat decay tick doesn't subtract years of decay at once.

Bot MUST be stopped before running this. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/007_jobs.py
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

MARKER = REPO_ROOT / "migrations" / "007.done"
LOGFILE = REPO_ROOT / "migrations" / "007.log"


# (column, type, default, not_null). Type/default mirror schema.sql exactly so a
# fresh install via psql -f schema.sql matches what this migration applies.
PROFILE_COLUMNS: list[tuple[str, str, str, bool]] = [
    ("heat",                     "integer", "0",            True),
    ("heat_last_decay",          "bigint",  "0",            True),
    ("faction_rep",              "jsonb",   "'{}'::jsonb",  True),
    ("jobs_completed",           "integer", "0",            True),
    ("jobs_failed",              "integer", "0",            True),
    ("jobs_near_missed",         "integer", "0",            True),
    ("cats_lost_to_jobs",        "integer", "0",            True),
    ("job_coins_won",            "bigint",  "0",            True),
    ("biggest_score_value",      "integer", "0",            True),
    ("big_score_season",         "integer", "-1",           True),
    ("big_score_wins",           "integer", "0",            True),
    ("big_score_perk_unlocked",  "boolean", "false",        True),
    ("whiskers_favor_active",    "boolean", "false",        True),
    ("whiskers_favor_season",    "integer", "-1",           True),
    ("jobs_send_screen_seen",    "boolean", "false",        True),
    ("tutorial_errand_complete", "boolean", "false",        True),
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


async def table_exists(conn: asyncpg.Connection, table: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = $1",
        table,
    )
    return row is not None


async def index_exists(conn: asyncpg.Connection, index: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND indexname = $1",
        index,
    )
    return row is not None


async def main() -> int:
    if MARKER.exists():
        log(f"marker {MARKER} exists — migration already applied. Delete it to re-run.")
        return 0

    LOGFILE.write_text("", encoding="utf-8")
    log("starting migration 007_jobs")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        # Step 1: add the profile columns.
        for col, coltype, default, not_null in PROFILE_COLUMNS:
            if await column_exists(conn, "profile", col):
                log(f"profile.{col} already exists, skipping ADD")
                continue
            null_clause = " NOT NULL" if not_null else ""
            sql = f"ALTER TABLE profile ADD COLUMN {col} {coltype} DEFAULT {default}{null_clause}"
            log(f"adding profile.{col}")
            await conn.execute(sql)

        # Step 2: backfill heat_last_decay to current epoch on existing rows
        # (rows where the default 0 was used because the column was newly added).
        # heat itself starts at 0, so the actual decay math is a no-op; we just
        # don't want lazy decay to compute hours-since-epoch the first time.
        now = int(time.time())
        result = await conn.execute(
            "UPDATE profile SET heat_last_decay = $1 WHERE heat_last_decay = 0",
            now,
        )
        log(f"backfilled heat_last_decay -> {now}: {result}")

        # Step 3: create jobinstance table + sequence + indexes.
        if not await table_exists(conn, "jobinstance"):
            log("creating jobinstance table")
            await conn.execute(
                """
                CREATE TABLE public.jobinstance (
                    id bigint NOT NULL,
                    template_id text DEFAULT ''::text NOT NULL,
                    user_id bigint NOT NULL,
                    guild_id bigint NOT NULL,
                    category text DEFAULT 'hit'::text NOT NULL,
                    tier integer NOT NULL,
                    offered_by text NOT NULL,
                    target_faction text DEFAULT ''::text NOT NULL,
                    difficulty integer NOT NULL,
                    send_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
                    send_total integer DEFAULT 0 NOT NULL,
                    success_chance real DEFAULT 0 NOT NULL,
                    roll real DEFAULT 0 NOT NULL,
                    outcome text DEFAULT ''::text NOT NULL,
                    cats_destroyed jsonb DEFAULT '{}'::jsonb NOT NULL,
                    state text NOT NULL,
                    narrative text DEFAULT ''::text NOT NULL,
                    reward_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
                    rep_changes jsonb DEFAULT '{}'::jsonb NOT NULL,
                    heat_cost integer DEFAULT 0 NOT NULL,
                    offered_at bigint NOT NULL,
                    expires_at bigint NOT NULL,
                    resolved_at bigint DEFAULT 0 NOT NULL,
                    committed_at bigint DEFAULT 0 NOT NULL
                )
                """
            )
            await conn.execute("ALTER TABLE public.jobinstance OWNER TO cat_bot")
            await conn.execute(
                """
                CREATE SEQUENCE public.jobinstance_id_seq
                    AS bigint
                    START WITH 1
                    INCREMENT BY 1
                    NO MINVALUE
                    NO MAXVALUE
                    CACHE 1
                """
            )
            await conn.execute("ALTER TABLE public.jobinstance_id_seq OWNER TO cat_bot")
            await conn.execute(
                "ALTER SEQUENCE public.jobinstance_id_seq OWNED BY public.jobinstance.id"
            )
            await conn.execute(
                "ALTER TABLE ONLY public.jobinstance ALTER COLUMN id SET DEFAULT nextval('public.jobinstance_id_seq'::regclass)"
            )
            await conn.execute(
                "ALTER TABLE ONLY public.jobinstance ADD CONSTRAINT jobinstance_pkey PRIMARY KEY (id)"
            )
        else:
            log("jobinstance table already exists, skipping CREATE")

        if not await index_exists(conn, "jobinstance_active"):
            log("creating index jobinstance_active")
            await conn.execute(
                "CREATE INDEX jobinstance_active ON public.jobinstance (user_id, guild_id, state)"
            )
        else:
            log("index jobinstance_active already exists, skipping")

        if not await index_exists(conn, "jobinstance_expiry"):
            log("creating index jobinstance_expiry")
            await conn.execute(
                "CREATE INDEX jobinstance_expiry ON public.jobinstance (expires_at) WHERE state = 'offered'"
            )
        else:
            log("index jobinstance_expiry already exists, skipping")

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
