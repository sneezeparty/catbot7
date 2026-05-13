"""Data-driven achievement trigger engine.

Each entry in `config/aches.json` MAY carry an optional `trigger` field:

    {
      "title": "...",
      "description": "...",
      "category": "...",
      "is_hidden": false,
      "trigger": {
        "event": "catch",
        "condition": {"type": "catch_time_le", "value": 5}
      }
    }

Aches without a `trigger` field are still awarded the old-fashioned way (a
direct `achemb(...)` call somewhere in main.py). The engine and hardcoded
paths coexist.

USAGE (from main.py):

    from ach_engine import TriggerEngine
    ach_engine = TriggerEngine(ach_list)
    ...
    await ach_engine.evaluate("catch", profile, {
        "time": user.time, "timeslow": user.timeslow,
        "total_catches": profile.total_catches,
        "cat_type": "Fine",
        "rain_active": channel.cat_rains > 0,
        "prism_boosted": True,
    }, message=message, achemb=achemb)
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

# Map of condition type -> async eval function. Each evaluator returns True
# if the condition is satisfied. Evaluators are sync (no I/O) — they read
# only from `profile`, `ctx`, and their params dict.

CondFn = Callable[[Any, dict, dict], bool]
_evaluators: dict[str, CondFn] = {}


def _evaluator(name: str):
    def deco(fn: CondFn):
        _evaluators[name] = fn
        return fn

    return deco


# ---- evaluators ----------------------------------------------------------


def _get_profile_field(profile, name: str, default=0):
    """Read a profile attribute by name, tolerating missing columns."""
    try:
        return profile[name]
    except (KeyError, AttributeError):
        return default


def _compare(value, op: str, target) -> bool:
    if op == ">=":
        return value >= target
    if op == "<=":
        return value <= target
    if op == ">":
        return value > target
    if op == "<":
        return value < target
    if op == "==":
        return value == target
    if op == "!=":
        return value != target
    raise ValueError(f"unknown op: {op}")


@_evaluator("catch_count")
def _catch_count(profile, ctx, p):
    threshold = int(p["value"])
    current = ctx.get("total_catches", _get_profile_field(profile, "total_catches", 0))
    return current >= threshold


@_evaluator("cat_rarity_count")
def _cat_rarity_count(profile, ctx, p):
    rarity = p["rarity"]
    threshold = int(p["value"])
    column = f"cat_{rarity}"
    return _get_profile_field(profile, column, 0) >= threshold


@_evaluator("catch_time_le")
def _catch_time_le(profile, ctx, p):
    # ctx supplies the catch time (user.time field is also fine as fallback)
    t = ctx.get("time")
    if t is None:
        t = _get_profile_field(profile, "time", 99999999999999)
    return t <= float(p["value"])


@_evaluator("catch_time_ge")
def _catch_time_ge(profile, ctx, p):
    t = ctx.get("timeslow")
    if t is None:
        t = _get_profile_field(profile, "timeslow", 0)
    return t >= float(p["value"])


@_evaluator("catch_time_exact")
def _catch_time_exact(profile, ctx, p):
    t = ctx.get("time")
    if t is None:
        t = _get_profile_field(profile, "time", -1)
    values = [float(v) for v in p["values"]]
    return any(abs(t - v) < 1e-9 for v in values)


@_evaluator("text_match")
def _text_match(profile, ctx, p):
    text = ctx.get("text", "")
    if text is None:
        return False
    target = p["text"]
    mode = p.get("mode", "exact")
    if mode == "exact":
        return text == target
    if mode == "substring":
        return target in text
    if mode == "regex":
        try:
            return re.search(target, text) is not None
        except re.error:
            log.warning("invalid regex in ach trigger: %r", target)
            return False
    raise ValueError(f"unknown text_match mode: {mode}")


@_evaluator("command_use")
def _command_use(profile, ctx, p):
    return ctx.get("command") == p["command"]


@_evaluator("command_count")
def _command_count(profile, ctx, p):
    if ctx.get("command") != p["command"]:
        return False
    # The caller may pre-compute the running count and pass it in.
    return int(ctx.get("count", 0)) >= int(p["value"])


@_evaluator("stat_threshold")
def _stat_threshold(profile, ctx, p):
    stat = p["stat"]
    op = p.get("op", ">=")
    target = p["value"]
    current = ctx.get(stat, _get_profile_field(profile, stat, 0))
    return _compare(current, op, target)


@_evaluator("random_chance")
def _random_chance(profile, ctx, p):
    return random.random() < float(p["chance"])


@_evaluator("event_flag")
def _event_flag(profile, ctx, p):
    return bool(ctx.get(p["flag"]))


@_evaluator("compound")
def _compound(profile, ctx, p):
    if "all_of" in p:
        return all(_eval_condition(profile, ctx, c) for c in p["all_of"])
    if "any_of" in p:
        return any(_eval_condition(profile, ctx, c) for c in p["any_of"])
    raise ValueError("compound trigger requires all_of or any_of")


def _eval_condition(profile, ctx, condition: dict) -> bool:
    t = condition.get("type")
    fn = _evaluators.get(t)
    if fn is None:
        log.warning("unknown ach condition type: %r", t)
        return False
    try:
        return fn(profile, ctx, condition)
    except Exception:
        log.exception("error evaluating ach condition %r", condition)
        return False


# ---- engine --------------------------------------------------------------


class TriggerEngine:
    """Indexes aches by event name for O(1) dispatch.

    Call `reindex()` after `ach_list` is reloaded (e.g. after `cat!restart`).
    main.py creates exactly one engine and rebuilds the index on import.
    """

    def __init__(self, ach_list: dict):
        self._ach_list = ach_list
        self._by_event: dict[str, list[tuple[str, dict]]] = {}
        self.reindex()

    def reindex(self) -> None:
        self._by_event.clear()
        for ach_id, entry in self._ach_list.items():
            trig = entry.get("trigger") if isinstance(entry, dict) else None
            if not trig:
                continue
            event = trig.get("event")
            if not event:
                log.warning("ach %s has trigger without event, skipping", ach_id)
                continue
            self._by_event.setdefault(event, []).append((ach_id, trig.get("condition") or {}))

    def event_names(self) -> list[str]:
        return list(self._by_event.keys())

    async def evaluate(
        self,
        event: str,
        profile,
        ctx: dict,
        *,
        message=None,
        achemb: Callable[..., Awaitable[None]] | None = None,
        send_type: str = "send",
        author_string=None,
    ) -> list[str]:
        """Evaluate every trigger registered for `event` against `profile`.

        For each one that matches and that the user doesn't already have,
        call `achemb(message, ach_id, send_type, author_string)` to award.
        `author_string` (a discord.User/Member) overrides the implicit author
        from `message`; used for multi-party events (trade) where the
        profile being checked isn't the message author.

        Returns the list of ach IDs newly unlocked.
        """
        candidates = self._by_event.get(event, ())
        if not candidates:
            return []
        unlocked: list[str] = []
        for ach_id, condition in candidates:
            try:
                if profile.has_ach(ach_id):
                    continue
            except AttributeError:
                continue
            if not _eval_condition(profile, ctx, condition):
                continue
            unlocked.append(ach_id)
            if achemb is not None and message is not None:
                try:
                    await achemb(message, ach_id, send_type, author_string)
                except Exception:
                    log.exception("achemb failed for %s", ach_id)
        return unlocked
