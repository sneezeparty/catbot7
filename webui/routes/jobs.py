"""Jobs / Mafia Killings editor for config/jobs.json.

Sections:
  send_power   — per-cat-type SP int (inline editable table)
  probability  — k, floor, ceiling, near_miss_band
  tuning       — 8 integer/float knobs (offer windows, heat, pinch, etc.)
  tiers        — 5 tier objects (name, difficulty_range, reward_coin_range, heat, min_catnip_level)
  npcs         — 6 NPC objects (display_name, min_hire_level, tiers_offered, hires_against,
                  reward_mult, heat_mult, reward_bias, optional rep_unlock_at_100 / ally_protection_threshold)
  big_score    — singleton heist config (read/write)
  rep          — reputation math knobs + tier_rep_gain/loss dicts
  narrative_pools — per-NPC flavor text (list of strings, freeform textarea per NPC)

Referential invariants (enforced at save time):
  - tiers_offered values must be tier keys that exist in tiers.
  - hires_against values should be npc keys that exist in npcs or targets_only, OR the magic
    string "dynamic_higher_rank" / "commoners" — validated as a warning only (not a hard block)
    because the bot resolves those at runtime.
  - big_score.patron_npc and big_score.target_npc must exist in npcs / targets_only.
  - tier_rep_gain and tier_rep_loss keys must match the tier keys in tiers.

All edits are written atomically to config/jobs.json. Apply with Reload Bot.
"""

import aiohttp_jinja2
from aiohttp import web

from webui import io_locks, state, validators

JOBS_PATH = "config/jobs.json"


# ------------------------------------------------------------------ helpers --

def _get_jobs_mutable():
    """Returns config.jobs dict (live object — mutate + persist)."""
    import config
    return getattr(config, "jobs", None)


def _get_jobs_help_mutable():
    import config
    return getattr(config, "jobs_help", None)


def _referential_warnings(jobs: dict) -> list[str]:
    """Non-blocking consistency warnings surfaced as inline notes."""
    warnings = []
    tier_keys = set(jobs.get("tiers", {}).keys())
    npc_keys = set(jobs.get("npcs", {}).keys())
    target_keys = set(jobs.get("targets_only", {}).keys())
    all_npc_like = npc_keys | target_keys | {"dynamic_higher_rank", "commoners"}
    # known rarity names from send_power keys (same as cattypes)
    known_rarities = set(jobs.get("send_power", {}).keys())

    for npc_key, npc in jobs.get("npcs", {}).items():
        for t in npc.get("tiers_offered", []):
            if str(t) not in tier_keys:
                warnings.append(f"NPC {npc_key}: tiers_offered contains {t!r} which is not in tiers")
        for h in npc.get("hires_against", []):
            if h not in all_npc_like:
                warnings.append(f"NPC {npc_key}: hires_against contains {h!r} (not in npcs/targets_only/magic)")
        # reward_recipes rarity keys should be known rarities
        for tier_key, recipes in npc.get("reward_recipes", {}).items():
            for recipe in recipes:
                for rarity in recipe.get("cats", {}).keys():
                    if known_rarities and rarity not in known_rarities:
                        warnings.append(f"NPC {npc_key} reward_recipes[{tier_key}]: cat rarity {rarity!r} not in send_power keys")

    bs = jobs.get("big_score", {})
    if bs.get("patron_npc") and bs["patron_npc"] not in npc_keys:
        warnings.append(f"big_score.patron_npc={bs['patron_npc']!r} not found in npcs")
    if bs.get("target_npc") and bs["target_npc"] not in (npc_keys | target_keys):
        warnings.append(f"big_score.target_npc={bs['target_npc']!r} not found in npcs/targets_only")

    # complication_pools event ids should appear in complication_flavor
    flavor_keys = set(jobs.get("complication_flavor", {}).keys())
    for tier_key, entries in jobs.get("complication_pools", {}).items():
        for entry in entries:
            eid = entry.get("id", "")
            if eid and flavor_keys and eid not in flavor_keys:
                warnings.append(f"complication_pools[{tier_key}] event {eid!r} has no entry in complication_flavor")

    # complication_quips rarity keys should be known rarities
    for event_id, rarity_map in jobs.get("complication_quips", {}).items():
        for rarity in rarity_map.keys():
            if known_rarities and rarity not in known_rarities:
                warnings.append(f"complication_quips[{event_id!r}]: rarity {rarity!r} not in send_power keys")

    # complications.sloppy_target pack tiers should be valid
    from webui.validators import PACK_TIER_LIST
    sloppy = jobs.get("complications", {}).get("sloppy_target_default_pack_tier_by_tier", {})
    for tier_key, pack_tier in sloppy.items():
        if pack_tier and pack_tier not in PACK_TIER_LIST:
            warnings.append(f"complications.sloppy_target_default_pack_tier_by_tier[{tier_key}]={pack_tier!r} is not a known pack tier")

    # --- Perks (Phase 4) ---
    perks_block = jobs.get("perks", {}) or {}
    catalog_ids = set((perks_block.get("catalog", {}) or {}).keys())
    drop_pools = perks_block.get("drop_pools", {}) or {}
    # Pools: every entry's id must be in catalog; tier keys should be in jobs.tiers
    # (or "5" if it lives under "whiskers" — the Big Score patron).
    for npc_key, by_tier in drop_pools.items():
        if npc_key not in npc_keys:
            warnings.append(f"perks.drop_pools: NPC {npc_key!r} is not in npcs")
            continue
        npc_offered = {str(t) for t in (jobs.get("npcs", {}).get(npc_key, {}).get("tiers_offered") or [])}
        for tk, pool in (by_tier or {}).items():
            if tk not in tier_keys:
                warnings.append(f"perks.drop_pools[{npc_key}][{tk}]: tier {tk!r} is not in jobs.tiers")
            if tk not in npc_offered and not (tk == "5" and npc_key == "whiskers"):
                warnings.append(f"perks.drop_pools[{npc_key}][{tk}]: NPC doesn't offer tier {tk}")
            for entry in (pool or []):
                pid = (entry or {}).get("id")
                if pid and catalog_ids and pid not in catalog_ids:
                    warnings.append(f"perks.drop_pools[{npc_key}][{tk}]: perk {pid!r} not in catalog")
                # tier-table check: pool tier should have an entry in catalog[pid].tier_table
                if pid and pid in catalog_ids:
                    tt = (perks_block.get("catalog", {}).get(pid, {}).get("tier_table", {}) or {})
                    if tk not in tt and "2" not in tt:
                        warnings.append(f"perks.drop_pools[{npc_key}][{tk}]: perk {pid!r} has no T{tk} or T2 tier_table entry")
    # Catalog: every tier_table key should be a known tier.
    for pid, cat in (perks_block.get("catalog", {}) or {}).items():
        for tk in (cat.get("tier_table", {}) or {}).keys():
            if tk not in tier_keys:
                warnings.append(f"perks.catalog[{pid}].tier_table: tier {tk!r} is not in jobs.tiers")
    # Catalog perks not in any pool → unreachable (soft warn).
    pool_ids = {(e or {}).get("id") for by_tier in drop_pools.values() for pool in (by_tier or {}).values() for e in (pool or [])}
    for pid in catalog_ids:
        if pid not in pool_ids:
            warnings.append(f"perks.catalog[{pid}] is not in any drop_pool (unreachable in-game)")
    # drop_chance_by_tier sanity
    chances = perks_block.get("drop_chance_by_tier", {}) or {}
    for tk in tier_keys:
        if tk in chances:
            v = chances[tk]
            try:
                if float(v) < 0 or float(v) > 1:
                    warnings.append(f"perks.drop_chance_by_tier[{tk}]={v!r} should be in [0, 1]")
            except (ValueError, TypeError):
                warnings.append(f"perks.drop_chance_by_tier[{tk}]={v!r} is not a number")

    return warnings


