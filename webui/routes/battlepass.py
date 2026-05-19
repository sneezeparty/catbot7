"""Battlepass editor for config/battlepass.json.

Two surfaces:
- Seasons: 17 × 30 levels, each {xp, reward, amount}
- Quests: vote/catch/misc/extra/challenge, each entry {emoji, title, xp_min, xp_max, progress}

Deleting a quest requires reference-counting: if any profile row still has
that quest assigned, refuse the delete.
"""

import aiohttp_jinja2
from aiohttp import web

from webui import io_locks, state, validators

BATTLEPASS_PATH = "config/battlepass.json"

QUEST_PROFILE_COLUMN = {
    "catch": "catch_quest",
    "misc": "misc_quest",
    # "vote" quest is not stored on profile (vote streak tracked on user)
    "vote": None,
    # "extra" quests use profile.extra_quest; dynamic_reward entries have xp_min=xp_max=0
    "extra": "extra_quest",
    # "challenge" quests use profile.challenge_quest; 5th quest slot added in season with challenge track
    "challenge": "challenge_quest",
}


async def index(request):
    battle = state.get_battle()
    season_keys = sorted(battle.get("seasons", {}).keys(), key=lambda s: int(s))
    sel = request.query.get("season", season_keys[-1] if season_keys else "1")
    season = battle.get("seasons", {}).get(sel, [])
    return aiohttp_jinja2.render_template(
        "battlepass.html",
        request,
        {
            "title": "Battlepass",
            "active_section": "battlepass",
            "season_keys": season_keys,
            "selected_season": sel,
            "levels": season,
            "quests": battle.get("quests", {}),
        },
    )


# -------- level routes --------

async def edit_level(request):
    n = request.match_info["n"]
    i = int(request.match_info["i"])
    battle = state.get_battle()
    levels = battle.get("seasons", {}).get(n, [])
    if i >= len(levels):
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "battlepass_level_row.html",
        request,
        {"n": n, "i": i, "level": levels[i], "editing": True},
    )


async def cancel_level(request):
    n = request.match_info["n"]
    i = int(request.match_info["i"])
    battle = state.get_battle()
    levels = battle.get("seasons", {}).get(n, [])
    if i >= len(levels):
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "battlepass_level_row.html",
        request,
        {"n": n, "i": i, "level": levels[i], "editing": False},
    )


async def save_level(request):
    n = request.match_info["n"]
    i = int(request.match_info["i"])
    form = await request.post()
    battle = state.get_battle()
    levels = battle.get("seasons", {}).get(n)
    if levels is None or i >= len(levels):
        return web.Response(status=404)
    try:
        xp = int(form.get("xp", "0"))
        amount = int(form.get("amount", "0"))
    except ValueError:
        return web.Response(status=400, text="xp/amount must be integers")
    reward = (form.get("reward") or "").strip() or "Stone"

    if err := validators.validate_battlepass_level(xp, amount, reward):
        return web.Response(status=400, text=err)

    async with io_locks.lock_for(BATTLEPASS_PATH):
        levels[i] = {"xp": xp, "reward": reward, "amount": amount}
        await io_locks.atomic_write_json(BATTLEPASS_PATH, battle)
        state.mark_dirty("battlepass")

    return aiohttp_jinja2.render_template(
        "battlepass_level_row.html",
        request,
        {"n": n, "i": i, "level": levels[i], "editing": False, "just_saved": True},
    )


# -------- quest routes --------

async def edit_quest(request):
    qtype = request.match_info["qtype"]
    name = request.match_info["name"]
    battle = state.get_battle()
    quest = battle.get("quests", {}).get(qtype, {}).get(name)
    if quest is None:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "battlepass_quest_row.html",
        request,
        {"qtype": qtype, "name": name, "quest": quest, "editing": True},
    )


