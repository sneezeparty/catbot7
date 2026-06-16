"""User table (read-only) — global per-user state."""

import aiohttp_jinja2
from aiohttp import web

from webui import names, state
from webui.pagination import make_pager, parse_page

PER_PAGE = 50

# Field groupings for the read-only detail view.
INT_FIELDS = [
    "rain_minutes",
    "rain_minutes_bought",
    "daily_catch_streak",
    "max_daily_streak",
    "streak_freezes",
    "total_votes",
    "vote_time_topgg",
    "reminder_vote",
    "custom_num",
    "cats_blessed",
    "dms",
    "last_bakegg_send",
    "last_bakegg_get",
    "last_catch_day",
    "dm_channel_id",
]
BOOL_FIELDS = [
    "premium",
    "claimed_free_rain",
    "blessings_enabled",
    "blessings_anonymous",
    "queued_chef_pack",
]
STR_FIELDS = [
    "custom",
    "emoji",
    "color",
    "image",
    "username",
    "news_state",
]


async def index(request):
    pool = state.get_pool()
    rows = []
    total = 0
    q = request.query.get("q", "").strip()
    page = parse_page(request)
    offset = (page - 1) * PER_PAGE
    if pool is not None and q:
        async with pool.acquire() as conn:
            total = await conn.fetchval(
                'SELECT COUNT(*) FROM "user" WHERE CAST(user_id AS TEXT) LIKE $1 OR username ILIKE $2',
                f"%{q}%", f"%{q}%",
            ) or 0
            rows = await conn.fetch(
                'SELECT * FROM "user" WHERE CAST(user_id AS TEXT) LIKE $1 OR username ILIKE $2 '
                'ORDER BY user_id LIMIT $3 OFFSET $4',
                f"%{q}%", f"%{q}%", PER_PAGE, offset,
            )
    elif pool is not None:
        async with pool.acquire() as conn:
            total = await conn.fetchval('SELECT COUNT(*) FROM "user"') or 0
            rows = await conn.fetch(
                'SELECT * FROM "user" ORDER BY total_votes DESC NULLS LAST LIMIT $1 OFFSET $2',
                PER_PAGE, offset,
            )
    unames = await names.resolve_users(state.get_bot(), [r["user_id"] for r in rows])
    pager = make_pager(
        request,
        page=page,
        per_page=PER_PAGE,
        total=int(total),
        base_path="/db/user",
        params={"q": q},
        target="#pager-user",
    )
    return aiohttp_jinja2.render_template(
        "db_user.html",
        request,
        {
            "title": "Users",
            "active_section": "user_table",
            "rows": rows,
            "q": q,
            "unames": unames,
            "pager": pager,
        },
    )


async def detail(request):
    user_id = int(request.match_info["id"])
    pool = state.get_pool()
    if pool is None:
        return web.Response(status=503)
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT * FROM "user" WHERE user_id = $1', user_id)
    if row is None:
        return web.Response(status=404)
    unames = await names.resolve_users(state.get_bot(), [user_id])
    return aiohttp_jinja2.render_template(
        "db_user_detail.html",
        request,
        {
            "title": f"User {user_id}",
            "active_section": "user_table",
            "row": dict(row),
            "int_fields": INT_FIELDS,
            "bool_fields": BOOL_FIELDS,
            "str_fields": STR_FIELDS,
            "unames": unames,
        },
    )


def register(app: web.Application) -> None:
    app.router.add_get("/db/user", index)
    app.router.add_get(r"/db/user/{id:\d+}", detail)
