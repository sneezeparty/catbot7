"""Lazy accessors for bot state.

Never `from main import X` in webui modules — `main` is a discord.py extension
that gets unloaded/reimported on `cat!restart`. Capture pointers here once at
boot and resolve attributes on every request so reloads pick up automatically.
"""

import importlib
import time

import catpg
import config

_bot = None
_dirty: dict[str, int] = {}
_last_reload_seen = 0.0


def init(bot):
    global _bot, _last_reload_seen
    _bot = bot
    _last_reload_seen = getattr(config, "SOFT_RESTART_TIME", 0) or getattr(config, "HARD_RESTART_TIME", 0) or 0


def get_bot():
    return _bot


def get_main():
    """Return the live main module (re-resolved per call to survive reloads)."""
    import sys

    return sys.modules.get("main")


def get_pool():
    return catpg.pool


def get_battle():
    return config.battle


def get_catnip():
    m = get_main()
    return getattr(m, "catnip_list", {}) if m else {}


def get_tuning():
    return getattr(config, "tuning", {})


def get_jobs():
    return getattr(config, "jobs", {})


def get_jobs_help():
    return getattr(config, "jobs_help", {})


def get_hard_restart_time() -> float:
    return getattr(config, "HARD_RESTART_TIME", 0) or 0


def get_soft_restart_time() -> float:
    return getattr(config, "SOFT_RESTART_TIME", 0) or 0


def mark_dirty(name: str) -> None:
    _dirty[name] = _dirty.get(name, 0) + 1


def get_dirty() -> dict[str, int]:
    """Edits since last reload. Cleared when SOFT_RESTART_TIME advances."""
    global _last_reload_seen
    current_soft = get_soft_restart_time()
    if current_soft > _last_reload_seen:
        _dirty.clear()
        _last_reload_seen = current_soft
    return dict(_dirty)


def get_dirty_total() -> int:
    return sum(get_dirty().values())


def reload_seen_at() -> float:
    return _last_reload_seen


def uptime_seconds() -> float:
    return time.time() - get_hard_restart_time() if get_hard_restart_time() else 0


def reload_main():
    """Returns the coroutine bot.cat_bot_reload_hook(False)."""
    bot = get_bot()
    if bot is None:
        raise RuntimeError("webui.state not initialized")
    return bot.cat_bot_reload_hook(False)


def reload_db():
    bot = get_bot()
    if bot is None:
        raise RuntimeError("webui.state not initialized")
    return bot.cat_bot_reload_hook(True)


# Re-export importlib for tests / debug
__all__ = [
    "init",
    "get_bot",
    "get_main",
    "get_pool",
    "get_battle",
    "get_catnip",
    "get_tuning",
    "get_jobs",
    "get_jobs_help",
    "get_hard_restart_time",
    "get_soft_restart_time",
    "mark_dirty",
    "get_dirty",
    "get_dirty_total",
    "uptime_seconds",
    "reload_main",
    "reload_db",
    "importlib",
]
