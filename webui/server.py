"""aiohttp server entry. Bound to 127.0.0.1:9445."""

import logging
from pathlib import Path

import aiohttp_jinja2
import jinja2
from aiohttp import web

from webui import names, state
from webui.routes import register as register_routes

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 9445

WEBUI_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEBUI_DIR / "templates"
STATIC_DIR = WEBUI_DIR / "static"


def _format_ts(value):
    import datetime

    if not value:
        return "never"
    try:
        return datetime.datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        return str(value)


def _format_duration(value):
    if not value:
        return "0s"
    seconds = int(float(value))
    parts = []
    for label, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            parts.append(f"{seconds // size}{label}")
            seconds %= size
        if len(parts) >= 2:
            break
    return " ".join(parts) or "0s"


def build_app(bot) -> web.Application:
    state.init(bot)
    app = web.Application()

    env = aiohttp_jinja2.setup(
        app,
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
    )
    env.filters["fmt_ts"] = _format_ts
    env.filters["fmt_dur"] = _format_duration
    env.globals["state"] = state
    env.globals["guild_name"] = names.guild_name
    env.globals["channel_name"] = names.channel_name

    app.router.add_static("/static/", STATIC_DIR, name="static")
    register_routes(app)
    return app


async def start_server(bot) -> None:
    """Mounted from bot.py setup_hook via asyncio.create_task."""
    try:
        app = build_app(bot)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, HOST, PORT)
        await site.start()
        log.info("webui listening on http://%s:%s", HOST, PORT)
    except Exception:  # noqa: BLE001
        log.exception("webui failed to start; bot continues running")
