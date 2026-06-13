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

"""Stock market v2 schema + clean break from the old order-book MM.

Three things happen, all idempotent:

  1. Create `public.newsevent` (+ sequence) — the persisted feed that the
     new simulated market posts headlines to (earnings, surprise, crash,
     boom, dividend). Charts read from `pricehistory` as before; this
     table is the news layer.

  2. Cancel + refund every live limit order (rows in `public.order` with
     `time > 0`). Buy orders refund coins (and reduce `stock_coins_spent`
     symmetrically with the runtime 7-day sweep); sell orders refund
     shares. Each refund writes a `portfoliohistory` row of type 'c'
     (coin refund) or 'C' (share refund) so users can see in their
     activity log what happened. Then the rows are deleted.

  3. Delete every market-maker order (rows in `public.order` with
     `time = 0`). The bot's profile is NOT refunded — its accumulated
     MM-inventory and coins are deliberately written off; the new
     simulated market fills market trades against the house, which has
     no per-row inventory.

  4. Seed `newsevent` with one row per ticker so the news feed has
     content on day 1. impulse_pct=0, applied=true.

User holdings (`profile.stock_*` columns) are NOT touched. Existing
`pricehistory` rows are NOT touched — charts transition smoothly from
the MM-era prices into the simulated-market prices.

Bot MUST be stopped before running this. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/030_stock_market_v2.py
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

MARKER = REPO_ROOT / "migrations" / "030.done"
LOGFILE = REPO_ROOT / "migrations" / "030.log"

# Hardcoded to keep the migration self-contained; matches `stock_data`
# in main.py.
TICKERS = ["PRSM", "CTNP", "PASS", "ACHS", "RAIN"]


NEWS_EVENT_DDL = """
CREATE TABLE public.newsevent (
    id integer NOT NULL,
    time bigint NOT NULL,
    fires_at bigint NOT NULL,
    ticker character varying(10),
    event_type text NOT NULL,
    headline text NOT NULL,
    impulse_pct real DEFAULT 0 NOT NULL,
    applied boolean DEFAULT false NOT NULL,
    PRIMARY KEY (id)
);
"""

NEWS_EVENT_SEQ_DDL = """
CREATE SEQUENCE public.newsevent_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
"""

NEWS_EVENT_SEQ_OWNED_DDL = """
ALTER SEQUENCE public.newsevent_id_seq OWNED BY public.newsevent.id;
"""

NEWS_EVENT_DEFAULT_DDL = """
ALTER TABLE ONLY public.newsevent
    ALTER COLUMN id SET DEFAULT nextval('public.newsevent_id_seq'::regclass);
"""

NEWS_EVENT_OWNER_DDL = """
ALTER TABLE public.newsevent OWNER TO cat_bot;
"""

NEWS_EVENT_SEQ_OWNER_DDL = """
ALTER SEQUENCE public.newsevent_id_seq OWNER TO cat_bot;
"""

NEWS_EVENT_TIME_IDX_DDL = """
CREATE INDEX newsevent_time_desc ON public.newsevent USING btree (time DESC);
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


async def create_newsevent(conn: asyncpg.Connection) -> None:
    if await table_exists(conn, "newsevent"):
        log("newsevent already exists, skipping CREATE")
        return
    log("creating newsevent table + sequence + index")
    async with conn.transaction():
        await conn.execute(NEWS_EVENT_DDL)
        await conn.execute(NEWS_EVENT_OWNER_DDL)
        await conn.execute(NEWS_EVENT_SEQ_DDL)
        await conn.execute(NEWS_EVENT_SEQ_OWNER_DDL)
        await conn.execute(NEWS_EVENT_SEQ_OWNED_DDL)
        await conn.execute(NEWS_EVENT_DEFAULT_DDL)
        await conn.execute(NEWS_EVENT_TIME_IDX_DDL)


