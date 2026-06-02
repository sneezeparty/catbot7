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
    "get_main",
    "get_pool",
    "get_hard_restart_time",
    "get_soft_restart_time",
    "uptime_seconds",
]
