"""Resolve Discord snowflake IDs to human names for the dashboard.

Guild names: come free from the bot's cache for guilds the bot is still in.
For guilds the bot has left (or hasn't reached in the shard rollout yet) the
fall-back chain is `server.name`, populated by the bot's snapshot loop +
on_guild_join. Synchronous Jinja-global resolution consults an in-process
cache that route handlers refresh asynchronously before render.

Channel names: bot-cache only — channels we render are always live ones.

Usernames: not cached by the bot, but the bot writes `user.username` on
every `/` interaction, so the DB is a useful fallback before fetch_user.
Fetch results memoize forever in `_user_cache`.

When resolution fails completely we return a distinctively-formatted short
placeholder (`guild #123456`, `user #654321`) rather than the bare snowflake
— a bare ID looks like a UI bug; the short form makes it clear the row is
still inspectable and intentional.
"""

import asyncio
import time

from webui import state

# user_id -> resolved name (or short-form fallback). Persists across requests;
# survives cat!restart since webui is not reloaded.
_user_cache: dict[int, str] = {}

# guild_id -> resolved name, populated from `server.name`. Refreshed
# asynchronously by route handlers; consulted synchronously by the Jinja global.
_guild_name_cache: dict[int, str] = {}
_guild_cache_last_refresh: float = 0.0
_guild_cache_lock = asyncio.Lock()
_GUILD_CACHE_TTL = 60.0  # seconds; cheap query, low risk of staleness

# cap concurrent fetch_user calls so a cold leaderboard load can't stampede
_fetch_sem = asyncio.Semaphore(8)


def _short_id(snowflake) -> str:
    """Last 6 digits of an id, intentionally distinct from a bare snowflake."""
    s = str(snowflake)
    return s[-6:] if len(s) > 6 else s


def guild_name(gid) -> str:
    """Cached, synchronous. Tries the bot cache first, then the DB-backed
    cache populated by `refresh_guild_name_cache()`. Falls back to
    `guild #<short>` rather than a bare snowflake — bare IDs read as a bug."""
    if not gid:
        return ""
    try:
        gid_int = int(gid)
    except (ValueError, TypeError):
        return str(gid)
    bot = state.get_bot()
    if bot is not None:
        g = bot.get_guild(gid_int)
        if g is not None and g.name:
            return g.name
    cached = _guild_name_cache.get(gid_int)
    if cached:
        return cached
    return f"guild #{_short_id(gid_int)}"


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


async def refresh_guild_name_cache(force: bool = False) -> None:
    """Pull every populated server.name into the in-process cache.

    Throttled by `_GUILD_CACHE_TTL` — call freely from route handlers before
    rendering, only the first call within the window hits the DB. Concurrent
    callers serialize on the lock. Silently no-ops if the pool is unavailable
    or the column is missing (pre-migration).
    """
    global _guild_cache_last_refresh
    now = time.time()
    if not force and (now - _guild_cache_last_refresh) < _GUILD_CACHE_TTL:
        return
    pool = state.get_pool()
    if pool is None:
        return
    async with _guild_cache_lock:
        if not force and (time.time() - _guild_cache_last_refresh) < _GUILD_CACHE_TTL:
            return
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT server_id, name FROM server WHERE name <> ''"
                )
        except Exception:
            # Column missing or transient DB error — leave cache untouched.
            _guild_cache_last_refresh = time.time()
            return
        for r in rows:
            _guild_name_cache[int(r["server_id"])] = r["name"]
        _guild_cache_last_refresh = time.time()


def _name_of(user) -> str:
    return getattr(user, "global_name", None) or getattr(user, "name", None) or ""


async def _resolve_one(bot, uid: int) -> None:
    if uid in _user_cache:
        return
    # 1) bot cache (no API)
    cached = bot.get_user(uid)
    if cached is not None and _name_of(cached):
        _user_cache[uid] = _name_of(cached)
        return
    # 2) API fetch (concurrency-capped)
    async with _fetch_sem:
        if uid in _user_cache:
            return
        try:
            user = await bot.fetch_user(uid)
            name = _name_of(user)
            if name:
                _user_cache[uid] = name
                return
        except Exception:  # noqa: BLE001 — NotFound/Forbidden/HTTP all proceed to DB
            pass
    # 3) DB fallback (user.username, written by main on every /interaction)
    pool = state.get_pool()
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                name = await conn.fetchval(
                    'SELECT username FROM "user" WHERE user_id = $1 AND username <> \'\'',
                    uid,
                )
            if name:
                _user_cache[uid] = name
                return
        except Exception:
            pass
    # 4) Short-form unknown — memoize so we don't re-fetch every page load.
    _user_cache[uid] = f"user #{_short_id(uid)}"


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
    return {u: _user_cache.get(u, f"user #{_short_id(u)}") for u in unique}
