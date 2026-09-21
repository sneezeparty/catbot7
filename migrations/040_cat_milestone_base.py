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

"""Freeze every profile's cat counters so the pow2_* achievements aren't retroactive.

Adds one column:

  - profile.cat_milestone_base jsonb (NULLABLE, no default) — a snapshot of
    every cat_<rarity> counter as it stood the moment this migration ran, e.g.
    {"Fine": 3000, "eGirl": 5, ...}.

WHAT IT'S FOR: the "Powers of Two" achievement ladder (pow2_8 .. pow2_1048576)
awards a rung when a single rarity climbs PAST that number — and only for
ground covered after the feature shipped. main.award_pow2_milestones compares
the live counters against this snapshot, so a player sitting on 3,000 Fine
skips 8 through 2048 and earns 4096 honestly. The snapshot is per-rarity and
never updated again: that same player's 5 eGirl can still cross 8, because
eGirl froze at 5 even though Fine froze at 3,000.

WHY NULLABLE, NO DEFAULT: NULL is a real sentinel meaning "this profile has
never been baselined". award_pow2_milestones freezes the baseline on the spot
and awards nothing when it sees NULL, so a row this migration somehow misses
degrades to "baselined a bit later" rather than to "dumped 10 achievement
embeds on a veteran". A DEFAULT of '{}' would be exactly the wrong failure
mode: every unmigrated veteran would read as starting from zero cats and get
the entire ladder at once. New profiles created after this migration also come
up NULL, which is correct — they get frozen at ~0 cats on their first catch
and then walk the whole ladder.

Ordering note: the snapshot is taken in ONE statement per batch straight from
the row's own columns, so it can't tear across rarities. The bot MUST be
stopped anyway; a catch landing mid-backfill would otherwise bake the caught
cat into the player's own floor and cost them one rung.

Idempotent (column-gated, and the backfill only touches rows still NULL, so
re-running never re-freezes a baseline the bot has since set). Bot MUST be
stopped before running. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/040_cat_milestone_base.py
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

MARKER = REPO_ROOT / "migrations" / "040.done"
LOGFILE = REPO_ROOT / "migrations" / "040.log"

BATCH_SIZE = 10_000

# Must match main.type_dict's keys. Read from the live table rather than
# hardcoded here, so a rarity added in a later season can't silently fall out
# of the snapshot — see discover_cat_columns().
CAT_COLUMN_PREFIX = "cat_"

# Per-rarity counters are exactly cat_<Rarity> with no further underscore:
# cat_Fine, cat_8bit, cat_eGirl. NOT a LIKE pattern on purpose — in SQL LIKE,
# '_' is a single-character wildcard, so 'cat_%' would also drag in
# catnip_level, catslots_spins, catch_streak and cats_gifted.
CAT_COLUMN_RE = r"^cat_[A-Za-z0-9]+$"

# Matches the shape above but isn't a counter. The data_type filter already
# rejects both (ARRAY and boolean); listed anyway so the intent is explicit.
CAT_COLUMN_EXCLUDE = {"cat_auras", "cat_rain"}


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


async def discover_cat_columns(conn: asyncpg.Connection) -> list[str]:
    """Every integer cat_<Rarity> counter on profile, straight from the
    catalog. Deriving this from the table instead of a hardcoded list means a
    rarity introduced after this file was written still gets a floor."""
    rows = await conn.fetch(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'profile' "
        "AND column_name ~ $1 ORDER BY ordinal_position",
        CAT_COLUMN_RE,
    )
    cols = [
        r["column_name"]
        for r in rows
        if r["column_name"] not in CAT_COLUMN_EXCLUDE
        and r["data_type"] in ("integer", "bigint", "smallint")
    ]
    return cols


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
    log("starting migration 040_cat_milestone_base")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        if await column_exists(conn, "profile", "cat_milestone_base"):
            log("profile.cat_milestone_base already exists, skipping ADD")
        else:
            log("adding profile.cat_milestone_base")
            await conn.execute("ALTER TABLE profile ADD COLUMN cat_milestone_base jsonb")

        cat_cols = await discover_cat_columns(conn)
        if not cat_cols:
            raise SystemExit("found no cat_<Rarity> counter columns on profile — refusing to backfill")
        log(f"snapshotting {len(cat_cols)} rarity counters: {', '.join(c[len(CAT_COLUMN_PREFIX):] for c in cat_cols)}")

        # jsonb_build_object('Fine', COALESCE("cat_Fine",0), ...). Column names
        # come from the catalog, not user input; still quoted for the
        # mixed-case ones ("cat_Divine", "cat_eGirl", "cat_8bit").
        pairs = []
        for col in cat_cols:
            rarity = col[len(CAT_COLUMN_PREFIX):]
            quoted = '"' + col.replace('"', '""') + '"'
            pairs.append(f"'{rarity}', COALESCE({quoted}, 0)")
        build_obj = "jsonb_build_object(" + ", ".join(pairs) + ")"

        total = 0
        while True:
            result = await conn.execute(
                f"""
                UPDATE profile p
                SET cat_milestone_base = {build_obj}
                WHERE p.id IN (
                    SELECT id FROM profile
                    WHERE cat_milestone_base IS NULL
                    LIMIT $1
                )
                """,
                BATCH_SIZE,
            )
            n = int(result.split()[-1]) if result.startswith("UPDATE ") else 0
            total += n
            if n:
                log(f"  baselined {n} rows (running total {total})")
            if n < BATCH_SIZE:
                break

        log(f"backfill complete: {total} profiles baselined")

        MARKER.write_text(
            json.dumps({"completed_at": time.time(), "profiles_baselined": total, "rarities": len(cat_cols)}, indent=2),
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
