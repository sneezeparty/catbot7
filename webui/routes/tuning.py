"""Tuning editor for config/tuning.json (extracted magic numbers).

Field metadata is auto-derived from the JSON shape: scalar values get an
input box, nested dicts (like type_dict) get a sub-table. Anything new in
tuning.json appears here automatically.
"""

import aiohttp_jinja2
from aiohttp import web

from webui import io_locks, state, validators

TUNING_PATH = "config/tuning.json"

# Optional human-friendly labels for keys we know about. Anything not listed
# falls back to the raw key. The sync agent appends to this map.
LABELS: dict[str, str] = {
    "type_dict": "Cat rarity spawn weights",
    "quest_cooldown_seconds": "Quest cooldown",
    "fast_catcher_threshold_seconds": "\"Fast catcher\" achievement threshold",
    "slow_catcher_threshold_seconds": "\"Slow catcher\" achievement threshold",
    "rainboost_short_seconds": "Short rainboost duration",
    "rainboost_long_seconds": "Long rainboost duration",
    "prism_boost_global_coef": "Prism boost: global coefficient",
    "prism_boost_user_coef": "Prism boost: per-user coefficient",
    "prism_boost_floor": "Prism boost: minimum threshold for quest credit",
    "catnip_timer_extend_seconds": "Catnip Time Manipulator extension",
    "coin_per_pack": "Coins per wooden pack",
    "bakery_cost_cookies": "Bakery cost: cookies",
    "bakery_cost_coffees": "Bakery cost: coffees",
    "bakery_cost_nice_cats": "Bakery cost: Nice cats",
    "main_loop_interval_seconds": "Background loop interval",
    "anti_double_catch_cooldown_seconds": "Anti-double-catch cooldown",
    "view_timeout_seconds": "Discord view (button) timeout",
    "pack_drop_chance_on_catch": "Pack drop chance per catch",
    "pack_tier_weights": "Pack tier spawn weights",
}

UNITS: dict[str, str] = {
    "quest_cooldown_seconds": "s",
    "fast_catcher_threshold_seconds": "s",
    "slow_catcher_threshold_seconds": "s",
    "rainboost_short_seconds": "s",
    "rainboost_long_seconds": "s",
    "catnip_timer_extend_seconds": "s",
    "main_loop_interval_seconds": "s",
    "anti_double_catch_cooldown_seconds": "s",
    "view_timeout_seconds": "s",
}


def _flatten_scalars(tuning: dict) -> list[dict]:
    rows = []
    for key, value in tuning.items():
        if isinstance(value, (int, float, str, bool)):
            rows.append({
                "key": key,
                "value": value,
                "label": LABELS.get(key, key),
                "unit": UNITS.get(key, ""),
                "type": "int" if isinstance(value, int) and not isinstance(value, bool) else ("float" if isinstance(value, float) else ("bool" if isinstance(value, bool) else "str")),
            })
    return rows


def _dict_sections(tuning: dict) -> list[dict]:
    sections = []
    for key, value in tuning.items():
        if isinstance(value, dict):
            sections.append({
                "key": key,
                "label": LABELS.get(key, key),
                "entries": list(value.items()),
            })
    return sections


async def index(request):
    tuning = state.get_tuning()
    return aiohttp_jinja2.render_template(
        "tuning.html",
        request,
        {
            "title": "Tuning",
            "active_section": "tuning",
            "scalars": _flatten_scalars(tuning),
            "dict_sections": _dict_sections(tuning),
        },
    )


async def save_scalar(request):
    key = request.match_info["key"]
    form = await request.post()
    raw = form.get("value", "")
    tuning = state.get_tuning()
    if key not in tuning:
        return web.Response(status=404, text=f"unknown key: {key}")
    existing = tuning[key]
    try:
        if isinstance(existing, bool):
            new_value = raw.lower() in ("1", "true", "on", "yes")
        elif isinstance(existing, int):
            new_value = int(raw)
        elif isinstance(existing, float):
            new_value = float(raw)
        else:
            new_value = raw
    except ValueError:
        return web.Response(status=400, text=f"invalid value for {key}")

    if err := validators.validate_tuning_scalar(key, new_value):
        return web.Response(status=400, text=err)

    async with io_locks.lock_for(TUNING_PATH):
        tuning[key] = new_value
        await io_locks.atomic_write_json(TUNING_PATH, tuning)
        state.mark_dirty("tuning")

    return aiohttp_jinja2.render_template(
        "tuning_row.html",
        request,
        {
            "row": {
                "key": key,
                "value": new_value,
                "label": LABELS.get(key, key),
                "unit": UNITS.get(key, ""),
                "type": _flatten_scalars({key: new_value})[0]["type"],
            },
            "editing": False,
            "just_saved": True,
        },
    )


