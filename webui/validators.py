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
