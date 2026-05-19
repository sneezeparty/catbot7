"""Add columns for the new 'challenge' quest slot and the gift3 quest's
distinct-recipient tracking.

Idempotent: skips work if migrations/003.done already exists. Each
ALTER TABLE is gated on `column_exists` so re-running won't double-add.

Bot MUST be stopped before running this. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/003_challenge_slot.py
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

MARKER = REPO_ROOT / "migrations" / "003.done"
LOGFILE = REPO_ROOT / "migrations" / "003.log"

COLUMNS: list[tuple[str, str]] = [
    ("challenge_quest", "character varying(30) DEFAULT ''::character varying NOT NULL"),
    ("challenge_progress", "integer DEFAULT 0 NOT NULL"),
    ("challenge_cooldown", "bigint DEFAULT 0 NOT NULL"),
    ("challenge_reward", "smallint DEFAULT 0 NOT NULL"),
    ("reminder_challenge", "integer DEFAULT 0 NOT NULL"),
    ("gift3_recipients", "text DEFAULT ''::text NOT NULL"),
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

    LOGFILE.write_text("", encoding="utf-8")  # truncate
    log("starting migration 003_challenge_slot")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        for col_name, col_spec in COLUMNS:
            if await column_exists(conn, "profile", col_name):
                log(f"profile.{col_name} already exists, skipping")
                continue
            log(f"adding profile.{col_name}")
            await conn.execute(f"ALTER TABLE profile ADD COLUMN {col_name} {col_spec}")

        MARKER.write_text(
            json.dumps({"completed_at": time.time(), "columns_added": [c[0] for c in COLUMNS]}, indent=2),
            encoding="utf-8",
        )
        log(f"DONE. marker written to {MARKER}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    if not os.environ.get("psql_password"):
        print("ERROR: psql_password env var required (see bot.py)", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main()))
