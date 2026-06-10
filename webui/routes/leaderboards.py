"""Leaderboards: top users across several axes.

Read-only. Each board aggregates per user_id across all their (user, guild)
profiles, mirroring the SQL shapes used by main.py's /leaderboards command.
"""

import aiohttp_jinja2
from aiohttp import web

from webui import names, state

LIMIT = 15

# (key, title, unit, sql) — sql returns (user_id, value) rows, value DESC.
BOARDS = [
    ("catches", "Catches", "catches",
     "SELECT user_id, SUM(total_catches)::bigint AS value FROM profile "
     "GROUP BY user_id ORDER BY value DESC NULLS LAST LIMIT $1"),
    ("coins", "Coins", "coins",
     "SELECT user_id, SUM(coins)::bigint AS value FROM profile "
     "GROUP BY user_id ORDER BY value DESC NULLS LAST LIMIT $1"),
    ("prisms", "Prisms crafted", "prisms",
     "SELECT user_id, COUNT(*)::bigint AS value FROM prism "
     "GROUP BY user_id ORDER BY value DESC NULLS LAST LIMIT $1"),
    ("battlepass", "Highest battlepass", "level",
     "SELECT user_id, MAX(battlepass)::bigint AS value FROM profile "
     "GROUP BY user_id ORDER BY value DESC NULLS LAST LIMIT $1"),
    ("jobs", "Jobs completed", "jobs",
     "SELECT user_id, SUM(jobs_completed)::bigint AS value FROM profile "
     "GROUP BY user_id ORDER BY value DESC NULLS LAST LIMIT $1"),
    ("catnip", "Highest catnip level", "level",
     "SELECT user_id, MAX(catnip_level)::bigint AS value FROM profile "
     "GROUP BY user_id ORDER BY value DESC NULLS LAST LIMIT $1"),
]


async def index(request):
    pool = state.get_pool()
    boards: list = []
    if pool is not None:
        async with pool.acquire() as conn:
            for key, title, unit, sql in BOARDS:
                rows = await conn.fetch(sql, LIMIT)
                entries = [
                    {"user_id": r["user_id"], "value": int(r["value"] or 0)}
                    for r in rows if (r["value"] or 0) > 0
                ]
                top = entries[0]["value"] if entries else 1
                boards.append({
                    "key": key, "title": title, "unit": unit,
                    "entries": entries, "top": top,
                })
    await names.refresh_guild_name_cache()
    unames = await names.resolve_users(
        state.get_bot(), [e["user_id"] for b in boards for e in b["entries"]]
    )
    return aiohttp_jinja2.render_template(
        "leaderboards.html",
        request,
        {
            "title": "Leaderboards",
            "active_section": "leaderboards",
            "boards": boards,
            "limit": LIMIT,
            "unames": unames,
        },
    )


def register(app: web.Application) -> None:
    app.router.add_get("/leaderboards", index)
