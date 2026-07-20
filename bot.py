# Cat Bot - A Discord bot about catching cats.
# Copyright (C) 2026 Lia Milenakos & Cat Bot Contributors
# Copyright (C) 2026 sneezeparty
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import asyncio
import collections
import importlib
import logging
import sys
import threading
import time
import traceback

import discord
import sentry_sdk
import winuvloop
from discord.ext import commands

import catpg
import config
import database

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
logger.addHandler(handler)
log_level = logging.INFO

try:
    # this is a messy closed source script which injects into logging module to do statistics
    # inside discord.py, it only intercepts the amount of status codes and ratelimits
    # everything else is from main.py logging.debug() statements
    import stats  # noqa: F401

    log_level = logging.DEBUG
except ImportError:
    pass

# We pass log_handler=None to bot.run() (below) so discord.py doesn't attach
# its own handler to the `discord` logger — without that, every discord.*
# record was emitted twice (once by discord's handler, once by root's after
# propagation). discord.py's setup_logging would have set both the handler's
# level and the discord logger's level to log_level; we do that ourselves
# here. We also install its _ColourFormatter on the handler when the stream
# supports it, so logs stay colored — that formatter is the other thing
# setup_logging would have wired up for us.
handler.setLevel(log_level)
logging.getLogger("discord").setLevel(log_level)
if discord.utils.stream_supports_colour(handler.stream):
    handler.setFormatter(discord.utils._ColourFormatter())
else:
    handler.setFormatter(
        logging.Formatter(
            "[{asctime}] [{levelname:<8}] {name}: {message}",
            "%Y-%m-%d %H:%M:%S",
            style="{",
        )
    )


winuvloop.install()

filtered_errors = [
    # inactionable/junk discord api errors
    "Too Many Requests",
    "You are being rate limited",
    "Invalid Webhook Token",
    "Unknown Interaction",
    "Unknown Webhook",
    "Failed to convert",
    "CommandNotFound",
    "CommandAlreadyRegistered",
    "Cannot send an empty message",
    "Missing Permissions",
    # connection errors and warnings (why are there so many)
    "ClientConnectorError",
    "ClientConnectorDNSError",
    "NameResolutionError",
    "DiscordServerError",
    "WSServerHandshakeError",
    "ConnectionClosed",
    "ConnectionResetError",
    "TimeoutError",
    "ServerDisconnectedError",
    "ClientOSError",
    "TransferEncodingError",
    "Request Timeout",
    "Session is closed",
    "Unclosed connection",
    "unable to perform operation on",
    "Event loop is closed",
    "503 Service Unavailable",
]


def before_send(event, hint):
    if "exc_info" not in hint:
        return event
    for i in filtered_errors:
        if i.lower() in str(hint["exc_info"][0]).lower() + str(hint["exc_info"][1]).lower():
            return None
    return event


if config.SENTRY_DSN:
    sentry_sdk.init(
        dsn=config.SENTRY_DSN,
        before_send=before_send,
        include_local_variables=False,
        send_default_pii=False,
    )


bot = commands.AutoShardedBot(
    command_prefix="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    help_command=None,
    chunk_guilds_at_startup=False,
    allowed_contexts=discord.app_commands.AppCommandContext(guild=True, dm_channel=False, private_channel=False),
    intents=discord.Intents(message_content=True, messages=True, guilds=True),
    member_cache_flags=discord.MemberCacheFlags.none(),
    allowed_mentions=discord.AllowedMentions.none(),
)


# ---------------------------------------------------------------------------
# fetch_user rate-limit forensics
# ---------------------------------------------------------------------------
# The `GET /users/{id}` route occasionally 429s. Both of main.py's
# bot.fetch_user() call sites cache-then-reuse (they only fetch when a field is
# empty), so a 429 means a *burst* — many ids at once, or the same id re-fetched
# in a race. discord.py's warning tells us we were limited but not which of our
# call sites fired it. This wrapper (a) tallies calls per call-site + per user
# id on `config` — inspect live via `cat!eval config.fetch_user_counts` /
# `config.fetch_user_id_counts` — and (b) logs a WARNING with the breakdown the
# moment calls burst past a threshold in a short window, so the culprit lands
# right next to the discord.http 429 line. Lives in bot.py so it survives
# cat!restart (the bot instance persists; only the `main` extension reloads).
# Remove this whole block once the offending call site is fixed.
if not getattr(config, "_fetch_user_wrapped", False):
    config.fetch_user_counts = collections.Counter()          # "file:line in func()" -> total
    config.fetch_user_id_counts = collections.Counter()       # user_id -> total fetches
    config.fetch_user_recent = collections.deque(maxlen=256)  # (monotonic_ts, callsite, user_id, is_webui_names)
    _FETCH_BURST_WINDOW = 10.0   # seconds
    _FETCH_BURST_THRESHOLD = 8   # calls within the window before we shout
    _orig_fetch_user = bot.fetch_user

    async def _tracked_fetch_user(user_id, *args, **kwargs):
        # Caller is captured synchronously (before any await) so the frame is
        # exactly the line that called bot.fetch_user — main.py:825 (DM channel)
        # or main.py:8079 (blessing), or any future site.
        frame = sys._getframe(1)
        filename = frame.f_code.co_filename
        callsite = f"{filename.rsplit('/', 1)[-1]}:{frame.f_lineno} in {frame.f_code.co_name}()"
        now = time.monotonic()
        config.fetch_user_counts[callsite] += 1
        config.fetch_user_id_counts[user_id] += 1
        recent = config.fetch_user_recent
        # The admin webui (webui/names.py) legitimately batch-resolves a
        # leaderboard's usernames via asyncio.gather on every cold-cache page
        # load — a concurrency-capped, cached burst, not the runaway this
        # wrapper hunts (which is main.py's fetch sites). Tag those records by
        # full path (a bare "names.py:" basename match would also exempt any
        # future names.py elsewhere) and drop them from the burst trigger so a
        # dashboard visit doesn't cry wolf; they're still tallied in
        # fetch_user_counts above for the record.
        is_webui_names = filename.endswith("webui/names.py")
        recent.append((now, callsite, user_id, is_webui_names))
        suspect = [r for r in recent if now - r[0] <= _FETCH_BURST_WINDOW and not r[3]]
        if len(suspect) >= _FETCH_BURST_THRESHOLD and now - getattr(config, "_last_fetch_burst_warn", 0.0) >= _FETCH_BURST_WINDOW:
            config._last_fetch_burst_warn = now
            by_site = collections.Counter(r[1] for r in suspect)
            by_id = collections.Counter(r[2] for r in suspect)
            logging.warning(
                "fetch_user burst: %d calls in <%.0fs | by call-site: %s | top ids: %s",
                len(suspect), _FETCH_BURST_WINDOW, dict(by_site.most_common(5)), dict(by_id.most_common(5)),
            )
        return await _orig_fetch_user(user_id, *args, **kwargs)

    bot.fetch_user = _tracked_fetch_user  # pyright: ignore
    config._fetch_user_wrapped = True


