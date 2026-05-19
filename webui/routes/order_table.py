"""Stock market order book — read-only viewer.

Displays open orders (all tickers) with market-maker orders clearly labelled.
MM orders are identified by time = 0; they are recreated automatically by the
background loop every ~5 min if deleted.
"""

import aiohttp_jinja2
from aiohttp import web

from webui import state

# The bot's own profile row uses guild_id=0 / user_id=bot.user.id.
# We surface the bot's profile id in the template so admins can see which
# user_id belongs to the MM without having to know the bot snowflake.
_MM_TIME_SENTINEL = 0


async def index(request):
    pool = state.get_pool()
    rows = []
    total = 0
    mm_user_id = None
    by_ticker = []
    if pool is not None:
        async with pool.acquire() as conn:
            total = await conn.fetchval('SELECT COUNT(*) FROM "order"')
            rows = await conn.fetch(
                'SELECT id, user_id, time, ticker, type_buy, quantity, price '
                'FROM "order" '
                "ORDER BY ticker, type_buy DESC, price DESC "
                "LIMIT 500"
            )
            by_ticker = await conn.fetch(
                'SELECT ticker, '
                "  COUNT(*) FILTER (WHERE type_buy)  AS buy_count, "
                "  COUNT(*) FILTER (WHERE NOT type_buy) AS sell_count "
                'FROM "order" GROUP BY ticker ORDER BY ticker'
            )
            # Resolve the bot's profile id so the template can annotate MM rows.
            mm_row = await conn.fetchrow(
                'SELECT id FROM profile WHERE guild_id = 0 LIMIT 1'
            )
            if mm_row:
                mm_user_id = mm_row["id"]
    return aiohttp_jinja2.render_template(
        "db_order.html",
        request,
        {
            "title": "Orders",
            "active_section": "order_table",
            "rows": rows,
            "total": total,
            "by_ticker": by_ticker,
            "mm_user_id": mm_user_id,
            "MM_TIME": _MM_TIME_SENTINEL,
        },
    )


def register(app: web.Application) -> None:
    app.router.add_get("/db/order", index)
