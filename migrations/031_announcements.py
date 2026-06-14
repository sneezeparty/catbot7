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

"""Announcements broadcaster — schema for the webui /announce editor.

Adds one table:

  public.announcement
    History + state for operator-authored broadcasts sent from the webui
    Announcements section. Each row is one broadcast: the body text, the
    `one_per_server` dedupe flag, current status ('pending' | 'sending' |
    'sent' | 'failed'), and the per-broadcast counts (target / sent /
    failed / skipped). webui/announce_sender.py mutates the row through
    its lifecycle; the webui dashboard reads it for the history table.

  public.announcement_id_seq
    Sequence backing announcement.id (auto-increment).

Idempotent — second run logs "already exists" and exits. No backfill needed.

Bot MUST be stopped before running this. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/031_announcements.py
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

MARKER = REPO_ROOT / "migrations" / "031.done"
LOGFILE = REPO_ROOT / "migrations" / "031.log"


ANNOUNCEMENT_TABLE_DDL = """
CREATE TABLE public.announcement (
    id integer NOT NULL,
    created_at bigint NOT NULL,
    sent_at bigint DEFAULT 0 NOT NULL,
    body text NOT NULL,
    status text DEFAULT 'pending' NOT NULL,
    one_per_server boolean DEFAULT true NOT NULL,
    target_count integer DEFAULT 0 NOT NULL,
    sent_count integer DEFAULT 0 NOT NULL,
    failed_count integer DEFAULT 0 NOT NULL,
    skipped_count integer DEFAULT 0 NOT NULL,
    error text DEFAULT '' NOT NULL,
    PRIMARY KEY (id)
);
"""

# status values: 'pending' | 'sending' | 'sent' | 'failed' — webui/announce_sender.py owns the lifecycle.

ANNOUNCEMENT_SEQ_DDL = """
CREATE SEQUENCE public.announcement_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
"""

ANNOUNCEMENT_WIRE_DDL = """
ALTER TABLE public.announcement_id_seq OWNER TO cat_bot;
ALTER SEQUENCE public.announcement_id_seq OWNED BY public.announcement.id;
ALTER TABLE ONLY public.announcement ALTER COLUMN id SET DEFAULT nextval('public.announcement_id_seq'::regclass);
ALTER TABLE public.announcement OWNER TO cat_bot;
"""


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


async def table_exists(conn: asyncpg.Connection, table: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = $1",
        table,
    )
    return row is not None


async def sequence_exists(conn: asyncpg.Connection, sequence: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM information_schema.sequences "
        "WHERE sequence_schema = 'public' AND sequence_name = $1",
        sequence,
    )
    return row is not None


async def main() -> int:
    if MARKER.exists():
        log(f"marker {MARKER} exists — migration already applied. Delete it to re-run.")
        return 0

    LOGFILE.write_text("", encoding="utf-8")
    log("starting migration 031_announcements")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        if await table_exists(conn, "announcement"):
            log("announcement table already exists, skipping CREATE")
        else:
            log("creating announcement table")
            await conn.execute(ANNOUNCEMENT_TABLE_DDL)

        if await sequence_exists(conn, "announcement_id_seq"):
            log("announcement_id_seq already exists, skipping CREATE")
        else:
            log("creating announcement_id_seq")
            await conn.execute(ANNOUNCEMENT_SEQ_DDL)

        # Owner + default-nextval wiring. These are idempotent on their own —
        # re-running them on an already-wired schema is a no-op.
        log("wiring owner + default nextval on announcement.id")
        await conn.execute(ANNOUNCEMENT_WIRE_DDL)

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
