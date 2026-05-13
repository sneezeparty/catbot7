"""Bot lifecycle actions: reload main, reload main+db."""

import aiohttp_jinja2
from aiohttp import web

from webui import state


async def reload_bot(request):
    try:
        await state.reload_main()
        msg = "Reloaded main extension."
        ok = True
    except Exception as exc:  # noqa: BLE001
        msg = f"Reload failed: {exc!r}"
        ok = False
    return aiohttp_jinja2.render_template(
        "partials/_flash.html", request, {"ok": ok, "message": msg}
    )


async def reload_db_and_bot(request):
    try:
        await state.reload_db()
        msg = "Reloaded database + main extension."
        ok = True
    except Exception as exc:  # noqa: BLE001
        msg = f"Reload failed: {exc!r}"
        ok = False
    return aiohttp_jinja2.render_template(
        "partials/_flash.html", request, {"ok": ok, "message": msg}
    )


def register(app: web.Application) -> None:
    app.router.add_post("/actions/reload-bot", reload_bot)
    app.router.add_post("/actions/reload-db", reload_db_and_bot)
