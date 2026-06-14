# Cat Bot - A Discord bot about catching cats.
# Copyright (C) 2026 Lia Milenakos & Cat Bot Contributors
# Copyright (C) 2026 sneezeparty
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Announcements editor — the SECOND editable section of the dashboard.

Lets the operator draft a Discord-markdown announcement and broadcast it to
every setupped catch channel. The actual broadcast runs in
webui/announce_sender.broadcast_announcement(), spawned as an asyncio task so
the HTTP handler returns immediately."""

import asyncio
import datetime
import time

import aiohttp_jinja2
from aiohttp import web

from webui import state
from webui.announce_sender import broadcast_announcement

DISCORD_MESSAGE_LIMIT = 2000
HISTORY_LIMIT = 30


def _fmt_ts(ts) -> str:
    if not ts:
        return "—"
    try:
        return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, OSError, TypeError):
        return "—"


async def _channel_count_estimate(pool, one_per_server: bool) -> int:
    """Best-effort count for the preview screen. one_per_server requires the
    bot cache to dedupe, so it's an upper bound — we can't precount skipped
    channels without iterating, so we just return total setupped count."""
    if pool is None:
        return 0
    async with pool.acquire() as conn:
        return int(await conn.fetchval("SELECT COUNT(*) FROM channel") or 0)


async def _load_history(pool):
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, created_at, sent_at, body, status, one_per_server, "
            "target_count, sent_count, failed_count, skipped_count, error "
            "FROM announcement ORDER BY id DESC LIMIT $1",
            HISTORY_LIMIT,
        )
    return [dict(r) for r in rows]


async def index(request):
    pool = state.get_pool()
    history = await _load_history(pool)
    body = request.query.get("body", "")
    one_per_server = request.query.get("one_per_server", "1") == "1"
    return aiohttp_jinja2.render_template(
        "announce.html",
        request,
        {
            "title": "Announcements",
            "active_section": "announce",
            "body": body,
            "one_per_server": one_per_server,
            "preview": None,
            "history": history,
            "saved": request.query.get("saved"),
            "saved_id": request.query.get("id"),
            "fmt_ts": _fmt_ts,
            "message_limit": DISCORD_MESSAGE_LIMIT,
        },
    )


async def preview(request):
    pool = state.get_pool()
    form = await request.post()
    body = (form.get("body") or "").strip()
    one_per_server = form.get("one_per_server") == "1"
    error = None
    if not body:
        error = "Body is empty — there's nothing to send."
    elif len(body) > DISCORD_MESSAGE_LIMIT:
        error = f"Body is {len(body):,} chars — Discord's limit is {DISCORD_MESSAGE_LIMIT:,}. Trim it down."
    target_estimate = await _channel_count_estimate(pool, one_per_server)
    history = await _load_history(pool)
    return aiohttp_jinja2.render_template(
        "announce.html",
        request,
        {
            "title": "Announcements",
            "active_section": "announce",
            "body": body,
            "one_per_server": one_per_server,
            "preview": {
                "body": body,
                "target_estimate": target_estimate,
                "char_count": len(body),
                "error": error,
            },
            "history": history,
            "saved": None,
            "saved_id": None,
            "fmt_ts": _fmt_ts,
            "message_limit": DISCORD_MESSAGE_LIMIT,
        },
    )


async def send(request):
    pool = state.get_pool()
    if pool is None:
        return web.Response(status=503, text="pool unavailable")
    form = await request.post()
    body = (form.get("body") or "").strip()
    one_per_server = form.get("one_per_server") == "1"
    if not body or len(body) > DISCORD_MESSAGE_LIMIT:
        raise web.HTTPSeeOther("/announce?saved=invalid")
    async with pool.acquire() as conn:
        ann_id = await conn.fetchval(
            "INSERT INTO announcement (created_at, body, one_per_server) "
            "VALUES ($1, $2, $3) RETURNING id",
            int(time.time()), body, one_per_server,
        )
    # Fire-and-forget on the bot's loop. Webui shares the loop; this works.
    asyncio.create_task(broadcast_announcement(int(ann_id)))
    raise web.HTTPSeeOther(f"/announce?saved=launched&id={ann_id}")


def register(app: web.Application) -> None:
    app.router.add_get("/announce", index)
    app.router.add_post("/announce/preview", preview)
    app.router.add_post("/announce/send", send)
