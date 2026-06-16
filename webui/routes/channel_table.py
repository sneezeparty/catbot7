"""Channel table (read-only) — spawn config + live spawn state."""

import time

import aiohttp_jinja2
from aiohttp import web

from webui import state
from webui.pagination import make_pager, parse_page

PER_PAGE = 50


async def index(request):
    pool = state.get_pool()
    rows = []
    total = 0
    q = request.query.get("q", "").strip()
    page = parse_page(request)
    if pool is not None:
        async with pool.acquire() as conn:
            if q:
                total = await conn.fetchval(
                    "SELECT COUNT(*) FROM channel WHERE CAST(channel_id AS TEXT) LIKE $1",
                    f"%{q}%",
                ) or 0
                rows = await conn.fetch(
                    "SELECT * FROM channel WHERE CAST(channel_id AS TEXT) LIKE $1 "
                    "ORDER BY channel_id LIMIT $2 OFFSET $3",
                    f"%{q}%", PER_PAGE, (page - 1) * PER_PAGE,
                )
            else:
                total = await conn.fetchval("SELECT COUNT(*) FROM channel") or 0
                rows = await conn.fetch(
                    "SELECT * FROM channel "
                    "ORDER BY (cat <> 0 OR yet_to_spawn > 0) DESC, channel_id "
                    "LIMIT $1 OFFSET $2",
                    PER_PAGE, (page - 1) * PER_PAGE,
                )
    pager = make_pager(
        request,
        page=page,
        per_page=PER_PAGE,
        total=int(total),
        base_path="/db/channel",
        params={"q": q},
        target="#pager-channel",
    )
    return aiohttp_jinja2.render_template(
        "db_channel.html",
        request,
        {
            "title": "Channels",
            "active_section": "channel_table",
            "rows": rows,
            "q": q,
            "now": int(time.time()),
            "pager": pager,
        },
    )


def register(app: web.Application) -> None:
    app.router.add_get("/db/channel", index)