# ------------------------------------------------------------------ index  --

async def index(request):
    import json as _json
    jobs = state.get_jobs()
    warnings = _referential_warnings(jobs)
    # Build a flat list for the rep tier tables
    tier_keys = sorted(jobs.get("tiers", {}).keys(), key=lambda x: int(x))
    rep = jobs.get("rep", {})
    tier_rep_gain = rep.get("tier_rep_gain", {})
    tier_rep_loss = rep.get("tier_rep_loss", {})
    # Pre-render complication pool JSON strings for display
    complication_pools_json = {
        tk: _json.dumps(entries, indent=2)
        for tk, entries in jobs.get("complication_pools", {}).items()
    }
    # Pre-render reward_recipes JSON per NPC per tier
    npc_recipes_json = {}
    for npc_key, npc in jobs.get("npcs", {}).items():
        npc_recipes_json[npc_key] = {
            tk: _json.dumps(entries, indent=2)
            for tk, entries in npc.get("reward_recipes", {}).items()
        }
    return aiohttp_jinja2.render_template(
        "jobs.html",
        request,
        {
            "title": "Jobs",
            "active_section": "jobs",
            "jobs": jobs,
            "send_power": jobs.get("send_power", {}),
            "probability": jobs.get("probability", {}),
            "tuning": jobs.get("tuning", {}),
            "tiers": jobs.get("tiers", {}),
            "tier_keys": tier_keys,
            "npcs": jobs.get("npcs", {}),
            "targets_only": jobs.get("targets_only", {}),
            "big_score": jobs.get("big_score", {}),
            "rep": rep,
            "tier_rep_gain": tier_rep_gain,
            "tier_rep_loss": tier_rep_loss,
            "narrative_pools": jobs.get("narrative_pools", {}),
            "narrative_pools_big_score": jobs.get("narrative_pools_big_score", []),
            "complications": jobs.get("complications", {}),
            "complication_pools": jobs.get("complication_pools", {}),
            "complication_pools_json": complication_pools_json,
            "npc_recipes_json": npc_recipes_json,
            "cat_voices": jobs.get("cat_voices", {}),
            "complication_quips": jobs.get("complication_quips", {}),
            "complication_flavor": jobs.get("complication_flavor", {}),
            # Perks (Phase 4 + 5 — sub-section editors)
            "perks_block": jobs.get("perks", {}) or {},
            "perks_drop_chance_by_tier": (jobs.get("perks", {}) or {}).get("drop_chance_by_tier", {}),
            "perks_drop_pools": (jobs.get("perks", {}) or {}).get("drop_pools", {}),
            "perks_catalog": (jobs.get("perks", {}) or {}).get("catalog", {}),
            "perks_pools_json": {
                npc: {
                    tk: _json.dumps(pool, indent=2)
                    for tk, pool in (by_tier or {}).items()
                }
                for npc, by_tier in ((jobs.get("perks", {}) or {}).get("drop_pools", {}) or {}).items()
            },
            "perks_catalog_tier_table_json": {
                pid: _json.dumps(cat.get("tier_table", {}), indent=2)
                for pid, cat in ((jobs.get("perks", {}) or {}).get("catalog", {}) or {}).items()
            },
            "warnings": warnings,
        },
    )


# ------------------------------------------------------ send_power routes  --

async def edit_sp(request):
    cat_type = request.match_info["cat_type"]
    jobs = state.get_jobs()
    sp = jobs.get("send_power", {})
    if cat_type not in sp:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "jobs_sp_row.html",
        request,
        {"cat_type": cat_type, "value": sp[cat_type], "editing": True},
    )


async def cancel_sp(request):
    cat_type = request.match_info["cat_type"]
    jobs = state.get_jobs()
    sp = jobs.get("send_power", {})
    if cat_type not in sp:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "jobs_sp_row.html",
        request,
        {"cat_type": cat_type, "value": sp[cat_type], "editing": False},
    )


async def save_sp(request):
    cat_type = request.match_info["cat_type"]
    jobs = _get_jobs_mutable()
    if jobs is None:
        return web.Response(status=503, text="config.jobs not loaded")
    sp = jobs.get("send_power", {})
    if cat_type not in sp:
        return web.Response(status=404)
    form = await request.post()
    try:
        value = int(form.get("value", "0"))
    except ValueError:
        return web.Response(status=400, text="value must be an integer")
    if err := validators.validate_jobs_send_power(cat_type, value):
        return web.Response(status=400, text=err)
    async with io_locks.lock_for(JOBS_PATH):
        sp[cat_type] = value
        await io_locks.atomic_write_json(JOBS_PATH, jobs)
        state.mark_dirty("jobs")
    return aiohttp_jinja2.render_template(
        "jobs_sp_row.html",
        request,
        {"cat_type": cat_type, "value": value, "editing": False, "just_saved": True},
    )


# --------------------------------------------------- probability routes  ----

async def save_probability(request):
    jobs = _get_jobs_mutable()
    if jobs is None:
        return web.Response(status=503, text="config.jobs not loaded")
    form = await request.post()
    try:
        k = float(form.get("k", "2.5"))
        floor_ = float(form.get("floor", "0.05"))
        ceiling = float(form.get("ceiling", "0.95"))
        near_miss_band = float(form.get("near_miss_band", "0.10"))
    except ValueError:
        return web.Response(status=400, text="all probability fields must be numeric")
    if err := validators.validate_jobs_probability(k, floor_, ceiling, near_miss_band):
        return web.Response(status=400, text=err)
    async with io_locks.lock_for(JOBS_PATH):
        jobs["probability"] = {
            "k": k, "floor": floor_,
            "ceiling": ceiling, "near_miss_band": near_miss_band,
        }
        await io_locks.atomic_write_json(JOBS_PATH, jobs)
        state.mark_dirty("jobs")
    return aiohttp_jinja2.render_template(
        "jobs_probability_form.html",
        request,
        {"probability": jobs["probability"], "editing": False, "just_saved": True},
    )


async def edit_probability(request):
    jobs = state.get_jobs()
    return aiohttp_jinja2.render_template(
        "jobs_probability_form.html",
        request,
        {"probability": jobs.get("probability", {}), "editing": True},
    )


async def cancel_probability(request):
    jobs = state.get_jobs()
    return aiohttp_jinja2.render_template(
        "jobs_probability_form.html",
        request,
        {"probability": jobs.get("probability", {}), "editing": False},
    )


# --------------------------------------------------- tuning routes  ---------

_TUNING_FIELD_TYPES = {
    "offer_refresh_window_seconds": int,
    "decline_cooldown_seconds": int,
    "max_concurrent_offers": int,
    "cancel_grace_seconds": int,
    "heat_decay_per_hour": float,
    "pinch_threshold": int,
    "pinch_lockout_seconds": int,
    "pinch_reset_heat": int,
    "reroll_price_per_level": int,
    "reroll_price_min": int,
}


