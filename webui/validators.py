"""Pre-write validators for config edits made through the admin webui.

Each validator returns `None` on success or a short human-readable error
string. Callers translate the string into a `web.Response(status=400, text=...)`
which surfaces via the htmx error toast in `htmx-config.js`.

Bounds were chosen to keep the bot from crashing or going into nonsensical
states (negative cooldowns, all-zero spawn weights, etc.). They are *not*
meant to enforce game balance.
"""

from __future__ import annotations

from typing import Any


# ============================================================== tuning =====

# (lo, hi) bounds per scalar key. `None` means unbounded on that side.
TUNING_SCALAR_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    # *_seconds — must be positive enough to avoid spammy/nonsensical loops
    "quest_cooldown_seconds":              (1,     None),
    "fast_catcher_threshold_seconds":      (0,     None),
    "slow_catcher_threshold_seconds":      (1,     None),
    "rainboost_short_seconds":             (1,     None),
    "rainboost_long_seconds":              (1,     None),
    "catnip_timer_extend_seconds":         (1,     None),
    "main_loop_interval_seconds":          (10,    None),
    "anti_double_catch_cooldown_seconds":  (0,     None),
    "view_timeout_seconds":                (1,     None),

    # probabilities / coefficients in [0, 1]
    "pack_drop_chance_on_catch":           (0.0,   1.0),
    "prism_boost_global_coef":             (0.0,   1.0),
    "prism_boost_user_coef":               (0.0,   1.0),
    "prism_boost_floor":                   (0.0,   1.0),

    # counts / quantities — non-negative
    "coin_per_pack":                       (0,     None),
    "bakery_cost_cookies":                 (0,     None),
    "bakery_cost_coffees":                 (0,     None),
    "bakery_cost_nice_cats":               (0,     None),
}

# Dict sections whose values feed weighted random picks. They must stay
# non-empty and contain at least one positive entry, otherwise random.choices()
# raises "Total of weights must be greater than zero".
TUNING_WEIGHT_DICTS: set[str] = {"type_dict", "pack_tier_weights"}


def validate_tuning_scalar(key: str, value: Any) -> str | None:
    lo, hi = TUNING_SCALAR_BOUNDS.get(key, (None, None))
    # bools shouldn't be range-checked even though they're ints in Python
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if lo is not None and value < lo:
        return f"{key} must be ≥ {lo} (got {value})"
    if hi is not None and value > hi:
        return f"{key} must be ≤ {hi} (got {value})"
    return None


def validate_tuning_weight_dict(section: str, hypothetical: dict) -> str | None:
    """Validate the would-be state of a weight dict after an edit."""
    if section not in TUNING_WEIGHT_DICTS:
        return None
    if not hypothetical:
        return f"{section} must have at least one entry"
    total = 0.0
    for k, v in hypothetical.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return f"{section}.{k} must be numeric (got {type(v).__name__})"
        if v < 0:
            return f"{section}.{k} must be ≥ 0 (got {v})"
        total += v
    if total <= 0:
        return f"{section} weights must sum to > 0 (at least one positive value required)"
    return None


# ========================================================= stock_market =====

_SM_SCALAR_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    "spread":            (0.0,  1.0),   # fraction; 1.0 = 100% spread (nonsensical but not crashing)
    "mm_order_quantity": (1,    None),  # at least 1 share per side
    "price_floor":       (1,    None),  # must be at least 1 coin
    "price_ceiling":     (1,    None),  # will be cross-checked against floor below
    "metric_eps":        (0.0,  None),  # smoothing constant; 0 is technically ok (no divide if metric > 0)
}


def validate_stock_market_scalar(key: str, value: Any) -> str | None:
    if isinstance(value, bool):
        return None  # `enabled` is bool; no range check needed
    lo, hi = _SM_SCALAR_BOUNDS.get(key, (None, None))
    if not isinstance(value, (int, float)):
        return None
    if lo is not None and value < lo:
        return f"stock_market.{key} must be >= {lo} (got {value})"
    if hi is not None and value > hi:
        return f"stock_market.{key} must be <= {hi} (got {value})"
    return None


def validate_stock_market_ticker(ticker: str, base: int, baseline: float, alpha: float) -> str | None:
    if base < 1:
        return f"{ticker}.base must be >= 1 (got {base})"
    if baseline <= 0:
        return f"{ticker}.baseline must be > 0 (got {baseline})"
    if alpha <= 0:
        return f"{ticker}.alpha must be > 0 (got {alpha})"
    return None


# =========================================================== battlepass =====

def validate_battlepass_level(xp: int, amount: int, reward: str) -> str | None:
    if xp < 0:
        return f"xp must be ≥ 0 (got {xp})"
    if amount < 1:
        return f"amount must be ≥ 1 (got {amount})"
    if not reward.strip():
        return "reward cannot be empty"
    return None