async def cancel_and_refund_limit_orders(conn: asyncpg.Connection) -> tuple[int, int, int]:
    """Refund every live limit order (time > 0) and delete it.

    Mirrors the runtime 7-day expiry sweep in main.py's background_loop:
      - buy orders: profile.coins += quantity*price, stock_coins_spent -= same,
        portfoliohistory row of type 'c' with quantity=cost
      - sell orders: profile.stock_<ticker> += quantity, portfoliohistory row
        of type 'C' with quantity=shares and the ticker

    Returns (refunded_buys, refunded_sells, skipped_orphans).
    """
    rows = await conn.fetch(
        'SELECT id, user_id, ticker, type_buy, quantity, price '
        'FROM public."order" WHERE time > 0'
    )
    if not rows:
        log("no live limit orders to refund")
        return (0, 0, 0)

    now = int(time.time())
    refunded_buys = 0
    refunded_sells = 0
    skipped = 0

    async with conn.transaction():
        for row in rows:
            profile_id = row["user_id"]
            ticker = row["ticker"]
            quantity = int(row["quantity"])
            price = int(row["price"])
            cost = quantity * price

            present = await conn.fetchval(
                "SELECT 1 FROM public.profile WHERE id = $1", profile_id
            )
            if not present:
                # Orphan order — owner profile vanished. Just drop it.
                skipped += 1
                continue

            if row["type_buy"]:
                await conn.execute(
                    "UPDATE public.profile SET coins = coins + $1, "
                    "stock_coins_spent = stock_coins_spent - $1 WHERE id = $2",
                    cost,
                    profile_id,
                )
                await conn.execute(
                    'INSERT INTO public.portfoliohistory '
                    '(user_id, type, quantity, time, ticker) '
                    "VALUES ($1, 'c', $2, $3, NULL)",
                    profile_id,
                    cost,
                    now,
                )
                refunded_buys += 1
            else:
                stock_col = f'stock_{ticker.lower()}'
                await conn.execute(
                    f'UPDATE public.profile SET "{stock_col}" = "{stock_col}" + $1 '
                    "WHERE id = $2",
                    quantity,
                    profile_id,
                )
                await conn.execute(
                    'INSERT INTO public.portfoliohistory '
                    '(user_id, type, quantity, time, ticker) '
                    "VALUES ($1, 'C', $2, $3, $4)",
                    profile_id,
                    quantity,
                    now,
                    ticker,
                )
                refunded_sells += 1

        await conn.execute('DELETE FROM public."order" WHERE time > 0')

    return refunded_buys, refunded_sells, skipped


async def delete_mm_orders(conn: asyncpg.Connection) -> int:
    """Drop every time=0 (market-maker) row. The bot's profile is NOT
    refunded — the new market doesn't use per-row inventory, so any
    coins/shares that accreted on the bot's profile via MM history are
    deliberately written off."""
    result = await conn.execute('DELETE FROM public."order" WHERE time = 0')
    # asyncpg returns 'DELETE N'
    try:
        deleted = int(result.split()[-1])
    except (ValueError, IndexError):
        deleted = 0
    return deleted


async def seed_newsevents(conn: asyncpg.Connection) -> int:
    """One headline per ticker at `now - 1` so the feed isn't empty on
    day 1. Idempotent: if any seed row for these tickers already exists,
    do nothing."""
    existing = await conn.fetchval(
        "SELECT COUNT(*) FROM public.newsevent WHERE event_type = 'system'"
    )
    if existing and int(existing) > 0:
        log(f"newsevent already seeded ({existing} system rows), skipping")
        return 0

    now_minus_1 = int(time.time()) - 1
    inserted = 0
    async with conn.transaction():
        for ticker in TICKERS:
            await conn.execute(
                'INSERT INTO public.newsevent '
                '(time, fires_at, ticker, event_type, headline, impulse_pct, applied) '
                "VALUES ($1, $1, $2, 'system', $3, 0, true)",
                now_minus_1,
                ticker,
                f"{ticker} resumes trading on the new market",
            )
            inserted += 1
    return inserted


async def main() -> int:
    if MARKER.exists():
        log(f"marker {MARKER} exists — migration already applied. Delete it to re-run.")
        return 0

    LOGFILE.write_text("", encoding="utf-8")
    log("starting migration 030_stock_market_v2")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        await create_newsevent(conn)

        buys, sells, skipped = await cancel_and_refund_limit_orders(conn)
        log(
            f"refunded limit orders: buys={buys} sells={sells} orphaned={skipped}"
        )

        mm_deleted = await delete_mm_orders(conn)
        log(f"deleted MM orders (time=0): {mm_deleted}")

        seeded = await seed_newsevents(conn)
        log(f"seeded newsevent rows: {seeded}")

        MARKER.write_text(
            json.dumps(
                {
                    "completed_at": time.time(),
                    "refunded_buys": buys,
                    "refunded_sells": sells,
                    "orphaned_orders": skipped,
                    "mm_orders_deleted": mm_deleted,
                    "news_seed_rows": seeded,
                },
                indent=2,
            ),
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
