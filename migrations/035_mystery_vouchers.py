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

"""Mystery outcome expansion 🎁 — vouchers + rain-seconds bank.

Adds two columns:

  public.profile.vouchers jsonb DEFAULT '[]' NOT NULL
    One-shot voucher list from battlepass Mystery rewards. Entries:
    {"id": "double_pack" | "egirl_bonus" | "bounty_skip", "granted_at": int}.
    Consumed by pack opens / catslots spins / bounty catches respectively.
    Wiped at season rollover (pack-adjacent value).

  public.profile.rain_seconds smallint DEFAULT 0
    Sub-minute rain bank from Mystery rain drops (15s/30s/60s). Grant-time
    rollover keeps it in 0-59: every full 60s converts into +1
    profile.rain_minutes (the per-server bonus minutes /rain spends first).
    PRESERVED at season rollover, same as rain_minutes.

Idempotent — ADD COLUMN IF NOT EXISTS; second run logs and exits. No backfill.

Bot MUST be stopped before running this. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/035_mystery_vouchers.py
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

MARKER = REPO_ROOT / "migrations" / "035.done"
LOGFILE = REPO_ROOT / "migrations" / "035.log"

COLUMN_DDL = """
ALTER TABLE public.profile ADD COLUMN IF NOT EXISTS vouchers jsonb DEFAULT '[]'::jsonb NOT NULL;
ALTER TABLE public.profile ADD COLUMN IF NOT EXISTS rain_seconds smallint DEFAULT 0;
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
    log("starting migration 035_mystery_vouchers")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        for col in ("vouchers", "rain_seconds"):
            if await column_exists(conn, "profile", col):
                log(f"profile.{col} already exists, skipping")
            else:
                log(f"adding profile.{col}")
        await conn.execute(COLUMN_DDL)

        # sanity: jsonb round-trips through asyncpg (returned as a str by
        # default in this codebase — main._vouchers_load handles both shapes)
        probe = await conn.fetchval("""SELECT '[{"id": "double_pack", "granted_at": 1}]'::jsonb""")
        parsed = json.loads(probe) if isinstance(probe, str) else probe
        if not (isinstance(parsed, list) and parsed and parsed[0].get("id") == "double_pack"):
            log(f"WARNING: jsonb round-trip returned {probe!r} — investigate before starting the bot")
        else:
            log("jsonb round-trip OK")

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
