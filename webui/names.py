"""Resolve Discord snowflake IDs to human names for the dashboard.

Guild names: come free from the bot's cache for guilds the bot is still in.
For guilds the bot has left (or hasn't reached in the shard rollout yet) the
fall-back chain is `server.name`, populated by the bot's snapshot loop +
on_guild_join. Synchronous Jinja-global resolution consults an in-process
cache that route handlers refresh asynchronously before render.

Channel names: bot-cache only — channels we render are always live ones.

Usernames: the expensive one, and the reason this module is shaped the way
it is. The bot caches no members (`MemberCacheFlags.none()`, no guild
chunking), so `bot.get_user()` almost always misses, and `user.username` is
only written by `/bless` — in practice it is empty for nearly every row.
That left `bot.fetch_user()` as the de-facto resolver: one Discord API call
per row, on the request path. A 50-row /db/user page meant 50 calls and a
/leaderboards page up to 120, which is exactly how the dashboard earned its
429s and multi-second loads.

So request handlers never touch the Discord API any more. `resolve_users`
answers from memory, then from one batched DB query, and anything still
unknown renders as a short placeholder and is queued for `_resolver_loop()`
— a single background task that fetches at `_FETCH_INTERVAL` and can never
stall a page. Results memoize in `_user_cache` and are mirrored to
`.name-cache.json`, so a bot restart starts warm instead of re-fetching
everything.

When resolution fails completely we return a distinctively-formatted short
placeholder (`guild #123456`, `user #654321`) rather than the bare snowflake
— a bare ID looks like a UI bug; the short form makes it clear the row is
still inspectable and intentional.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

from webui import state

log = logging.getLogger(__name__)

# user_id -> resolved name (or short-form fallback). Persists across requests;
# survives cat!restart since webui is not reloaded.
_user_cache: dict[int, str] = {}

# On-disk mirror of _user_cache, so a bot restart doesn't re-fetch every name.
_CACHE_FILE = Path(__file__).resolve().parent / ".name-cache.json"
_cache_dirty = False
_cache_loaded = False

# IDs seen in a render that we couldn't resolve offline. Drained by
# _resolver_loop in the background — never awaited by a request handler.
_pending: set[int] = set()
_pending_event = asyncio.Event()
_resolver_task = None

# Spacing between background fetch_user calls. Discord's per-route budget for
# GET /users/{id} is far higher than this; the point is that a cold 120-name
# leaderboard costs the API a slow trickle instead of a burst.
_FETCH_INTERVAL = 0.25

# Placeholders are memoized like real names so we stop re-queueing a deleted
# account forever, but they expire so a transient outage isn't permanent.
_PLACEHOLDER_TTL = 3600.0
_placeholder_at: dict[int, float] = {}

# guild_id -> resolved name, populated from `server.name`. Refreshed
# asynchronously by route handlers; consulted synchronously by the Jinja global.
_guild_name_cache: dict[int, str] = {}
_guild_cache_last_refresh: float = 0.0
_guild_cache_lock = asyncio.Lock()
_GUILD_CACHE_TTL = 60.0  # seconds; cheap query, low risk of staleness

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


def _load_cache() -> None:
    """Seed _user_cache from the on-disk mirror. Best-effort, once per process."""
    global _cache_loaded
    if _cache_loaded:
        return
    _cache_loaded = True
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in data.items():
            if isinstance(v, str) and v:
                _user_cache.setdefault(int(k), v)
    except FileNotFoundError:
        pass
    except Exception:
        log.warning("name cache unreadable, starting cold", exc_info=True)


def _save_cache() -> None:
    """Mirror real (non-placeholder) names to disk. Called from the resolver
    loop only, so it never lands on a request path."""
    global _cache_dirty
    if not _cache_dirty:
        return
    _cache_dirty = False
    keep = {str(k): v for k, v in _user_cache.items() if k not in _placeholder_at}
    tmp = _CACHE_FILE.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(keep, f)
        tmp.replace(_CACHE_FILE)
    except Exception:
        log.warning("could not persist name cache", exc_info=True)


def _is_stale_placeholder(uid: int) -> bool:
    at = _placeholder_at.get(uid)
    return at is not None and (time.time() - at) > _PLACEHOLDER_TTL


async def _lookup_db_usernames(missing: list[int]) -> dict[int, str]:
    """One batched query for `user.username` across every unresolved id.

    Was a per-id round trip behind the old fetch_user path; batching keeps the
    offline resolution step at exactly one query regardless of page size.
    """
    pool = state.get_pool()
    if pool is None or not missing:
        return {}
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                'SELECT user_id, username FROM "user" '
                "WHERE user_id = ANY($1::bigint[]) AND username <> ''",
                missing,
            )
        return {int(r["user_id"]): r["username"] for r in rows}
    except Exception:
        log.debug("username batch lookup failed", exc_info=True)
        return {}


async def _resolver_loop() -> None:
    """Drain `_pending` against the Discord API, slowly, forever.

    The only place in the webui that calls fetch_user. Runs off the request
    path, so a cold cache costs page latency nothing — names simply appear on
    a later load.
    """
    global _cache_dirty
    while True:
        try:
            if not _pending:
                _pending_event.clear()
                _save_cache()
                await _pending_event.wait()
                continue
            uid = _pending.pop()
            bot = state.get_bot()
            if bot is None:
                await asyncio.sleep(5)
                continue
            name = ""
            try:
                user = await bot.fetch_user(uid)
                name = _name_of(user)
            except Exception:  # noqa: BLE001 — NotFound/Forbidden/429 all fall through
                log.debug("fetch_user(%s) failed", uid, exc_info=True)
            if name:
                _user_cache[uid] = name
                _placeholder_at.pop(uid, None)
                _cache_dirty = True
            else:
                _user_cache[uid] = f"user #{_short_id(uid)}"
                _placeholder_at[uid] = time.time()
            await asyncio.sleep(_FETCH_INTERVAL)
        except asyncio.CancelledError:
            _save_cache()
            raise
        except Exception:
            log.exception("name resolver iteration failed")
            await asyncio.sleep(5)


def start_resolver() -> None:
    """Launch the background resolver. Idempotent; called from start_server."""
    global _resolver_task
    _load_cache()
    if _resolver_task is None or _resolver_task.done():
        _resolver_task = asyncio.create_task(_resolver_loop())


async def resolve_users(bot, ids) -> dict[int, str]:
    """Map user_ids to names using only in-process + DB state.

    Never calls the Discord API — unknowns are queued for `_resolver_loop`
    and returned as `user #123456` for this render. Callers get a dict back
    immediately no matter how many ids they pass.

    Returns {} (callers fall back to the id) when there's no bot.
    """
    global _cache_dirty
    if bot is None:
        return {}
    _load_cache()
    unique = {int(i) for i in ids if i}

    # 1) in-process cache, ignoring placeholders that have aged out
    missing = [u for u in unique if u not in _user_cache or _is_stale_placeholder(u)]

    # 2) bot cache (free — no API, but usually a miss given MemberCacheFlags.none())
    still_missing = []
    for uid in missing:
        cached = bot.get_user(uid)
        if cached is not None and _name_of(cached):
            _user_cache[uid] = _name_of(cached)
            _placeholder_at.pop(uid, None)
            _cache_dirty = True
        else:
            still_missing.append(uid)

    # 3) one batched DB read
    if still_missing:
        found = await _lookup_db_usernames(still_missing)
        for uid, name in found.items():
            _user_cache[uid] = name
            _placeholder_at.pop(uid, None)
            _cache_dirty = True
        still_missing = [u for u in still_missing if u not in found]

    # 4) hand the rest to the background resolver and render placeholders now
    if still_missing:
        for uid in still_missing:
            _user_cache.setdefault(uid, f"user #{_short_id(uid)}")
            _placeholder_at.setdefault(uid, time.time())
        _pending.update(still_missing)
        _pending_event.set()

    return {u: _user_cache.get(u, f"user #{_short_id(u)}") for u in unique}