async def save_tuning(request):
    jobs = _get_jobs_mutable()
    if jobs is None:
        return web.Response(status=503, text="config.jobs not loaded")
    form = await request.post()
    existing = dict(jobs.get("tuning", {}))
    for field, cast in _TUNING_FIELD_TYPES.items():
        raw = form.get(field)
        if raw is None:
            continue
        try:
            value = cast(raw)
        except ValueError:
            return web.Response(status=400, text=f"{field} must be numeric")
        if err := validators.validate_jobs_tuning(field, value):
            return web.Response(status=400, text=err)
        existing[field] = value
    async with io_locks.lock_for(JOBS_PATH):
        jobs["tuning"] = existing
        await io_locks.atomic_write_json(JOBS_PATH, jobs)
        state.mark_dirty("jobs")
    return aiohttp_jinja2.render_template(
        "jobs_tuning_form.html",
        request,
        {"tuning": jobs["tuning"], "editing": False, "just_saved": True},
    )


async def edit_tuning(request):
    jobs = state.get_jobs()
    return aiohttp_jinja2.render_template(
        "jobs_tuning_form.html",
        request,
        {"tuning": jobs.get("tuning", {}), "editing": True},
    )


async def cancel_tuning(request):
    jobs = state.get_jobs()
    return aiohttp_jinja2.render_template(
        "jobs_tuning_form.html",
        request,
        {"tuning": jobs.get("tuning", {}), "editing": False},
    )


# --------------------------------------------------- tier routes  -----------

async def edit_tier(request):
    tier_key = request.match_info["tier"]
    jobs = state.get_jobs()
    tiers = jobs.get("tiers", {})
    if tier_key not in tiers:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "jobs_tier_row.html",
        request,
        {"tier_key": tier_key, "tier": tiers[tier_key], "editing": True},
    )


async def cancel_tier(request):
    tier_key = request.match_info["tier"]
    jobs = state.get_jobs()
    tiers = jobs.get("tiers", {})
    if tier_key not in tiers:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "jobs_tier_row.html",
        request,
        {"tier_key": tier_key, "tier": tiers[tier_key], "editing": False},
    )


async def save_tier(request):
    tier_key = request.match_info["tier"]
    jobs = _get_jobs_mutable()
    if jobs is None:
        return web.Response(status=503, text="config.jobs not loaded")
    tiers = jobs.get("tiers", {})
    if tier_key not in tiers:
        return web.Response(status=404)
    form = await request.post()
    try:
        name = (form.get("name") or "").strip()
        diff_lo = int(form.get("diff_lo", "0"))
        diff_hi = int(form.get("diff_hi", "0"))
        coin_lo = int(form.get("coin_lo", "0"))
        coin_hi = int(form.get("coin_hi", "0"))
        heat = int(form.get("heat", "0"))
        min_catnip_level = int(form.get("min_catnip_level", "0"))
    except ValueError:
        return web.Response(status=400, text="numeric fields invalid")
    if err := validators.validate_jobs_tier(name, diff_lo, diff_hi, coin_lo, coin_hi, heat, min_catnip_level):
        return web.Response(status=400, text=err)
    async with io_locks.lock_for(JOBS_PATH):
        tiers[tier_key].update({
            "name": name,
            "difficulty_range": [diff_lo, diff_hi],
            "reward_coin_range": [coin_lo, coin_hi],
            "heat": heat,
            "min_catnip_level": min_catnip_level,
        })
        await io_locks.atomic_write_json(JOBS_PATH, jobs)
        state.mark_dirty("jobs")
    return aiohttp_jinja2.render_template(
        "jobs_tier_row.html",
        request,
        {"tier_key": tier_key, "tier": tiers[tier_key], "editing": False, "just_saved": True},
    )


# --------------------------------------------------- npc routes  ------------

async def edit_npc(request):
    npc_key = request.match_info["npc"]
    jobs = state.get_jobs()
    npcs = jobs.get("npcs", {})
    if npc_key not in npcs:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "jobs_npc_row.html",
        request,
        {"npc_key": npc_key, "npc": npcs[npc_key], "editing": True},
    )


async def cancel_npc(request):
    npc_key = request.match_info["npc"]
    jobs = state.get_jobs()
    npcs = jobs.get("npcs", {})
    if npc_key not in npcs:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "jobs_npc_row.html",
        request,
        {"npc_key": npc_key, "npc": npcs[npc_key], "editing": False},
    )


async def save_npc(request):
    npc_key = request.match_info["npc"]
    jobs = _get_jobs_mutable()
    if jobs is None:
        return web.Response(status=503, text="config.jobs not loaded")
    npcs = jobs.get("npcs", {})
    if npc_key not in npcs:
        return web.Response(status=404)
    form = await request.post()
    try:
        display_name = (form.get("display_name") or "").strip()
        min_hire_level = int(form.get("min_hire_level", "1"))
        reward_mult = float(form.get("reward_mult", "1.0"))
        heat_mult = float(form.get("heat_mult", "1.0"))
        reward_bias = (form.get("reward_bias") or "standard").strip()
        # tiers_offered: comma-sep ints from form
        tiers_offered_raw = form.get("tiers_offered", "")
        tiers_offered = [int(x.strip()) for x in tiers_offered_raw.split(",") if x.strip()]
        # hires_against: comma-sep strings
        hires_against_raw = form.get("hires_against", "")
        hires_against = [x.strip() for x in hires_against_raw.split(",") if x.strip()]
    except ValueError:
        return web.Response(status=400, text="numeric fields invalid")
    if err := validators.validate_jobs_npc(display_name, min_hire_level, reward_mult, heat_mult):
        return web.Response(status=400, text=err)

    # referential check: tiers_offered vs tiers
    tier_keys = set(jobs.get("tiers", {}).keys())
    invalid_tiers = [t for t in tiers_offered if str(t) not in tier_keys]
    if invalid_tiers:
        return web.Response(status=400, text=f"tiers_offered contains unknown tier(s): {invalid_tiers}")

    async with io_locks.lock_for(JOBS_PATH):
        npc = npcs[npc_key]
        npc["display_name"] = display_name
        npc["min_hire_level"] = min_hire_level
        npc["tiers_offered"] = tiers_offered
        npc["hires_against"] = hires_against
        npc["reward_mult"] = reward_mult
        npc["heat_mult"] = heat_mult
        npc["reward_bias"] = reward_bias
        # optional fields — preserve if not in form, allow clearing
        rep_unlock = (form.get("rep_unlock_at_100") or "").strip() or None
        if rep_unlock is not None:
            npc["rep_unlock_at_100"] = rep_unlock
        elif "rep_unlock_at_100" in npc and not rep_unlock:
            # blank input = remove the key
            npc.pop("rep_unlock_at_100", None)
        ally_raw = form.get("ally_protection_threshold", "").strip()
        if ally_raw:
            try:
                npc["ally_protection_threshold"] = int(ally_raw)
            except ValueError:
                return web.Response(status=400, text="ally_protection_threshold must be an integer")
        else:
            npc.pop("ally_protection_threshold", None)
        await io_locks.atomic_write_json(JOBS_PATH, jobs)
        state.mark_dirty("jobs")
    return aiohttp_jinja2.render_template(
        "jobs_npc_row.html",
        request,
        {"npc_key": npc_key, "npc": npcs[npc_key], "editing": False, "just_saved": True},
    )