async def cancel_quest(request):
    qtype = request.match_info["qtype"]
    name = request.match_info["name"]
    battle = state.get_battle()
    quest = battle.get("quests", {}).get(qtype, {}).get(name)
    if quest is None:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "battlepass_quest_row.html",
        request,
        {"qtype": qtype, "name": name, "quest": quest, "editing": False},
    )


async def save_quest(request):
    qtype = request.match_info["qtype"]
    name = request.match_info["name"]
    form = await request.post()
    battle = state.get_battle()
    quests = battle.get("quests", {}).get(qtype)
    if quests is None or name not in quests:
        return web.Response(status=404)
    try:
        xp_min = int(form.get("xp_min", "0"))
        xp_max = int(form.get("xp_max", "0"))
        progress = int(form.get("progress", "1"))
    except ValueError:
        return web.Response(status=400, text="xp_min/xp_max/progress must be integers")
    emoji = (form.get("emoji") or "").strip()
    title = (form.get("title") or "").strip()

    if err := validators.validate_battlepass_quest(xp_min, xp_max, progress, title):
        return web.Response(status=400, text=err)

    async with io_locks.lock_for(BATTLEPASS_PATH):
        updated = {"emoji": emoji, "title": title, "xp_min": xp_min, "xp_max": xp_max, "progress": progress}
        # preserve "dynamic_reward": true if it exists on extra quests
        if quests[name].get("dynamic_reward"):
            updated["dynamic_reward"] = True
        quests[name] = updated
        await io_locks.atomic_write_json(BATTLEPASS_PATH, battle)
        state.mark_dirty("battlepass")

    return aiohttp_jinja2.render_template(
        "battlepass_quest_row.html",
        request,
        {"qtype": qtype, "name": name, "quest": quests[name], "editing": False, "just_saved": True},
    )


async def delete_quest(request):
    qtype = request.match_info["qtype"]
    name = request.match_info["name"]
    battle = state.get_battle()
    quests = battle.get("quests", {}).get(qtype)
    if quests is None or name not in quests:
        return web.Response(status=404)

    column = QUEST_PROFILE_COLUMN.get(qtype)
    if column is not None:
        pool = state.get_pool()
        if pool is not None:
            async with pool.acquire() as conn:
                live = await conn.fetchval(f'SELECT COUNT(*) FROM profile WHERE {column} = $1', name)
            if live and live > 0:
                return aiohttp_jinja2.render_template(
                    "battlepass_quest_row.html",
                    request,
                    {
                        "qtype": qtype,
                        "name": name,
                        "quest": quests[name],
                        "editing": False,
                        "delete_blocked": True,
                        "live_refs": live,
                    },
                    status=409,
                )

    async with io_locks.lock_for(BATTLEPASS_PATH):
        del quests[name]
        await io_locks.atomic_write_json(BATTLEPASS_PATH, battle)
        state.mark_dirty("battlepass")

    return web.Response(text="")


def register(app: web.Application) -> None:
    app.router.add_get("/battlepass", index)
    app.router.add_get(r"/battlepass/season/{n:\d+}/level/{i:\d+}/edit", edit_level)
    app.router.add_get(r"/battlepass/season/{n:\d+}/level/{i:\d+}/cancel", cancel_level)
    app.router.add_post(r"/battlepass/season/{n:\d+}/level/{i:\d+}", save_level)
    app.router.add_get(r"/battlepass/quest/{qtype:vote|catch|misc|extra|challenge}/{name:[A-Za-z0-9_+\-]+}/edit", edit_quest)
    app.router.add_get(r"/battlepass/quest/{qtype:vote|catch|misc|extra|challenge}/{name:[A-Za-z0-9_+\-]+}/cancel", cancel_quest)
    app.router.add_post(r"/battlepass/quest/{qtype:vote|catch|misc|extra|challenge}/{name:[A-Za-z0-9_+\-]+}", save_quest)
    app.router.add_post(r"/battlepass/quest/{qtype:vote|catch|misc|extra|challenge}/{name:[A-Za-z0-9_+\-]+}/delete", delete_quest)
