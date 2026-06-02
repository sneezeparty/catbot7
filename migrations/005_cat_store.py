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

"""Add profile.discovered_cats + profile.store_purchased_rarities for /catstore.

The Cat Store gates buy/sell on lifetime per-server discovery: a player can
only trade a cat type they've ever owned at least one of in that server.
discovered_cats tracks that gate; store_purchased_rarities backs the hidden
"buy one of every rarity" achievement.

Idempotent: skips work if migrations/005.done already exists. Each ALTER and
backfill is per-column gated so partial completion is recoverable by re-running.

Bot MUST be stopped before running this. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/005_cat_store.py
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

MARKER = REPO_ROOT / "migrations" / "005.done"
LOGFILE = REPO_ROOT / "migrations" / "005.log"

# Source-of-truth rarity list. Pulled from main.py's type_dict at the time
# this migration was authored; if main.py adds a new rarity later, existing
# profiles will discover it organically via the runtime mark_discovered hooks.
CATTYPES = [
    "Fine", "Nice", "Good", "Rare", "Wild", "Baby", "Epic", "Sus", "Brave",
    "Rickroll", "Reverse", "Superior", "Trash", "Legendary", "Mythic",
    "8bit", "Corrupt", "Professor", "Divine", "Real", "Ultimate", "eGirl",
]

BATCH_SIZE = 5_000


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
    log("starting migration 005_cat_store")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        # Step 1: add the columns.
        if not await column_exists(conn, "profile", "discovered_cats"):
            log("adding profile.discovered_cats")
            await conn.execute(
                "ALTER TABLE profile ADD COLUMN discovered_cats jsonb DEFAULT '[]'::jsonb NOT NULL"
            )
        else:
            log("profile.discovered_cats already exists, skipping ADD")

        if not await column_exists(conn, "profile", "store_purchased_rarities"):
            log("adding profile.store_purchased_rarities")
            await conn.execute(
                "ALTER TABLE profile ADD COLUMN store_purchased_rarities jsonb DEFAULT '[]'::jsonb NOT NULL"
            )
        else:
            log("profile.store_purchased_rarities already exists, skipping ADD")

        # Step 2: backfill discovered_cats from existing cat_<Type> counters.
        # For every profile, the set of types where cat_<Type> > 0 is the
        # discovery set. Append any missing types to the JSONB array, batched
        # to keep transactions short.
        log("backfilling discovered_cats from per-type counters")
        total_touched = 0
        for cat_type in CATTYPES:
            col = f"cat_{cat_type}"
            if not await column_exists(conn, "profile", col):
                log(f"  skip {cat_type} (column {col} missing)")
                continue
            while True:
                # NOTE: cat_type is interpolated as a literal in `to_jsonb`;
                # column name is interpolated as identifier. Both come from
                # the hardcoded CATTYPES list, NOT user input.
                quoted_col = '"' + col.replace('"', '""') + '"'
                result = await conn.execute(
                    f"""
                    UPDATE profile p
                    SET discovered_cats = discovered_cats || to_jsonb($1::text)
                    WHERE p.id IN (
                        SELECT id FROM profile
                        WHERE {quoted_col} > 0
                          AND NOT (discovered_cats @> to_jsonb($1::text))
                        LIMIT $2
                    )
                    """,
                    cat_type,
                    BATCH_SIZE,
                )
                n = int(result.split()[-1]) if result.startswith("UPDATE ") else 0
                total_touched += n
                if n:
                    log(f"  {cat_type}: +{n} rows")
                if n < BATCH_SIZE:
                    break

        MARKER.write_text(
            json.dumps({"completed_at": time.time(), "rows_touched": total_touched}, indent=2),
            encoding="utf-8",
        )
        log(f"DONE. touched {total_touched} (cat_type, profile) pairs; marker at {MARKER}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    if not os.environ.get("psql_password"):
        print("ERROR: psql_password env var required (see bot.py)", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main()))