# --------------------------------------------------- big_score routes  ------

async def edit_big_score(request):
    jobs = state.get_jobs()
    return aiohttp_jinja2.render_template(
        "jobs_big_score_form.html",
        request,
        {"big_score": jobs.get("big_score", {}), "editing": True},
    )


async def cancel_big_score(request):
    jobs = state.get_jobs()
    return aiohttp_jinja2.render_template(
        "jobs_big_score_form.html",
        request,
        {"big_score": jobs.get("big_score", {}), "editing": False},
    )


async def save_big_score(request):
    jobs = _get_jobs_mutable()
    if jobs is None:
        return web.Response(status=503, text="config.jobs not loaded")
    form = await request.post()
    try:
        difficulty = int(form.get("difficulty", "800"))
        patron_npc = (form.get("patron_npc") or "").strip()
        target_npc = (form.get("target_npc") or "").strip()
        reward_egirl = int(form.get("reward_egirl", "3"))
        reward_coins = int(form.get("reward_coins", "15000"))
        reward_perk = (form.get("reward_perk") or "big_score").strip()
        near_miss_consolation_coins = int(form.get("near_miss_consolation_coins", "5000"))
        heat_cost = int(form.get("heat_cost", "100"))
        perk_spawn_extra_bonus = float(form.get("perk_spawn_extra_bonus", "0.05"))
        once_per_season = form.get("once_per_season") in ("on", "true", "1")
        perk_one_time_only = form.get("perk_one_time_only") in ("on", "true", "1")
    except ValueError:
        return web.Response(status=400, text="numeric fields invalid")

    if difficulty < 1:
        return web.Response(status=400, text="difficulty must be >= 1")
    if reward_egirl < 0:
        return web.Response(status=400, text="reward.eGirl must be >= 0")
    if reward_coins < 0:
        return web.Response(status=400, text="reward.coins must be >= 0")
    if near_miss_consolation_coins < 0:
        return web.Response(status=400, text="near_miss_consolation_coins must be >= 0")
    if heat_cost < 0:
        return web.Response(status=400, text="heat_cost must be >= 0")
    if perk_spawn_extra_bonus < 0:
        return web.Response(status=400, text="perk_spawn_extra_bonus must be >= 0")

    # referential check
    npc_keys = set(jobs.get("npcs", {}).keys())
    target_keys = set(jobs.get("targets_only", {}).keys())
    if patron_npc and patron_npc not in npc_keys:
        return web.Response(status=400, text=f"patron_npc {patron_npc!r} not found in npcs")
    if target_npc and target_npc not in (npc_keys | target_keys):
        return web.Response(status=400, text=f"target_npc {target_npc!r} not found in npcs or targets_only")

    # Parse rep_changes from form: rep_changes_success_<npc> and rep_changes_failure_<npc>
    existing_bs = jobs.get("big_score", {})
    rep_changes = existing_bs.get("rep_changes", {"success": {}, "failure": {}})
    success_rep = {}
    failure_rep = {}
    for k, v in form.items():
        if k.startswith("rep_success_") and v.strip():
            npc = k[len("rep_success_"):]
            try:
                success_rep[npc] = int(v)
            except ValueError:
                return web.Response(status=400, text=f"rep_changes.success.{npc} must be int")
        elif k.startswith("rep_failure_") and v.strip():
            npc = k[len("rep_failure_"):]
            try:
                failure_rep[npc] = int(v)
            except ValueError:
                return web.Response(status=400, text=f"rep_changes.failure.{npc} must be int")

    async with io_locks.lock_for(JOBS_PATH):
        jobs["big_score"] = {
            "difficulty": difficulty,
            "patron_npc": patron_npc,
            "target_npc": target_npc,
            "reward": {"eGirl": reward_egirl, "coins": reward_coins, "perk": reward_perk},
            "near_miss_consolation_coins": near_miss_consolation_coins,
            "heat_cost": heat_cost,
            "rep_changes": {"success": success_rep, "failure": failure_rep},
            "once_per_season": once_per_season,
            "perk_one_time_only": perk_one_time_only,
            "perk_spawn_extra_bonus": perk_spawn_extra_bonus,
        }
        await io_locks.atomic_write_json(JOBS_PATH, jobs)
        state.mark_dirty("jobs")
    return aiohttp_jinja2.render_template(
        "jobs_big_score_form.html",
        request,
        {"big_score": jobs["big_score"], "editing": False, "just_saved": True},
    )


# --------------------------------------------------- rep routes  ------------

async def edit_rep(request):
    jobs = state.get_jobs()
    tier_keys = sorted(jobs.get("tiers", {}).keys(), key=lambda x: int(x))
    return aiohttp_jinja2.render_template(
        "jobs_rep_form.html",
        request,
        {
            "rep": jobs.get("rep", {}),
            "tier_keys": tier_keys,
            "editing": True,
        },
    )


async def cancel_rep(request):
    jobs = state.get_jobs()
    tier_keys = sorted(jobs.get("tiers", {}).keys(), key=lambda x: int(x))
    return aiohttp_jinja2.render_template(
        "jobs_rep_form.html",
        request,
        {
            "rep": jobs.get("rep", {}),
            "tier_keys": tier_keys,
            "editing": False,
        },
    )


_REP_SCALAR_FIELDS = [
    "offerer_bonus_per_point", "offerer_bonus_cap",
    "target_difficulty_per_negative_point", "target_difficulty_cap",
    "unlock_threshold", "refuse_threshold", "hostile_threshold",
    "failure_penalty", "premium_reward_bonus_at_100",
    "hostile_target_heat_discount", "slot_weight_at_50",
]


async def save_rep(request):
    jobs = _get_jobs_mutable()
    if jobs is None:
        return web.Response(status=503, text="config.jobs not loaded")
    form = await request.post()
    tier_keys = sorted(jobs.get("tiers", {}).keys(), key=lambda x: int(x))
    rep = dict(jobs.get("rep", {}))

    # Scalar fields
    for field in _REP_SCALAR_FIELDS:
        raw = form.get(field)
        if raw is None:
            continue
        try:
            # threshold/penalty fields are ints; bonus/coef fields are floats
            if field in ("unlock_threshold", "refuse_threshold", "hostile_threshold", "failure_penalty"):
                value = int(raw)
            else:
                value = float(raw)
        except ValueError:
            return web.Response(status=400, text=f"rep.{field} must be numeric")
        if err := validators.validate_jobs_rep(field, value):
            return web.Response(status=400, text=err)
        rep[field] = value

    # tier_rep_gain and tier_rep_loss per-tier
    new_gain = {}
    new_loss = {}
    for tk in tier_keys:
        gain_raw = form.get(f"tier_rep_gain_{tk}")
        loss_raw = form.get(f"tier_rep_loss_{tk}")
        if gain_raw is not None:
            try:
                new_gain[tk] = int(gain_raw)
            except ValueError:
                return web.Response(status=400, text=f"tier_rep_gain.{tk} must be integer")
        if loss_raw is not None:
            try:
                new_loss[tk] = int(loss_raw)
            except ValueError:
                return web.Response(status=400, text=f"tier_rep_loss.{tk} must be integer")
    if new_gain:
        rep["tier_rep_gain"] = {**rep.get("tier_rep_gain", {}), **new_gain}
    if new_loss:
        rep["tier_rep_loss"] = {**rep.get("tier_rep_loss", {}), **new_loss}

    async with io_locks.lock_for(JOBS_PATH):
        jobs["rep"] = rep
        await io_locks.atomic_write_json(JOBS_PATH, jobs)
        state.mark_dirty("jobs")
    return aiohttp_jinja2.render_template(
        "jobs_rep_form.html",
        request,
        {"rep": rep, "tier_keys": tier_keys, "editing": False, "just_saved": True},
    )