def validate_battlepass_quest(xp_min: int, xp_max: int, progress: int, title: str) -> str | None:
    if xp_min < 0:
        return f"xp_min must be ≥ 0 (got {xp_min})"
    if xp_max < xp_min:
        return f"xp_max must be ≥ xp_min (xp_min={xp_min}, xp_max={xp_max}) — otherwise random.randint crashes"
    if progress < 1:
        return f"progress must be ≥ 1 (got {progress})"
    if not title.strip():
        return "title cannot be empty"
    return None


# =============================================================== catnip =====

def validate_catnip_perk(weight: int, values: list) -> str | None:
    if weight < 0:
        return f"weight must be ≥ 0 (got {weight})"
    for i, v in enumerate(values):
        if v < 0:
            return f"values[{i}] must be ≥ 0 (got {v})"
    return None


def validate_jobs_send_power(cat_type: str, value: int) -> str | None:
    if value < 0:
        return f"send_power[{cat_type}] must be >= 0 (got {value})"
    return None


def validate_jobs_probability(k: float, floor: float, ceiling: float, near_miss_band: float) -> str | None:
    if k <= 0 or k > 10:
        return f"k must be in (0, 10] (got {k})"
    if not (0.0 <= floor <= 1.0):
        return f"floor must be in [0, 1] (got {floor})"
    if not (0.0 <= ceiling <= 1.0):
        return f"ceiling must be in [0, 1] (got {ceiling})"
    if floor >= ceiling:
        return f"floor must be < ceiling (floor={floor}, ceiling={ceiling})"
    if not (0.0 <= near_miss_band <= 1.0):
        return f"near_miss_band must be in [0, 1] (got {near_miss_band})"
    return None


def validate_jobs_tuning(field: str, value: int | float) -> str | None:
    non_negative_int = {
        "offer_refresh_window_seconds", "decline_cooldown_seconds",
        "max_concurrent_offers", "cancel_grace_seconds",
        "pinch_threshold", "pinch_lockout_seconds",
    }
    non_negative = {
        "heat_decay_per_hour", "pinch_reset_heat",
    }
    if field in non_negative_int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return f"jobs.tuning.{field} must be a non-negative integer (got {value!r})"
    elif field in non_negative:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            return f"jobs.tuning.{field} must be >= 0 (got {value!r})"
    return None


def validate_jobs_tier(
    name: str,
    diff_lo: int, diff_hi: int,
    coin_lo: int, coin_hi: int,
    heat: int,
    min_catnip_level: int,
) -> str | None:
    if not name.strip():
        return "tier name cannot be empty"
    if diff_lo < 0:
        return f"difficulty_range[0] must be >= 0 (got {diff_lo})"
    if diff_hi < diff_lo:
        return f"difficulty_range[1] must be >= difficulty_range[0] ({diff_lo}), got {diff_hi}"
    if coin_lo < 0:
        return f"reward_coin_range[0] must be >= 0 (got {coin_lo})"
    if coin_hi < coin_lo:
        return f"reward_coin_range[1] must be >= reward_coin_range[0] ({coin_lo}), got {coin_hi}"
    if heat < 0:
        return f"heat must be >= 0 (got {heat})"
    if min_catnip_level < 0:
        return f"min_catnip_level must be >= 0 (got {min_catnip_level})"
    return None


def validate_jobs_npc(
    display_name: str,
    min_hire_level: int,
    reward_mult: float,
    heat_mult: float,
) -> str | None:
    if not display_name.strip():
        return "display_name cannot be empty"
    if min_hire_level < 0:
        return f"min_hire_level must be >= 0 (got {min_hire_level})"
    if reward_mult <= 0:
        return f"reward_mult must be > 0 (got {reward_mult})"
    if heat_mult <= 0:
        return f"heat_mult must be > 0 (got {heat_mult})"
    return None


def validate_jobs_rep(field: str, value: float) -> str | None:
    unit_fraction_fields = {
        "offerer_bonus_per_point", "offerer_bonus_cap",
        "target_difficulty_per_negative_point", "target_difficulty_cap",
        "premium_reward_bonus_at_100", "hostile_target_heat_discount",
    }
    if field in unit_fraction_fields:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            return f"rep.{field} must be >= 0 (got {value!r})"
    return None


PACK_TIER_LIST = ["wooden", "stone", "bronze", "silver", "gold", "diamond", "emerald", "obsidian"]
COMPLICATION_PHASES = {"pre_roll", "post_roll", "aftermath"}


