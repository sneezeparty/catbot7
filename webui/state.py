"""Lazy accessors for bot state.

Never `from main import X` in webui modules — `main` is a discord.py extension
that gets unloaded/reimported on `cat!restart`. Capture pointers here once at
boot and resolve attributes on every request so reloads pick up automatically.

The webui is read-only: it never mutates game state or configs, so there are
no reload/dirty helpers here — the bot is reloaded only by the owner in chat.
"""

import time

import catpg
import config

_bot = None


def init(bot):
    global _bot
    _bot = bot


def get_bot():
    return _bot


def get_bot_user_id() -> int | None:
    """Discord user_id of the bot itself, once it has logged in. Returned for
    aggregate queries that should exclude Cat Bot's own profile/user rows
    (e.g., the bot accumulated coins from gift/sacrifice flows but isn't a
    'real' player). None until on_ready fires."""
    if _bot is None or _bot.user is None:
        return None
    return int(_bot.user.id)


def bot_user_id_or_zero() -> int:
    """Same as `get_bot_user_id` but returns 0 when the bot hasn't logged in
    yet, so a `WHERE user_id <> $bot_id` predicate degrades to a no-op
    (Discord user_ids are never 0) rather than failing on a None comparison.
    Use in webui aggregate queries that must exclude the bot."""
    return get_bot_user_id() or 0


def economy_outlier_ids() -> list[int]:
    """Discord user_ids the operator flagged as economy outliers (env
    `economy_outlier_user_ids`). Excluded from the coins-in-circulation total +
    graph so admin-granted/test wallets don't dominate. Empty list is a no-op —
    a `user_id <> ALL('{}')` predicate is true for every row."""
    return sorted(getattr(config, "ECONOMY_OUTLIER_USER_IDS", set()) or set())


def get_main():
    """Return the live main module (re-resolved per call to survive reloads)."""
    import sys

    return sys.modules.get("main")


def get_pool():
    return catpg.pool


def get_hard_restart_time() -> float:
    return getattr(config, "HARD_RESTART_TIME", 0) or 0


def get_soft_restart_time() -> float:
    return getattr(config, "SOFT_RESTART_TIME", 0) or 0


def uptime_seconds() -> float:
    return time.time() - get_hard_restart_time() if get_hard_restart_time() else 0


__all__ = [
    "init",
    "get_bot",
    "get_bot_user_id",
    "bot_user_id_or_zero",
    "economy_outlier_ids",
    "get_main",
    "get_pool",
    "get_hard_restart_time",
    "get_soft_restart_time",
    "uptime_seconds",
]
