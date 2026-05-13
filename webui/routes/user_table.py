"""User table — global per-user state. Curated editable fields only."""

import aiohttp_jinja2
from aiohttp import web

from webui import state

# Whitelisted editable fields. Everything else is view-only to avoid foot-guns.
INT_FIELDS = [
    "rain_minutes",
    "rain_minutes_bought",
    "vote_streak",
    "max_vote_streak",
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
    q = request.query.get("q", "").strip()
    if pool is not None and q:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                'SELECT * FROM "user" WHERE CAST(user_id AS TEXT) LIKE $1 OR username ILIKE $2 ORDER BY user_id LIMIT 100',
                f"%{q}%", f"%{q}%",
            )
    elif pool is not None:
        async with pool.acquire() as conn:
            rows = await conn.fetch('SELECT * FROM "user" ORDER BY total_votes DESC NULLS LAST LIMIT 50')
    return aiohttp_jinja2.render_template(
        "db_user.html",
        request,
        {
            "title": "Users",
            "active_section": "user_table",
            "rows": rows,
            "q": q,
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
        },
    )


async def update(request):
    user_id = int(request.match_info["id"])
    field = request.match_info["field"]
    if field not in INT_FIELDS + BOOL_FIELDS + STR_FIELDS:
        return web.Response(status=400, text="field not editable")
    form = await request.post()
    raw = form.get("value", "")
    pool = state.get_pool()
    if pool is None:
        return web.Response(status=503)
    try:
        if field in INT_FIELDS:
            new_value = int(raw)
        elif field in BOOL_FIELDS:
            new_value = raw.lower() in ("1", "true", "on", "yes")
        else:
            new_value = raw
    except ValueError:
        return web.Response(status=400, text="invalid value")
    async with pool.acquire() as conn:
        await conn.execute(f'UPDATE "user" SET {field} = $1 WHERE user_id = $2', new_value, user_id)
    return web.Response(text=f"saved {field}")


def register(app: web.Application) -> None:
    app.router.add_get("/db/user", index)
    app.router.add_get(r"/db/user/{id:\d+}", detail)
    app.router.add_post(r"/db/user/{id:\d+}/{field:[a-z_]+}", update)
