"""Stock market order book — read-only viewer.

Displays open limit orders across every ticker. The simulated-market engine
(see docs/design/economy.md → "Simulated market") doesn't write market-maker
rows anymore — every row is a user-placed limit order. Migration 030 cleared
the legacy `time = 0` MM rows; this page won't see any.
"""

import aiohttp_jinja2
from aiohttp import web

from webui import names, state
from webui.pagination import make_pager, parse_page

PER_PAGE = 100


async def index(request):
    pool = state.get_pool()
    rows = []
    total = 0
    by_ticker = []
    page = parse_page(request)
    if pool is not None:
        async with pool.acquire() as conn:
            total = await conn.fetchval('SELECT COUNT(*) FROM "order"') or 0
            rows = await conn.fetch(
                'SELECT id, user_id, time, ticker, type_buy, quantity, price '
                'FROM "order" '
                "ORDER BY ticker, type_buy DESC, price DESC "
                "LIMIT $1 OFFSET $2",
                PER_PAGE, (page - 1) * PER_PAGE,
            )
            by_ticker = await conn.fetch(
                'SELECT ticker, '
                "  COUNT(*) FILTER (WHERE type_buy)  AS buy_count, "
                "  COUNT(*) FILTER (WHERE NOT type_buy) AS sell_count "
                'FROM "order" GROUP BY ticker ORDER BY ticker'
            )
    unames = await names.resolve_users(state.get_bot(), [r["user_id"] for r in rows])
    pager = make_pager(
        request,
        page=page,
        per_page=PER_PAGE,
        total=int(total),
        base_path="/db/order",
        target="#pager-order",
    )
    return aiohttp_jinja2.render_template(
        "db_order.html",
        request,
        {
            "title": "Orders",
            "active_section": "order_table",
            "rows": rows,
            "total": total,
            "by_ticker": by_ticker,
            "unames": unames,
            "pager": pager,
        },
    )


def register(app: web.Application) -> None:
    app.router.add_get("/db/order", index)
