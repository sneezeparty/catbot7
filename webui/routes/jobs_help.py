"""Jobs help-pages editor for config/jobs_help.json.

Structure: {"pages": [{title, body, min_level_to_see}, ...]}

Pages are edited in-place by index. Ordering matters (pages are shown in
order in /jobs help). No add/delete routes — use direct JSON editing and
Reload Bot for structural changes. Title and body are the only safety-relevant
fields; min_level_to_see controls which mafia ranks see each page.
"""

import aiohttp_jinja2
from aiohttp import web

from webui import io_locks, state, validators

JOBS_HELP_PATH = "config/jobs_help.json"


def _get_help_mutable():
    import config
    return getattr(config, "jobs_help", None)


async def index(request):
    jobs_help = state.get_jobs_help()
    pages = jobs_help.get("pages", [])
    return aiohttp_jinja2.render_template(
        "jobs_help.html",
        request,
        {
            "title": "Jobs Help",
            "active_section": "jobs_help",
            "pages": pages,
        },
    )


async def edit_page(request):
    i = int(request.match_info["i"])
    jobs_help = state.get_jobs_help()
    pages = jobs_help.get("pages", [])
    if i >= len(pages):
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "jobs_help_page_row.html",
        request,
        {"i": i, "page": pages[i], "editing": True},
    )


async def cancel_page(request):
    i = int(request.match_info["i"])
    jobs_help = state.get_jobs_help()
    pages = jobs_help.get("pages", [])
    if i >= len(pages):
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "jobs_help_page_row.html",
        request,
        {"i": i, "page": pages[i], "editing": False},
    )


async def save_page(request):
    i = int(request.match_info["i"])
    jobs_help = _get_help_mutable()
    if jobs_help is None:
        return web.Response(status=503, text="config.jobs_help not loaded")
    pages = jobs_help.get("pages", [])
    if i >= len(pages):
        return web.Response(status=404)
    form = await request.post()
    title = (form.get("title") or "").strip()
    body = (form.get("body") or "").strip()
    try:
        min_level = int(form.get("min_level_to_see", "0"))
    except ValueError:
        return web.Response(status=400, text="min_level_to_see must be an integer")
    if err := validators.validate_jobs_help_page(title, body, min_level):
        return web.Response(status=400, text=err)
    async with io_locks.lock_for(JOBS_HELP_PATH):
        pages[i]["title"] = title
        pages[i]["body"] = body
        pages[i]["min_level_to_see"] = min_level
        await io_locks.atomic_write_json(JOBS_HELP_PATH, jobs_help)
        state.mark_dirty("jobs_help")
    return aiohttp_jinja2.render_template(
        "jobs_help_page_row.html",
        request,
        {"i": i, "page": pages[i], "editing": False, "just_saved": True},
    )


def register(app: web.Application) -> None:
    app.router.add_get("/jobs/help", index)
    app.router.add_get(r"/jobs/help/{i:\d+}/edit", edit_page)
    app.router.add_get(r"/jobs/help/{i:\d+}/cancel", cancel_page)
    app.router.add_post(r"/jobs/help/{i:\d+}", save_page)
