"""Tuning editor for config/tuning.json (extracted magic numbers).

Field metadata is auto-derived from the JSON shape: scalar values get an
input box, nested dicts (like type_dict) get a sub-table. Anything new in
tuning.json appears here automatically.

Special handling: the `stock_market` top-level key is a deeply-nested object
(scalars + a `tickers` sub-dict of dicts). It gets its own structured section
rendered by `_stock_market_section()` and dedicated save/edit routes, rather
than being flattened into the generic scalar/dict renderers.
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
    "coin_per_pack": "Coins per wooden pack",
    "bakery_cost_cookies": "Bakery cost: cookies",
    "bakery_cost_coffees": "Bakery cost: coffees",
    "bakery_cost_nice_cats": "Bakery cost: Nice cats",
    "main_loop_interval_seconds": "Background loop interval",
    "anti_double_catch_cooldown_seconds": "Anti-double-catch cooldown",
    "view_timeout_seconds": "Discord view (button) timeout",
    "pack_drop_chance_on_catch": "Pack drop chance per catch",
    "pack_tier_weights": "Pack tier spawn weights",
    "spawn_revival_interval_seconds": "Spawn-revival background task tick interval",
    "season_announce_interval_seconds": "Season-end warning broadcast re-check interval",
    "respect": "Respect meter tuning block (max, default, decay, job rewards, level-loss rules)",
    "catstore_tier_mult": "Cat Store price multipliers by rarity tier",
    "prism_craft_coin_cost": "Prism crafting coin cost (base, growth exponent, cap)",
    # stock_market scalar sub-keys (namespaced, used in stock_market section)
    "stock_market.enabled": "Kill switch — off means MM tick is a no-op",
    "stock_market.spread": "Bid/ask offset from fair price (0.05 = ±5%)",
    "stock_market.mm_order_quantity": "Shares posted per side per MM tick",
    "stock_market.price_floor": "Minimum clamped fair price (coins)",
    "stock_market.price_ceiling": "Maximum clamped fair price (coins)",
    "stock_market.metric_eps": "Smoothing constant — prevents division-by-zero when activity metric is 0",
}

UNITS: dict[str, str] = {
    "quest_cooldown_seconds": "s",
    "fast_catcher_threshold_seconds": "s",
    "slow_catcher_threshold_seconds": "s",
    "rainboost_short_seconds": "s",
    "rainboost_long_seconds": "s",
    "main_loop_interval_seconds": "s",
    "anti_double_catch_cooldown_seconds": "s",
    "view_timeout_seconds": "s",
    "spawn_revival_interval_seconds": "s",
    "season_announce_interval_seconds": "s",
    "stock_market.spread": "fraction",
    "stock_market.price_floor": "coins",
    "stock_market.price_ceiling": "coins",
}

# Keys whose top-level value is a dict but whose entries are NOT simple
# scalars — they are handled by dedicated routes rather than _dict_sections.
# `respect` is excluded because it contains a nested sub-dict (job_reward)
# that the flat _dict_sections renderer can't handle cleanly; it needs a
# dedicated structured editor (flagged in .sync-log for future work).
_DEEP_DICT_KEYS: set[str] = {"stock_market", "respect"}


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
        if isinstance(value, dict) and key not in _DEEP_DICT_KEYS:
            sections.append({
                "key": key,
                "label": LABELS.get(key, key),
                "entries": list(value.items()),
            })
    return sections


# ---------- stock_market structured section ----------

# Scalar sub-keys inside stock_market (in display order).
_SM_SCALAR_KEYS = ["enabled", "spread", "mm_order_quantity", "price_floor", "price_ceiling", "metric_eps"]

# Sub-keys present on every ticker entry.
_TICKER_SUB_KEYS = ["base", "baseline", "alpha"]
_TICKER_SUB_LABELS = {
    "base": "Base/target price (coins)",
    "baseline": "Metric value that maps to base price",
    "alpha": "Power-law exponent (< 1 = sublinear / less volatile, > 1 = superlinear)",
}


def _sm_scalar_row(key: str, value) -> dict:
    ns_key = f"stock_market.{key}"
    vtype = (
        "bool" if isinstance(value, bool)
        else "int" if isinstance(value, int)
        else "float" if isinstance(value, float)
        else "str"
    )
    return {
        "key": key,
        "value": value,
        "label": LABELS.get(ns_key, key),
        "unit": UNITS.get(ns_key, ""),
        "type": vtype,
    }


def _stock_market_section(tuning: dict) -> dict | None:
    """Build the context dict for the stock_market editor section.

    Returns None if the key is absent (feature not yet in config).
    """
    sm = tuning.get("stock_market")
    if not isinstance(sm, dict):
        return None
    scalars = [_sm_scalar_row(k, sm[k]) for k in _SM_SCALAR_KEYS if k in sm]
    tickers_raw = sm.get("tickers", {})
    tickers = []
    for ticker, cfg in tickers_raw.items():
        tickers.append({"ticker": ticker, "cfg": cfg})
    return {"scalars": scalars, "tickers": tickers}


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
            "stock_market": _stock_market_section(tuning),
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


# ---------- stock_market routes ----------

async def sm_edit_scalar(request):
    """Render a stock_market scalar row in edit mode."""
    key = request.match_info["key"]
    tuning = state.get_tuning()
    sm = tuning.get("stock_market", {})
    if key not in sm or not isinstance(sm[key], (int, float, bool)):
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "tuning_sm_scalar_row.html",
        request,
        {"row": _sm_scalar_row(key, sm[key]), "editing": True},
    )


async def sm_cancel_scalar(request):
    """Render a stock_market scalar row back in view mode."""
    key = request.match_info["key"]
    tuning = state.get_tuning()
    sm = tuning.get("stock_market", {})
    if key not in sm or not isinstance(sm[key], (int, float, bool)):
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "tuning_sm_scalar_row.html",
        request,
        {"row": _sm_scalar_row(key, sm[key]), "editing": False},
    )


async def sm_save_scalar(request):
    """Save a stock_market scalar value."""
    key = request.match_info["key"]
    form = await request.post()
    raw = form.get("value", "")
    tuning = state.get_tuning()
    sm = tuning.get("stock_market")
    if sm is None or key not in sm or not isinstance(sm[key], (int, float, bool)):
        return web.Response(status=404, text=f"unknown stock_market key: {key}")
    existing = sm[key]
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
        return web.Response(status=400, text=f"invalid value for stock_market.{key}")

    if err := validators.validate_stock_market_scalar(key, new_value):
        return web.Response(status=400, text=err)

    async with io_locks.lock_for(TUNING_PATH):
        sm[key] = new_value
        await io_locks.atomic_write_json(TUNING_PATH, tuning)
        state.mark_dirty("tuning")

    return aiohttp_jinja2.render_template(
        "tuning_sm_scalar_row.html",
        request,
        {"row": _sm_scalar_row(key, new_value), "editing": False, "just_saved": True},
    )


async def sm_edit_ticker(request):
    """Render a per-ticker row in edit mode."""
    ticker = request.match_info["ticker"].upper()
    tuning = state.get_tuning()
    sm = tuning.get("stock_market", {})
    tickers = sm.get("tickers", {})
    if ticker not in tickers:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "tuning_sm_ticker_row.html",
        request,
        {"ticker": ticker, "cfg": tickers[ticker], "sub_keys": _TICKER_SUB_KEYS,
         "sub_labels": _TICKER_SUB_LABELS, "editing": True},
    )


async def sm_cancel_ticker(request):
    """Render a per-ticker row back in view mode."""
    ticker = request.match_info["ticker"].upper()
    tuning = state.get_tuning()
    sm = tuning.get("stock_market", {})
    tickers = sm.get("tickers", {})
    if ticker not in tickers:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "tuning_sm_ticker_row.html",
        request,
        {"ticker": ticker, "cfg": tickers[ticker], "sub_keys": _TICKER_SUB_KEYS,
         "sub_labels": _TICKER_SUB_LABELS, "editing": False},
    )


async def sm_save_ticker(request):
    """Save all three sub-fields for a ticker (base, baseline, alpha) in one POST."""
    ticker = request.match_info["ticker"].upper()
    form = await request.post()
    tuning = state.get_tuning()
    sm = tuning.get("stock_market")
    if sm is None:
        return web.Response(status=404)
    tickers = sm.get("tickers", {})
    if ticker not in tickers:
        return web.Response(status=404, text=f"unknown ticker: {ticker}")

    try:
        new_base = int(form.get("base", ""))
        new_baseline = float(form.get("baseline", ""))
        new_alpha = float(form.get("alpha", ""))
    except ValueError:
        return web.Response(status=400, text="base must be int; baseline and alpha must be numeric")

    if err := validators.validate_stock_market_ticker(ticker, new_base, new_baseline, new_alpha):
        return web.Response(status=400, text=err)

    new_cfg = {"base": new_base, "baseline": new_baseline, "alpha": new_alpha}
    async with io_locks.lock_for(TUNING_PATH):
        tickers[ticker] = new_cfg
        await io_locks.atomic_write_json(TUNING_PATH, tuning)
        state.mark_dirty("tuning")

    return aiohttp_jinja2.render_template(
        "tuning_sm_ticker_row.html",
        request,
        {"ticker": ticker, "cfg": new_cfg, "sub_keys": _TICKER_SUB_KEYS,
         "sub_labels": _TICKER_SUB_LABELS, "editing": False, "just_saved": True},
    )


def register(app: web.Application) -> None:
    app.router.add_get("/tuning", index)
    app.router.add_get(r"/tuning/scalar/{key:[A-Za-z0-9_]+}/edit", edit_scalar)
    app.router.add_get(r"/tuning/scalar/{key:[A-Za-z0-9_]+}/cancel", cancel_scalar)
    app.router.add_post(r"/tuning/scalar/{key:[A-Za-z0-9_]+}", save_scalar)
    app.router.add_get(r"/tuning/dict/{section:[A-Za-z0-9_]+}/{entry}/edit", edit_dict_entry)
    app.router.add_get(r"/tuning/dict/{section:[A-Za-z0-9_]+}/{entry}/cancel", cancel_dict_entry)
    app.router.add_post(r"/tuning/dict/{section:[A-Za-z0-9_]+}/{entry}", save_dict_entry)
    # stock_market structured section
    app.router.add_get(r"/tuning/stock_market/scalar/{key:[A-Za-z0-9_]+}/edit", sm_edit_scalar)
    app.router.add_get(r"/tuning/stock_market/scalar/{key:[A-Za-z0-9_]+}/cancel", sm_cancel_scalar)
    app.router.add_post(r"/tuning/stock_market/scalar/{key:[A-Za-z0-9_]+}", sm_save_scalar)
    app.router.add_get(r"/tuning/stock_market/tickers/{ticker:[A-Z]+}/edit", sm_edit_ticker)
    app.router.add_get(r"/tuning/stock_market/tickers/{ticker:[A-Z]+}/cancel", sm_cancel_ticker)
    app.router.add_post(r"/tuning/stock_market/tickers/{ticker:[A-Z]+}", sm_save_ticker)
