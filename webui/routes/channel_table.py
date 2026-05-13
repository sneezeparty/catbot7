"""Channel table — spawn config + active spawn state."""

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


async def edit(request):
    channel_id = int(request.match_info["id"])
    pool = state.get_pool()
    if pool is None:
        return web.Response(status=503)
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM channel WHERE channel_id = $1", channel_id)
    if row is None:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "db_channel_row.html",
        request,
        {"row": row, "editing": True, "now": int(time.time())},
    )


async def cancel(request):
    channel_id = int(request.match_info["id"])
    pool = state.get_pool()
    if pool is None:
        return web.Response(status=503)
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM channel WHERE channel_id = $1", channel_id)
    if row is None:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "db_channel_row.html",
        request,
        {"row": row, "editing": False, "now": int(time.time())},
    )


async def save(request):
    channel_id = int(request.match_info["id"])
    form = await request.post()
    try:
        spawn_min = int(form.get("spawn_times_min", "60"))
        spawn_max = int(form.get("spawn_times_max", "600"))
    except ValueError:
        return web.Response(status=400, text="spawn times must be integers")
    if spawn_min < 1 or spawn_max < spawn_min:
        return web.Response(status=400, text="spawn_times_min < spawn_times_max and both > 0")
    pool = state.get_pool()
    if pool is None:
        return web.Response(status=503)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE channel SET spawn_times_min = $1, spawn_times_max = $2 WHERE channel_id = $3",
            spawn_min, spawn_max, channel_id,
        )
        row = await conn.fetchrow("SELECT * FROM channel WHERE channel_id = $1", channel_id)
    return aiohttp_jinja2.render_template(
        "db_channel_row.html",
        request,
        {"row": row, "editing": False, "now": int(time.time()), "just_saved": True},
    )


def register(app: web.Application) -> None:
    app.router.add_get("/db/channel", index)
    app.router.add_get(r"/db/channel/{id:\d+}/edit", edit)
    app.router.add_get(r"/db/channel/{id:\d+}/cancel", cancel)
    app.router.add_post(r"/db/channel/{id:\d+}", save)