async def edit_scalar(request):
    key = request.match_info["key"]
    tuning = state.get_tuning()
    if key not in tuning:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "tuning_row.html",
        request,
        {
            "row": {
                "key": key,
                "value": tuning[key],
                "label": LABELS.get(key, key),
                "unit": UNITS.get(key, ""),
                "type": _flatten_scalars({key: tuning[key]})[0]["type"],
            },
            "editing": True,
        },
    )


async def cancel_scalar(request):
    key = request.match_info["key"]
    tuning = state.get_tuning()
    if key not in tuning:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "tuning_row.html",
        request,
        {
            "row": {
                "key": key,
                "value": tuning[key],
                "label": LABELS.get(key, key),
                "unit": UNITS.get(key, ""),
                "type": _flatten_scalars({key: tuning[key]})[0]["type"],
            },
            "editing": False,
        },
    )


async def save_dict_entry(request):
    section = request.match_info["section"]
    entry_key = request.match_info["entry"]
    form = await request.post()
    raw = form.get("value", "")
    tuning = state.get_tuning()
    if section not in tuning or not isinstance(tuning[section], dict):
        return web.Response(status=404)
    target = tuning[section]
    existing = target.get(entry_key)
    try:
        if isinstance(existing, int) and not isinstance(existing, bool):
            new_value = int(raw)
        elif isinstance(existing, float):
            new_value = float(raw)
        else:
            new_value = raw
    except ValueError:
        return web.Response(status=400, text="invalid value")

    # simulate the would-be state, validate, then commit
    hypothetical = dict(target)
    hypothetical[entry_key] = new_value
    if err := validators.validate_tuning_weight_dict(section, hypothetical):
        return web.Response(status=400, text=err)

    async with io_locks.lock_for(TUNING_PATH):
        target[entry_key] = new_value
        await io_locks.atomic_write_json(TUNING_PATH, tuning)
        state.mark_dirty("tuning")

    return aiohttp_jinja2.render_template(
        "tuning_dict_row.html",
        request,
        {"section": section, "entry": (entry_key, new_value), "editing": False, "just_saved": True},
    )


async def edit_dict_entry(request):
    section = request.match_info["section"]
    entry_key = request.match_info["entry"]
    tuning = state.get_tuning()
    if section not in tuning:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "tuning_dict_row.html",
        request,
        {"section": section, "entry": (entry_key, tuning[section][entry_key]), "editing": True},
    )


async def cancel_dict_entry(request):
    section = request.match_info["section"]
    entry_key = request.match_info["entry"]
    tuning = state.get_tuning()
    if section not in tuning:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "tuning_dict_row.html",
        request,
        {"section": section, "entry": (entry_key, tuning[section][entry_key]), "editing": False},
    )


def register(app: web.Application) -> None:
    app.router.add_get("/tuning", index)
    app.router.add_get(r"/tuning/scalar/{key:[A-Za-z0-9_]+}/edit", edit_scalar)
    app.router.add_get(r"/tuning/scalar/{key:[A-Za-z0-9_]+}/cancel", cancel_scalar)
    app.router.add_post(r"/tuning/scalar/{key:[A-Za-z0-9_]+}", save_scalar)
    app.router.add_get(r"/tuning/dict/{section:[A-Za-z0-9_]+}/{entry}/edit", edit_dict_entry)
    app.router.add_get(r"/tuning/dict/{section:[A-Za-z0-9_]+}/{entry}/cancel", cancel_dict_entry)
    app.router.add_post(r"/tuning/dict/{section:[A-Za-z0-9_]+}/{entry}", save_dict_entry)