# --------------------------------------------------- narrative pool routes --

async def edit_narrative(request):
    npc_key = request.match_info["npc"]
    jobs = state.get_jobs()
    pools = jobs.get("narrative_pools", {})
    if npc_key not in pools and npc_key != "_big_score":
        return web.Response(status=404)
    if npc_key == "_big_score":
        lines = jobs.get("narrative_pools_big_score", [])
    else:
        lines = pools.get(npc_key, [])
    return aiohttp_jinja2.render_template(
        "jobs_narrative_row.html",
        request,
        {"npc_key": npc_key, "lines": lines, "editing": True},
    )


async def cancel_narrative(request):
    npc_key = request.match_info["npc"]
    jobs = state.get_jobs()
    pools = jobs.get("narrative_pools", {})
    if npc_key not in pools and npc_key != "_big_score":
        return web.Response(status=404)
    if npc_key == "_big_score":
        lines = jobs.get("narrative_pools_big_score", [])
    else:
        lines = pools.get(npc_key, [])
    return aiohttp_jinja2.render_template(
        "jobs_narrative_row.html",
        request,
        {"npc_key": npc_key, "lines": lines, "editing": False},
    )


async def save_narrative(request):
    npc_key = request.match_info["npc"]
    jobs = _get_jobs_mutable()
    if jobs is None:
        return web.Response(status=503, text="config.jobs not loaded")
    pools = jobs.get("narrative_pools", {})
    is_big_score = npc_key == "_big_score"
    if not is_big_score and npc_key not in pools:
        return web.Response(status=404)
    form = await request.post()
    raw_text = form.get("lines", "")
    # Each non-empty line is a separate entry
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    async with io_locks.lock_for(JOBS_PATH):
        if is_big_score:
            jobs["narrative_pools_big_score"] = lines
        else:
            pools[npc_key] = lines
        await io_locks.atomic_write_json(JOBS_PATH, jobs)
        state.mark_dirty("jobs")
    return aiohttp_jinja2.render_template(
        "jobs_narrative_row.html",
        request,
        {"npc_key": npc_key, "lines": lines, "editing": False, "just_saved": True},
    )


# --------------------------------------------------- complications routes ---

async def edit_complications(request):
    jobs = state.get_jobs()
    return aiohttp_jinja2.render_template(
        "jobs_complications_form.html",
        request,
        {"complications": jobs.get("complications", {}), "editing": True},
    )


async def cancel_complications(request):
    jobs = state.get_jobs()
    return aiohttp_jinja2.render_template(
        "jobs_complications_form.html",
        request,
        {"complications": jobs.get("complications", {}), "editing": False},
    )


async def save_complications(request):
    jobs = _get_jobs_mutable()
    if jobs is None:
        return web.Response(status=503, text="config.jobs not loaded")
    form = await request.post()
    existing = dict(jobs.get("complications", {}))

    # base_chance_by_tier — form fields: base_chance_<tier>
    tier_keys = sorted(jobs.get("tiers", {}).keys(), key=lambda x: int(x))
    new_base_chance = dict(existing.get("base_chance_by_tier", {}))
    for tk in tier_keys:
        raw = form.get(f"base_chance_{tk}")
        if raw is not None:
            try:
                new_base_chance[tk] = float(raw)
            except ValueError:
                return web.Response(status=400, text=f"base_chance_by_tier[{tk}] must be a float")
    existing["base_chance_by_tier"] = new_base_chance

    # heat_modifier — form fields: heat_mod_<level>
    new_heat_mod = dict(existing.get("heat_modifier", {}))
    for level in ("low", "watching", "scrutiny"):
        raw = form.get(f"heat_mod_{level}")
        if raw is not None:
            try:
                new_heat_mod[level] = float(raw)
            except ValueError:
                return web.Response(status=400, text=f"heat_modifier.{level} must be a float")
    existing["heat_modifier"] = new_heat_mod

    # scalar floats
    for field in ("rep_discount_per_point", "rep_discount_cap"):
        raw = form.get(field)
        if raw is not None:
            try:
                existing[field] = float(raw)
            except ValueError:
                return web.Response(status=400, text=f"complications.{field} must be a float")

    # sloppy_target_default_pack_tier_by_tier — form fields: sloppy_pack_<tier>
    new_sloppy = dict(existing.get("sloppy_target_default_pack_tier_by_tier", {}))
    for tk in tier_keys:
        raw = form.get(f"sloppy_pack_{tk}")
        if raw is not None:
            new_sloppy[tk] = raw.strip() or None
            if new_sloppy[tk] is None:
                new_sloppy.pop(tk, None)
    existing["sloppy_target_default_pack_tier_by_tier"] = new_sloppy

    if err := validators.validate_jobs_complications(existing):
        return web.Response(status=400, text=err)

    async with io_locks.lock_for(JOBS_PATH):
        jobs["complications"] = existing
        await io_locks.atomic_write_json(JOBS_PATH, jobs)
        state.mark_dirty("jobs")
    return aiohttp_jinja2.render_template(
        "jobs_complications_form.html",
        request,
        {"complications": jobs["complications"], "editing": False, "just_saved": True},
    )


# --------------------------------------------------- complication pool routes

async def edit_complication_pool(request):
    tier_key = request.match_info["tier"]
    jobs = state.get_jobs()
    pools = jobs.get("complication_pools", {})
    if tier_key not in pools:
        return web.Response(status=404)
    import json as _json
    entries = pools[tier_key]
    return aiohttp_jinja2.render_template(
        "jobs_complication_pool_row.html",
        request,
        {"tier_key": tier_key, "entries_json": _json.dumps(entries, indent=2), "editing": True},
    )


async def cancel_complication_pool(request):
    tier_key = request.match_info["tier"]
    jobs = state.get_jobs()
    pools = jobs.get("complication_pools", {})
    if tier_key not in pools:
        return web.Response(status=404)
    import json as _json
    entries = pools[tier_key]
    return aiohttp_jinja2.render_template(
        "jobs_complication_pool_row.html",
        request,
        {"tier_key": tier_key, "entries_json": _json.dumps(entries, indent=2), "editing": False},
    )


