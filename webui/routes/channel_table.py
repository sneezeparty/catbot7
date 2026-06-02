"""Channel table (read-only) — spawn config + live spawn state."""

import time

import aiohttp_jinja2
from aiohttp import web

from webui import state


async def index(request):
    pool = state.get_pool()
    rows = []
    if pool is not None:
        q = request.query.get("q", "").strip()
        async with pool.acquire() as conn:
            if q:
                rows = await conn.fetch(
                    "SELECT * FROM channel WHERE CAST(channel_id AS TEXT) LIKE $1 ORDER BY channel_id LIMIT 200",
                    f"%{q}%",
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM channel ORDER BY (cat <> 0 OR yet_to_spawn > 0) DESC, channel_id LIMIT 200"
                )
    return aiohttp_jinja2.render_template(
        "db_channel.html",
        request,
        {
            "title": "Channels",
            "active_section": "channel_table",
            "rows": rows,
            "q": request.query.get("q", ""),
            "now": int(time.time()),
        },
    )


def register(app: web.Application) -> None:
    app.router.add_get("/db/channel", index)
