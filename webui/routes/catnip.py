"""Catnip editor for config/catnip.json.

Four top-level lists: perks (14), quotes (10 bosses), bounties (3 templates),
levels (12 mafia tiers).
"""

import aiohttp_jinja2
from aiohttp import web

from webui import io_locks, state, validators

CATNIP_PATH = "config/catnip.json"


async def index(request):
    catnip = state.get_catnip()
    return aiohttp_jinja2.render_template(
        "catnip.html",
        request,
        {
            "title": "Catnip",
            "active_section": "catnip",
            "perks": catnip.get("perks", []),
            "levels": catnip.get("levels", []),
            "quotes": catnip.get("quotes", []),
            "bounties": catnip.get("bounties", []),
        },
    )


def _get_catnip_mutable():
    main = state.get_main()
    if main is None:
        return None
    return getattr(main, "catnip_list", None)


# -------- perk routes --------

async def edit_perk(request):
    i = int(request.match_info["i"])
    catnip = state.get_catnip()
    perks = catnip.get("perks", [])
    if i >= len(perks):
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "catnip_perk_row.html",
        request,
        {"i": i, "perk": perks[i], "editing": True},
    )


async def cancel_perk(request):
    i = int(request.match_info["i"])
    catnip = state.get_catnip()
    perks = catnip.get("perks", [])
    if i >= len(perks):
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "catnip_perk_row.html",
        request,
        {"i": i, "perk": perks[i], "editing": False},
    )


async def save_perk(request):
    i = int(request.match_info["i"])
    catnip = _get_catnip_mutable()
    if catnip is None:
        return web.Response(status=503, text="main not loaded")
    perks = catnip.get("perks", [])
    if i >= len(perks):
        return web.Response(status=404)
    form = await request.post()
    try:
        weight = int(form.get("weight", "0"))
        values = [float(form.get(f"v{n}", "0")) for n in range(5)]
    except ValueError:
        return web.Response(status=400, text="weight/values must be numeric")

    if err := validators.validate_catnip_perk(weight, values):
        return web.Response(status=400, text=err)

    async with io_locks.lock_for(CATNIP_PATH):
        perks[i]["name"] = (form.get("name") or "").strip()
        perks[i]["desc"] = (form.get("desc") or "").strip()
        perks[i]["weight"] = weight
        perks[i]["values"] = values
        perks[i]["exclusive"] = form.get("exclusive") in ("on", "true", "1")
        await io_locks.atomic_write_json(CATNIP_PATH, catnip)
        state.mark_dirty("catnip")

    return aiohttp_jinja2.render_template(
        "catnip_perk_row.html",
        request,
        {"i": i, "perk": perks[i], "editing": False, "just_saved": True},
    )


# -------- mafia level routes --------

async def edit_level(request):
    i = int(request.match_info["i"])
    catnip = state.get_catnip()
    levels = catnip.get("levels", [])
    if i >= len(levels):
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "catnip_level_row.html",
        request,
        {"i": i, "level": levels[i], "editing": True},
    )


async def cancel_level(request):
    i = int(request.match_info["i"])
    catnip = state.get_catnip()
    levels = catnip.get("levels", [])
    if i >= len(levels):
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "catnip_level_row.html",
        request,
        {"i": i, "level": levels[i], "editing": False},
    )


async def save_level(request):
    i = int(request.match_info["i"])
    catnip = _get_catnip_mutable()
    if catnip is None:
        return web.Response(status=503, text="main not loaded")
    levels = catnip.get("levels", [])
    if i >= len(levels):
        return web.Response(status=404)
    form = await request.post()
    try:
        duration = int(form.get("duration", "0"))
        cost = int(form.get("cost", "0"))
        bounty_difficulty = int(form.get("bounty_difficulty", "0"))
        bounty_amount = int(form.get("bounty_amount", "0"))
        bonus = float(form.get("bonus", "0"))
        max_amount = int(form.get("max_amount", "9999"))
        weights = {
            r: int(form.get(f"weight_{r}", "0"))
            for r in ("common", "uncommon", "rare", "epic", "legendary")
        }
    except ValueError:
        return web.Response(status=400, text="numeric fields invalid")

    if err := validators.validate_catnip_level(
        duration, cost, bounty_difficulty, bounty_amount, bonus, max_amount, weights
    ):
        return web.Response(status=400, text=err)

    async with io_locks.lock_for(CATNIP_PATH):
        levels[i].update({
            "duration": duration,
            "cost": cost,
            "bounty_difficulty": bounty_difficulty,
            "bounty_amount": bounty_amount,
            "bonus": bonus,
            "max_amount": max_amount,
            "weights": weights,
        })
        name = (form.get("name") or "").strip()
        if name:
            levels[i]["name"] = name
        await io_locks.atomic_write_json(CATNIP_PATH, catnip)
        state.mark_dirty("catnip")

    return aiohttp_jinja2.render_template(
        "catnip_level_row.html",
        request,
        {"i": i, "level": levels[i], "editing": False, "just_saved": True},
    )


def register(app: web.Application) -> None:
    app.router.add_get("/catnip", index)
    app.router.add_get(r"/catnip/perk/{i:\d+}/edit", edit_perk)
    app.router.add_get(r"/catnip/perk/{i:\d+}/cancel", cancel_perk)
    app.router.add_post(r"/catnip/perk/{i:\d+}", save_perk)
    app.router.add_get(r"/catnip/level/{i:\d+}/edit", edit_level)
    app.router.add_get(r"/catnip/level/{i:\d+}/cancel", cancel_level)
    app.router.add_post(r"/catnip/level/{i:\d+}", save_level)