async def save_complication_pool(request):
    import json as _json
    tier_key = request.match_info["tier"]
    jobs = _get_jobs_mutable()
    if jobs is None:
        return web.Response(status=503, text="config.jobs not loaded")
    pools = jobs.get("complication_pools", {})
    if tier_key not in pools:
        return web.Response(status=404)
    form = await request.post()
    raw = form.get("entries_json", "[]")
    try:
        entries = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        return web.Response(status=400, text=f"Invalid JSON: {exc}")
    if not isinstance(entries, list):
        return web.Response(status=400, text="complication_pools[tier] must be a JSON array")
    for entry in entries:
        if err := validators.validate_jobs_complication_pool_entry(entry):
            return web.Response(status=400, text=err)
    async with io_locks.lock_for(JOBS_PATH):
        pools[tier_key] = entries
        await io_locks.atomic_write_json(JOBS_PATH, jobs)
        state.mark_dirty("jobs")
    return aiohttp_jinja2.render_template(
        "jobs_complication_pool_row.html",
        request,
        {"tier_key": tier_key, "entries_json": _json.dumps(entries, indent=2), "editing": False, "just_saved": True},
    )


# --------------------------------------------------- reward_recipes routes --

async def edit_recipe(request):
    npc_key = request.match_info["npc"]
    tier_key = request.match_info["tier"]
    jobs = state.get_jobs()
    npcs = jobs.get("npcs", {})
    if npc_key not in npcs:
        return web.Response(status=404)
    import json as _json
    recipes = npcs[npc_key].get("reward_recipes", {})
    entries = recipes.get(tier_key, [])
    return aiohttp_jinja2.render_template(
        "jobs_recipe_row.html",
        request,
        {"npc_key": npc_key, "tier_key": tier_key, "entries_json": _json.dumps(entries, indent=2), "editing": True},
    )


async def cancel_recipe(request):
    npc_key = request.match_info["npc"]
    tier_key = request.match_info["tier"]
    jobs = state.get_jobs()
    npcs = jobs.get("npcs", {})
    if npc_key not in npcs:
        return web.Response(status=404)
    import json as _json
    recipes = npcs[npc_key].get("reward_recipes", {})
    entries = recipes.get(tier_key, [])
    return aiohttp_jinja2.render_template(
        "jobs_recipe_row.html",
        request,
        {"npc_key": npc_key, "tier_key": tier_key, "entries_json": _json.dumps(entries, indent=2), "editing": False},
    )


async def save_recipe(request):
    import json as _json
    npc_key = request.match_info["npc"]
    tier_key = request.match_info["tier"]
    jobs = _get_jobs_mutable()
    if jobs is None:
        return web.Response(status=503, text="config.jobs not loaded")
    npcs = jobs.get("npcs", {})
    if npc_key not in npcs:
        return web.Response(status=404)
    form = await request.post()
    raw = form.get("entries_json", "[]")
    try:
        entries = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        return web.Response(status=400, text=f"Invalid JSON: {exc}")
    if not isinstance(entries, list):
        return web.Response(status=400, text="reward_recipes[tier] must be a JSON array")
    for entry in entries:
        if err := validators.validate_jobs_recipe_entry(entry):
            return web.Response(status=400, text=err)
    async with io_locks.lock_for(JOBS_PATH):
        if "reward_recipes" not in npcs[npc_key]:
            npcs[npc_key]["reward_recipes"] = {}
        npcs[npc_key]["reward_recipes"][tier_key] = entries
        await io_locks.atomic_write_json(JOBS_PATH, jobs)
        state.mark_dirty("jobs")
    return aiohttp_jinja2.render_template(
        "jobs_recipe_row.html",
        request,
        {"npc_key": npc_key, "tier_key": tier_key, "entries_json": _json.dumps(entries, indent=2), "editing": False, "just_saved": True},
    )


# --------------------------------------------------- cat_voices routes ------

async def edit_voice(request):
    rarity = request.match_info["rarity"]
    jobs = state.get_jobs()
    voices = jobs.get("cat_voices", {})
    if rarity not in voices:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "jobs_voice_row.html",
        request,
        {"rarity": rarity, "voice": voices[rarity], "editing": True},
    )


async def cancel_voice(request):
    rarity = request.match_info["rarity"]
    jobs = state.get_jobs()
    voices = jobs.get("cat_voices", {})
    if rarity not in voices:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "jobs_voice_row.html",
        request,
        {"rarity": rarity, "voice": voices[rarity], "editing": False},
    )


async def save_voice(request):
    rarity = request.match_info["rarity"]
    jobs = _get_jobs_mutable()
    if jobs is None:
        return web.Response(status=503, text="config.jobs not loaded")
    voices = jobs.get("cat_voices", {})
    if rarity not in voices:
        return web.Response(status=404)
    form = await request.post()
    new_voice = {}
    for outcome in ("success", "near_miss", "total_failure"):
        raw_text = form.get(outcome, "")
        lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
        for ln in lines:
            if err := validators.validate_jobs_voice_entry(ln):
                return web.Response(status=400, text=f"{rarity}.{outcome}: {err}")
        new_voice[outcome] = lines
    async with io_locks.lock_for(JOBS_PATH):
        voices[rarity] = new_voice
        await io_locks.atomic_write_json(JOBS_PATH, jobs)
        state.mark_dirty("jobs")
    return aiohttp_jinja2.render_template(
        "jobs_voice_row.html",
        request,
        {"rarity": rarity, "voice": voices[rarity], "editing": False, "just_saved": True},
    )


# --------------------------------------------------- complication_quips routes

async def edit_quip(request):
    event_id = request.match_info["event_id"]
    jobs = state.get_jobs()
    quips = jobs.get("complication_quips", {})
    if event_id not in quips:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "jobs_quip_row.html",
        request,
        {"event_id": event_id, "quip": quips[event_id], "editing": True},
    )


async def cancel_quip(request):
    event_id = request.match_info["event_id"]
    jobs = state.get_jobs()
    quips = jobs.get("complication_quips", {})
    if event_id not in quips:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "jobs_quip_row.html",
        request,
        {"event_id": event_id, "quip": quips[event_id], "editing": False},
    )


async def save_quip(request):
    event_id = request.match_info["event_id"]
    jobs = _get_jobs_mutable()
    if jobs is None:
        return web.Response(status=503, text="config.jobs not loaded")
    quips = jobs.get("complication_quips", {})
    if event_id not in quips:
        return web.Response(status=404)
    form = await request.post()
    # Form fields are named quip_<rarity>
    new_quip = {}
    for key in form:
        if key.startswith("quip_"):
            rarity = key[5:]
            raw_text = form.get(key, "")
            lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
            if lines:
                new_quip[rarity] = lines
    async with io_locks.lock_for(JOBS_PATH):
        quips[event_id] = new_quip
        await io_locks.atomic_write_json(JOBS_PATH, jobs)
        state.mark_dirty("jobs")
    return aiohttp_jinja2.render_template(
        "jobs_quip_row.html",
        request,
        {"event_id": event_id, "quip": quips[event_id], "editing": False, "just_saved": True},
    )


# --------------------------------------------------- complication_flavor routes

async def edit_flavor(request):
    event_id = request.match_info["event_id"]
    jobs = state.get_jobs()
    flavor = jobs.get("complication_flavor", {})
    if event_id not in flavor:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "jobs_flavor_row.html",
        request,
        {"event_id": event_id, "lines": flavor[event_id], "editing": True},
    )


async def cancel_flavor(request):
    event_id = request.match_info["event_id"]
    jobs = state.get_jobs()
    flavor = jobs.get("complication_flavor", {})
    if event_id not in flavor:
        return web.Response(status=404)
    return aiohttp_jinja2.render_template(
        "jobs_flavor_row.html",
        request,
        {"event_id": event_id, "lines": flavor[event_id], "editing": False},
    )


