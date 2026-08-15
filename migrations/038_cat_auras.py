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

"""Cat auras (ported from upstream) + the global compact-inventory toggle.

Adds two columns:

  - profile.cat_auras char(1)[] DEFAULT array_fill(' ', ARRAY[24]) — one slot
    per entry in main.cattypes, holding that (user, guild)'s aura for that
    rarity. ' ' = none. 'y'/'c'/'p' are the >2%/>4%/>7%-of-server-supply tiers,
    'a' is the server's #1 holder, 'r' is the permanent rainbow drop. Rendered
    by main.get_aura_emoji as an emoji-name suffix (finecat -> finecat_y).

  - "user".compact_inventory boolean DEFAULT false — false means /inventory
    renders its cat list in two columns. Lives on the user (not the profile)
    so the display preference follows the player across servers.

Then backfills every guild's threshold auras in one pass, so existing servers
light up immediately instead of waiting for someone to run /lb. 'r' is never
touched by the backfill (nobody can have one yet, but the CASE is written to
preserve it anyway, exactly like the runtime path).

The backfill SQL is a deliberate copy of main.refresh_auras — that function is
the source of truth; this is a frozen one-shot snapshot of it. If you change
the tier boundaries later, change them there, not here.

Idempotent (per-column gated; the backfill is naturally re-runnable). Bot MUST
be stopped before running. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/038_cat_auras.py
"""

from __future__ import annotations

import ast
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

MARKER = REPO_ROOT / "migrations" / "038.done"
LOGFILE = REPO_ROOT / "migrations" / "038.log"


# (table, column, type, default, not_null)
COLUMNS: list[tuple[str, str, str, str, bool]] = [
    ("profile", "cat_auras", "character(1)[]", "array_fill(' '::character(1), ARRAY[24])", False),
    ('"user"', "compact_inventory", "boolean", "false", False),
]


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_cattypes() -> list[str]:
    """Read main.py's type_dict without importing it.

    Importing main pulls in discord.py and the whole bot, which we very much do
    not want in a migration that runs while the bot is stopped. The key order of
    the literal IS the aura array order, so parsing the AST gives us exactly what
    main.cattypes would.
    """
    tree = ast.parse((REPO_ROOT / "main.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "type_dict" for t in node.targets):
            return list(ast.literal_eval(node.value).keys())
    raise RuntimeError("could not find a module-level type_dict in main.py")


def build_backfill_sql(cattypes: list[str]) -> str:
    """One statement: recompute every guild's threshold auras.

    Frozen copy of main.refresh_auras' SQL — see the module docstring.
    """
    stats = ", ".join(
        f'COALESCE(SUM(GREATEST("cat_{cat}", 0)), 0) AS total_{i}, COALESCE(MAX("cat_{cat}"), 0) AS maximum_{i}'
        for i, cat in enumerate(cattypes, start=1)
    )

    def case(i: int, cat: str) -> str:
        col = f'p."cat_{cat}"'
        cur = f"COALESCE(p.cat_auras[{i}], ' ')"
        return (
            f"CASE WHEN {cur} = 'r' THEN 'r' "
            # `> 0` matters: without it, every profile in a guild where nobody
            # owns this rarity ties MAX() = 0 and the whole server gets the
            # #1-holder aura for a cat none of them have.
            f"WHEN {col} > 0 AND {col} = s.maximum_{i} THEN 'a' "
            f"WHEN {col} > s.total_{i} * 0.07 THEN 'p' "
            f"WHEN {col} > s.total_{i} * 0.04 THEN 'c' "
            f"WHEN {col} > s.total_{i} * 0.02 THEN 'y' "
            f"WHEN {cur} IN ('y', 'c', 'p', 'a') THEN ' ' "
            f"ELSE {cur} END"
        )

    auras = ", ".join(case(i, cat) for i, cat in enumerate(cattypes, start=1))
    return f"""
        WITH s AS (
            SELECT guild_id, {stats}
            FROM profile
            GROUP BY guild_id
        )
        UPDATE profile AS p
        SET cat_auras = ARRAY[{auras}]::character(1)[]
        FROM s
        WHERE p.guild_id = s.guild_id
          AND p.cat_auras IS DISTINCT FROM ARRAY[{auras}]::character(1)[]
    """


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
    migration's marker — it reports "already applied" and never touches the
    database. That is exactly what happened while writing this one (it shipped
    as 011, colliding with 011_perks_received). Fail loudly instead.
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
    log("starting migration 038_cat_auras")

    cattypes = load_cattypes()
    log(f"{len(cattypes)} cat types from main.type_dict: {', '.join(cattypes)}")

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

        log("backfilling threshold auras for every guild")
        status = await conn.execute(build_backfill_sql(cattypes))
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
