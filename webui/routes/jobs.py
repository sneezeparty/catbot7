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

    for npc_key, npc in jobs.get("npcs", {}).items():
        for t in npc.get("tiers_offered", []):
            if str(t) not in tier_keys:
                warnings.append(f"NPC {npc_key}: tiers_offered contains {t!r} which is not in tiers")
        for h in npc.get("hires_against", []):
            if h not in all_npc_like:
                warnings.append(f"NPC {npc_key}: hires_against contains {h!r} (not in npcs/targets_only/magic)")

    bs = jobs.get("big_score", {})
    if bs.get("patron_npc") and bs["patron_npc"] not in npc_keys:
        warnings.append(f"big_score.patron_npc={bs['patron_npc']!r} not found in npcs")
    if bs.get("target_npc") and bs["target_npc"] not in (npc_keys | target_keys):
        warnings.append(f"big_score.target_npc={bs['target_npc']!r} not found in npcs/targets_only")

    return warnings


# ------------------------------------------------------------------ index  --

async def index(request):
    jobs = state.get_jobs()
    warnings = _referential_warnings(jobs)
    # Build a flat list for the rep tier tables
    tier_keys = sorted(jobs.get("tiers", {}).keys(), key=lambda x: int(x))
    rep = jobs.get("rep", {})
    tier_rep_gain = rep.get("tier_rep_gain", {})
    tier_rep_loss = rep.get("tier_rep_loss", {})
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
