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

"""Add the Cat Bot Store entitlement column to the `user` table.

Backs the Discord-native monetization system (SKUs + Entitlements). The
column stores the SKU ids the user currently holds an active entitlement
for. user.premium is the OR of "has any supporter-tier SKU" and is
recomputed in _recompute_premium() each time entitlements change.

Adds one column:

    - user.entitlements JSONB NOT NULL DEFAULT '[]'::jsonb

Existing rows get [] — no backfill needed. The first on_ready after
upgrade reconciles state via the Discord API.

Idempotent (per-column gated). Bot MUST be stopped before running. Run with
the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/017_store_entitlements.py
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

MARKER = REPO_ROOT / "migrations" / "017.done"
LOGFILE = REPO_ROOT / "migrations" / "017.log"


# (table, column, type, default, not_null)
COLUMNS: list[tuple[str, str, str, str, bool]] = [
    ("user", "entitlements", "jsonb", "'[]'::jsonb", True),
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


async def main() -> int:
    if MARKER.exists():
        log(f"marker {MARKER} exists — migration already applied. Delete it to re-run.")
        return 0

    LOGFILE.write_text("", encoding="utf-8")
    log("starting migration 017_store_entitlements")

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
            # Note: user is a reserved word in PG so it gets double-quoted.
            sql = (
                f'ALTER TABLE "{table}" ADD COLUMN {col} {coltype} '
                f"DEFAULT {default}{null_clause}"
            )
            log(f"adding {table}.{col}")
            await conn.execute(sql)

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