async def save_flavor(request):
    event_id = request.match_info["event_id"]
    jobs = _get_jobs_mutable()
    if jobs is None:
        return web.Response(status=503, text="config.jobs not loaded")
    flavor = jobs.get("complication_flavor", {})
    if event_id not in flavor:
        return web.Response(status=404)
    form = await request.post()
    raw_text = form.get("lines", "")
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    async with io_locks.lock_for(JOBS_PATH):
        flavor[event_id] = lines
        await io_locks.atomic_write_json(JOBS_PATH, jobs)
        state.mark_dirty("jobs")
    return aiohttp_jinja2.render_template(
        "jobs_flavor_row.html",
        request,
        {"event_id": event_id, "lines": lines, "editing": False, "just_saved": True},
    )


# --------------------------------------------------- perks routes  ----------
#
# Three sub-editors for the Phase 4 perks block in config/jobs.json:
#   perks.drop_chance_by_tier  — 5 floats (one per tier), inline form
#   perks.drop_pools           — per-(NPC, tier) JSON list of {id, weight}
#   perks.catalog              — per-perk name/desc/tier_table
#
# Referential warnings (rendered on the index alongside the rest):
#   - any perk id in a drop_pool that's not in the catalog
#   - any tier key in drop_pools that's not in jobs.tiers (or "5" for Big Score)
#   - any tier key in a catalog tier_table that's not in jobs.tiers

async def edit_perks_chances(request):
    jobs = state.get_jobs()
    perks = jobs.get("perks", {}) or {}
    return aiohttp_jinja2.render_template(
        "jobs_perks_chances_form.html",
        request,
        {"perks": perks, "editing": True},
    )


async def cancel_perks_chances(request):
    jobs = state.get_jobs()
    perks = jobs.get("perks", {}) or {}
    return aiohttp_jinja2.render_template(
        "jobs_perks_chances_form.html",
        request,
        {"perks": perks, "editing": False},
    )


async def save_perks_chances(request):
    jobs = _get_jobs_mutable()
    if jobs is None:
        return web.Response(status=503, text="config.jobs not loaded")
    form = await request.post()
    perks = jobs.setdefault("perks", {})
    chances = dict(perks.get("drop_chance_by_tier", {}))
    tier_keys = sorted(jobs.get("tiers", {}).keys(), key=lambda x: int(x))
    for tk in tier_keys:
        raw = form.get(f"chance_{tk}")
        if raw is None or raw == "":
            chances.pop(tk, None)
            continue
        try:
            v = float(raw)
        except ValueError:
            return web.Response(status=400, text=f"drop_chance_by_tier[{tk}] must be a float")
        if v < 0 or v > 1:
            return web.Response(status=400, text=f"drop_chance_by_tier[{tk}] must be in [0, 1]")
        chances[tk] = v
    # Also accept max_active.
    raw_max = form.get("max_active")
    if raw_max is not None and raw_max != "":
        try:
            ma = int(raw_max)
        except ValueError:
            return web.Response(status=400, text="max_active must be an integer")
        if ma < 1 or ma > 50:
            return web.Response(status=400, text="max_active must be in [1, 50]")
        perks["max_active"] = ma
    async with io_locks.lock_for(JOBS_PATH):
        perks["drop_chance_by_tier"] = chances
        await io_locks.atomic_write_json(JOBS_PATH, jobs)
        state.mark_dirty("jobs")
    return aiohttp_jinja2.render_template(
        "jobs_perks_chances_form.html",
        request,
        {"perks": perks, "editing": False, "just_saved": True},
    )


async def edit_perks_pool(request):
    import json as _json
    npc = request.match_info["npc"]
    tier_key = request.match_info["tier"]
    jobs = state.get_jobs()
    pool = ((jobs.get("perks", {}).get("drop_pools", {}) or {}).get(npc, {}) or {}).get(tier_key, [])
    return aiohttp_jinja2.render_template(
        "jobs_perks_pool_row.html",
        request,
        {"npc_key": npc, "tier_key": tier_key, "entries_json": _json.dumps(pool, indent=2), "editing": True},
    )


async def cancel_perks_pool(request):
    import json as _json
    npc = request.match_info["npc"]
    tier_key = request.match_info["tier"]
    jobs = state.get_jobs()
    pool = ((jobs.get("perks", {}).get("drop_pools", {}) or {}).get(npc, {}) or {}).get(tier_key, [])
    return aiohttp_jinja2.render_template(
        "jobs_perks_pool_row.html",
        request,
        {"npc_key": npc, "tier_key": tier_key, "entries_json": _json.dumps(pool, indent=2), "editing": False},
    )


async def save_perks_pool(request):
    import json as _json
    npc = request.match_info["npc"]
    tier_key = request.match_info["tier"]
    jobs = _get_jobs_mutable()
    if jobs is None:
        return web.Response(status=503, text="config.jobs not loaded")
    form = await request.post()
    raw = form.get("entries_json", "[]")
    try:
        entries = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        return web.Response(status=400, text=f"Invalid JSON: {exc}")
    if not isinstance(entries, list):
        return web.Response(status=400, text="drop_pools entry must be a JSON array")
    catalog_ids = set((jobs.get("perks", {}).get("catalog", {}) or {}).keys())
    for e in entries:
        if not isinstance(e, dict):
            return web.Response(status=400, text="each entry must be a {id, weight} object")
        pid = e.get("id")
        w = e.get("weight")
        if not isinstance(pid, str) or not pid:
            return web.Response(status=400, text="entry.id must be a non-empty string")
        if not isinstance(w, (int, float)) or w < 0:
            return web.Response(status=400, text=f"entry.weight for {pid!r} must be a non-negative number")
        # Hard block: unknown perk id (the player would silently see nothing).
        if catalog_ids and pid not in catalog_ids:
            return web.Response(status=400, text=f"unknown perk id {pid!r} (not in perks.catalog)")
    async with io_locks.lock_for(JOBS_PATH):
        perks = jobs.setdefault("perks", {})
        pools = perks.setdefault("drop_pools", {})
        pools.setdefault(npc, {})[tier_key] = entries
        await io_locks.atomic_write_json(JOBS_PATH, jobs)
        state.mark_dirty("jobs")
    return aiohttp_jinja2.render_template(
        "jobs_perks_pool_row.html",
        request,
        {"npc_key": npc, "tier_key": tier_key, "entries_json": _json.dumps(entries, indent=2), "editing": False, "just_saved": True},
    )


async def edit_perks_catalog(request):
    import json as _json
    perk_id = request.match_info["perk_id"]
    jobs = state.get_jobs()
    cat = (jobs.get("perks", {}).get("catalog", {}) or {}).get(perk_id, {})
    tier_table_json = _json.dumps(cat.get("tier_table", {}), indent=2)
    return aiohttp_jinja2.render_template(
        "jobs_perks_catalog_row.html",
        request,
        {
            "perk_id": perk_id,
            "name": cat.get("name", ""),
            "desc": cat.get("desc", ""),
            "tier_table_json": tier_table_json,
            "editing": True,
        },
    )