# ---------------------------------------------------------------------------
# event-loop stall watchdog
# ---------------------------------------------------------------------------
# "Can't keep up, shard ID 0 websocket is Ns behind" is discord.py's keep-alive
# thread reporting that the event loop failed to run a trivial send_heartbeat
# coroutine for N seconds — i.e. the loop was frozen that long. By the time the
# warning lands the culprit is already gone, so we can't see what did it. This
# catches it red-handed: an on-loop heartbeat task stamps a monotonic clock
# every _LOOP_HB_INTERVAL; a real OS thread (which keeps running even while the
# loop is frozen) watches that stamp and, the moment it goes stale past
# _LOOP_STALL_THRESHOLD, dumps the loop thread's Python stack — exactly what was
# executing when the loop stopped ticking.
#
# Reading the dump:
#   * a real blocking call (requests.get, json on a huge blob, a big sync loop,
#     a blocking DB/DNS call) => our code froze the loop; fix that call site.
#   * plain asyncio/selector idle frames => our code WASN'T blocking; the OS
#     parked the process (App Nap / QoS demotion to efficiency cores / timer
#     coalescing) or the gateway socket stalled — i.e. host/network, not us.
# One report per stall; re-arms after recovery. Lives in bot.py so it survives
# cat!restart (only the `main` extension reloads; this thread/task do not).
# Temporary diagnostic — remove once the stalls are explained.
_LOOP_STALL_THRESHOLD = 3.0   # seconds the loop may be frozen before we shout
_LOOP_HB_INTERVAL = 0.5       # how often the on-loop heartbeat stamps the clock


async def _loop_heartbeat():
    # Runs ON the event loop. If the loop is blocked, this stops stamping —
    # which is precisely the staleness the watchdog thread looks for.
    config._loop_thread_ident = threading.get_ident()
    while True:
        config._loop_heartbeat_ts = time.monotonic()
        await asyncio.sleep(_LOOP_HB_INTERVAL)


def _loop_watchdog():
    # A plain daemon OS thread — unaffected by the event loop being frozen.
    stall_start = None
    while True:
        time.sleep(0.5)
        last = getattr(config, "_loop_heartbeat_ts", None)
        if last is None:
            continue
        now = time.monotonic()
        lag = now - last
        if lag > _LOOP_STALL_THRESHOLD:
            if stall_start is None:
                # anchor to the last good heartbeat (when the loop actually
                # froze), not `now` (detection time, already >threshold later),
                # so the recovery line reports the full freeze duration.
                stall_start = last
                ident = getattr(config, "_loop_thread_ident", None)
                frame = sys._current_frames().get(ident) if ident else None
                stack = "".join(traceback.format_stack(frame)) if frame else "<loop-thread frame unavailable>"
                logging.warning(
                    "event loop STALLED >%.1fs (now ~%.1fs behind) — loop-thread stack at detection:\n%s",
                    _LOOP_STALL_THRESHOLD, lag, stack.rstrip(),
                )
        elif stall_start is not None:
            logging.warning("event loop recovered (was stalled ~%.1fs)", now - stall_start)
            stall_start = None


@bot.event
async def setup_hook():
    await database.connect()
    await bot.load_extension("main")
    from webui import start_server

    asyncio.create_task(start_server(bot))

    # event-loop stall watchdog (see block above) — start once per process.
    if not getattr(config, "_loop_watchdog_installed", False):
        config._loop_heartbeat_ts = time.monotonic()
        asyncio.create_task(_loop_heartbeat())
        threading.Thread(target=_loop_watchdog, name="loop-watchdog", daemon=True).start()
        config._loop_watchdog_installed = True


async def reload(reload_db):
    try:
        await bot.unload_extension("main")
    except commands.ExtensionNotLoaded:
        pass
    if reload_db:
        await database.close()
        importlib.reload(database)
        importlib.reload(catpg)
        await database.connect()
    await bot.load_extension("main")


config.cat_cought_rain = {}
config.rain_starter = {}

bot.cat_bot_reload_hook = reload  # pyright: ignore

try:
    config.HARD_RESTART_TIME = time.time()
    # log_handler=None — see the comment block above where log_level is set.
    bot.run(config.TOKEN, log_handler=None)
finally:
    asyncio.run(database.close())
