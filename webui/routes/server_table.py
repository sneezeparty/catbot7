"""Server table viewer (read-only) — per-guild feature toggles."""

import aiohttp_jinja2
from aiohttp import web

from webui import names, state
from webui.pagination import make_pager, parse_page

PER_PAGE = 50

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
    total = 0
    q_filter = request.query.get("q", "").strip()
    page = parse_page(request)
    if pool is not None:
        async with pool.acquire() as conn:
            if q_filter:
                total = await conn.fetchval(
                    "SELECT COUNT(*) FROM server WHERE CAST(server_id AS TEXT) LIKE $1",
                    f"%{q_filter}%",
                ) or 0
                rows = await conn.fetch(
                    "SELECT * FROM server WHERE CAST(server_id AS TEXT) LIKE $1 "
                    "ORDER BY server_id LIMIT $2 OFFSET $3",
                    f"%{q_filter}%", PER_PAGE, (page - 1) * PER_PAGE,
                )
            else:
                total = await conn.fetchval("SELECT COUNT(*) FROM server") or 0
                rows = await conn.fetch(
                    "SELECT * FROM server ORDER BY server_id LIMIT $1 OFFSET $2",
                    PER_PAGE, (page - 1) * PER_PAGE,
                )
    await names.refresh_guild_name_cache()
    pager = make_pager(
        request,
        page=page,
        per_page=PER_PAGE,
        total=int(total),
        base_path="/db/server",
        params={"q": q_filter},
        target="#pager-server",
    )
    return aiohttp_jinja2.render_template(
        "db_server.html",
        request,
        {
            "title": "Server settings",
            "active_section": "server_table",
            "rows": rows,
            "toggles": TOGGLES,
            "q": q_filter,
            "pager": pager,
        },
    )


def register(app: web.Application) -> None:
    app.router.add_get("/db/server", index)
