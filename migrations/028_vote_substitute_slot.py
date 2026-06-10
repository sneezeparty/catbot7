"""Add the vote_quest column to profile so the vote slot can occasionally
host a substitute misc-pool quest instead of the literal Top.gg vote quest.

vote_quest == '' (default) → the slot is the real vote quest (existing flow).
vote_quest != ''           → the slot is hosting that misc quest as a
                             single-action substitute; vote_reward / vote_cooldown
                             reuse for XP + claim time tracking.

Idempotent: skips work if migrations/028.done already exists. Re-run by
deleting the marker.

Bot MUST be stopped before running this. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/028_vote_substitute_slot.py
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

MARKER = REPO_ROOT / "migrations" / "028.done"
LOGFILE = REPO_ROOT / "migrations" / "028.log"

COLUMNS: list[tuple[str, str]] = [
    ("vote_quest", "character varying(30) DEFAULT ''::character varying NOT NULL"),
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
    log("starting migration 028_vote_substitute_slot")

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
