"""Prism listing (read-only)."""

import aiohttp_jinja2
from aiohttp import web

from webui import names, state


async def index(request):
    pool = state.get_pool()
    rows = []
    total = 0
    by_guild = []
    if pool is not None:
        async with pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM prism")
            rows = await conn.fetch(
                'SELECT id, user_id, guild_id, "time", creator, name, catches_boosted '
                "FROM prism ORDER BY catches_boosted DESC NULLS LAST LIMIT 200"
            )
            by_guild = await conn.fetch(
                "SELECT guild_id, COUNT(*) AS n FROM prism GROUP BY guild_id ORDER BY n DESC LIMIT 15"
            )
    await names.refresh_guild_name_cache()
    unames = await names.resolve_users(
        state.get_bot(),
        [r["user_id"] for r in rows] + [r["creator"] for r in rows],
    )
    return aiohttp_jinja2.render_template(
        "db_prism.html",
        request,
        {
            "title": "Prisms",
            "active_section": "prism_table",
            "rows": rows,
            "total": total,
            "by_guild": by_guild,
            "unames": unames,
        },
    )


def register(app: web.Application) -> None:
    app.router.add_get("/db/prism", index)
