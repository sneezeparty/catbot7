"""Resolve Discord snowflake IDs to human names for the dashboard.

Guild and channel names come free from the bot's cache (the bot is in those
guilds), so those resolvers are synchronous and usable as Jinja globals.

Usernames are NOT cached by the bot (it caches no members and the stored
`user.username` column is empty), so they require an API fetch. We fetch once
per user and memoize the result in-process — including failures, as the bare
ID — so repeat page loads are instant and we never hammer the API.
"""

import asyncio

from webui import state

# user_id -> resolved name (or str(id) on failure). Persists across requests;
# survives cat!restart since webui is not reloaded. Bounded by distinct users.
_user_cache: dict[int, str] = {}

# cap concurrent fetch_user calls so a cold leaderboard load can't stampede
_fetch_sem = asyncio.Semaphore(8)


def guild_name(gid) -> str:
    """Cached, synchronous. Falls back to the raw id."""
    bot = state.get_bot()
    if bot is not None and gid:
        try:
            g = bot.get_guild(int(gid))
            if g is not None and g.name:
                return g.name
        except (ValueError, TypeError):
            pass
    return str(gid)


def channel_name(cid) -> str:
    """Cached, synchronous. Returns '#name'; falls back to the raw id."""
    bot = state.get_bot()
    if bot is not None and cid:
        try:
            c = bot.get_channel(int(cid))
            if c is not None and getattr(c, "name", None):
                return f"#{c.name}"
        except (ValueError, TypeError):
            pass
    return str(cid)


def _name_of(user) -> str:
    return getattr(user, "global_name", None) or getattr(user, "name", None) or ""


async def _resolve_one(bot, uid: int) -> None:
    if uid in _user_cache:
        return
    # cache-only hit first (no API)
    cached = bot.get_user(uid)
    if cached is not None and _name_of(cached):
        _user_cache[uid] = _name_of(cached)
        return
    async with _fetch_sem:
        if uid in _user_cache:  # filled while we waited
            return
        try:
            user = await bot.fetch_user(uid)
            _user_cache[uid] = _name_of(user) or str(uid)
        except Exception:  # noqa: BLE001 — NotFound/Forbidden/HTTP all fall back to the id
            _user_cache[uid] = str(uid)


async def resolve_users(bot, ids) -> dict[int, str]:
    """Map a collection of user_ids to names, fetching + caching the unknowns.

    Returns {} (callers fall back to the id) when there's no bot.
    """
    if bot is None:
        return {}
    unique = {int(i) for i in ids if i}
    missing = [u for u in unique if u not in _user_cache]
    if missing:
        await asyncio.gather(*(_resolve_one(bot, u) for u in missing))
    return {u: _user_cache.get(u, str(u)) for u in unique}
