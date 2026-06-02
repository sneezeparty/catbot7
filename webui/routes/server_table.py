"""Server table viewer (read-only) — per-guild feature toggles."""

import aiohttp_jinja2
from aiohttp import web

from webui import state

TOGGLES = [
    "only_setupped_channels",
    "do_reactions",
    "do_responses",
    "do_rain",
    "do_catnip",
    "auto_delete_achievements",
    "auto_delete_catches",
    "mute_achievements",
    "anti_double_catch",
    "season_announcements",
]


async def index(request):
    pool = state.get_pool()
    rows = []
    if pool is not None:
        q_filter = request.query.get("q", "").strip()
        async with pool.acquire() as conn:
            if q_filter:
                rows = await conn.fetch(
                    f"SELECT * FROM server WHERE CAST(server_id AS TEXT) LIKE $1 ORDER BY server_id LIMIT 200",
                    f"%{q_filter}%",
                )
            else:
                rows = await conn.fetch("SELECT * FROM server ORDER BY server_id LIMIT 200")
    return aiohttp_jinja2.render_template(
        "db_server.html",
        request,
        {
            "title": "Server settings",
            "active_section": "server_table",
            "rows": rows,
            "toggles": TOGGLES,
            "q": request.query.get("q", ""),
        },
    )


def register(app: web.Application) -> None:
    app.router.add_get("/db/server", index)