def validate_jobs_complications(complications: dict) -> str | None:
    """Validate the top-level complications scalar block."""
    base_chance = complications.get("base_chance_by_tier", {})
    for tier, chance in base_chance.items():
        if not isinstance(chance, (int, float)) or not (0.0 <= chance <= 1.0):
            return f"complications.base_chance_by_tier[{tier}] must be a float in [0, 1] (got {chance!r})"
    heat_mod = complications.get("heat_modifier", {})
    for level, mod in heat_mod.items():
        if not isinstance(mod, (int, float)) or mod < 0:
            return f"complications.heat_modifier[{level}] must be a non-negative float (got {mod!r})"
    rep_discount = complications.get("rep_discount_per_point", 0.0)
    if not isinstance(rep_discount, (int, float)) or not (0.0 <= rep_discount <= 0.01):
        return f"complications.rep_discount_per_point must be in [0, 0.01] (got {rep_discount!r})"
    rep_cap = complications.get("rep_discount_cap", 0.0)
    if not isinstance(rep_cap, (int, float)) or not (0.0 <= rep_cap <= 1.0):
        return f"complications.rep_discount_cap must be in [0, 1] (got {rep_cap!r})"
    sloppy = complications.get("sloppy_target_default_pack_tier_by_tier", {})
    for tier, pack_tier in sloppy.items():
        if pack_tier not in PACK_TIER_LIST:
            return f"complications.sloppy_target_default_pack_tier_by_tier[{tier}]={pack_tier!r} is not a known pack tier"
    return None


def validate_jobs_complication_pool_entry(entry: dict) -> str | None:
    """Validate a single entry in a complication_pools tier list."""
    if not entry.get("id", "").strip():
        return "complication pool entry: id cannot be empty"
    weight = entry.get("weight", 0)
    if not isinstance(weight, (int, float)) or weight < 0:
        return f"complication pool entry {entry.get('id')!r}: weight must be >= 0 (got {weight!r})"
    phase = entry.get("phase", "")
    if phase not in COMPLICATION_PHASES:
        return f"complication pool entry {entry.get('id')!r}: phase must be one of {sorted(COMPLICATION_PHASES)} (got {phase!r})"
    return None


def validate_jobs_recipe_entry(entry: dict) -> str | None:
    """Validate a single reward_recipes entry for an NPC tier."""
    weight = entry.get("weight", 0)
    if not isinstance(weight, (int, float)) or weight < 0:
        return f"recipe entry: weight must be >= 0 (got {weight!r})"
    coins = entry.get("coins", [0, 0])
    if not isinstance(coins, list) or len(coins) != 2:
        return "recipe entry: coins must be a two-element list [lo, hi]"
    lo, hi = coins
    if not isinstance(lo, int) or not isinstance(hi, int):
        return "recipe entry: coins [lo, hi] must both be integers"
    if lo > hi:
        return f"recipe entry: coins lo ({lo}) must be <= hi ({hi})"
    cats = entry.get("cats", {})
    if not isinstance(cats, dict):
        return "recipe entry: cats must be a dict"
    for rarity, count in cats.items():
        if not isinstance(count, int) or count < 1:
            return f"recipe entry: cats[{rarity!r}] must be an integer >= 1 (got {count!r})"
    pack = entry.get("pack")
    if pack is not None and pack not in PACK_TIER_LIST:
        return f"recipe entry: pack {pack!r} is not a known pack tier"
    return None


def validate_jobs_voice_entry(line: str) -> str | None:
    """Validate a single voice line."""
    if not line.strip():
        return "voice line cannot be empty"
    return None


def validate_jobs_help_page(title: str, body: str, min_level_to_see: int) -> str | None:
    if not title.strip():
        return "title cannot be empty"
    if min_level_to_see < 0:
        return f"min_level_to_see must be >= 0 (got {min_level_to_see})"
    return None


def validate_catnip_level(
    duration: int,
    cost: int,
    bounty_difficulty: int,
    bounty_amount: int,
    bonus: float,
    max_amount: int,
    weights: dict,
    store_discount: int = 0,
) -> str | None:
    if duration < 1:
        return f"duration must be ≥ 1 (got {duration})"
    if cost < 0:
        return f"cost must be ≥ 0 (got {cost})"
    if bounty_difficulty < 0:
        return f"bounty_difficulty must be ≥ 0 (got {bounty_difficulty})"
    if bounty_amount < 0:
        return f"bounty_amount must be ≥ 0 (got {bounty_amount})"
    if max_amount < 1:
        return f"max_amount must be ≥ 1 (got {max_amount})"
    for r, w in weights.items():
        if w < 0:
            return f"weight_{r} must be ≥ 0 (got {w})"
    if sum(weights.values()) <= 0:
        return "at least one rarity weight must be > 0 (otherwise the level can't roll a cat)"
    # store_discount: negative = tax (Newbie pays more), positive = discount (high-level bonus)
    # Hard-cap at ±50 to prevent free/negative-cost purchases crashing buy handler
    if store_discount < -50 or store_discount > 50:
        return f"store_discount must be between -50 and 50 (got {store_discount})"
    return None