async def cancel_perks_catalog(request):
    import json as _json
    perk_id = request.match_info["perk_id"]
    jobs = state.get_jobs()
    cat = (jobs.get("perks", {}).get("catalog", {}) or {}).get(perk_id, {})
    tier_table_json = _json.dumps(cat.get("tier_table", {}), indent=2)
    return aiohttp_jinja2.render_template(
        "jobs_perks_catalog_row.html",
        request,
        {
            "perk_id": perk_id,
            "name": cat.get("name", ""),
            "desc": cat.get("desc", ""),
            "tier_table_json": tier_table_json,
            "editing": False,
        },
    )


async def save_perks_catalog(request):
    import json as _json
    perk_id = request.match_info["perk_id"]
    jobs = _get_jobs_mutable()
    if jobs is None:
        return web.Response(status=503, text="config.jobs not loaded")
    form = await request.post()
    name = (form.get("name") or "").strip()
    desc = (form.get("desc") or "").strip()
    raw = form.get("tier_table_json", "{}")
    try:
        tier_table = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        return web.Response(status=400, text=f"Invalid JSON in tier_table: {exc}")
    if not isinstance(tier_table, dict):
        return web.Response(status=400, text="tier_table must be a JSON object {tier: {...}}")
    tier_keys = set(jobs.get("tiers", {}).keys())
    for tk, tdata in tier_table.items():
        if tk not in tier_keys:
            return web.Response(status=400, text=f"tier_table key {tk!r} is not a known tier (got: {sorted(tier_keys)})")
        if not isinstance(tdata, dict):
            return web.Response(status=400, text=f"tier_table[{tk}] must be an object")
    async with io_locks.lock_for(JOBS_PATH):
        catalog = jobs.setdefault("perks", {}).setdefault("catalog", {})
        existing = catalog.get(perk_id, {})
        if name:
            existing["name"] = name
        if desc:
            existing["desc"] = desc
        existing["tier_table"] = tier_table
        catalog[perk_id] = existing
        await io_locks.atomic_write_json(JOBS_PATH, jobs)
        state.mark_dirty("jobs")
    return aiohttp_jinja2.render_template(
        "jobs_perks_catalog_row.html",
        request,
        {
            "perk_id": perk_id,
            "name": existing.get("name", ""),
            "desc": existing.get("desc", ""),
            "tier_table_json": _json.dumps(tier_table, indent=2),
            "editing": False,
            "just_saved": True,
        },
    )


# --------------------------------------------------- register  --------------

def register(app: web.Application) -> None:
    app.router.add_get("/jobs", index)

    # send_power
    app.router.add_get(r"/jobs/sp/{cat_type}/edit", edit_sp)
    app.router.add_get(r"/jobs/sp/{cat_type}/cancel", cancel_sp)
    app.router.add_post(r"/jobs/sp/{cat_type}", save_sp)

    # probability
    app.router.add_get("/jobs/probability/edit", edit_probability)
    app.router.add_get("/jobs/probability/cancel", cancel_probability)
    app.router.add_post("/jobs/probability", save_probability)

    # tuning
    app.router.add_get("/jobs/tuning/edit", edit_tuning)
    app.router.add_get("/jobs/tuning/cancel", cancel_tuning)
    app.router.add_post("/jobs/tuning", save_tuning)

    # tiers
    app.router.add_get(r"/jobs/tier/{tier}/edit", edit_tier)
    app.router.add_get(r"/jobs/tier/{tier}/cancel", cancel_tier)
    app.router.add_post(r"/jobs/tier/{tier}", save_tier)

    # npcs
    app.router.add_get(r"/jobs/npc/{npc}/edit", edit_npc)
    app.router.add_get(r"/jobs/npc/{npc}/cancel", cancel_npc)
    app.router.add_post(r"/jobs/npc/{npc}", save_npc)

    # big_score
    app.router.add_get("/jobs/big_score/edit", edit_big_score)
    app.router.add_get("/jobs/big_score/cancel", cancel_big_score)
    app.router.add_post("/jobs/big_score", save_big_score)

    # rep
    app.router.add_get("/jobs/rep/edit", edit_rep)
    app.router.add_get("/jobs/rep/cancel", cancel_rep)
    app.router.add_post("/jobs/rep", save_rep)

    # narrative pools
    app.router.add_get(r"/jobs/narrative/{npc}/edit", edit_narrative)
    app.router.add_get(r"/jobs/narrative/{npc}/cancel", cancel_narrative)
    app.router.add_post(r"/jobs/narrative/{npc}", save_narrative)

    # complications scalars
    app.router.add_get("/jobs/complications/edit", edit_complications)
    app.router.add_get("/jobs/complications/cancel", cancel_complications)
    app.router.add_post("/jobs/complications", save_complications)

    # complication pools (per-tier textarea)
    app.router.add_get(r"/jobs/complication_pool/{tier}/edit", edit_complication_pool)
    app.router.add_get(r"/jobs/complication_pool/{tier}/cancel", cancel_complication_pool)
    app.router.add_post(r"/jobs/complication_pool/{tier}", save_complication_pool)

    # reward_recipes (per-NPC, per-tier)
    app.router.add_get(r"/jobs/npc/{npc}/recipe/{tier}/edit", edit_recipe)
    app.router.add_get(r"/jobs/npc/{npc}/recipe/{tier}/cancel", cancel_recipe)
    app.router.add_post(r"/jobs/npc/{npc}/recipe/{tier}", save_recipe)

    # cat_voices (per-rarity)
    app.router.add_get(r"/jobs/voice/{rarity}/edit", edit_voice)
    app.router.add_get(r"/jobs/voice/{rarity}/cancel", cancel_voice)
    app.router.add_post(r"/jobs/voice/{rarity}", save_voice)

    # complication_quips (per-event_id)
    app.router.add_get(r"/jobs/quip/{event_id}/edit", edit_quip)
    app.router.add_get(r"/jobs/quip/{event_id}/cancel", cancel_quip)
    app.router.add_post(r"/jobs/quip/{event_id}", save_quip)

    # complication_flavor (per-event_id)
    app.router.add_get(r"/jobs/flavor/{event_id}/edit", edit_flavor)
    app.router.add_get(r"/jobs/flavor/{event_id}/cancel", cancel_flavor)
    app.router.add_post(r"/jobs/flavor/{event_id}", save_flavor)

    # perks.drop_chance_by_tier + max_active (single form)
    app.router.add_get("/jobs/perks/chances/edit", edit_perks_chances)
    app.router.add_get("/jobs/perks/chances/cancel", cancel_perks_chances)
    app.router.add_post("/jobs/perks/chances", save_perks_chances)

    # perks.drop_pools[npc][tier] (one row per (NPC, tier))
    app.router.add_get(r"/jobs/perks/pool/{npc}/{tier}/edit", edit_perks_pool)
    app.router.add_get(r"/jobs/perks/pool/{npc}/{tier}/cancel", cancel_perks_pool)
    app.router.add_post(r"/jobs/perks/pool/{npc}/{tier}", save_perks_pool)

    # perks.catalog[perk_id] (one row per perk; name + desc + tier_table JSON)
    app.router.add_get(r"/jobs/perks/catalog/{perk_id}/edit", edit_perks_catalog)
    app.router.add_get(r"/jobs/perks/catalog/{perk_id}/cancel", cancel_perks_catalog)
    app.router.add_post(r"/jobs/perks/catalog/{perk_id}", save_perks_catalog)
