"""News editor — the ONE editable section of the otherwise read-only dashboard.

Manages config/news.json, the data source for the bot's /news command (The Cat
Bot Times). The bot reads that file fresh on each /news call, so saves here go
live with no restart. Deletes also splice every user's positional news_state so
read/unread tracking stays aligned.
"""

import datetime
import json
from pathlib import Path

import aiohttp_jinja2
from aiohttp import web

from webui import state
from webui.io_locks import atomic_write_json

NEWS_PATH = "config/news.json"
_ROOT = Path(__file__).resolve().parent.parent.parent


def _load() -> list:
    try:
        with open(_ROOT / NEWS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        arts = data.get("articles", [])
        return arts if isinstance(arts, list) else []
    except Exception:
        return []


def _parse_date(raw: str):
    """'YYYY-MM-DD' -> unix ts at 00:00 UTC, or None."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None


def _date_input_value(ts) -> str:
    """unix ts -> 'YYYY-MM-DD' for the <input type=date> value (or '')."""
    if not ts:
        return ""
    try:
        return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OSError, TypeError):
        return ""


def _article_from_form(form) -> dict:
    art = {
        "emoji": form.get("emoji", "").strip(),
        "title": form.get("title", "").strip(),
        "body": form.get("body", "").strip(),
    }
    ts = _parse_date(form.get("date", ""))
    if ts:
        art["date"] = ts
    buttons = []
    labels = form.getall("button_label", [])
    urls = form.getall("button_url", [])
    for label, url in zip(labels, urls):
        url = (url or "").strip()
        if url:
            buttons.append({"label": (label or "Link").strip(), "url": url})
    if buttons:
        art["buttons"] = buttons
    return art


async def index(request):
    articles = _load()
    edit_idx = request.query.get("edit")
    try:
        edit_idx = int(edit_idx) if edit_idx is not None else None
        if edit_idx is not None and not (0 <= edit_idx < len(articles)):
            edit_idx = None
    except ValueError:
        edit_idx = None
    return aiohttp_jinja2.render_template(
        "news.html",
        request,
        {
            "title": "News",
            "active_section": "news",
            "articles": articles,
            "edit_idx": edit_idx,
            "edit_article": articles[edit_idx] if edit_idx is not None else None,
            "date_input_value": _date_input_value,
            "saved": request.query.get("saved"),
        },
    )


async def add(request):
    form = await request.post()
    art = _article_from_form(form)
    if not art["title"] and not art["body"]:
        raise web.HTTPSeeOther("/news?saved=empty")
    articles = _load()
    articles.append(art)
    await atomic_write_json(NEWS_PATH, {"articles": articles})
    raise web.HTTPSeeOther("/news?saved=added")


async def edit(request):
    idx = int(request.match_info["idx"])
    articles = _load()
    if not (0 <= idx < len(articles)):
        return web.Response(status=404, text="no such article")
    form = await request.post()
    articles[idx] = _article_from_form(form)
    await atomic_write_json(NEWS_PATH, {"articles": articles})
    raise web.HTTPSeeOther("/news?saved=edited")


async def delete(request):
    idx = int(request.match_info["idx"])
    articles = _load()
    if not (0 <= idx < len(articles)):
        return web.Response(status=404, text="no such article")
    del articles[idx]
    await atomic_write_json(NEWS_PATH, {"articles": articles})
    # keep positional read-state aligned: drop char at index `idx` for everyone
    pool = state.get_pool()
    if pool is not None:
        async with pool.acquire() as conn:
            await conn.execute(
                'UPDATE "user" SET news_state = left(news_state, $1) || substr(news_state, $1 + 2) '
                "WHERE length(news_state) > $1",
                idx,
            )
    raise web.HTTPSeeOther("/news?saved=deleted")


def register(app: web.Application) -> None:
    app.router.add_get("/news", index)
    app.router.add_post("/news/add", add)
    app.router.add_post(r"/news/edit/{idx:\d+}", edit)
    app.router.add_post(r"/news/delete/{idx:\d+}", delete)
