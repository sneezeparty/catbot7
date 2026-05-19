# Cat Bot - A Discord bot about catching cats.
# Copyright (C) 2026 Lia Milenakos & Cat Bot Contributors
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
import base64
import datetime
import hashlib
import hmac
import io
import json
import logging
import math
import os
import platform
import random
import re
import subprocess
import sys
import time
import traceback
from typing import Literal, Optional

import aiohttp
import discord
import discord_emoji
import emoji
import psutil
import unidecode  # type: ignore
from aiohttp import web
from discord import ButtonStyle
from discord.ext import commands
from discord.ui import ActionRow, Button, LayoutView, Modal, Separator, TextDisplay, TextInput, Thumbnail, View
from PIL import Image

import config
import graph
import msg2img
from catpg import RawSQL, pool, transaction
from database import Channel, JobInstance, Order, PortfolioHistory, PriceHistory, Prism, Profile, Reminder, Reward, Server, User, _coerce_array

try:
    import exportbackup  # type: ignore
except ImportError:
    exportbackup = None

# trigger warning, base64 encoded for your convinience
NONOWORDS = [base64.b64decode(i).decode("utf-8") for i in ["bmlja2E=", "bmlja2Vy", "bmlnYQ==", "bmlnZ2E=", "bmlnZ2Vy"]]

type_dict = {
    "Fine": 1000,
    "Nice": 750,
    "Good": 500,
    "Rare": 350,
    "Wild": 275,
    "Baby": 230,
    "Epic": 200,
    "Sus": 175,
    "Brave": 150,
    "Rickroll": 125,
    "Reverse": 100,
    "Superior": 80,
    "Trash": 50,
    "Legendary": 35,
    "Mythic": 25,
    "8bit": 20,
    "Corrupt": 15,
    "Professor": 10,
    "Divine": 8,
    "Real": 5,
    "Ultimate": 3,
    "eGirl": 2,
}

# this list stores unique non-duplicate cattypes
cattypes = list(type_dict.keys())

# generate a dict with lowercase'd keys
cattype_lc_dict = {i.lower(): i for i in cattypes}

# Cat tiers from "Legendary" downward (rarer). Used by the `legendary+`
# challenge quest trigger. The list in type_dict is ordered most-common to
# rarest, so slicing from Legendary onward gives the full rarer-than-Epic set.
LEGENDARY_PLUS = frozenset(cattypes[cattypes.index("Legendary"):])

# XP awarded per cat for the "sacrifice" battlepass quest. Hidden from users —
# they only see "depends on the cat". Range is 25 (most common) to 300 (rarest).
SACRIFICE_XP = {
    "Fine": 25,
    "Nice": 30,
    "Good": 40,
    "Rare": 55,
    "Wild": 65,
    "Baby": 75,
    "Epic": 85,
    "Sus": 95,
    "Brave": 105,
    "Rickroll": 115,
    "Reverse": 125,
    "Superior": 140,
    "Trash": 155,
    "Legendary": 170,
    "Mythic": 185,
    "8bit": 200,
    "Corrupt": 215,
    "Professor": 230,
    "Divine": 250,
    "Real": 270,
    "Ultimate": 285,
    "eGirl": 300,
}

allowedemojis = [i.lower() + "cat" for i in cattypes]

pack_data = [
    # event/special
    {"name": "Christmas", "value": 45, "upgrade": 70, "totalvalue": 338, "special": True},
    {"name": "Valentine", "value": 45, "upgrade": 70, "totalvalue": 338, "special": True},
    {"name": "Chef", "value": 45, "upgrade": 70, "totalvalue": 338, "special": True},
    {"name": "Birthday", "value": 45, "upgrade": 70, "totalvalue": 338, "special": True},
    # normal
    {"name": "Wooden", "value": 98, "upgrade": 30, "totalvalue": 113, "special": False},
    {"name": "Stone", "value": 135, "upgrade": 30, "totalvalue": 150, "special": False},
    {"name": "Bronze", "value": 150, "upgrade": 30, "totalvalue": 195, "special": False},
    {"name": "Silver", "value": 173, "upgrade": 30, "totalvalue": 300, "special": False},
    {"name": "Gold", "value": 345, "upgrade": 30, "totalvalue": 600, "special": False},
    {"name": "Platinum", "value": 945, "upgrade": 30, "totalvalue": 1200, "special": False},
    {"name": "Diamond", "value": 1290, "upgrade": 30, "totalvalue": 1800, "special": False},
    {"name": "Celestial", "value": 3000, "upgrade": 0, "totalvalue": 3000, "special": False},  # is that a madeline celeste reference????
]

stock_data = [
    {"name": "Prisms", "ticker": "PRSM", "emoji": "prism", "amount": 10_000, "init_price": 40},
    {"name": "Catnip", "ticker": "CTNP", "emoji": "catnip", "amount": 10_000, "init_price": 40},
    {"name": "Cattlepass", "ticker": "PASS", "emoji": "⬆️", "amount": 10_000, "init_price": 40},
    {"name": "Achievements", "ticker": "ACHS", "emoji": "ach", "amount": 10_000, "init_price": 40},
    {"name": "Rain", "ticker": "RAIN", "emoji": "☔", "amount": 10_000, "init_price": 40},
]

prism_names_start = [
    "Alpha",
    "Bravo",
    "Charlie",
    "Delta",
    "Echo",
    "Foxtrot",
    "Golf",
    "Hotel",
    "India",
    "Juliett",
    "Kilo",
    "Lima",
    "Mike",
    "November",
    "Oscar",
    "Papa",
    "Quebec",
    "Romeo",
    "Sierra",
    "Tango",
    "Uniform",
    "Victor",
    "Whiskey",
    "X-ray",
    "Yankee",
    "Zulu",
]
prism_names_end = [
    "",
    " Two",
    " Three",
    " Four",
    " Five",
    " Six",
    " Seven",
    " Eight",
    " Nine",
    " Ten",
    " Eleven",
    " Twelve",
    " Thirteen",
    " Fourteen",
    " Fifteen",
    " Sixteen",
    " Seventeen",
    " Eighteen",
    " Nineteen",
    " Twenty",
]
prism_names = [j + i for i in prism_names_end for j in prism_names_start]

vote_button_texts = [
    "You havent voted today!",
    "I know you havent voted ;)",
    "If vote cat will you friend :)",
    "Vote cat for president",
    "vote = 0.01% to escape basement",
    "vote vote vote vote vote",
    "mrrp mrrow go and vote now",
    "if you vote you'll be free (no)",
    "vote. btw, i have a pipebomb",
    "No votes? :megamind:",
    "Cat says you should vote",
    "cat will be happy if you vote",
    "VOTE NOW!!!!!",
    "I voted and got 1000000$",
    "I voted and found a gf",
    "lebron james forgot to vote",
    "vote if you like cats",
    "vote if cats > dogs",
    "you should vote for cat NOW!",
    "I'd vote if I were you",
]

# various hints/fun facts
hints = [
    "Cat Bot has a wiki! <https://catbot.wiki>",
    "Cat Bot is open source! <https://github.com/milenakos/cat-bot>",
    "View all cats and rarities with /catalogue",
    "Cat Bot's birthday is on the 21st of April",
    "Unlike the normal one, Cat's /8ball isn't rigged",
    "/rate says /rate is 100% correct",
    "/casino is *surely* not rigged",
    "You probably shouldn't use a Discord bot for /remind-ers",
    "Cat /Rain is an excellent way to support development!",
    "Cat Bot was made later than its support server",
    "Cat Bot reached 100 servers 3 days after release",
    "Cat died for 2+ weeks bc the servers were flooded with water",
    # RE-ENABLE WHEN VOTING IS PUBLIC: "Cat Bot's top.gg page was deleted at one point",
    "Cat Bot has an official soundtrack! <https://youtu.be/Ww1opmRwYF0>",
    "4 with 832 zeros cats were deleted on September 5th, 2024",
    # RE-ENABLE WHEN VOTING IS PUBLIC: "Cat Bot has reached top #19 on top.gg in January 2025",
    # RE-ENABLE WHEN VOTING IS PUBLIC: "Cat Bot has reached top #17 on top.gg in February 2025",
    # RE-ENABLE WHEN VOTING IS PUBLIC: "Cat Bot has reached top #12 on top.gg in March 2025",
    # RE-ENABLE WHEN VOTING IS PUBLIC: "Cat Bot has reached top #9 on top.gg in April 2025",
    # RE-ENABLE WHEN VOTING IS PUBLIC: "Cat Bot has reached top #7 on top.gg in May 2025",
    # RE-ENABLE WHEN VOTING IS PUBLIC: "Cat Bot has reached top #5 on top.gg in September 2025",
    # RE-ENABLE WHEN VOTING IS PUBLIC: "Cat Bot has reached top #3 on top.gg in March 2026",
    "Most Cat Bot features were made within 2 weeks",
    "Cat Bot was initially made for only one server",
    "Cat Bot is made in Python with discord.py",
    "Discord didn't verify Cat properly the first time",
    "Looking at Cat's code won't make you regret your life choices!",
    "Cats aren't shared between servers to make it more fair and fun",
    "Cat Bot can go offline! Don't panic if it does",
    "By default, cats spawn 1-10 minutes apart",
    "View the last catch as well as the next one with /last",
    # RE-ENABLE WHEN VOTING IS PUBLIC: "Make sure to leave Cat Bot [a review on top.gg](<https://top.gg/bot/966695034340663367#reviews>)!",
]

# laod the jsons
with open("config/aches.json", "r") as f:
    ach_list = json.load(f)

with open("config/battlepass.json", "r", encoding="utf-8") as f:
    config.battle = json.load(f)

with open("config/catnip.json", "r", encoding="utf-8") as f:
    catnip_list = json.load(f)

with open("config/tuning.json", "r", encoding="utf-8") as f:
    config.tuning = json.load(f)

with open("config/jobs.json", "r", encoding="utf-8") as f:
    config.jobs = json.load(f)

with open("config/jobs_help.json", "r", encoding="utf-8") as f:
    config.jobs_help = json.load(f)

# Named aliases for tunables. Refreshed on every module reload via the
# json.load above. Edits to config/tuning.json apply on next cat!restart.
QUEST_COOLDOWN = config.tuning["quest_cooldown_seconds"]
RAINBOOST_LONG = config.tuning["rainboost_long_seconds"]
RAINBOOST_SHORT = config.tuning["rainboost_short_seconds"]
PRISM_BOOST_GLOBAL_COEF = config.tuning["prism_boost_global_coef"]
PRISM_BOOST_USER_COEF = config.tuning["prism_boost_user_coef"]
PRISM_BOOST_FLOOR = config.tuning["prism_boost_floor"]
CATNIP_TIMER_EXTEND = config.tuning["catnip_timer_extend_seconds"]
COIN_PER_PACK = config.tuning["coin_per_pack"]
BAKERY_COST_COOKIES = config.tuning["bakery_cost_cookies"]
BAKERY_COST_COFFEES = config.tuning["bakery_cost_coffees"]
BAKERY_COST_NICE = config.tuning["bakery_cost_nice_cats"]
MAIN_LOOP_INTERVAL = config.tuning["main_loop_interval_seconds"]
SPAWN_REVIVAL_INTERVAL = config.tuning.get("spawn_revival_interval_seconds", 60)
ANTI_DOUBLE_CATCH_COOLDOWN = config.tuning["anti_double_catch_cooldown_seconds"]
FAST_CATCHER_THRESHOLD = config.tuning["fast_catcher_threshold_seconds"]
SLOW_CATCHER_THRESHOLD = config.tuning["slow_catcher_threshold_seconds"]
PACK_DROP_CHANCE_ON_CATCH = config.tuning["pack_drop_chance_on_catch"]
PACK_TIER_WEIGHTS = config.tuning["pack_tier_weights"]
STOCK_MARKET = config.tuning.get("stock_market", {"enabled": False})

# Jobs / Mafia Killings. Loaded from config/jobs.json above; re-read here on every
# module reload so cat!restart picks up edits to send power, tiers, NPCs, etc.
JOBS_SEND_POWER = config.jobs["send_power"]
JOBS_PROB = config.jobs["probability"]
JOBS_TUNING = config.jobs["tuning"]
JOBS_TIERS = config.jobs["tiers"]
JOBS_NPCS = config.jobs["npcs"]
JOBS_BIG_SCORE = config.jobs["big_score"]
JOBS_REP = config.jobs["rep"]

# Data-driven achievement trigger engine. Reads `trigger` blocks from
# ach_list and fires them on matching events. Hardcoded `achemb()` call
# sites still work; engine + hardcoded paths coexist.
from ach_engine import TriggerEngine  # noqa: E402

ach_engine = TriggerEngine(ach_list)

with open("facts.txt") as f:
    cat_facts_list = f.read().split("\n")

with open("fanhalo.txt") as f:
    fanhalo_list = f.read().split("\n")

# convert achievement json to a few other things
ach_names = ach_list.keys()
ach_titles = {value["title"].lower(): key for (key, value) in ach_list.items()}

bot = commands.AutoShardedBot(
    command_prefix="this is a placebo bot which will be replaced when this will get loaded",
    intents=discord.Intents.default(),
)

funny = [
    "why did you click this this arent yours",
    "absolutely not",
    "cat bot not responding, try again later",
    "you cant",
    "can you please stop",
    "try again",
    "403 not allowed",
    "stop",
    "get a life",
    "not for you",
    "no",
    "nuh uh",
    "access denied",
    "forbidden",
    "don't do this",
    "cease",
    "wrong",
    "aw dangit",
    "why don't you press buttons from your commands",
    "you're only making me angrier",
    "why are you like this",
    "legends say you get something for clicking it 1000 times",
]


class Colors:
    brown = 0x6E593C
    gray = 0xCCCCCC
    green = 0x007F0E
    yellow = 0xFFFF00
    maroon = 0x750F0E
    demonic = 0xC12929
    rose = 0xFF81C6
    red = 0xFF0000


# rain shill message for footers
rain_shill = ""

# timeout for views
# higher one means buttons work for longer but uses more ram to keep track of them
VIEW_TIMEOUT = config.tuning["view_timeout_seconds"]

# store credits usernames to prevent excessive api calls
gen_credits = {}

# due to some stupid individuals spamming the hell out of reactions, we ratelimit them
# you can do 50 reactions before they stop, limit resets on global cat loop
reactions_ratelimit = {}

# sort of the same thing but for pointlaughs and per channel instead of peruser
pointlaugh_ratelimit = {}

# cooldowns for some commands
catchcooldown = {}
fakecooldown = {}
customcatcooldown = {}

# cat bot auto-claims in the channel user last ran /vote in
# this is a failsafe to store the fact they voted until they ran that atleast once
pending_votes = []

# prevent ratelimits
casino_lock = []
slots_lock = []

# ???
rigged_users = []


# WELCOME TO THE TEMP_.._STORAGE HELL

# to prevent double catches
temp_catches_storage = []

# to prevent double spawns
temp_spawns_storage = []

# to prevent double belated battlepass progress and for "faster than 10 seconds" belated bp quest
temp_belated_storage = {}

# to avoid expensive db queries
temp_stock_prices = {}

# docs suggest on_ready can be called multiple times
on_ready_debounce = False

# fallback for fetching missing votes on background loops using top.gg replay api thing
try:
    with open("cursor.txt", "r", encoding="utf-8") as f:
        last_vote_cursor = f.read().strip() or None
except FileNotFoundError:
    last_vote_cursor = None

# d.py doesnt cache app emojis so we do it on our own yippe
emojis = {}

# for mentioning it in catch message, will be auto-fetched in on_ready()
RAIN_ID = 1270470307102195752
PLUSH_ID = 0

# for dev commands, this is fetched in on_ready
OWNER_ID = 553093932012011520

# for funny stats, you can probably edit background_loop to restart every X of them
loop_count = 0

# loops in dpy can randomly break, i check if is been over X minutes since last loop to restart it
last_loop_time = 0


def get_emoji(name):
    global emojis
    if name in emojis.keys():
        return emojis[name]
    elif name in emoji.EMOJI_DATA:
        return name
    elif name.endswith("_claimed"):
        # Battlepass progress dots want a "✅ over the icon" appearance.
        # If the _claimed variant wasn't uploaded to the bot's app emojis,
        # fall back to a checkmark so users can still tell what's earned.
        return "✅"
    else:
        return "🔳"


async def fetch_dm_channel(user: User) -> discord.PartialMessageable:
    if user.dm_channel_id:
        return bot.get_partial_messageable(user.dm_channel_id)
    else:
        person = await bot.fetch_user(user.user_id)
        if not person.dm_channel:
            await person.create_dm()
        user.dm_channel_id = person.dm_channel.id
        await user.save()
        return person.dm_channel


# Increments on the first catch of each new UTC day; resets to 1 if the user
# skipped a day entirely. Returns True iff this was the first catch of the UTC
# day, so the caller can award the first-catch-of-day passive XP.
async def update_daily_catch_streak(user: User) -> bool:
    today = int(time.time() // 86400)
    if user.last_catch_day == today:
        return False
    if user.last_catch_day == today - 1:
        user.daily_catch_streak += 1
    else:
        user.daily_catch_streak = 1
    if user.daily_catch_streak > user.max_daily_streak:
        user.max_daily_streak = user.daily_catch_streak
    user.last_catch_day = today
    await user.save()
    return True


async def get_stock_price(ticker: str) -> int:
    try:
        stock_price = temp_stock_prices[ticker]
    except KeyError:
        try:
            stock_price = (await PriceHistory.collect("ticker = $1 ORDER BY time DESC LIMIT 1", ticker))[0].price
        except IndexError:
            stock_price = 40
        temp_stock_prices[ticker] = stock_price
    return stock_price


async def _fair_price_metric(ticker: str) -> float:
    """Per-ticker in-game activity signal. Each branch returns a non-negative
    float; zero is fine (the smoothing +eps in _compute_fair_price guards
    against division blow-up). Hardcoded dispatch on purpose — the SQL for
    each ticker is different and there's no benefit to making it configurable."""
    if ticker == "PRSM":
        return float(await Prism.count() or 0)
    if ticker == "CTNP":
        return float(await Profile.count("catnip_active > $1", time.time()) or 0)
    if ticker == "PASS":
        return float(await pool.fetchval('SELECT AVG(battlepass) FROM profile WHERE battlepass > 0') or 0)
    if ticker == "ACHS":
        return float(await pool.fetchval('SELECT AVG(jsonb_array_length(unlocked_aches)) FROM profile WHERE jsonb_array_length(unlocked_aches) > 0') or 0)
    if ticker == "RAIN":
        return float(await User.sum("rain_minutes_bought") or 0)
    return 0.0


async def _compute_fair_price(ticker: str) -> int:
    """Power-law smoothed price from an in-game activity metric. Clamped to
    [floor, ceiling] from config. Always returns a positive int >= 1."""
    cfg = STOCK_MARKET.get("tickers", {}).get(ticker)
    if not cfg:
        return 40
    base = cfg.get("base", 40)
    baseline = cfg.get("baseline", 1)
    alpha = cfg.get("alpha", 0.5)
    eps = STOCK_MARKET.get("metric_eps", 1.0)
    floor = STOCK_MARKET.get("price_floor", 1)
    ceiling = STOCK_MARKET.get("price_ceiling", 1000)
    metric = await _fair_price_metric(ticker)
    fair = base * ((metric + eps) / (baseline + eps)) ** alpha
    fair = max(floor, min(ceiling, round(fair)))
    return max(1, int(fair))


# ---------------------------------------------------------------------------
# Cat Store helpers
# ---------------------------------------------------------------------------
# A cat's "value" is the same scaling that /trade and /gift use: total weight
# of all rarities divided by this rarity's weight. Rarer cats = bigger number.
# Sum is cached at module load — type_dict doesn't change at runtime.
_TYPE_DICT_VALUE_SUM = sum(type_dict.values())


def cat_value(cat_type: str) -> int:
    """Value of one cat of the given rarity, in coins. Matches the formula used
    by /trade and /gift so the store doesn't introduce a new pricing scale."""
    weight = type_dict.get(cat_type)
    if not weight:
        return 0
    return _TYPE_DICT_VALUE_SUM // weight


def store_discount_pct(catnip_level: int) -> int:
    """Cat Mafia store discount for the given catnip level. Negative numbers
    are a tax (Newbie/Lurker get charged extra), positive numbers are a real
    discount (Boss+ saves on every purchase). Defaults to 0 if a level entry
    is missing the key (e.g. someone retiring a level without updating the
    store_discount config — better to charge face value than crash)."""
    try:
        level_data = catnip_list["levels"][catnip_level]
    except (IndexError, KeyError):
        return 0
    return int(level_data.get("store_discount", 0))


def store_buy_price(cat_type: str, catnip_level: int) -> int:
    """Coins to buy one cat. Discount applies multiplicatively then ceils so a
    1-coin floor: ceil(value * (1 - discount/100)). Sell price is intentionally
    NOT routed through here — sell is always at face value (see store_sell_price)."""
    value = cat_value(cat_type)
    discount = store_discount_pct(catnip_level)
    price = math.ceil(value * (1 - discount / 100))
    return max(1, int(price))


def store_sell_pct(catnip_level: int) -> int:
    """What fraction of face value the mafia pays out on a sell, as a percent.
    The "natural" curve is 50% at Newbie + 5% per level (so El Patrón would
    sell at 100% face) — but the natural curve crosses the buy curve at Lv7
    and beyond, which would create a buy<face<sell arbitrage loop. We cap
    sell at `buy_pct - 5` so the round-trip stays at least 5 percentage
    points negative at every level. Practical effect: sell tops out around
    65% face at El Patrón rather than the named 100%."""
    natural = 50 + max(0, catnip_level) * 5
    buy_pct = 100 - store_discount_pct(catnip_level)
    return min(natural, buy_pct - 5)


def store_sell_price(cat_type: str, catnip_level: int) -> int:
    """Coins received per cat sold. Scales with mafia level: a Newbie only
    gets 50% of face value back, El Patrón gets the full 100%. The asymmetry
    with the buy discount is intentional — sell ceiling is 100% face while
    buy floor is 70% face at max mafia, so round-trips always net negative."""
    value = cat_value(cat_type)
    pct = store_sell_pct(catnip_level)
    return max(1, value * pct // 100)


# ---------------------------------------------------------------------------
# Jobs / Mafia Killings — Phase 1 helpers (offer-board generation, read-only).
# Commit/resolve paths land in Phase 2; rep + heat math wires up in Phases 3-4.
# ---------------------------------------------------------------------------

JOBS_OFFER_REFRESH = JOBS_TUNING["offer_refresh_window_seconds"]
JOBS_MAX_SLOTS = JOBS_TUNING["max_concurrent_offers"]


def _jobs_window_index(now: int) -> int:
    """Hard global window. All players share the same boundary every 6h —
    deterministic-seed acceptance follows from this."""
    return now // JOBS_OFFER_REFRESH


def _jobs_window_bounds(window_idx: int) -> tuple[int, int]:
    return window_idx * JOBS_OFFER_REFRESH, (window_idx + 1) * JOBS_OFFER_REFRESH


def _jobs_seed_rng(user_id: int, guild_id: int, window_idx: int, salt: str = "") -> random.Random:
    """Per-(user,guild,window) RNG. `salt` derives independent streams per
    slot so reshuffling slot 1 doesn't ripple into slot 2."""
    seed = f"jobs:{user_id}:{guild_id}:{window_idx}:{salt}"
    digest = hashlib.sha256(seed.encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _jobs_npc_display(key: str) -> str:
    npc = JOBS_NPCS.get(key)
    if npc:
        return npc.get("display_name", key.replace("_", " ").title())
    targets = config.jobs.get("targets_only", {})
    t = targets.get(key)
    if t:
        return t.get("display_name", key.replace("_", " ").title())
    return key.replace("_", " ").title()


def _jobs_faction_rep(profile: Profile) -> dict:
    """`faction_rep` is JSONB OBJECT. Return a plain dict, never None."""
    raw = getattr(profile, "faction_rep", None)
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw) or {}
        except (ValueError, TypeError):
            return {}
    return dict(raw)


def _jobs_eligible_npcs(catnip_level: int, faction_rep: dict) -> list[str]:
    """NPCs willing to hire this player. Honors min_hire_level + refusal
    threshold from rep."""
    refuse = JOBS_REP["refuse_threshold"]
    out = []
    for key, npc in JOBS_NPCS.items():
        if catnip_level < npc.get("min_hire_level", 1):
            continue
        if faction_rep.get(key, 0) < refuse:
            continue
        out.append(key)
    return out


def _jobs_eligible_tiers(npc_key: str, catnip_level: int) -> list[int]:
    npc = JOBS_NPCS[npc_key]
    out = []
    for t in npc.get("tiers_offered", []):
        gate = JOBS_TIERS.get(str(t), {}).get("min_catnip_level", 0)
        if catnip_level >= gate:
            out.append(int(t))
    return out


def _jobs_pick_tier(npc_key: str, catnip_level: int, rng: random.Random) -> int | None:
    tiers = _jobs_eligible_tiers(npc_key, catnip_level)
    if not tiers:
        return None
    top = max(tiers)
    # Weight 2 for the highest unlocked tier ("favors player rank"), 1 for the
    # rest. Keeps the "rank gates are availability, not lockouts" rule.
    weights = [2 if t == top else 1 for t in tiers]
    return rng.choices(tiers, weights=weights, k=1)[0]


def _jobs_pick_target(npc_key: str, catnip_level: int, faction_rep: dict, rng: random.Random) -> str | None:
    """Returns the target faction key, or None to signal "reroll this slot"."""
    npc = JOBS_NPCS[npc_key]
    hires = npc.get("hires_against", [])
    if not hires:
        return None

    if "dynamic_higher_rank" in hires:
        # Lucian Sr's vendetta mechanic.
        ally_threshold = npc.get("ally_protection_threshold", 50)
        candidates = []
        targets_only = config.jobs.get("targets_only", {})
        for key, other in JOBS_NPCS.items():
            if key == npc_key:
                continue
            if other.get("min_hire_level", 0) <= catnip_level:
                continue
            if faction_rep.get(key, 0) >= ally_threshold:
                continue
            candidates.append(key)
        for key, t in targets_only.items():
            min_lvl = t.get("min_catnip_level", 0)
            if min_lvl <= catnip_level:
                continue
            if faction_rep.get(key, 0) >= ally_threshold:
                continue
            candidates.append(key)
        if not candidates:
            return None
        return rng.choice(candidates)

    return rng.choice(hires)


def _jobs_pick_narrative(npc_key: str, target_key: str | None, rng: random.Random, *, pool_key: str | None = None) -> str:
    pools = config.jobs.get("narrative_pools", {})
    key = pool_key or npc_key
    pool = pools.get(key) or [f"{_jobs_npc_display(npc_key)} has a job for you."]
    line = rng.choice(pool)
    if "{target}" in line:
        target_display = _jobs_npc_display(target_key) if target_key else "them"
        line = line.replace("{target}", target_display)
    return line


def _jobs_resolve_difficulty(tier: int, target_key: str | None, faction_rep: dict, rng: random.Random) -> int:
    lo, hi = JOBS_TIERS[str(tier)]["difficulty_range"]
    base = rng.randint(lo, hi)
    if target_key is not None:
        neg = -min(0, faction_rep.get(target_key, 0))
        bump = min(JOBS_REP["target_difficulty_cap"], neg * JOBS_REP["target_difficulty_per_negative_point"])
        return math.ceil(base * (1 + bump))
    return base


def _jobs_roll_recipe(npc_key: str, tier: int, rng: random.Random) -> dict:
    """Resolve a (NPC, tier) reward by picking a weighted entry from the
    NPC's reward_recipes table. Returns {coins, cats, pack}. reward_mult is
    NOT applied here — _jobs_resolve_reward composes that on top so the
    operator sees recipe values in their pre-mult form in the config."""
    npc = JOBS_NPCS.get(npc_key, {})
    recipes = (npc.get("reward_recipes") or {}).get(str(tier))
    if not recipes:
        logging.warning("jobs: no reward recipe for %s tier %s — falling back to coin-only",
                        npc_key, tier)
        lo, hi = JOBS_TIERS[str(tier)]["reward_coin_range"]
        return {"coins": rng.randint(lo, hi), "cats": {}, "pack": None}

    weights = [max(0, int(e.get("weight", 0))) for e in recipes]
    if sum(weights) <= 0:
        return {"coins": 0, "cats": {}, "pack": None}

    entry = rng.choices(recipes, weights=weights, k=1)[0]
    coins_range = entry.get("coins")
    if coins_range and len(coins_range) >= 2:
        coins = rng.randint(int(coins_range[0]), int(coins_range[1]))
    else:
        coins = 0
    cats = {t: int(c) for t, c in (entry.get("cats") or {}).items() if int(c) > 0}
    pack = entry.get("pack") or None
    return {"coins": coins, "cats": cats, "pack": pack}


def _jobs_resolve_reward(tier: int, npc_key: str, rng: random.Random) -> dict:
    """Picks a recipe entry, then applies the NPC's reward_mult to coins +
    cat counts. Pack tier is preserved as-is (mult doesn't apply to packs)."""
    npc = JOBS_NPCS.get(npc_key, {})
    recipe = _jobs_roll_recipe(npc_key, tier, rng)
    mult = float(npc.get("reward_mult", 1.0))
    coins = math.ceil(int(recipe.get("coins", 0)) * mult)
    cats = {t: max(1, math.ceil(int(c) * mult)) for t, c in (recipe.get("cats") or {}).items()}
    return {"coins": coins, "cats": cats, "pack": recipe.get("pack")}


def _jobs_heat_scrutiny_mult(heat: int) -> float:
    if heat >= 71:
        return 1.25
    if heat >= 31:
        return 1.10
    return 1.0


def _jobs_resolve_heat_cost(tier: int, npc_key: str, current_heat: int) -> int:
    base = JOBS_TIERS[str(tier)]["heat"]
    npc = JOBS_NPCS[npc_key]
    heat = base * npc.get("heat_mult", 1.0) * _jobs_heat_scrutiny_mult(current_heat)
    return max(0, math.ceil(heat))


def _jobs_category_for(npc_key: str, tier: int) -> str:
    """Hits = coin-paying destructive low-tier. Heists = cat-paying acquisition."""
    if npc_key == "jeremy":
        return "hit"
    if npc_key == "sofia":
        return "heist"
    return "hit" if tier <= 2 else "heist"


def _jobs_template_id(window_idx: int, slot_idx: int, npc: str, tier: int) -> str:
    return f"w{window_idx}:s{slot_idx}:{npc}:t{tier}"


def _jobs_try_pick_slot(eligible_npcs: list[str], rng: random.Random, catnip_level: int, faction_rep: dict, current_heat: int) -> dict | None:
    """Up to 5 attempts to land on a valid (npc, tier, target). Returns the
    raw offer dict ready for DB insert, sans book-keeping columns."""
    attempts = list(eligible_npcs)
    rng.shuffle(attempts)
    for npc_key in attempts[:5]:
        tier = _jobs_pick_tier(npc_key, catnip_level, rng)
        if tier is None:
            continue
        target = _jobs_pick_target(npc_key, catnip_level, faction_rep, rng)
        if target is None:
            continue
        difficulty = _jobs_resolve_difficulty(tier, target, faction_rep, rng)
        reward = _jobs_resolve_reward(tier, npc_key, rng)
        heat_cost = _jobs_resolve_heat_cost_with_rep(tier, npc_key, current_heat, target, faction_rep)
        narrative = _jobs_pick_narrative(npc_key, target, rng)
        return {
            "category": _jobs_category_for(npc_key, tier),
            "tier": tier,
            "offered_by": npc_key,
            "target_faction": target or "",
            "difficulty": difficulty,
            "narrative": narrative,
            "reward_snapshot": reward,
            "heat_cost": heat_cost,
        }
    return None


def _jobs_build_tutorial_offer(rng: random.Random, current_heat: int) -> dict:
    """Lv2-3 single-slot errand from Lucian Jr. Always tier 1 hit on
    Whiskers's outfit. Narrative comes from the dedicated 'tutorial' pool."""
    target = "whiskers"
    tier = 1
    npc = "lucian_jr"
    difficulty = _jobs_resolve_difficulty(tier, target, {}, rng)
    reward = _jobs_resolve_reward(tier, npc, rng)
    heat_cost = _jobs_resolve_heat_cost(tier, npc, current_heat)
    narrative = _jobs_pick_narrative(npc, target, rng, pool_key="tutorial")
    return {
        "category": "hit",
        "tier": tier,
        "offered_by": npc,
        "target_faction": target,
        "difficulty": difficulty,
        "narrative": narrative,
        "reward_snapshot": reward,
        "heat_cost": heat_cost,
    }


def _jobs_generate_offers(profile: Profile, window_idx: int, user_season: int = 0) -> list[dict]:
    """Returns a list of 0/1/3 offer dicts ready for DB insert. Deterministic
    in (user_id, guild_id, window_idx) — calling twice returns the same set."""
    level = int(getattr(profile, "catnip_level", 0) or 0)
    rep = _jobs_faction_rep(profile)
    current_heat = int(getattr(profile, "heat", 0) or 0)
    user_id = int(profile.user_id)
    guild_id = int(profile.guild_id)

    if level < 2:
        return []

    if level < 4:
        rng = _jobs_seed_rng(user_id, guild_id, window_idx, salt="tutorial")
        offer = _jobs_build_tutorial_offer(rng, current_heat)
        offer["_slot_idx"] = 0
        offer["_template_id"] = _jobs_template_id(window_idx, 0, offer["offered_by"], offer["tier"])
        return [offer]

    out = []
    big_score_eligible = _jobs_big_score_available(profile, user_season)
    for slot_idx in range(JOBS_MAX_SLOTS):
        rng = _jobs_seed_rng(user_id, guild_id, window_idx, salt=f"slot:{slot_idx}")
        if slot_idx == 0 and big_score_eligible:
            picked = _jobs_build_big_score_offer(rng, user_season, rep)
        else:
            eligible = _jobs_eligible_npcs(level, rep)
            if not eligible:
                continue
            picked = _jobs_try_pick_slot(eligible, rng, level, rep, current_heat)
            if picked is None:
                continue
        picked["_slot_idx"] = slot_idx
        picked["_template_id"] = _jobs_template_id(window_idx, slot_idx, picked["offered_by"], picked["tier"])
        out.append(picked)
    return out


async def _jobs_refresh_offers_if_needed(profile: Profile, now: int) -> list:
    """SELECT-then-INSERT idempotent refresh. Returns the JobInstance rows for
    the current window, sorted by slot_idx encoded in template_id."""
    window_idx = _jobs_window_index(now)
    win_start, win_end = _jobs_window_bounds(window_idx)
    existing = await JobInstance.collect(
        "user_id = $1 AND guild_id = $2 AND state = 'offered' AND offered_at >= $3 AND offered_at < $4",
        int(profile.user_id),
        int(profile.guild_id),
        win_start,
        win_end,
    )
    existing_templates = {row.template_id for row in existing}

    # Big Score's once-per-season gate. `season` lives on Profile (per-server),
    # not on User — Cat Bot tracks BP seasons per-(user, guild).
    try:
        season = int(profile.season or 0)
    except KeyError:
        season = 0
    desired = _jobs_generate_offers(profile, window_idx, user_season=season)
    new_rows = []
    for offer in desired:
        if offer["_template_id"] in existing_templates:
            continue
        new_row = await JobInstance.create(
            template_id=offer["_template_id"],
            user_id=int(profile.user_id),
            guild_id=int(profile.guild_id),
            category=offer["category"],
            tier=offer["tier"],
            offered_by=offer["offered_by"],
            target_faction=offer["target_faction"],
            difficulty=offer["difficulty"],
            send_snapshot={},
            send_total=0,
            success_chance=0.0,
            roll=0.0,
            outcome="",
            cats_destroyed={},
            state="offered",
            narrative=offer["narrative"],
            reward_snapshot=offer["reward_snapshot"],
            rep_changes={},
            heat_cost=offer["heat_cost"],
            offered_at=now,
            expires_at=win_end,
            resolved_at=0,
            committed_at=0,
        )
        new_rows.append(new_row)

    all_rows = list(existing) + new_rows
    # Stable order: parse slot_idx from template_id ("w<W>:s<S>:...").
    def _slot_key(row):
        try:
            return int(row.template_id.split(":")[1][1:])
        except Exception:
            return 99
    all_rows.sort(key=_slot_key)
    return all_rows


def _jobs_reward_summary(reward: dict) -> str:
    coins = int(reward.get("coins", 0))
    cats = reward.get("cats", {}) or {}
    pack = reward.get("pack")
    parts = []
    if coins:
        parts.append(f"🪙 {coins:,}")
    for t, c in cats.items():
        emoji = get_emoji(t.lower()) if t else ""
        parts.append(f"{c}× {emoji} {t}".strip())
    if pack:
        pack_emoji = get_emoji(f"{pack}pack") or "📦"
        parts.append(f"{pack_emoji} 1× {pack.title()} Pack")
    return "  ·  ".join(parts) if parts else "—"


# ---------------------------------------------------------------------------
# Jobs / Mafia Killings — Phase 2: send/commit/resolve.
# ---------------------------------------------------------------------------

JOBS_DIMINISHING_ALPHA = float(JOBS_TUNING.get("diminishing_returns_alpha", 0.75))
JOBS_MAX_DAILY_COMMITS = int(JOBS_TUNING.get("max_commits_per_day", 3))


def _jobs_coerce_dict(value) -> dict:
    """JSONB columns sometimes arrive as strings; normalize to dict."""
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value) or {}
        except (ValueError, TypeError):
            return {}
    return dict(value)


def _jobs_success_chance(send_total: int, difficulty: int, offerer_rep_bonus: float = 0.0) -> float:
    """Sigmoid of (ratio - 1.0), shifted by rep bonus, clamped to [floor, ceiling]."""
    ratio = send_total / max(1, difficulty)
    k = JOBS_PROB["k"]
    raw = 1 / (1 + math.exp(-k * (ratio - 1)))
    return max(JOBS_PROB["floor"], min(JOBS_PROB["ceiling"], raw + offerer_rep_bonus))


def _jobs_offerer_rep_bonus(npc_key: str, faction_rep: dict) -> float:
    """+0.075% per point with the offerer, capped at ±12%."""
    rep = faction_rep.get(npc_key, 0)
    bonus = rep * JOBS_REP["offerer_bonus_per_point"]
    cap = JOBS_REP["offerer_bonus_cap"]
    return max(-cap, min(cap, bonus))


def _jobs_effective_sp_for_type(cat_type: str, count: int) -> float:
    """Diminishing returns per type: contribution = sp_per × count^α (α<1).
    Penalizes mono-rarity stacking; mixed crews are barely affected since the
    penalty applies to each type's count independently."""
    n = int(count or 0)
    if n <= 0:
        return 0.0
    sp_per = JOBS_SEND_POWER.get(cat_type, 0)
    if sp_per == 0:
        return 0.0
    return sp_per * (n ** JOBS_DIMINISHING_ALPHA)


def _jobs_send_total(send: dict) -> int:
    return int(round(sum(_jobs_effective_sp_for_type(t, c) for t, c in send.items())))


def _jobs_send_count(send: dict) -> int:
    return sum(int(c or 0) for c in send.values())


def _jobs_feel_label(chance: float) -> tuple[str, int]:
    if chance <= 0.20:
        return "Suicide mission", Colors.red
    if chance <= 0.40:
        return "Risky", Colors.red
    if chance <= 0.60:
        return "Coin flip", Colors.brown
    if chance <= 0.80:
        return "Good odds", Colors.green
    return "Lock", Colors.green


def _jobs_gauge(chance: float, width: int = 18) -> str:
    filled = max(0, min(width, int(round(chance * width))))
    return "█" * filled + "░" * (width - filled)


def _jobs_select_near_miss_casualties(send: dict, rng: random.Random) -> dict:
    """Random by count. Half (rounded up) of sent cats are destroyed."""
    flat: list[str] = []
    for t, c in send.items():
        flat.extend([t] * int(c))
    total = len(flat)
    if total == 0:
        return {}
    rng.shuffle(flat)
    n_lost = math.ceil(total / 2)
    out: dict = {}
    for t in flat[:n_lost]:
        out[t] = out.get(t, 0) + 1
    return out


def _jobs_resolve_outcome(send_total: int, difficulty: int, send: dict, rng: random.Random) -> dict:
    return _jobs_resolve_outcome_with_rep(send_total, difficulty, send, 0.0, rng)


def _jobs_resolve_outcome_with_rep(send_total: int, difficulty: int, send: dict, rep_bonus: float, rng: random.Random) -> dict:
    chance = _jobs_success_chance(send_total, difficulty, rep_bonus)
    r = rng.random()
    band = JOBS_PROB["near_miss_band"]
    if r < chance:
        outcome, destroyed = "success", {}
    elif r < chance + band:
        outcome, destroyed = "near_miss", _jobs_select_near_miss_casualties(send, rng)
    else:
        outcome, destroyed = "total_failure", dict(send)
    return {"outcome": outcome, "roll": float(r), "success_chance": float(chance), "cats_destroyed": destroyed}


# ---------------------------------------------------------------------------
# Complications: independent second die rolled per commit. Closes the 95%
# success-ceiling loophole and adds texture (heat raids, rival crews, jackpots,
# pending aftermath effects). See docs/design/jobs.md.
# ---------------------------------------------------------------------------

JOBS_COMPLICATIONS = config.jobs.get("complications", {})
JOBS_COMPLICATION_POOLS = config.jobs.get("complication_pools", {})
JOBS_COMPLICATION_FLAVOR = config.jobs.get("complication_flavor", {})

# Order matters — used by sloppy_target to pick "one tier above" defaults.
PACK_TIER_ORDER = ["wooden", "stone", "bronze", "silver", "gold", "platinum", "diamond", "celestial"]


def _jobs_col(profile, name, default):
    """Safe read for newly-migrated profile columns. catpg's __getattr__ raises
    KeyError (not AttributeError) when a column is missing, so the standard
    getattr-with-default idiom doesn't fall back. This wraps that for the
    Phase 1 columns that may not exist yet pre-migration-009."""
    try:
        v = getattr(profile, name)
    except KeyError:
        return default
    return default if v is None else v


def _jobs_heat_band(heat: int) -> str:
    if heat >= 71:
        return "scrutiny"
    if heat >= 31:
        return "watching"
    return "low"


def _jobs_complication_chance(tier: int, current_heat: int, offerer_rep: int) -> float:
    """final = base_by_tier * (1 + heat_factor) * (1 - clamp(rep_discount))."""
    base = float(JOBS_COMPLICATIONS.get("base_chance_by_tier", {}).get(str(tier), 0.0))
    if base <= 0:
        return 0.0
    heat_factor = float(JOBS_COMPLICATIONS.get("heat_modifier", {}).get(_jobs_heat_band(int(current_heat)), 0.0))
    rep_per = float(JOBS_COMPLICATIONS.get("rep_discount_per_point", 0.0))
    rep_cap = float(JOBS_COMPLICATIONS.get("rep_discount_cap", 0.0))
    rep_discount = min(rep_cap, max(0, int(offerer_rep)) * rep_per)
    return max(0.0, base * (1.0 + heat_factor) * (1.0 - rep_discount))


def _jobs_roll_complication(tier: int, chance: float, rng: random.Random) -> dict | None:
    """Roll the complication die. Returns a copy of the chosen event dict
    from the per-tier pool, or None if the die misses or the pool is empty."""
    if chance <= 0 or rng.random() >= chance:
        return None
    pool = JOBS_COMPLICATION_POOLS.get(str(tier)) or []
    if not pool:
        return None
    weights = [max(0, int(e.get("weight", 0))) for e in pool]
    if sum(weights) <= 0:
        return None
    picked = rng.choices(pool, weights=weights, k=1)[0]
    return dict(picked)


def _jobs_apply_pre_roll(event: dict, difficulty: int, send_total: int) -> tuple[int, str | None]:
    """Mutate difficulty / force outcome before the success die rolls.
    Returns (effective_difficulty, forced_outcome). forced_outcome is None
    unless the event short-circuits (rival_crew when SP wall fails)."""
    eid = event.get("id")
    if eid == "rival_crew":
        frac = float(event.get("wall_fraction", 0.4))
        wall = max(1, math.ceil(difficulty * frac))
        if send_total < wall:
            return difficulty, "near_miss"
        return difficulty, None
    if eid == "boss_arrives":
        mult = float(event.get("difficulty_mult", 1.4))
        return max(1, math.ceil(difficulty * mult)), None
    return difficulty, None


def _jobs_bump_pack_tier(current: str | None, recipe_tier: int) -> str:
    """sloppy_target: pick a pack tier one above the default for this recipe
    tier (or one above the existing pack if the recipe already had one).
    Capped at Celestial."""
    if current:
        try:
            idx = PACK_TIER_ORDER.index(current)
            return PACK_TIER_ORDER[min(len(PACK_TIER_ORDER) - 1, idx + 1)]
        except ValueError:
            pass
    default = JOBS_COMPLICATIONS.get("sloppy_target_default_pack_tier_by_tier", {}).get(str(recipe_tier))
    if default and default in PACK_TIER_ORDER:
        try:
            idx = PACK_TIER_ORDER.index(default)
            return PACK_TIER_ORDER[min(len(PACK_TIER_ORDER) - 1, idx + 1)]
        except ValueError:
            pass
    return "wooden"


def _jobs_cat_one_tier_above(reward_cats: dict) -> str | None:
    """For found_a_stash: pick a rarity one step rarer than the rarest cat in
    the existing reward. If no cats in reward, fall back to Rare."""
    if not reward_cats:
        return "Rare"
    rarest = None
    rarest_idx = -1
    for t in reward_cats.keys():
        try:
            idx = cattypes.index(t)
        except ValueError:
            continue
        if idx > rarest_idx:
            rarest_idx = idx
            rarest = t
    if rarest is None:
        return "Rare"
    next_idx = min(len(cattypes) - 1, rarest_idx + 1)
    return cattypes[next_idx]


def _jobs_apply_post_roll(event: dict, outcome_dict: dict, reward: dict, recipe_tier: int, send: dict, rng: random.Random) -> tuple[dict, dict, int, bool]:
    """Apply post_roll effects after the success die has resolved. Returns
    (mutated_outcome_dict, mutated_reward_dict, extra_heat, fired_meaningfully).
    `fired_meaningfully` is False if the event's effect is null on the actual
    outcome (e.g. easy_mark on a total_failure) — the caller clears the
    complication id in that case so the result screen doesn't lie."""
    eid = event.get("id")
    outcome = outcome_dict.get("outcome", "")
    extra_heat = 0
    reward = dict(reward) if isinstance(reward, dict) else {}
    reward.setdefault("cats", {})
    if not isinstance(reward["cats"], dict):
        reward["cats"] = {}

    if eid == "cat_police_raid":
        extra_heat = int(event.get("heat_bonus", 30))
        return outcome_dict, reward, extra_heat, True

    if eid == "informant":
        # Only downgrades a success → near_miss. If already worse, no effect.
        if outcome == "success":
            new_outcome = dict(outcome_dict)
            new_outcome["outcome"] = "near_miss"
            new_outcome["cats_destroyed"] = _jobs_select_near_miss_casualties(send, rng)
            return new_outcome, reward, 0, True
        return outcome_dict, reward, 0, False

    # Reward modifiers — only fire on success (cleaner UX; near_miss/wipe have
    # no reward to modify).
    if outcome != "success":
        return outcome_dict, reward, 0, False

    if eid == "easy_mark":
        reward["coins"] = int(reward.get("coins", 0) or 0) * 2
        reward["cats"] = {t: int(c) * 2 for t, c in (reward.get("cats") or {}).items()}
        return outcome_dict, reward, 0, True

    if eid == "double_cross":
        new_cats = {}
        for t, c in (reward.get("cats") or {}).items():
            kept = max(0, int(c) // 2)
            if kept > 0:
                new_cats[t] = kept
        if new_cats == (reward.get("cats") or {}):
            # Skim had no bite (e.g. recipe only paid 1 cat — //2 = 0 cats kept
            # makes the player feel robbed which IS the flavor). Allow that —
            # the all-floor case here is the intended teeth.
            pass
        reward["cats"] = new_cats
        return outcome_dict, reward, 0, True

    if eid == "found_a_stash":
        extra_type = _jobs_cat_one_tier_above(reward.get("cats") or {})
        if extra_type:
            reward["cats"] = dict(reward.get("cats") or {})
            reward["cats"][extra_type] = int(reward["cats"].get(extra_type, 0)) + 1
        return outcome_dict, reward, 0, True

    if eid == "sloppy_target":
        new_pack = _jobs_bump_pack_tier(reward.get("pack"), recipe_tier)
        reward["pack"] = new_pack
        reward["cats"] = {}  # replaces the cat reward
        return outcome_dict, reward, 0, True

    return outcome_dict, reward, 0, False


def _jobs_apply_aftermath(event: dict, profile: Profile) -> None:
    """Persist aftermath effects to the player's profile so the NEXT commit
    consumes them. Both columns are reset to defaults whenever they're consumed."""
    eid = event.get("id")
    if eid == "witness":
        mult = float(event.get("difficulty_mult", 1.2))
        # Compose with any already-pending mult (rare edge case if two
        # aftermaths stack — just multiply, the cap is loose).
        cur = float(_jobs_col(profile, "jobs_pending_difficulty_mult", 1.0))
        try:
            profile.jobs_pending_difficulty_mult = round(cur * mult, 4)
        except KeyError:
            logging.warning("jobs: aftermath witness skipped — migration 009 not applied")
    elif eid == "loose_end":
        bonus = int(event.get("heat_bonus", 10))
        cur = int(_jobs_col(profile, "jobs_pending_heat_bonus", 0))
        try:
            profile.jobs_pending_heat_bonus = cur + bonus
        except KeyError:
            logging.warning("jobs: aftermath loose_end skipped — migration 009 not applied")


def _jobs_complication_flavor(event_id: str, rng: random.Random) -> str:
    """One-line flavor pulled from the per-event pool. Empty if no flavor set."""
    pool = JOBS_COMPLICATION_FLAVOR.get(event_id) or []
    if not pool:
        return ""
    return rng.choice(pool)


# ---------------------------------------------------------------------------
# Phase 3: cat dialogue. One survivor (or casualty on a wipe) gets the last
# word on the result screen. Weighted toward rarer cats so the eGirl talks
# when she comes home alive.
# ---------------------------------------------------------------------------

JOBS_CAT_VOICES = config.jobs.get("cat_voices", {})
JOBS_COMPLICATION_QUIPS = config.jobs.get("complication_quips", {})
JOBS_ACCEPT_ANN = config.jobs.get("accept_announcements", {})
JOBS_ACCEPT_ANN_BIG_SCORE = config.jobs.get("accept_announcements_big_score", [])
JOBS_OUTCOME_ANN = config.jobs.get("outcome_announcements", {})


def _jobs_format_accept_line(job, player_mention: str, rng: random.Random) -> str:
    """Build a one-line thematic announcement for a public accept embed."""
    if int(job.tier or 0) == 5 and JOBS_ACCEPT_ANN_BIG_SCORE:
        template = rng.choice(JOBS_ACCEPT_ANN_BIG_SCORE)
    else:
        pool = JOBS_ACCEPT_ANN.get(job.offered_by) or [
            f"{{player}} just took a job from **{_jobs_npc_display(job.offered_by)}**."
        ]
        template = rng.choice(pool)
    return template.replace("{player}", player_mention)


def _jobs_format_outcome_line(job, outcome: str, casualties: int, player_mention: str, rng: random.Random) -> str:
    """Build the body line of the outcome embed. Substitutes {player}, {npc}, {casualties}."""
    pool = JOBS_OUTCOME_ANN.get(outcome) or [
        f"{{player}}'s job for **{{npc}}** ended in {outcome.replace('_', '-')}."
    ]
    template = rng.choice(pool)
    return (template
            .replace("{player}", player_mention)
            .replace("{npc}", _jobs_npc_display(job.offered_by))
            .replace("{casualties}", str(casualties)))


def _jobs_outcome_color(outcome: str) -> int:
    if outcome == "success":
        return Colors.green
    if outcome == "near_miss":
        return Colors.brown
    return Colors.red


async def _jobs_announce_accept(channel, job, player_mention: str) -> None:
    """Post a public embed when a player accepts a contract. Best-effort —
    swallows errors so an embed-post failure can't block the send screen."""
    if channel is None:
        return
    try:
        rng = random.Random(int(job.id or 0) ^ hash(player_mention))
        tier_info = JOBS_TIERS.get(str(job.tier), {})
        tier_name = tier_info.get("name", f"Tier {job.tier}")
        line = _jobs_format_accept_line(job, player_mention, rng)
        embed = discord.Embed(
            title="🎯 Contract Accepted",
            description=line,
            color=Colors.brown,
        )
        embed.add_field(name="Tier", value=f"{job.tier} ({tier_name})", inline=True)
        if job.target_faction:
            embed.add_field(name="Target", value=_jobs_npc_display(job.target_faction), inline=True)
        embed.add_field(name="Difficulty", value=f"{job.difficulty} SP", inline=True)
        if job.narrative:
            embed.set_footer(text=job.narrative[:200])
        await channel.send(embed=embed)
    except Exception:
        logging.exception("jobs: accept announcement failed (non-fatal)")


async def _jobs_announce_outcome(channel, job, profile, player_mention: str) -> None:
    """Post a public embed when a job resolves. Includes outcome, reward
    summary on success, complication flavor when applicable. Best-effort."""
    if channel is None:
        return
    try:
        outcome = job.outcome or ""
        if not outcome:
            return  # job didn't resolve cleanly; skip the embed
        cats_destroyed = _jobs_coerce_dict(job.cats_destroyed)
        casualties = sum(int(c or 0) for c in cats_destroyed.values())
        send_snap = _jobs_coerce_dict(job.send_snapshot)
        sent_total = sum(int(c or 0) for c in send_snap.values())
        survivors = max(0, sent_total - casualties)
        rng = random.Random(int(job.id or 0) ^ hash(outcome) ^ int(job.resolved_at or 0))

        if outcome == "success":
            title = "✅ Job Done"
        elif outcome == "near_miss":
            title = "🩹 Almost"
        else:
            title = "💀 Wiped"

        body = _jobs_format_outcome_line(job, outcome, casualties, player_mention, rng)
        embed = discord.Embed(title=title, description=body, color=_jobs_outcome_color(outcome))

        # Reward (success only)
        reward = _jobs_coerce_dict(job.reward_snapshot)
        if outcome == "success":
            reward_str = _jobs_reward_summary(reward)
            if reward_str and reward_str != "—":
                embed.add_field(name="Reward", value=reward_str, inline=False)
        elif outcome == "near_miss":
            embed.add_field(name="Crew", value=f"{survivors} survived  ·  {casualties} lost", inline=False)
        else:
            embed.add_field(name="Crew", value=f"All {casualties} lost", inline=False)

        # Complication (if it fired and was meaningful)
        comp_id = (_jobs_col(job, "complication", "") or "").strip()
        if comp_id:
            flavor = _jobs_complication_flavor(comp_id, rng)
            comp_pretty = comp_id.replace("_", " ").title()
            value = f"**{comp_pretty}** — *{flavor}*" if flavor else f"**{comp_pretty}**"
            embed.add_field(name="⚠️ Complication", value=value, inline=False)

        # Pinch tag
        rep_changes = _jobs_coerce_dict(job.rep_changes)
        if rep_changes.get("pinched"):
            embed.set_footer(text="🚓 Heat hit 100. The Cat Police picked them up.")

        await channel.send(embed=embed)
    except Exception:
        logging.exception("jobs: outcome announcement failed (non-fatal)")


def _jobs_survivors(send: dict, cats_destroyed: dict, outcome: str) -> dict:
    """For success — all sent cats survived. For near_miss — sent minus destroyed.
    For total_failure — all sent cats DIED (returned as the casualty set for
    posthumous lines)."""
    sent = {t: int(c or 0) for t, c in (send or {}).items() if int(c or 0) > 0}
    if outcome == "success":
        return sent
    destroyed = {t: int(c or 0) for t, c in (cats_destroyed or {}).items() if int(c or 0) > 0}
    if outcome == "total_failure":
        return destroyed  # posthumous voice pool
    # near_miss: sent − destroyed
    out = {}
    for t, c in sent.items():
        survived = c - destroyed.get(t, 0)
        if survived > 0:
            out[t] = survived
    return out


def _jobs_pick_speaking_rarity(candidates: dict, rng: random.Random) -> str | None:
    """Weight each candidate by `count × inverse_spawn_weight`. Rarer rarities
    have lower spawn weights in type_dict, so we invert to make them speak more.
    Returns the picked rarity name, or None if candidates is empty."""
    if not candidates:
        return None
    types = []
    weights = []
    for t, c in candidates.items():
        spawn_weight = type_dict.get(t)
        if not spawn_weight:
            continue
        # Inverse: a Fine has weight 1000 → 1/1000; an eGirl is 2 → 1/2.
        # Multiply by count so a 100-Fine crew still gets some voice probability.
        types.append(t)
        weights.append((c * 1.0) / spawn_weight)
    if not types:
        return None
    return rng.choices(types, weights=weights, k=1)[0]


def _jobs_pick_cat_voice(send: dict, cats_destroyed: dict, outcome: str, complication_id: str, rng: random.Random) -> str | None:
    """Return a formatted '> _The X cat:_ "line"' quote, or None if no cats
    eligible. Prefers complication_quips when a thematic match exists for any
    surviving/casualty rarity; otherwise falls back to cat_voices[rarity][outcome]."""
    pool = _jobs_survivors(send, cats_destroyed, outcome)
    if not pool:
        return None

    # First pass: try to find a thematic quip if a complication fired.
    quip_block = JOBS_COMPLICATION_QUIPS.get(complication_id, {}) if complication_id else {}
    if quip_block:
        themed = {t: c for t, c in pool.items() if t in quip_block and quip_block[t]}
        if themed:
            rarity = _jobs_pick_speaking_rarity(themed, rng)
            if rarity:
                line = rng.choice(quip_block[rarity])
                return f"> _The {rarity} cat:_ \"{line}\""

    # Fallback: generic cat_voices[rarity][outcome].
    rarity = _jobs_pick_speaking_rarity(pool, rng)
    if not rarity:
        return None
    rarity_block = JOBS_CAT_VOICES.get(rarity, {})
    lines = rarity_block.get(outcome) or []
    if not lines:
        return None
    line = rng.choice(lines)
    return f"> _The {rarity} cat:_ \"{line}\""


def _jobs_add_cat(profile: Profile, cat_type: str, count: int) -> None:
    """Safely increment a cat column. No-op if column doesn't exist."""
    if count == 0 or cat_type not in type_dict:
        return
    col = f"cat_{cat_type}"
    try:
        cur = int(profile[col] or 0)
    except KeyError:
        return
    profile[col] = max(0, cur + count)


def _jobs_subtract_cat(profile: Profile, cat_type: str, count: int) -> bool:
    """Subtract from a cat column. Returns False if insufficient (no mutation)."""
    if count == 0 or cat_type not in type_dict:
        return True
    col = f"cat_{cat_type}"
    try:
        cur = int(profile[col] or 0)
    except KeyError:
        return False
    if cur < count:
        return False
    profile[col] = cur - count
    return True


async def _jobs_apply_outcome(profile: Profile, job, outcome_dict: dict, rng: random.Random) -> None:
    """Apply outcome side-effects on profile + job. Caller saves both."""
    outcome = outcome_dict["outcome"]
    job.outcome = outcome
    job.roll = outcome_dict["roll"]
    job.success_chance = outcome_dict["success_chance"]
    job.cats_destroyed = outcome_dict["cats_destroyed"]

    # Heat — applies + may trigger the Pinch (Cat Police Station) at >=100.
    prior_heat = int(getattr(profile, "heat", 0) or 0)
    prior_suspended = int(getattr(profile, "perks_suspended_until", 0) or 0)
    prior_big_score_season = int(getattr(profile, "big_score_season", -1) or -1)
    prior_big_score_wins = int(getattr(profile, "big_score_wins", 0) or 0)
    prior_big_score_perk = bool(getattr(profile, "big_score_perk_unlocked", False))
    pinched = _jobs_apply_commit_heat(profile, int(job.heat_cost or 0), int(time.time()))

    # Rep — per-tier swing. Big Score uses fixed swings from JOBS_BIG_SCORE.
    rep = _jobs_faction_rep(profile)
    is_big_score = int(job.tier or 0) == 5
    if is_big_score:
        rep_block = JOBS_BIG_SCORE.get("rep_changes", {})
        if outcome == "success":
            rep_delta = {k: int(v) for k, v in (rep_block.get("success") or {}).items()}
        else:
            rep_delta = {k: int(v) for k, v in (rep_block.get("failure") or {}).items()}
        for k, v in rep_delta.items():
            rep[k] = rep.get(k, 0) + v
    else:
        offerer_gain = JOBS_REP["tier_rep_gain"].get(str(job.tier), 0)
        target_loss = JOBS_REP["tier_rep_loss"].get(str(job.tier), 0)
        failure_penalty = JOBS_REP["failure_penalty"]
        if outcome == "success":
            rep[job.offered_by] = rep.get(job.offered_by, 0) + offerer_gain
            if job.target_faction:
                rep[job.target_faction] = rep.get(job.target_faction, 0) + target_loss
            rep_delta = {job.offered_by: offerer_gain}
            if job.target_faction:
                rep_delta[job.target_faction] = target_loss
        else:
            rep[job.offered_by] = rep.get(job.offered_by, 0) + failure_penalty
            rep_delta = {job.offered_by: failure_penalty}
    profile.faction_rep = rep
    job.rep_changes = {
        "applied": rep_delta,
        "outcome": outcome,
        "pinched": pinched,
        "prior_heat": prior_heat,
        "prior_suspended_until": prior_suspended,
        "prior_big_score_season": prior_big_score_season,
        "prior_big_score_wins": prior_big_score_wins,
        "prior_big_score_perk_unlocked": prior_big_score_perk,
    }

    # Big Score: regardless of outcome the season is consumed.
    reward = _jobs_coerce_dict(job.reward_snapshot)
    if is_big_score:
        season = int(reward.get("_season", 0) or 0)
        profile.big_score_season = season

    # Lifetime counters + reward grant
    if outcome == "success":
        profile.jobs_completed = int(getattr(profile, "jobs_completed", 0) or 0) + 1
        coin_reward = int(reward.get("coins", 0) or 0)
        if coin_reward:
            profile.coins = int(getattr(profile, "coins", 0) or 0) + coin_reward
        for t, c in (reward.get("cats") or {}).items():
            _jobs_add_cat(profile, t, int(c or 0))
            await mark_discovered(profile, t)
        # Pack reward (Phase 2 recipes). Lands in /packs inventory, not auto-opened.
        pack_tier = reward.get("pack")
        if pack_tier:
            col = f"pack_{pack_tier}"
            try:
                profile[col] = int(profile[col] or 0) + 1
            except KeyError:
                logging.warning("jobs: unknown pack tier %r — pack not granted", pack_tier)
        profile.job_coins_won = int(getattr(profile, "job_coins_won", 0) or 0) + coin_reward
        haul = coin_reward + sum(cat_value(t) * int(c or 0) for t, c in (reward.get("cats") or {}).items())
        if haul > int(getattr(profile, "biggest_score_value", 0) or 0):
            profile.biggest_score_value = haul
        # Big Score: bump win counter, grant the one-time perk on first win.
        if is_big_score:
            profile.big_score_wins = int(getattr(profile, "big_score_wins", 0) or 0) + 1
            if reward.get("perk") == "big_score" and not bool(getattr(profile, "big_score_perk_unlocked", False)):
                profile.big_score_perk_unlocked = True
    elif outcome == "near_miss":
        profile.jobs_near_missed = int(getattr(profile, "jobs_near_missed", 0) or 0) + 1
        # Big Score near-miss consolation: 5,000 coins + half cats already returned.
        if is_big_score:
            consolation = int(reward.get("_near_miss_coins", 0) or 0)
            if consolation:
                profile.coins = int(getattr(profile, "coins", 0) or 0) + consolation
                profile.job_coins_won = int(getattr(profile, "job_coins_won", 0) or 0) + consolation
    else:
        profile.jobs_failed = int(getattr(profile, "jobs_failed", 0) or 0) + 1

    destroyed_total = sum(int(c or 0) for c in (outcome_dict["cats_destroyed"] or {}).values())
    if destroyed_total:
        profile.cats_lost_to_jobs = int(getattr(profile, "cats_lost_to_jobs", 0) or 0) + destroyed_total

    # Phase 6 achievement unlocks. Silent — players see them in /achievements.
    chance = float(outcome_dict.get("success_chance") or 0)
    target_rep_after = int(rep.get(job.target_faction, 0)) if job.target_faction else 0

    if outcome == "success":
        if int(getattr(profile, "jobs_completed", 0) or 0) == 1:
            profile.unlock_ach("first_job")
        if is_big_score:
            profile.unlock_ach("egirl_job")
        if chance > 0 and chance <= 0.25:
            profile.unlock_ach("the_house_wins")
        if job.target_faction and target_rep_after < -50:
            profile.unlock_ach("vendetta")
        # Bucketed counters (no dedicated columns — derive from jobs_completed
        # + category at unlock time). Slight approximation: counts all-time
        # successful jobs of the category, not just hits/heists specifically.
        completed = int(getattr(profile, "jobs_completed", 0) or 0)
        if job.category == "hit" and completed >= 25:
            profile.unlock_ach("hit_man")
        if job.category == "heist" and completed >= 10:
            profile.unlock_ach("cat_burglar")
    else:
        if chance >= 0.80:
            profile.unlock_ach("the_house_loses")
        if outcome == "near_miss":
            near_count = int(getattr(profile, "jobs_near_missed", 0) or 0)
            if near_count >= 10:
                profile.unlock_ach("got_out_alive")

    if pinched:
        profile.unlock_ach("feds")

    if int(getattr(profile, "cats_lost_to_jobs", 0) or 0) >= 100:
        profile.unlock_ach("lost_it_all")

    if int(rep.get("whiskers", 0)) >= 100:
        profile.unlock_ach("whiskers_pet")

    hostile_count = sum(1 for v in rep.values() if int(v) < -25)
    if hostile_count >= 5:
        profile.unlock_ach("outlaw")


def _jobs_start_of_utc_day(now: int) -> int:
    """Unix epoch for 00:00 UTC of the day containing `now`."""
    dt = datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp())


async def _jobs_commits_today(user_id: int, guild_id: int, now: int) -> int:
    """Count commits-this-UTC-day (per-server). Cancelled commits zero out
    `committed_at`, so they aren't counted — the daily cap doesn't punish misclicks."""
    start = _jobs_start_of_utc_day(now)
    return int(await JobInstance.count(
        "user_id = $1 AND guild_id = $2 AND committed_at >= $3 AND state IN ('resolved', 'committed')",
        int(user_id), int(guild_id), start,
    ) or 0)


# ---------------------------------------------------------------------------
# Phase 4: heat decay + Cat Police Station pinch.
# ---------------------------------------------------------------------------

JOBS_PINCH_THRESHOLD = JOBS_TUNING.get("pinch_threshold", 100)
JOBS_PINCH_LOCKOUT = JOBS_TUNING.get("pinch_lockout_seconds", 43200)
JOBS_PINCH_RESET = JOBS_TUNING.get("pinch_reset_heat", 30)
JOBS_HEAT_DECAY_PER_HOUR = JOBS_TUNING.get("heat_decay_per_hour", 2)


def _jobs_catnip_active(profile: Profile, now: int | None = None) -> bool:
    now = now if now is not None else int(time.time())
    return int(getattr(profile, "catnip_active", 0) or 0) > now


def _jobs_apply_heat_decay(profile: Profile, now: int | None = None) -> int:
    """Lazy decay. -2 heat per hour since last decay, paused while catnip is
    active. Returns the (possibly updated) heat value. Caller still has to
    save profile if anything else changed."""
    now = now if now is not None else int(time.time())
    last = int(getattr(profile, "heat_last_decay", 0) or 0)
    current = int(getattr(profile, "heat", 0) or 0)
    if last <= 0 or current <= 0 or _jobs_catnip_active(profile, now):
        profile.heat_last_decay = now
        return current
    hours = max(0.0, (now - last) / 3600.0)
    if hours <= 0:
        return current
    decay = int(JOBS_HEAT_DECAY_PER_HOUR * hours)
    if decay <= 0:
        return current
    new_heat = max(0, current - decay)
    profile.heat = new_heat
    profile.heat_last_decay = now
    return new_heat


def _jobs_perks_suspended(profile: Profile, now: int | None = None) -> bool:
    """True while the Cat Police lockout is in effect. Catnip perks treat the
    player as if catnip were inactive while this returns True."""
    now = now if now is not None else int(time.time())
    return int(getattr(profile, "perks_suspended_until", 0) or 0) > now


def _jobs_apply_commit_heat(profile: Profile, heat_cost: int, now: int) -> bool:
    """Apply heat cost from a job commit. If the threshold is crossed, fire
    the pinch: reset to 30, set the perk-suspension timestamp. Returns True
    iff the player got pinched this commit."""
    prior = int(getattr(profile, "heat", 0) or 0)
    new_heat = prior + max(0, int(heat_cost or 0))
    if new_heat >= JOBS_PINCH_THRESHOLD:
        profile.heat = JOBS_PINCH_RESET
        profile.perks_suspended_until = now + JOBS_PINCH_LOCKOUT
        profile.heat_last_decay = now
        return True
    profile.heat = min(JOBS_PINCH_THRESHOLD - 1, new_heat)
    profile.heat_last_decay = now
    return False


def _jobs_resolve_heat_cost_with_rep(tier: int, npc_key: str, current_heat: int, target_key: str | None, faction_rep: dict) -> int:
    """As _jobs_resolve_heat_cost, but applies the hostile-target discount
    when rep with the target is at or below the hostile threshold."""
    base = _jobs_resolve_heat_cost(tier, npc_key, current_heat)
    if not target_key:
        return base
    rep = int(faction_rep.get(target_key, 0))
    if rep <= JOBS_REP.get("hostile_threshold", -75):
        discount = JOBS_REP.get("hostile_target_heat_discount", 0.25)
        return max(0, math.ceil(base * (1 - discount)))
    return base


# ---------------------------------------------------------------------------
# Phase 5: The Big Score (Tier 5 capstone, once per battlepass season).
# ---------------------------------------------------------------------------


def _jobs_big_score_available(profile: Profile, user_season: int) -> bool:
    """At Lv10+, once per battlepass season, the Big Score appears in slot 1."""
    min_lvl = JOBS_BIG_SCORE.get("min_catnip_level", 10)
    if int(getattr(profile, "catnip_level", 0) or 0) < min_lvl:
        return False
    return int(getattr(profile, "big_score_season", -1) or -1) != int(user_season)


def _jobs_build_big_score_offer(rng: random.Random, user_season: int, faction_rep: dict) -> dict:
    """Construct the Tier 5 Big Score offer. Patron Whiskers, target Wilson,
    fixed difficulty 800, fixed reward 3 eGirls + 15k coins + perk flag.
    Heat is always +100 — that commit auto-pinches."""
    pool = config.jobs.get("narrative_pools_big_score") or [
        "Whiskers comes to you with the heist he tried in '08 and couldn't pull off."
    ]
    narrative = rng.choice(pool)
    bs = JOBS_BIG_SCORE
    reward_block = bs.get("reward", {"eGirl": 3, "coins": 15000, "perk": "big_score"})
    coin_reward = int(reward_block.get("coins", 0))
    cat_reward = {k: int(v) for k, v in reward_block.items() if k not in ("coins", "perk")}
    reward_snapshot = {
        "coins": coin_reward,
        "cats": cat_reward,
        "perk": reward_block.get("perk"),
        "_near_miss_coins": int(bs.get("near_miss_consolation_coins", 0)),
        "_season": int(user_season),
        "_big_score": True,
    }
    # Difficulty bump from negative Wilson rep applies normally.
    base_difficulty = int(bs.get("difficulty", 800))
    neg = -min(0, int(faction_rep.get("wilson", 0)))
    bump = min(JOBS_REP["target_difficulty_cap"], neg * JOBS_REP["target_difficulty_per_negative_point"])
    difficulty = math.ceil(base_difficulty * (1 + bump))
    return {
        "category": "heist",
        "tier": 5,
        "offered_by": bs.get("patron_npc", "whiskers"),
        "target_faction": bs.get("target_npc", "wilson"),
        "difficulty": difficulty,
        "narrative": narrative,
        "reward_snapshot": reward_snapshot,
        "heat_cost": int(bs.get("heat_cost", 100)),
    }


def _jobs_is_big_score(job_or_reward) -> bool:
    if hasattr(job_or_reward, "tier"):
        return int(getattr(job_or_reward, "tier", 0) or 0) == 5
    return bool(_jobs_coerce_dict(job_or_reward).get("_big_score"))


# ---------------------------------------------------------------------------
# Phase 7: paginated /jobs help.
# ---------------------------------------------------------------------------


def _jobs_help_pages_for(profile: Profile) -> list[dict]:
    """Pages the player is allowed to see, filtered by catnip level. Returns
    a new list of dicts with each page's title + body, indexed in spec order."""
    level = int(getattr(profile, "catnip_level", 0) or 0)
    pages = config.jobs_help.get("pages", [])
    return [p for p in pages if level >= int(p.get("min_level_to_see", 0))]


def _jobs_help_index_by_title(profile: Profile, title_substr: str) -> int:
    """Find the page index in the level-filtered list that contains `title_substr`
    (case-insensitive). 0 if not found — caller lands on page 1 instead."""
    pages = _jobs_help_pages_for(profile)
    needle = title_substr.lower()
    for i, p in enumerate(pages):
        if needle in p.get("title", "").lower():
            return i
    return 0


async def _jobs_send_help(interaction: discord.Interaction, profile: Profile, start_page: int = 0) -> None:
    """Render paginated help in a fresh ephemeral followup. Prev/Next buttons
    walk through the pages the player's level unlocks."""
    pages = _jobs_help_pages_for(profile)
    if not pages:
        await interaction.response.send_message("No help available yet.", ephemeral=True)
        return
    page_idx = max(0, min(len(pages) - 1, int(start_page)))

    async def render(target_interaction: discord.Interaction, idx: int, is_initial: bool):
        page = pages[idx]
        items: list = [
            f"## 💡 Jobs Help — {page['title']}",
            f"-# Page {idx + 1} / {len(pages)}",
            Separator(),
            page["body"],
        ]
        prev_btn = Button(label="← Prev", style=ButtonStyle.gray, custom_id="jobshelp_prev", disabled=idx == 0)
        next_btn = Button(label="Next →", style=ButtonStyle.gray, custom_id="jobshelp_next", disabled=idx >= len(pages) - 1)

        async def on_prev(intr: discord.Interaction):
            await render(intr, idx - 1, is_initial=False)

        async def on_next(intr: discord.Interaction):
            await render(intr, idx + 1, is_initial=False)

        prev_btn.callback = on_prev
        next_btn.callback = on_next
        items.append(ActionRow(prev_btn, next_btn))

        view = LayoutView(timeout=VIEW_TIMEOUT)
        container = Container(*items)
        try:
            container.accent_color = Colors.brown
        except Exception:
            pass
        view.add_item(container)

        if is_initial:
            await target_interaction.response.send_message(view=view, ephemeral=True)
        elif target_interaction.response.is_done():
            await target_interaction.edit_original_response(view=view)
        else:
            await target_interaction.response.edit_message(view=view)

    await render(interaction, page_idx, is_initial=True)


async def mark_discovered(profile: Profile, cat_type: str) -> None:
    """Record that this player has owned at least one cat of this rarity in
    this server. Idempotent — safe to call from every cat-acquisition site.
    Lifetime per (user, server); selling all of a rarity does NOT undiscover."""
    if not cat_type or cat_type not in type_dict:
        return
    discovered = _coerce_array(profile.discovered_cats)
    if cat_type in discovered:
        return
    profile.discovered_cats = discovered + [cat_type]
    await profile.save()


async def mark_store_purchased(profile: Profile, cat_type: str) -> None:
    """Record that this player has bought at least one cat of this rarity
    from the store. Backs the catstore_collector achievement (len(set(...))
    == len(type_dict)). Idempotent."""
    if not cat_type or cat_type not in type_dict:
        return
    purchased = _coerce_array(profile.store_purchased_rarities)
    if cat_type in purchased:
        return
    profile.store_purchased_rarities = purchased + [cat_type]
    await profile.save()


async def check_channel_setupped(guild: Server, channel: discord.TextChannel) -> bool:
    if not guild.only_setupped_channels:
        return True
    channel = await Channel.get_or_none(channel_id=channel.id)
    return channel is not None


# news stuff
news_list = [
    {"title": "Cat Bot Survey - win rains!", "emoji": "📜"},
    {"title": "New Cat Rains perks!", "emoji": "✨"},
    {"title": "Cat Bot Christmas 2024", "emoji": "🎅"},
    {"title": "Cattlepass Update", "emoji": "⬆️"},
    {"title": "Packs!", "emoji": "goldpack"},
    {"title": "Message from CEO of Cat Bot", "emoji": "finecat"},
    {"title": "Cat Bot Turns 3", "emoji": "🥳"},
    {"title": "100,000 SERVERS WHAT", "emoji": "🎉"},
    {"title": "Regarding recent instabilities", "emoji": "🗒️"},
    {"title": "cat bot reached #5 on top.gg", "emoji": "yippee"},
    {"title": "top.gg awards (outdated)", "emoji": "🏆"},
    {"title": "Welcome to the Cat Mafia", "emoji": "catnip"},
    {"title": "vote for cat bot as finalist in top.gg awards", "emoji": "❤️"},
    {"title": "Cat Bot Christmas 2025", "emoji": "christmaspack"},
    {"title": "Happy Valentine's!", "emoji": "💞"},
    {"title": "Cat Bot Stocks", "emoji": "📈"},
    {"title": "PackOrRain Event [ended]", "emoji": "🔥"},
    {"title": "200,000 servers giveaway [ended]", "emoji": "insane"},
    {"title": "Cat Bot's 4th Birthday!", "emoji": "b_babycat"},
    {"title": "Cat Bot Plush (really)", "emoji": "📦"},
]

achs = [
    ["cat?", "startswith", "???"],
    ["catn", "exact", "catn"],
    ["cat!coupon jr0f-pzka", "exact", "coupon_user"],
    ["pineapple", "exact", "pineapple"],
    ["cat!lia_is_cute", "exact", "nerd"],
    ["i read help", "exact", "patient_reader"],
    ["lol_i_have_dmed_the_cat_bot_and_got_an_ach", "exact", "dm"],
    ["dog", "exact", "not_quite"],
    ["egril", "exact", "egril"],
    ["-.-. .- -", "exact", "morse_cat"],
    ["tac", "exact", "reverse"],
    ["cat!n4lltvuCOKe2iuDCmc6JsU7Jmg4vmFBj8G8l5xvoDHmCoIJMcxkeXZObR6HbIV6", "veryexact", "dataminer"],
]

reactions = [
    ["v1;", "custom", "why_v1"],
    ["proglet", "custom", "professor_cat"],
    ["xnopyt", "custom", "vanish"],
    ["silly", "custom", "sillycat"],
    ["indev", "vanilla", "🐸"],
    ["bleh", "custom", "blepcat"],
    ["blep", "custom", "blepcat"],
]

responses = [
    [
        "cellua good",
        "in",
        ".".join([str(random.randint(2, 254)) for _ in range(4)]),
    ],
    [
        "https://tenor.com/view/this-cat-i-have-hired-this-cat-to-stare-at-you-hired-cat-cat-stare-gif-26392360",
        "exact",
        "https://tenor.com/view/cat-staring-cat-gif-16983064494644320763",
    ],
]

cat_translations = [
    "mace",
    "katu",
    "kot",
    "koshka",
    "macka",
    "gat",
    "gata",
    "kocka",
    "kat",
    "poes",
    "kass",
    "kissa",
    "chat",
    "chatte",
    "gato",
    "katze",
    "gata",
    "macska",
    "kottur",
    "gatto",
    "getta",
    "kakis",
    "kate",
    "qattus",
    "qattusa",
    "katt",
    "kit",
    "kishka",
    "cath",
    "qitta",
    "katu",
    "pisik",
    "biral",
    "kyaung",
    "mao",
    "pusa",
    "kata",
    "billi",
    "kucing",
    "neko",
    "bekku",
    "mysyq",
    "chhma",
    "goyangi",
    "pucha",
    "manjar",
    "muur",
    "biralo",
    "gorbeh",
    "punai",
    "pilli",
    "kedi",
    "mushuk",
    "meo",
    "demat",
    "nwamba",
    "jangwe",
    "adure",
    "katsi",
    "bisad",
    "paka",
    "ikati",
    "ologbo",
    "wesa",
    "popoki",
    "piqtuq",
    "negeru",
    "poti",
    "mosi",
    "michi",
    "pusi",
    "oratii",
]

illegal = [
    "bk",
    "fq",
    "jc",
    "jt",
    "mj",
    "qh",
    "qx",
    "vj",
    "wz",
    "zh",
    "bq",
    "fv",
    "jd",
    "jv",
    "mq",
    "qj",
    "qy",
    "vk",
    "xb",
    "zj",
    "bx",
    "fx",
    "jf",
    "jw",
    "mx",
    "qk",
    "qz",
    "vm",
    "xg",
    "zn",
    "cb",
    "fz",
    "jg",
    "jx",
    "mz",
    "ql",
    "sx",
    "vn",
    "xj",
    "zq",
    "cf",
    "gq",
    "jh",
    "jy",
    "pq",
    "qm",
    "sz",
    "vp",
    "xk",
    "zr",
    "cg",
    "gv",
    "jk",
    "jz",
    "pv",
    "qn",
    "tq",
    "vq",
    "xv",
    "zs",
    "cj",
    "gx",
    "jl",
    "kq",
    "px",
    "qo",
    "tx",
    "vt",
    "xz",
    "zx",
    "cp",
    "hk",
    "jm",
    "kv",
    "qb",
    "qp",
    "vb",
    "vw",
    "yq",
    "cv",
    "hv",
    "jn",
    "kx",
    "qc",
    "qr",
    "vc",
    "vx",
    "yv",
    "cw",
    "hx",
    "jp",
    "kz",
    "qd",
    "qs",
    "vd",
    "vz",
    "yz",
    "cx",
    "hz",
    "jq",
    "lq",
    "qe",
    "qt",
    "vf",
    "wq",
    "zb",
    "dx",
    "iy",
    "jr",
    "lx",
    "qf",
    "qv",
    "vg",
    "wv",
    "zc",
    "fk",
    "jb",
    "js",
    "mg",
    "qg",
    "qw",
    "vh",
    "wx",
    "zg",
]


def pick_random_pack_tier() -> str:
    """Return a pack name from PACK_TIER_WEIGHTS (weighted by config)."""
    names = list(PACK_TIER_WEIGHTS.keys())
    weights = [PACK_TIER_WEIGHTS[n] for n in names]
    return random.choices(names, weights=weights, k=1)[0]


def grant_bonus_pack(user: Profile) -> tuple[str, str]:
    """Award one random-tier pack to `user` (does NOT save). Returns
    (pack_name, description_line) so the caller can fold the description into
    whatever embed it's already building."""
    pack_name = pick_random_pack_tier()
    user[f"pack_{pack_name.lower()}"] += 1
    desc = f"{get_emoji(pack_name.lower() + 'pack')} Bonus: a **{pack_name}** pack! Open with /packs."
    return pack_name, desc


# Per-tier color for the bonus-pack-drop embed shown on a lucky catch. The
# higher the tier, the more dramatic the framing (see BONUS_PACK_VIBES).
BONUS_PACK_COLORS = {
    "Wooden": 0x8B5A2B,
    "Stone": 0x9E9E9E,
    "Bronze": 0xCD7F32,
    "Silver": 0xC0C0C0,
    "Gold": 0xFFD700,
    "Platinum": 0xE5E4E2,
    "Diamond": 0x66E0FF,
    "Celestial": 0xC084FC,
}

# Random opener flavor text — picked uniformly. Keep these short, lowercase,
# and in the bot's voice. They print as a small `-#` subtitle so they don't
# overpower the pack name.
BONUS_PACK_OPENERS = [
    "the cat dropped something on its way out",
    "something fell out of the cat's hat",
    "a wild pack appeared",
    "you found it tucked under the cat",
    "the cat left a tip",
    "📦? where did THAT come from?",
    "the universe is feeling generous",
    "you spotted something glinting in the grass",
    "a pack materialized out of thin air",
    "the cat winked at you and left this behind",
    "what's this???",
    "🐈 cat-shaped luck",
]

# Per-tier "vibe" line — the more dramatic the tier, the louder it gets.
# Each value is a list and one is picked at random for variety.
BONUS_PACK_VIBES = {
    "Wooden": ["neat.", "cool.", "okay, take it.", "humble beginnings."],
    "Stone": ["nice.", "not bad.", "respectable."],
    "Bronze": ["oooh.", "bronze, fancy.", "you're moving up."],
    "Silver": ["**ohhhh**.", "**silver?** lookin' good.", "**shiny.**"],
    "Gold": ["**WAIT — gold?**", "**🌟 GOLD! 🌟**", "**that's a good pack.**"],
    "Platinum": [
        "**PLATINUM?! the cat is showing off.**",
        "**💠 PLATINUM PACK 💠 you lucky thing**",
        "**you should buy a lottery ticket.**",
    ],
    "Diamond": [
        "**💎💎💎 DIAMOND DROP 💎💎💎**",
        "**A DIAMOND PACK?!?! someone screenshot this**",
        "**the cat has chosen you. a DIAMOND PACK.**",
    ],
    "Celestial": [
        "**🌌 CELESTIAL PACK 🌌 — what the *heck*?!**",
        "**the cat gods themselves have blessed you. CELESTIAL.**",
        "**🌠 you are the chosen one. 🌠 (celestial pack)**",
    ],
}


def build_bonus_pack_embed(user: Profile, pack_name: str) -> discord.Embed:
    """Build the spectacular drop embed shown on a lucky catch. The catch
    handler attaches this to the catch confirmation message."""
    color = BONUS_PACK_COLORS.get(pack_name, Colors.brown)
    opener = random.choice(BONUS_PACK_OPENERS)
    vibe = random.choice(BONUS_PACK_VIBES.get(pack_name, ["neat."]))
    pack_emoji = get_emoji(pack_name.lower() + "pack")
    new_count = user[f"pack_{pack_name.lower()}"]
    description = (
        f"-# {opener}\n"
        f"# {pack_emoji} {pack_name} Pack\n"
        f"{vibe}\n"
        f"-# you now have {new_count:,} of these — open with /packs"
    )
    return discord.Embed(description=description, color=color)


# Distinct opener/vibe pools for the level-up bonus pack embed. Same color
# palette as the catch drop (tier-meaningful) but different copy so players
# can tell a "lucky catch drop" apart from a "battlepass level reward".
LEVELUP_PACK_OPENERS = [
    "stashed inside the level reward",
    "and one more thing...",
    "the battlepass tossed in a bonus",
    "wait, there's more",
    "level-up loot includes...",
    "the cat is in a good mood today",
    "a little something extra for your trouble",
    "tucked behind the level-up confetti",
    "🎉 surprise sidekick",
    "and as if that wasn't enough...",
]

LEVELUP_PACK_VIBES = {
    "Wooden": ["a humble bonus.", "every bit helps.", "extra packs are good packs."],
    "Stone": ["solid bonus.", "respectable side dish.", "stone-cold extra."],
    "Bronze": ["nice side-dish.", "bronze bonus, bronzy.", "extra clinkin' loot."],
    "Silver": ["**a SILVER bonus?!**", "**shiny side reward.**", "**now we're talking.**"],
    "Gold": ["**a GOLDEN bonus!**", "**🌟 GOLD side reward 🌟**", "**big level-up energy.**"],
    "Platinum": [
        "**PLATINUM bonus pack 💠 — what a level**",
        "**this is what 'extra' should look like**",
        "**🌟 platinum side reward 🌟**",
    ],
    "Diamond": [
        "**💎 DIAMOND BONUS 💎 incredible level reward!**",
        "**a bonus DIAMOND pack — what?!**",
        "**this level was worth every catch.**",
    ],
    "Celestial": [
        "**🌌 CELESTIAL bonus pack 🌌 — the cat gods are smiling**",
        "**a CELESTIAL bonus on top of the level reward?? lucky.**",
        "**🌠 chosen one. (celestial bonus pack)**",
    ],
}


def build_levelup_pack_embed(user: Profile, pack_name: str) -> discord.Embed:
    """Build the bonus-pack embed shown alongside a battlepass level-up.
    Same color palette as the catch drop embed but distinct copy so the two
    sources are recognizable side by side."""
    color = BONUS_PACK_COLORS.get(pack_name, Colors.brown)
    opener = random.choice(LEVELUP_PACK_OPENERS)
    vibe = random.choice(LEVELUP_PACK_VIBES.get(pack_name, ["a bonus pack."]))
    pack_emoji = get_emoji(pack_name.lower() + "pack")
    new_count = user[f"pack_{pack_name.lower()}"]
    description = (
        f"-# {opener}\n"
        f"# {pack_emoji} +1 {pack_name} Pack\n"
        f"{vibe}\n"
        f"-# you now have {new_count:,} — open with /packs"
    )
    return discord.Embed(description=description, color=color)


async def grant_achievement_xp(user: Profile, amount: int) -> list[discord.Embed]:
    """Add achievement XP to user.progress and roll forward battlepass levels.

    Mirrors the level-up loop in `progress()` but is decoupled from the quest
    system. Returns the level-up embeds so the caller can send them alongside
    the achievement-unlock embed.
    """
    if amount <= 0:
        return []

    # New profiles default to season=0, which isn't in battlepass.json (seasons
    # start at 1). progress() routes through refresh_quests() which auto-bumps
    # season; we don't, so skip the XP grant rather than KeyError.
    if str(user.season) not in config.battle["seasons"]:
        return []
    season_levels = config.battle["seasons"][str(user.season)]
    if user.battlepass >= len(season_levels):
        level_data = {"xp": 1500, "reward": "Stone", "amount": 1}
    else:
        level_data = season_levels[user.battlepass]

    current_xp = user.progress + amount
    if current_xp < level_data["xp"]:
        user.progress = current_xp
        await user.save()
        return []

    embeds: list[discord.Embed] = []
    active_level_data = level_data
    xp_progress = current_xp
    while xp_progress >= active_level_data["xp"]:
        user.battlepass += 1
        xp_progress -= active_level_data["xp"]
        user.progress = xp_progress
        if active_level_data["reward"] in cattypes:
            user[f"cat_{active_level_data['reward']}"] += active_level_data["amount"]
        elif active_level_data["reward"] == "Rain":
            user.rain_minutes += active_level_data["amount"]
        else:
            user[f"pack_{active_level_data['reward'].lower()}"] += 1
        bonus_pack_name, _ = grant_bonus_pack(user)
        await user.save()
        if active_level_data["reward"] in cattypes:
            await mark_discovered(user, active_level_data["reward"])

        if active_level_data["reward"] == "Rain":
            description = f"You got ☔ {active_level_data['amount']} rain minutes!"
        elif active_level_data["reward"] in cattypes:
            description = (
                f"You got {get_emoji(active_level_data['reward'].lower() + 'cat')} {active_level_data['amount']} {active_level_data['reward']}!"
            )
        else:
            description = (
                f"You got a {get_emoji(active_level_data['reward'].lower() + 'pack')} {active_level_data['reward']} pack! Do /packs to open it!"
            )
        embeds.append(
            discord.Embed(
                title=f"Level {user.battlepass} Complete!",
                description=description,
                color=Colors.yellow,
            )
        )
        embeds.append(build_levelup_pack_embed(user, bonus_pack_name))

        if user.battlepass >= len(season_levels):
            active_level_data = {"xp": 1500, "reward": "Stone", "amount": 1}
        else:
            active_level_data = season_levels[user.battlepass]

    return embeds


# Casino quest bitmask. The "casino" extra-slot quest requires playing 3
# different casino games out of the four. We track which games have been
# played via casino_progress_temp; each bit set = that game contributed once.
CASINO_GAME_BITS = {"slots": 1, "roulette": 2, "pig": 4, "cookieclicker": 8}


async def progress_casino_quest(message, user: Profile, game_id: str) -> None:
    """Advance the casino extra-slot quest if `game_id` is a new game for
    this cycle. Does nothing if the quest isn't active or already complete."""
    if user.extra_quest != "casino" or user.extra_cooldown != 0:
        return
    bit = CASINO_GAME_BITS.get(game_id)
    if not bit:
        return
    if user.casino_progress_temp & bit:
        return
    user.casino_progress_temp |= bit
    await user.save()
    await progress(message, user, "casino")


async def grant_catnip_levelup_xp(user: Profile) -> list[discord.Embed]:
    """+100 XP per catnip level-up, capped at 1000 total per season.
    Tracked via profile.catnip_xp_awarded so re-levelling doesn't re-pay."""
    cap = 1000
    if user.catnip_xp_awarded >= cap:
        return []
    grant = min(100, cap - user.catnip_xp_awarded)
    user.catnip_xp_awarded += grant
    await user.save()
    return await grant_achievement_xp(user, grant)


async def grant_first_catch_of_day_xp(user: Profile) -> list[discord.Embed]:
    """+50 XP on the first catch of each UTC day. Caller decides whether to
    call (uses the bool returned by update_daily_catch_streak)."""
    return await grant_achievement_xp(user, 50)


async def grant_catch_streak_xp(user: Profile) -> list[discord.Embed]:
    """+20 XP every 10 consecutive successful catches. Increments
    profile.catch_streak; awards when it crosses a multiple of 10."""
    user.catch_streak += 1
    await user.save()
    if user.catch_streak % 10 == 0:
        return await grant_achievement_xp(user, 20)
    return []


# this is some common code which is run whether someone gets an achievement
async def achemb(message, ach_id, send_type, author_string=None):
    if not author_string:
        try:
            author_string = message.author
        except Exception:
            author_string = message.user
    author = author_string.id

    if not message.guild:
        return

    profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=author)

    # Use the JSONB-aware helpers. unlock_ach also keeps the legacy boolean
    # column in sync for old code paths that read profile.<ach_id> directly.
    if not profile.unlock_ach(ach_id):
        return
    await profile.save()
    logging.debug("Achievement unlocked: %s", ach_id)
    ach_data = ach_list[ach_id]
    desc = ach_data["description"]
    if ach_id == "dataminer":
        desc = "Your head hurts -- you seem to have forgotten what you just did to get this."

    ach_xp = int(ach_data.get("xp", 0))
    xp_suffix = f" • +{ach_xp} XP" if ach_xp else ""

    if ach_id != "thanksforplaying":
        embed = (
            discord.Embed(title=ach_data["title"], description=desc, color=Colors.green)
            .set_author(
                name="Achievement get!",
                icon_url="https://wsrv.nl/?url=raw.githubusercontent.com/staring-cat/emojis/main/ach.png",
            )
            .set_footer(text=f"Unlocked by {author_string.name}{xp_suffix}")
        )
    else:
        embed = (
            discord.Embed(
                title="Catnip Addict",
                description="Uncover the mafia's truth\nThanks for playing! ✨",
                color=Colors.demonic,
            )
            .set_author(
                name="Demonic achievement unlocked! 🌟",
                icon_url="https://wsrv.nl/?url=raw.githubusercontent.com/staring-cat/emojis/main/demonic_ach.png",
            )
            .set_footer(text=f"Congrats to {author_string.name}!!{xp_suffix}")
        )

        embed2 = (
            discord.Embed(
                title="Catnip Addict",
                description="Uncover the mafia's truth\nThanks for playing! ✨",
                color=Colors.yellow,
            )
            .set_author(
                name="Demonic achievement unlocked! 🌟",
                icon_url="https://wsrv.nl/?url=raw.githubusercontent.com/staring-cat/emojis/main/demonic_ach.png",
            )
            .set_footer(text=f"Congrats to {author_string.name}!!{xp_suffix}")
        )

    result = None
    server = None
    do = False
    try:
        server = await Server.get_or_create(server_id=message.guild.id)
        do = not server.mute_achievements and await check_channel_setupped(server, message.channel)
        if send_type == "ephemeral":
            result = await message.followup.send(embed=embed, ephemeral=True)
        if send_type == "reply" and do:
            result = await message.reply(embed=embed)
        if send_type == "send" and do:
            result = await message.channel.send(embed=embed)
        if send_type == "followup":
            result = await message.followup.send(embed=embed, ephemeral=not do)
        if send_type == "response":
            result = await message.response.send_message(embed=embed, ephemeral=not do)
    except Exception:
        logging.exception("achemb send failed for %s (send_type=%s)", ach_id, send_type)

    # XP + level-up sit in their own try so a Discord send failure here doesn't
    # silently swallow the level-up notification (and we can see the traceback).
    try:
        level_up_embeds = await grant_achievement_xp(profile, ach_xp)
        if level_up_embeds and do:
            await message.channel.send(f"<@{author}>", embeds=level_up_embeds)
    except Exception:
        logging.exception("achemb XP/level-up failed for %s", ach_id)

    # Also advance the "Get an achievement" misc-quest if the user has it
    # active. This is a separate XP grant path from the per-ach `xp` value.
    try:
        await progress(message, profile, "achievement")
    except Exception:
        logging.exception("achemb misc-quest progress failed for %s", ach_id)

    try:
        await finale(message, profile)
    except Exception:
        logging.exception("achemb finale failed for %s", ach_id)

    if result:
        if ach_id == "thanksforplaying":
            await asyncio.sleep(2)
            await result.edit(embed=embed2)
            await asyncio.sleep(2)
            await result.edit(embed=embed)
            await asyncio.sleep(2)
            await result.edit(embed=embed2)
            await asyncio.sleep(2)
            await result.edit(embed=embed)

        if server.auto_delete_achievements:
            await result.delete(delay=10)
        elif ach_id == "curious":
            await result.delete(delay=30)


async def generate_quest(user: Profile, quest_type: str):
    while True:
        quest = random.choice(list(config.battle["quests"][quest_type].keys()))
        if quest in ["slots", "reminder", "plush"]:
            # removed quests
            continue
        elif quest == "define" and not config.WORDNIK_API_KEY:
            # /define is conditionally registered on WORDNIK_API_KEY; without
            # the key the command doesn't exist, so the quest is unwinnable.
            continue
        elif quest == "catnip_session" and user.catnip_level <= 0:
            # catnip quest only makes sense if the user has unlocked catnip
            continue
        elif quest == "prism":
            total_count = await Prism.count("guild_id = $1", user.guild_id)
            user_count = await Prism.count("guild_id = $1 AND user_id = $2", user.guild_id, user.user_id)
            global_boost = PRISM_BOOST_GLOBAL_COEF * math.log(2 * total_count + 1)
            prism_boost = global_boost + PRISM_BOOST_USER_COEF * math.log(2 * user_count + 1)
            if prism_boost < PRISM_BOOST_FLOOR:
                continue
        elif quest == "news":
            global_user = await User.get_or_create(user_id=user.user_id)
            if len(news_list) <= len(global_user.news_state.strip()) and "0" not in global_user.news_state.strip()[-4:]:
                continue
        elif quest == "achievement":
            unlocked = 0
            for k in ach_names:
                if user.has_ach(k) and ach_list[k]["category"] != "Hidden":
                    unlocked += 1
            if unlocked > 30:
                continue
        break

    quest_data = config.battle["quests"][quest_type][quest]
    if quest_type == "vote":
        user.vote_reward = random.randint(quest_data["xp_min"] // 10, quest_data["xp_max"] // 10) * 10
        user.vote_cooldown = 0
    elif quest_type == "catch":
        user.catch_reward = random.randint(quest_data["xp_min"] // 10, quest_data["xp_max"] // 10) * 10
        user.catch_quest = quest
        user.catch_cooldown = 0
    elif quest_type == "misc":
        user.misc_reward = random.randint(quest_data["xp_min"] // 10, quest_data["xp_max"] // 10) * 10
        user.misc_quest = quest
        user.misc_cooldown = 0
    elif quest_type == "extra":
        # sacrifice's XP is decided per-cat at completion time, so pre-rolled
        # reward is 0 here — progress() reads the per-cat amount instead.
        if quest_data.get("dynamic_reward"):
            user.extra_reward = 0
        else:
            user.extra_reward = random.randint(quest_data["xp_min"] // 10, quest_data["xp_max"] // 10) * 10
        user.extra_quest = quest
        user.extra_cooldown = 0
        user.casino_progress_temp = 0
        user.gift3_recipients = ""
    elif quest_type == "challenge":
        user.challenge_reward = random.randint(quest_data["xp_min"] // 10, quest_data["xp_max"] // 10) * 10
        user.challenge_quest = quest
        user.challenge_cooldown = 0
    await user.save()


async def refresh_quests(user):
    await user.refresh_from_db()
    # season 1 = May 2026 (when this self-hosted instance went live).
    # Each calendar month is a new season; rollover happens on the 1st.
    start_date = datetime.datetime(2026, 4, 1)
    current_date = discord.utils.utcnow() + datetime.timedelta(hours=4)
    full_months_passed = (current_date.year - start_date.year) * 12 + (current_date.month - start_date.month)
    if current_date.day < start_date.day:
        full_months_passed -= 1
    if user.season != full_months_passed:
        user.bp_history = user.bp_history + f"{user.season},{user.battlepass},{user.progress};"
        user.battlepass = 0
        user.progress = 0

        user.catch_quest = ""
        user.catch_progress = 0
        user.catch_cooldown = 1
        user.catch_reward = 0

        user.misc_quest = ""
        user.misc_progress = 0
        user.misc_cooldown = 1
        user.misc_reward = 0

        user.extra_quest = ""
        user.extra_progress = 0
        user.extra_cooldown = 1
        user.extra_reward = 0
        user.casino_progress_temp = 0
        user.gift3_recipients = ""
        user.catnip_xp_awarded = 0

        user.challenge_quest = ""
        user.challenge_progress = 0
        user.challenge_cooldown = 1
        user.challenge_reward = 0

        user.season = full_months_passed
        await user.save()
    # If a saved quest was retired from the config, force a re-roll so /battlepass doesn't KeyError.
    if user.catch_quest and user.catch_quest not in config.battle["quests"]["catch"]:
        user.catch_quest = ""
        user.catch_progress = 0
        user.catch_cooldown = 1
        user.catch_reward = 0
    if user.misc_quest and user.misc_quest not in config.battle["quests"]["misc"]:
        user.misc_quest = ""
        user.misc_progress = 0
        user.misc_cooldown = 1
        user.misc_reward = 0
    # Evict `define` if the operator never set WORDNIK_API_KEY; the command
    # isn't registered in that case so the quest can never progress. Treat
    # like a retired quest: zero progress + cooldown=1 forces a re-roll.
    if user.misc_quest == "define" and not config.WORDNIK_API_KEY:
        user.misc_quest = ""
        user.misc_progress = 0
        user.misc_cooldown = 1
        user.misc_reward = 0
    if user.extra_quest and user.extra_quest not in config.battle["quests"]["extra"]:
        user.extra_quest = ""
        user.extra_progress = 0
        user.extra_cooldown = 1
        user.extra_reward = 0
        user.casino_progress_temp = 0
        user.gift3_recipients = ""
    if user.challenge_quest and user.challenge_quest not in config.battle["quests"]["challenge"]:
        user.challenge_quest = ""
        user.challenge_progress = 0
        user.challenge_cooldown = 1
        user.challenge_reward = 0
    if QUEST_COOLDOWN < user.vote_cooldown + QUEST_COOLDOWN < time.time():
        await generate_quest(user, "vote")
    if QUEST_COOLDOWN < user.catch_cooldown + QUEST_COOLDOWN < time.time():
        await generate_quest(user, "catch")
    if QUEST_COOLDOWN < user.misc_cooldown + QUEST_COOLDOWN < time.time():
        await generate_quest(user, "misc")
    if QUEST_COOLDOWN < user.extra_cooldown + QUEST_COOLDOWN < time.time():
        await generate_quest(user, "extra")
    # Challenge slot was added after the original schema, so existing profiles
    # have challenge_cooldown=0 (which the inequality above misses) and an
    # empty challenge_quest. Treat empty as "needs first generation" so the
    # /battlepass UI never sees an unset slot.
    if not user.challenge_quest or QUEST_COOLDOWN < user.challenge_cooldown + QUEST_COOLDOWN < time.time():
        await generate_quest(user, "challenge")


async def multi_progress(message: discord.Message | discord.Interaction, user: Profile, quests: list[str], is_belated: Optional[bool] = False):
    await refresh_quests(user)
    await user.refresh_from_db()
    for quest in quests:
        return_user = await progress(message, user, quest, is_belated, False)
        if return_user:
            user = return_user


async def progress(
    message: discord.Message | discord.Interaction, user: Profile, quest: str, is_belated: Optional[bool] = False, refetch: bool = True
) -> Profile:
    if refetch:
        await refresh_quests(user)
        await user.refresh_from_db()

    # progress
    quest_complete = False
    if user.catch_quest == quest:
        if user.catch_cooldown != 0:
            return user
        quest_data = config.battle["quests"]["catch"][quest]
        user.catch_progress += 1
        if user.catch_progress >= quest_data["progress"]:
            quest_complete = True
            user.catch_cooldown = int(time.time())
            current_xp = user.progress + user.catch_reward
            user.catch_progress = 0
            user.reminder_catch = 1
    elif quest == "vote":
        if user.vote_cooldown != 0:
            return user
        quest_data = config.battle["quests"]["vote"][quest]
        global_user = await User.get_or_create(user_id=user.user_id)
        user.vote_cooldown = global_user.vote_time_topgg

        # Weekdays 0 Mon - 6 Sun
        # double vote xp rewards if Friday, Saturday or Sunday
        voted_at = datetime.datetime.fromtimestamp(global_user.vote_time_topgg, tz=datetime.timezone.utc)
        if voted_at.weekday() >= 4:
            user.vote_reward *= 2

        streak_data = get_streak_reward(global_user.daily_catch_streak)
        if streak_data["reward"]:
            user[f"pack_{streak_data['reward']}"] += 1

        current_xp = user.progress + user.vote_reward
        quest_complete = True
    elif user.misc_quest == quest:
        if user.misc_cooldown != 0:
            return user
        quest_data = config.battle["quests"]["misc"][quest]
        user.misc_progress += 1
        if user.misc_progress >= quest_data["progress"]:
            quest_complete = True
            user.misc_cooldown = int(time.time())
            current_xp = user.progress + user.misc_reward
            user.misc_progress = 0
            user.reminder_misc = 1
    elif user.extra_quest == quest:
        if user.extra_cooldown != 0:
            return user
        quest_data = config.battle["quests"]["extra"][quest]
        user.extra_progress += 1
        if user.extra_progress >= quest_data["progress"]:
            quest_complete = True
            user.extra_cooldown = int(time.time())
            current_xp = user.progress + user.extra_reward
            user.extra_progress = 0
            user.casino_progress_temp = 0
            user.gift3_recipients = ""
    elif user.challenge_quest == quest:
        if user.challenge_cooldown != 0:
            return user
        quest_data = config.battle["quests"]["challenge"][quest]
        user.challenge_progress += 1
        if user.challenge_progress >= quest_data["progress"]:
            quest_complete = True
            user.challenge_cooldown = int(time.time())
            current_xp = user.progress + user.challenge_reward
            user.challenge_progress = 0
            user.reminder_challenge = 1
            if not user.has_ach("challenge_first"):
                # Fire the first-completion ach BEFORE the level-up flow so it
                # lands inline with the other catch-context embeds.
                await achemb(message, "challenge_first", "send")
    else:
        return user

    await user.save()
    if not quest_complete:
        return user

    user.quests_completed += 1

    logging.debug("Quest complete: %s", quest)
    old_xp = user.progress
    level_complete_embeds = []
    if user.battlepass >= len(config.battle["seasons"][str(user.season)]):
        level_data = {"xp": 1500, "reward": "Stone", "amount": 1}
        level_text = "Extra Rewards"
    else:
        level_data = config.battle["seasons"][str(user.season)][user.battlepass]
        level_text = f"Level {user.battlepass + 1}"

    if current_xp >= level_data["xp"]:
        logging.debug("Level complete %d", user.battlepass)
        xp_progress = current_xp
        active_level_data = level_data
        while xp_progress >= active_level_data["xp"]:
            user.battlepass += 1
            xp_progress -= active_level_data["xp"]
            user.progress = xp_progress
            cat_emojis = None
            if active_level_data["reward"] in cattypes:
                user[f"cat_{active_level_data['reward']}"] += active_level_data["amount"]
            elif active_level_data["reward"] == "Rain":
                user.rain_minutes += active_level_data["amount"]
            else:
                user[f"pack_{active_level_data['reward'].lower()}"] += 1
            bonus_pack_name, _ = grant_bonus_pack(user)
            await user.save()
            if active_level_data["reward"] in cattypes:
                await mark_discovered(user, active_level_data["reward"])

            if not cat_emojis:
                if active_level_data["reward"] == "Rain":
                    description = f"You got ☔ {active_level_data['amount']} rain minutes!"
                elif active_level_data["reward"] in cattypes:
                    description = (
                        f"You got {get_emoji(active_level_data['reward'].lower() + 'cat')} {active_level_data['amount']} {active_level_data['reward']}!"
                    )
                else:
                    description = (
                        f"You got a {get_emoji(active_level_data['reward'].lower() + 'pack')} {active_level_data['reward']} pack! Do /packs to open it!"
                    )
                title = f"Level {user.battlepass} Complete!"
            else:
                description = f"You got {cat_emojis}!"
                title = "Bonus Complete!"
            embed_level_up = discord.Embed(title=title, description=description, color=Colors.yellow)
            level_complete_embeds.append(embed_level_up)
            level_complete_embeds.append(build_levelup_pack_embed(user, bonus_pack_name))

            if user.battlepass >= len(config.battle["seasons"][str(user.season)]):
                active_level_data = {"xp": 1500, "reward": "Stone", "amount": 1}
                new_level_text = "Extra Rewards"
            else:
                active_level_data = config.battle["seasons"][str(user.season)][user.battlepass]
                new_level_text = f"Level {user.battlepass + 1}"

        embed_progress = await progress_embed(
            message,
            user,
            active_level_data,
            xp_progress,
            0,
            quest_data,
            current_xp - old_xp,
            new_level_text,
        )

    else:
        user.progress = current_xp
        await user.save()
        embed_progress = await progress_embed(
            message,
            user,
            level_data,
            current_xp,
            old_xp,
            quest_data,
            current_xp - old_xp,
            level_text,
        )

    if is_belated:
        embed_progress.set_footer(text="For catching within 3 seconds")

    server = await Server.get_or_create(server_id=message.guild.id)
    if await check_channel_setupped(server, message.channel):
        if level_complete_embeds:
            await message.channel.send(f"<@{user.user_id}>", embeds=level_complete_embeds + [embed_progress])
        else:
            await message.channel.send(f"<@{user.user_id}>", embed=embed_progress)

    return user


async def progress_embed(message, user, level_data, current_xp, old_xp, quest_data, diff, level_text) -> discord.Embed:
    percentage_before = int(old_xp / level_data["xp"] * 10)
    percentage_after = int(current_xp / level_data["xp"] * 10)
    percenteage_left = 10 - percentage_after

    progress_line = get_emoji("staring_square") * percentage_before + "🟨" * (percentage_after - percentage_before) + "⬛" * percenteage_left

    title = quest_data["title"] if "top.gg" not in quest_data["title"] else "Vote on Top.gg"

    if level_data["reward"] == "Rain":
        reward_text = get_emoji(str(level_data["amount"]) + "rain")
    elif level_data["reward"] == "random cats":
        reward_text = f"{level_data['amount']}x ❓"
    elif level_data["reward"] in cattypes:
        reward_text = f"{level_data['amount']}x {get_emoji(level_data['reward'].lower() + 'cat')}"
    else:
        reward_text = get_emoji(level_data["reward"].lower() + "pack")

    global_user = await User.get_or_create(user_id=user.user_id)
    streak_data = get_streak_reward(global_user.daily_catch_streak)
    if streak_data["reward"] and "top.gg" in quest_data["title"]:
        streak_reward = f"\n🔥 **Streak Bonus!** +1 {streak_data['emoji']} {streak_data['reward'].capitalize()} pack"
    else:
        streak_reward = ""

    return discord.Embed(
        title=f"✅ {title}",
        description=f"{progress_line} {reward_text}\n{current_xp}/{level_data['xp']} XP (+{diff}){streak_reward}",
        color=Colors.green,
    ).set_author(name="/battlepass " + level_text)


def get_streak_reward(streak):
    if streak % 5 != 0 or streak in [0, 5]:
        return {"reward": None, "emoji": "⬛", "done_emoji": "🟦"}

    pack_type = "gold"
    # these honestly don't add that much value but feel like good milestones
    if streak % 100 == 0:
        pack_type = "diamond"
    elif streak % 25 == 0:
        pack_type = "platinum"

    return {"reward": pack_type, "emoji": get_emoji(f"{pack_type}pack"), "done_emoji": get_emoji(f"{pack_type}pack_claimed")}


# handle curious people clicking buttons
async def do_funny(message):
    await message.response.send_message(random.choice(funny), ephemeral=True)
    user = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    user.funny += 1
    await user.save()
    await achemb(message, "curious", "reply")
    if user.funny >= 50:
        await achemb(message, "its_not_working", "followup")


# not :eyes:
async def debt_cutscene(message, user):
    if user.debt_seen:
        return

    user.debt_seen = True
    await user.save()

    debt_msgs = [
        "**\\*BANG\\***",
        "Your door gets slammed open and multiple man in black suits enter your room.",
        "**???**: Hello, you have unpaid debts. You owe us money. We are here to liquidate all your assets.",
        "*(oh for fu)*",
        "**You**: pls dont",
        "**???**: oh okay then we will come back to you later.",
        "They leave the room.",
        "**You**: Oh god this is bad",
        "**You**: I know of a solution though!",
        "**You**: I heard you can gamble your debts away in the slots machine!",
    ]

    for debt_msg in debt_msgs:
        await asyncio.sleep(4)
        await message.followup.send(debt_msg, ephemeral=True)


# :eyes:
async def finale(message, user):
    if user.finale_seen:
        return

    # check ach req
    for k in ach_names:
        if not user.has_ach(k) and ach_list[k]["category"] != "Hidden":
            return

    user.finale_seen = True
    await user.save()
    try:
        author_string = message.author
    except Exception:
        author_string = message.user
    await asyncio.sleep(5)
    await message.channel.send("...")
    await asyncio.sleep(3)
    await message.channel.send("You...")
    await asyncio.sleep(3)
    await message.channel.send("...actually did it.")
    await asyncio.sleep(3)
    await message.channel.send(
        embed=discord.Embed(
            title="True Ending achieved!",
            description="You are finally free.",
            color=Colors.rose,
        )
        .set_author(
            name="All achievements complete!",
            icon_url="https://wsrv.nl/?url=raw.githubusercontent.com/milenakos/cat-bot/main/images/cat.png",
        )
        .set_footer(text=f"Congrats to {author_string}")
    )


# function to autocomplete cat_type choices for /givecat, and /forcespawn, which also allows more than 25 options
async def cat_type_autocomplete(interaction: discord.Interaction, current: str) -> list[discord.app_commands.Choice[str]]:
    return [discord.app_commands.Choice(name=choice, value=choice) for choice in cattypes if current.lower() in choice.lower()][:25]


# function to autocomplete /cat, it only shows the cats you have
async def cat_command_autocomplete(interaction: discord.Interaction, current: str) -> list[discord.app_commands.Choice[str]]:
    user = await Profile.get_or_create(guild_id=interaction.guild.id, user_id=interaction.user.id)
    return [discord.app_commands.Choice(name=choice, value=choice) for choice in cattypes if current.lower() in choice.lower() and user[f"cat_{choice}"] > 0][
        :25
    ]


async def lb_type_autocomplete(interaction: discord.Interaction, current: str) -> list[discord.app_commands.Choice[str]]:
    return [
        discord.app_commands.Choice(name=choice, value=choice)
        for choice in ["All"] + await cats_in_server(interaction.guild_id)
        if current.lower() in choice.lower()
    ][:25]


async def cats_in_server(guild_id):
    return [cat_type for cat_type in cattypes if (await Profile.count(f'guild_id = $1 AND "cat_{cat_type}" > 0 LIMIT 1', guild_id))]


# function to autocomplete cat_type choices for /gift, which shows only cats user has and how many of them they have
async def gift_autocomplete(interaction: discord.Interaction, current: str) -> list[discord.app_commands.Choice[str]]:
    user = await Profile.get_or_create(guild_id=interaction.guild.id, user_id=interaction.user.id)
    actual_user = await User.get_or_create(user_id=interaction.user.id)
    choices = []
    for choice in cattypes:
        if current.lower() in choice.lower() and user[f"cat_{choice}"] > 0:
            choices.append(discord.app_commands.Choice(name=f"{choice} (x{user[f'cat_{choice}']})", value=choice))
    if current.lower() in "rain" and actual_user.rain_minutes > 0:
        choices.append(discord.app_commands.Choice(name=f"Rain ({actual_user.rain_minutes} minutes)", value="rain"))
    for choice in pack_data:
        if user[f"pack_{choice['name'].lower()}"] > 0:
            pack_name = choice["name"]
            pack_amount = user[f"pack_{pack_name.lower()}"]
            choices.append(discord.app_commands.Choice(name=f"{pack_name} pack (x{pack_amount})", value=pack_name.lower()))
    return choices[:25]


# function to autocomplete achievement choice for /giveachievement, which also allows more than 25 options
async def ach_autocomplete(interaction: discord.Interaction, current: str) -> list[discord.app_commands.Choice[str]]:
    return [
        discord.app_commands.Choice(name=val["title"], value=key)
        for (key, val) in ach_list.items()
        if (alnum(current) in alnum(key) or alnum(current) in alnum(val["title"]))
    ][:25]


# converts string to lowercase alphanumeric characters only
def alnum(string):
    return "".join(item for item in string.lower() if item.isalnum())


async def _revive_dead_spawns_tick() -> int:
    """Scan for channels where the scheduled spawn time has passed but no
    cat is alive, and respawn each one. Returns the number of channels
    revived (for logging). Called from two places:

    1. The on_message-driven `background_loop`, as defense-in-depth.
    2. The standalone `_spawn_revival_loop` background task below, so empty
       channels don't have their overdue spawns stuck waiting for a message.

    `spawn_cat` is self-guarded against double-spawning, so duplicate calls
    here and from a still-alive scheduled task race cleanly.
    """
    counter = 0
    try:
        async for channel in Channel.limit(["channel_id"], "yet_to_spawn < $1 AND cat = 0", time.time(), refetch=False):
            counter += 1
            await spawn_cat(str(channel.channel_id))
            await asyncio.sleep(0.1)
    except Exception:
        logging.exception("spawn revival tick failed")
    return counter


async def _spawn_revival_loop():
    """Background revival ticker. Unlike `background_loop`, this is NOT
    on_message-driven — it runs on a fixed cadence so quiet channels (no
    chatter, no /commands) still get their overdue spawns respawned.

    Started once per process from `setup(bot2)`. Reload-safe via the
    `config.spawn_revival_task` handle; `setup` cancels any prior task
    before creating a new one to avoid duplicates after `cat!restart`.
    """
    while not bot.is_closed():
        try:
            await asyncio.sleep(SPAWN_REVIVAL_INTERVAL)
            await _revive_dead_spawns_tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("spawn revival loop iteration failed")


async def spawn_cat(ch_id, localcat=None, force_spawn=None):
    try:
        channel = await Channel.get_or_none(channel_id=int(ch_id))
        if not channel:
            raise Exception
    except Exception:
        return False
    if channel.cat or channel.yet_to_spawn > time.time() + 10:
        return False

    if not localcat:
        localcat = random.choices(cattypes, weights=type_dict.values())[0]
    icon = get_emoji(localcat.lower() + "cat")
    file = discord.File(
        f"images/spawn/{localcat.lower()}_cat.png",
    )
    channeley = bot.get_partial_messageable(int(ch_id))

    appearstring = '{emoji} {type} cat has appeared! Type "cat" to catch it!' if not channel.appear else channel.appear

    if int(ch_id) in temp_spawns_storage:
        return False

    temp_spawns_storage.append(int(ch_id))

    try:
        message_is_sus = await channeley.send(
            appearstring.replace("{emoji}", str(icon)).replace("{type}", localcat),
            file=file,
            allowed_mentions=discord.AllowedMentions.all(),
        )
    except discord.Forbidden:
        await channel.delete()
        temp_spawns_storage.remove(int(ch_id))
        return False
    except discord.NotFound:
        await channel.delete()
        temp_spawns_storage.remove(int(ch_id))
        return False
    except Exception:
        temp_spawns_storage.remove(int(ch_id))
        return False

    channel.cat = message_is_sus.id
    channel.yet_to_spawn = 0
    channel.forcespawned = bool(force_spawn)
    channel.cattype = localcat
    await channel.save()
    temp_spawns_storage.remove(int(ch_id))
    logging.debug("Cat spawned, forced: %s", bool(force_spawn))
    return True


async def wait_and_do_stock(stock):
    await asyncio.sleep(stock.end_time - time.time())
    if random.random() * 100 < stock.chance:
        allowed_tickers = {s["ticker"] for s in stock_data}
        if stock.ticker not in allowed_tickers:
            return
        stock_column = f'"stock_{stock.ticker.lower()}"'

        # payout
        await pool.execute(
            f"""WITH stock_holders_raw AS (
            SELECT id AS user_id, {stock_column} AS quantity
            FROM profile
            WHERE {stock_column} > 0
            UNION ALL
            SELECT user_id, quantity
            FROM "order"
            WHERE ticker = $4 AND type_buy = false
        ),
        stock_holders AS (
            SELECT user_id, SUM(quantity) AS quantity
            FROM stock_holders_raw
            GROUP BY user_id
        ),
        "updated" AS (
            UPDATE profile p
            SET coins = coins + sh.quantity * $1
            FROM stock_holders sh
            WHERE p.id = sh.user_id
            RETURNING p.id AS profile_id, sh.quantity * $1 AS coin_change
        )
        INSERT INTO portfoliohistory (user_id, time, type, ticker, quantity)
        SELECT profile_id, $2, $3, $4, coin_change
        FROM "updated";""",
            stock.amount,
            stock.end_time,
            "r",
            stock.ticker,
        )
    await refresh_stock_rewards(stock.ticker)


async def refresh_stock_rewards(ticker):
    stock = await Reward.get_or_create(ticker=ticker)
    day = 3600 * 24
    current_price = await get_stock_price(ticker)
    stock.active = False
    stock.start_time = time.time() + random.randint(3 * day, 7 * day)
    stock.end_time = stock.start_time + day * 2
    stock.chance = min(100, max(0, round(random.gauss(50, 10))))
    stock.amount = round(random.gauss(0, current_price / 4))
    stock.chance_hidden = random.randint(0, 100) < 25
    stock.amount_hidden = random.randint(0, 100) < 75
    await stock.save()


async def postpone_reminder(interaction):
    reminder_type = interaction.data["custom_id"]
    if reminder_type == "vote":
        user = await User.get_or_create(user_id=interaction.user.id)
        user.reminder_vote = int(time.time()) + 30 * 60
        await user.save()
    else:
        guild_id = reminder_type.split("_")[1]
        user = await Profile.get_or_create(guild_id=int(guild_id), user_id=interaction.user.id)
        if reminder_type.startswith("catch"):
            user.reminder_catch = int(time.time()) + 30 * 60
        elif reminder_type.startswith("challenge"):
            user.reminder_challenge = int(time.time()) + 30 * 60
        else:
            user.reminder_misc = int(time.time()) + 30 * 60
        await user.save()
    logging.debug("Reminder postponed: %s", reminder_type)
    await interaction.response.send_message(f"ok, i will remind you <t:{int(time.time()) + 30 * 60}:R>", ephemeral=True)


async def _safe_background_loop():
    """Wrapper so failures inside background_loop don't end up as silent
    'Task exception was never retrieved' warnings — they go through logging."""
    try:
        await background_loop()
    except Exception:
        logging.exception("background_loop failed")


# a loop for various maintenance which is ran every 5 minutes
async def background_loop():
    global pointlaugh_ratelimit, reactions_ratelimit, last_loop_time, loop_count, catchcooldown, temp_belated_storage, fakecooldown, last_vote_cursor
    pointlaugh_ratelimit = {}
    reactions_ratelimit = {}
    catchcooldown = {}
    fakecooldown = {}
    await bot.change_presence(activity=discord.CustomActivity(name=f"Catting in {len(bot.guilds):,} servers"))

    # temp_belated_storage cleanup
    # clean up anything older than 1 minute
    baseflake = discord.utils.time_snowflake(discord.utils.utcnow() - datetime.timedelta(minutes=1))
    for id in temp_belated_storage.copy().keys():
        if id < baseflake:
            del temp_belated_storage[id]

    if config.TOP_GG_MODERN_TOKEN:
        async with aiohttp.ClientSession() as session:
            try:
                if not config.MIN_SERVER_SEND or len(bot.guilds) > config.MIN_SERVER_SEND:
                    # send server count to top.gg
                    r = await session.post(
                        "https://top.gg/api/v1/projects/@me/metrics",
                        headers={"Authorization": f"Bearer {config.TOP_GG_MODERN_TOKEN}"},
                        json={"server_count": len(bot.guilds), "shard_count": len(bot.shards)},
                    )
                    r.close()

                # post commands to top.gg
                r = await session.post(
                    "https://top.gg/api/v1/projects/@me/commands",
                    headers={"Authorization": f"Bearer {config.TOP_GG_MODERN_TOKEN}"},
                    json=[command.to_dict(bot.tree) for command in bot.tree._get_all_commands(guild=None) if command.to_dict(bot.tree)["type"] == 1],
                )
                r.close()

                # fallback fetch votes
                if config.VOTING_ENABLED:
                    if last_vote_cursor:
                        suffix = "cursor=" + last_vote_cursor
                    else:
                        timestamp = discord.utils.utcnow() - datetime.timedelta(minutes=5)
                        suffix = "startDate=" + timestamp.replace(tzinfo=None).isoformat()
                    r = await session.get(
                        f"https://top.gg/api/v1/projects/@me/votes?{suffix}",
                        headers={"Authorization": f"Bearer {config.TOP_GG_MODERN_TOKEN}"},
                    )
                    data = await r.json()
                    r.close()

                    the_votes = data.get("data", [])
                    for vote_data in the_votes:
                        if not vote_data.get("created_at", 0) or not vote_data.get("platform_id", 0):
                            continue
                        created_at = datetime.datetime.fromisoformat(vote_data["created_at"]).timestamp()
                        vote_user = await User.get_or_create(user_id=int(vote_data["platform_id"]))
                        await do_vote(vote_user, created_at)

                    last_vote_cursor = data.get("cursor", "")
                    with open("cursor.txt", "w") as f:
                        f.write(last_vote_cursor)
                    logging.info(f"Fetched {len(the_votes)} votes, cursor {last_vote_cursor}")

            except Exception:
                logging.warning("Posting to top.gg failed.")

    # payout stock market rewards/set up future rewards
    for stock_info in stock_data:
        stock = await Reward.get_or_create(ticker=stock_info["ticker"])
        if stock and stock.active and stock.end_time < time.time() + 60 * 5:
            bot.loop.create_task(wait_and_do_stock(stock))
            continue
        if stock.start_time == 0 or stock.end_time == 0:
            await refresh_stock_rewards(stock.ticker)
            continue
        if stock and not stock.active and stock.start_time < time.time():
            stock.active = True
            await stock.save()

    # stock market maker tick — keeps the order book liquid on a small instance
    # by maintaining bot-owned bid/ask orders at the activity-derived fair price.
    try:
        await _run_stock_market_maker()
    except Exception:
        logging.exception("stock market maker tick failed")

    # cancel old orders
    async for order in Order.filter("time > 0 AND time < $1", time.time() - 3600 * 24 * 7):
        profile = await Profile.get_or_none(id=order.user_id)
        if profile:
            if order.type_buy:
                profile.coins += order.quantity * order.price
                await PortfolioHistory.create(user_id=profile.id, type="c", quantity=order.price * order.quantity, time=int(time.time()))
            else:
                profile[f"stock_{order.ticker.lower()}"] += order.quantity
                await PortfolioHistory.create(user_id=profile.id, type="C", quantity=order.quantity, time=int(time.time()), ticker=order.ticker)
            await profile.save()
        await order.delete()

    # auto-sell stocks of people inactive for over a week
    if False:
        async for profile in Profile.filter("last_ran_stocks < $1 AND last_ran_stocks != 0", time.time() - 3600 * 24 * 7):
            for stock in stock_data:
                ticker = stock["ticker"]
                quantity = profile[f"stock_{ticker.lower()}"]
                price = await get_stock_price(ticker)
                if quantity > 0:
                    curr_time = int(time.time())
                    order = await Order.create(user_id=profile.id, ticker=ticker, quantity=quantity, price=price, type_buy=False, time=curr_time)
                    await PortfolioHistory.create(user_id=profile.id, type="s", price=price, quantity=quantity, time=curr_time, ticker=ticker)
                    profile[f"stock_{ticker.lower()}"] = 0
                    await resolve_orders(order)
            await profile.save()

    # revive dead catch loops — defense in depth alongside _spawn_revival_loop.
    # This runs whenever background_loop fires (i.e. on message activity);
    # _spawn_revival_loop covers the quiet-channel case on a fixed cadence.
    counter = await _revive_dead_spawns_tick()
    logging.debug("Channels revived: %d", counter)

    # THIS IS CONSENTUAL AND TURNED OFF BY DEFAULT DONT BAN ME
    #
    # i wont go into the details of this because its a complicated mess which took me like solid 30 minutes of planning
    #
    # vote reminders
    reminder_count = 0
    start_time = int(time.time())
    if config.VOTING_ENABLED:
        while True:
            user = await User.collect(
                f"vote_time_topgg != 0 AND vote_time_topgg + 43200 < {start_time} AND reminder_vote != 0 AND reminder_vote < {start_time} "
                + 'AND EXISTS(SELECT 1 FROM profile WHERE profile.user_id = "user".user_id AND reminders_enabled = true) LIMIT 1',
            )
            if not user or not user[0]:
                break
            user = user[0]
            await asyncio.sleep(0.2)

            view = View(timeout=VIEW_TIMEOUT)
            button = Button(
                emoji=get_emoji("topgg"),
                label=random.choice(vote_button_texts),
                url="https://top.gg/bot/966695034340663367/vote",
            )
            view.add_item(button)

            button = Button(label="Postpone", custom_id="vote")
            button.callback = postpone_reminder
            view.add_item(button)

            try:
                user_dm = await fetch_dm_channel(user)
                await user_dm.send(
                    "You can vote now!" if user.daily_catch_streak < 10 else f"Vote now to keep your {user.daily_catch_streak} streak going!",
                    view=view,
                )
            except Exception:
                pass

            # no repeat reminers for now
            user.reminder_vote = 0
            reminder_count += 1
            await user.save()

    logging.debug("Reminders sent: %d, type: %s", reminder_count, "vote")

    # i know the next two are similiar enough to be merged but its currently dec 30 and i cant be bothered
    # catch reminders
    reminder_count = 0
    while True:
        user = await Profile.collect(
            f"(reminders_enabled = true AND reminder_catch != 0) AND ((catch_cooldown != 0 AND catch_cooldown + 43200 < {start_time}) OR (reminder_catch > 1 AND reminder_catch < {start_time})) LIMIT 1",
        )
        if not user or not user[0]:
            break
        user = user[0]
        await asyncio.sleep(0.2)

        await refresh_quests(user)
        await user.refresh_from_db()

        quest_data = config.battle["quests"]["catch"][user.catch_quest]

        embed = discord.Embed(
            title=f"{get_emoji(quest_data['emoji'])} {quest_data['title']}",
            description=f"Reward: **{user.catch_reward}** XP",
            color=Colors.green,
        )

        view = View(timeout=VIEW_TIMEOUT)
        button = Button(label="Postpone", custom_id=f"catch_{user.guild_id}")
        button.callback = postpone_reminder
        view.add_item(button)

        guild = bot.get_guild(user.guild_id)
        if not guild:
            guild_name = "a server"
        else:
            guild_name = guild.name

        try:
            user_user = await User.get_or_create(id=user.user_id)
            user_dm = await fetch_dm_channel(user_user)
            await user_dm.send(f"A new quest is available in {guild_name}!", embed=embed, view=view)
        except Exception:
            pass
        user.reminder_catch = 0
        reminder_count += 1
        await user.save()

    logging.debug("Reminders sent: %d, type: %s", reminder_count, "catch")

    # misc reminders
    reminder_count = 0
    while True:
        user = await Profile.collect(
            f"(reminders_enabled = true AND reminder_misc != 0) AND ((misc_cooldown != 0 AND misc_cooldown + 43200 < {start_time}) OR (reminder_misc > 1 AND reminder_misc < {start_time})) LIMIT 1",
        )
        if not user or not user[0]:
            break
        user = user[0]
        await asyncio.sleep(0.2)

        await refresh_quests(user)
        await user.refresh_from_db()

        quest_data = config.battle["quests"]["misc"][user.misc_quest]

        embed = discord.Embed(
            title=f"{get_emoji(quest_data['emoji'])} {quest_data['title']}",
            description=f"Reward: **{user.misc_reward}** XP",
            color=Colors.green,
        )

        view = View(timeout=VIEW_TIMEOUT)
        button = Button(label="Postpone", custom_id=f"misc_{user.guild_id}")
        button.callback = postpone_reminder
        view.add_item(button)

        guild = bot.get_guild(user.guild_id)
        if not guild:
            guild_name = "a server"
        else:
            guild_name = guild.name

        try:
            user_user = await User.get_or_create(user_id=user.user_id)
            user_dm = await fetch_dm_channel(user_user)
            await user_dm.send(f"A new quest is available in {guild_name}!", embed=embed, view=view)
        except Exception:
            pass
        user.reminder_misc = 0
        reminder_count += 1
        await user.save()

    logging.debug("Reminders sent: %d, type: %s", reminder_count, "misc")

    # challenge reminders
    reminder_count = 0
    while True:
        user = await Profile.collect(
            f"(reminders_enabled = true AND reminder_challenge != 0) AND ((challenge_cooldown != 0 AND challenge_cooldown + 43200 < {start_time}) OR (reminder_challenge > 1 AND reminder_challenge < {start_time})) LIMIT 1",
        )
        if not user or not user[0]:
            break
        user = user[0]
        await asyncio.sleep(0.2)

        await refresh_quests(user)
        await user.refresh_from_db()

        quest_data = config.battle["quests"]["challenge"][user.challenge_quest]

        embed = discord.Embed(
            title=f"{get_emoji(quest_data['emoji'])} {quest_data['title']}",
            description=f"Reward: **{user.challenge_reward}** XP",
            color=Colors.green,
        )

        view = View(timeout=VIEW_TIMEOUT)
        button = Button(label="Postpone", custom_id=f"challenge_{user.guild_id}")
        button.callback = postpone_reminder
        view.add_item(button)

        guild = bot.get_guild(user.guild_id)
        if not guild:
            guild_name = "a server"
        else:
            guild_name = guild.name

        try:
            user_user = await User.get_or_create(user_id=user.user_id)
            user_dm = await fetch_dm_channel(user_user)
            await user_dm.send(f"A new quest is available in {guild_name}!", embed=embed, view=view)
        except Exception:
            pass
        user.reminder_challenge = 0
        reminder_count += 1
        await user.save()

    logging.debug("Reminders sent: %d, type: %s", reminder_count, "challenge")

    # manual reminders
    async for reminder in Reminder.filter("time < $1", time.time()):
        try:
            user = await User.get_or_create(user_id=reminder.user_id)
            user_dm = await fetch_dm_channel(user)
            await user_dm.send(reminder.text)
            await asyncio.sleep(0.5)
        except Exception:
            pass
        await reminder.delete()

    # Jobs: flip expired offers. Audit trail is preserved (state='expired'
    # instead of delete); jobinstance_expiry partial index makes this O(few).
    try:
        async with transaction() as conn:
            await conn.execute(
                "UPDATE jobinstance SET state = 'expired' WHERE state = 'offered' AND expires_at < $1",
                int(time.time()),
            )
    except Exception:
        logging.exception("jobs: expired-offer sweep failed")

    # db backups
    if config.BACKUP_ID:
        backupchannel = bot.get_partial_messageable(config.BACKUP_ID)

        if loop_count % 12 == 0:
            backup_file = "./backup.dump"
            try:
                os.remove(backup_file)
            except Exception:
                pass

            try:
                process = await asyncio.create_subprocess_shell(f"PGPASSWORD={config.DB_PASS} pg_dump -U cat_bot -Fc -Z 9 -f {backup_file} cat_bot")
                await process.wait()

                if exportbackup:
                    event_loop = asyncio.get_event_loop()
                    await event_loop.run_in_executor(None, exportbackup.export)

                    await backupchannel.send(f"In {len(bot.guilds)} servers, loop {loop_count}.\nBackup exported.")
                else:
                    await backupchannel.send(f"In {len(bot.guilds)} servers, loop {loop_count}.", file=discord.File(backup_file))
            except Exception as e:
                logging.warning(f"Error during backup: {e}")
        else:
            await backupchannel.send(f"In {len(bot.guilds)} servers, loop {loop_count}.")

    loop_count += 1


# fetch app emojis early
async def on_connect():
    global emojis
    if len(emojis) == 0:
        emojis = {emoji.name: str(emoji) for emoji in await bot.fetch_application_emojis()}


# some code which is run when bot is started
async def on_ready():
    global OWNER_ID, on_ready_debounce, gen_credits, emojis
    if on_ready_debounce:
        return
    on_ready_debounce = True
    logging.info("cat is now online")
    if len(emojis) == 0:
        emojis = {emoji.name: str(emoji) for emoji in await bot.fetch_application_emojis()}
    appinfo = bot.application
    if appinfo.team and appinfo.team.owner_id:
        OWNER_ID = appinfo.team.owner_id
    else:
        OWNER_ID = appinfo.owner.id

    gen_credits = "\n".join(
        [
            "Self-hosted instance based on Cat Bot by **Lia Milenakos**",
            "Source: <https://github.com/milenakos/cat-bot>",
        ]
    )

    # Stock init is N stocks × 2 DB queries. Spawn it so on_ready returns
    # promptly even if a query stalls — bot responsiveness doesn't hinge on
    # the stock market being primed.
    bot.loop.create_task(_init_stock_orders())


async def _init_stock_orders():
    try:
        uuh = await Profile.get_or_create(user_id=bot.user.id, guild_id=0)
        for stock in stock_data:
            total_stocks = await Profile.sum(f"stock_{stock['ticker'].lower()}")
            total_orders = await Order.count("ticker = $1", stock["ticker"])
            if total_stocks == 0 and total_orders == 0:
                await Order.create(
                    user_id=uuh.id,
                    time=0,
                    ticker=stock["ticker"],
                    type_buy=False,
                    quantity=stock["amount"],
                    price=stock["init_price"],
                )
    except Exception:
        logging.exception("initial stock orders setup failed")


async def _run_stock_market_maker() -> None:
    """Background-loop tick that maintains a bot-owned bid/ask spread at the
    fair price for each ticker. Without this, a small instance has no
    liquidity and prices never move off the initial 40. The standing legacy
    10k-share sell at price 40 from _init_stock_orders gets cancelled on the
    first tick (its shares are returned to the bot's inventory) and replaced
    with fresh fair-price-anchored orders each cycle.

    Identified as MM orders by `user_id=<bot profile> AND time=0`. The 7-day
    cleanup sweep (`Order.filter("time > 0 AND ...")`) skips them by design.
    """
    if not STOCK_MARKET.get("enabled"):
        return

    spread = STOCK_MARKET.get("spread", 0.05)
    mm_qty = STOCK_MARKET.get("mm_order_quantity", 100)
    floor = STOCK_MARKET.get("price_floor", 1)
    ceiling = STOCK_MARKET.get("price_ceiling", 1000)

    bot_profile = await Profile.get_or_create(user_id=bot.user.id, guild_id=0)

    for stock in stock_data:
        ticker = stock["ticker"]
        try:
            # Cancel existing MM orders for this ticker and refund whatever
            # they were holding back into the bot's inventory. Refetch each
            # order before deleting to detect races with resolve_orders.
            existing = await Order.collect(
                "user_id = $1 AND time = 0 AND ticker = $2",
                bot_profile.id,
                ticker,
            )
            for mm_order in existing:
                live = await Order.get_or_none(id=mm_order.id)
                if not live:
                    continue
                if live.type_buy:
                    bot_profile.coins += live.quantity * live.price
                else:
                    bot_profile[f"stock_{ticker.lower()}"] += live.quantity
                await live.delete()
            await bot_profile.save()

            fair = await _compute_fair_price(ticker)
            bid = max(floor, round(fair * (1 - spread)))
            ask = min(ceiling, round(fair * (1 + spread)))
            if bid >= ask:
                ask = bid + 1  # never let the bot self-cross

            # Sell side: only post what we actually have.
            ask_qty = min(mm_qty, bot_profile[f"stock_{ticker.lower()}"])
            if ask_qty > 0:
                bot_profile[f"stock_{ticker.lower()}"] -= ask_qty
                await Order.create(
                    user_id=bot_profile.id,
                    time=0,
                    ticker=ticker,
                    type_buy=False,
                    quantity=ask_qty,
                    price=ask,
                )

            # Buy side: only post what we can afford.
            bid_cost = mm_qty * bid
            bid_qty = mm_qty if bot_profile.coins >= bid_cost else bot_profile.coins // bid
            if bid_qty > 0:
                bot_profile.coins -= bid_qty * bid
                await Order.create(
                    user_id=bot_profile.id,
                    time=0,
                    ticker=ticker,
                    type_buy=True,
                    quantity=bid_qty,
                    price=bid,
                )
            await bot_profile.save()

            # Record fair price so charts have data even without user trades.
            await PriceHistory.create(ticker=ticker, price=fair, time=int(time.time()))
            temp_stock_prices[ticker] = fair
            logging.debug("stock MM tick: %s fair=%d bid=%d ask=%d", ticker, fair, bid, ask)
        except Exception:
            logging.exception("stock MM tick failed for %s", ticker)


# this is all the code which is ran on every message sent
# a lot of it is for easter eggs or achievements
async def on_message(message: discord.Message):
    global emojis, last_loop_time
    text = message.content
    if not bot.user or message.author.id == bot.user.id:
        return

    if time.time() > last_loop_time + MAIN_LOOP_INTERVAL:
        last_loop_time = time.time()
        bot.loop.create_task(_safe_background_loop())

    if message.guild is None and not message.author.bot:
        try:
            user = await User.get_or_create(user_id=message.author.id)
            if text.startswith("disable"):
                # disable reminders
                try:
                    where = text.split(" ")[1]
                    user = await Profile.get_or_create(guild_id=int(where), user_id=message.author.id)
                    user.reminders_enabled = False
                    await user.save()
                    await message.reply("reminders disabled")
                except Exception:
                    await message.reply("failed. check if your guild id is correct")
                    return
            elif text == "lol_i_have_dmed_the_cat_bot_and_got_an_ach":
                await message.reply('which part of "send in server" was unclear?')
            elif user.dms < 15:
                await message.reply('good job! please send "lol_i_have_dmed_the_cat_bot_and_got_an_ach" in server to get your ach!')
                user.dms += 1
                await user.save()
            else:
                await message.reply(random.choice(fanhalo_list))
        except Exception:
            pass
        return

    server = None

    # here are some automation hooks for giving out purchases and similiar
    if config.RAIN_CHANNEL_ID and message.channel.id == config.RAIN_CHANNEL_ID and text.lower().startswith("cat!rain"):
        arguements = text.split(" ")
        user = await User.get_or_create(user_id=int(arguements[1]))
        rain_duration = arguements[2]
        if not user.rain_minutes:
            user.rain_minutes = 0

        if rain_duration == "short":
            user.rain_minutes += 2
        elif rain_duration == "medium":
            user.rain_minutes += 10
        elif rain_duration == "long":
            user.rain_minutes += 20
        else:
            user.rain_minutes += int(rain_duration)
            user.rain_minutes_bought += int(rain_duration)
        user.premium = True
        await user.save()

        # try to dm the user the thanks msg
        try:
            person = await fetch_dm_channel(user)
            await person.send(
                f"**You have recieved {rain_duration} minutes of Cat Rain!** ☔\n\nThanks for your support!\nYou can start a rain with `/rain`. By buying you also get access to `/editprofile` and `/customcat` commands as well as a role in [our Discord server](<https://discord.gg/staring>)!\n\nEnjoy your goods!"
            )
        except Exception:
            pass

        return

    react_count = 0

    # :staring_cat: reaction on "bullshit"
    if " " not in text and len(text) > 7 and text.isalnum():
        s = text.lower()
        total_vow = 0
        total_illegal = 0
        for i in "aeuio":
            total_vow += s.count(i)
        for j in illegal:
            if j in s:
                total_illegal += 1
        vow_perc = total_vow / len(text)
        if (vow_perc >= 0.82) or total_illegal >= 2:
            try:
                if reactions_ratelimit.get(message.guild.id, 0) < 100:
                    if not server:
                        server = await Server.get_or_create(server_id=message.guild.id)
                    if server.do_reactions and await check_channel_setupped(server, message.channel):
                        await message.add_reaction(get_emoji("staring_cat"))
                    react_count += 1
                    reactions_ratelimit[message.guild.id] = reactions_ratelimit.get(message.guild.id, 0) + 1
                    logging.debug("Reaction added: %s", "staring_cat")
            except Exception:
                pass

    if message.author.bot or message.webhook_id is not None:
        return

    for achievement in achs:
        match_text, match_method, achievement_name = achievement
        text_lowered = text.lower()
        if any(
            [
                match_method == "startswith" and text_lowered.startswith(match_text),
                match_method == "re" and re.search(match_text, text_lowered),
                match_method == "exact" and match_text == text_lowered,
                match_method == "veryexact" and match_text == text,
                match_method == "in" and match_text in text_lowered,
            ]
        ):
            try:
                await achemb(message, achievement_name, "reply")
            except Exception:
                logging.exception("achemb raised for %s — continuing on_message", achievement_name)

    # Data-driven message_text triggers (UI-added aches).
    if message.guild is not None:
        try:
            msg_profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.author.id)
            await ach_engine.evaluate(
                "message_text",
                msg_profile,
                {"text": text},
                message=message,
                achemb=achemb,
                send_type="reply",
            )
        except Exception:
            logging.exception("message_text ach_engine evaluate failed")

    if "fuck you bot" in text.lower():
        rude_profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.author.id)
        if not rude_profile.thats_rude:
            if rude_profile.cat_Fine > 0:
                rude_profile.cat_Fine -= 1
                await rude_profile.save()
                penalty_msg = f"that's rude. i took 1 of your {get_emoji('finecat')} Fine cats. you now have {rude_profile.cat_Fine:,} of dat type."
            else:
                penalty_msg = f"that's rude. i would take 1 of your {get_emoji('finecat')} Fine cats but you have none."
            try:
                await message.reply(penalty_msg)
            except Exception:
                pass
            await achemb(message, "thats_rude", "send")

    if unidecode.unidecode(text).lower().strip() in cat_translations:
        await achemb(message, "multilingual", "reply")

    if str(bot.user.id) in message.content:
        await achemb(message, "who_ping", "reply")

    for reaction in reactions:
        reaction_prompt, reaction_type, reaction_name = reaction
        if reaction_prompt in text.lower() and reactions_ratelimit.get(message.guild.id, 0) < 100:
            if reaction_type == "custom":
                resolved_emoji = get_emoji(reaction_name)
            elif reaction_type == "vanilla":
                resolved_emoji = reaction_name

            try:
                if not server:
                    server = await Server.get_or_create(server_id=message.guild.id)
                if server.do_reactions and await check_channel_setupped(server, message.channel):
                    await message.add_reaction(resolved_emoji)
                react_count += 1
                reactions_ratelimit[message.guild.id] = reactions_ratelimit.get(message.guild.id, 0) + 1
                logging.debug("Reaction added: %s", reaction_name)
            except Exception:
                pass

    for response in responses:
        match_text, match_method, response_reply = response
        text_lowered = text.lower()
        if any(
            [
                match_method == "startswith" and text_lowered.startswith(match_text),
                match_method == "re" and re.search(match_text, text_lowered),
                match_method == "exact" and match_text == text_lowered,
                match_method == "in" and match_text in text_lowered,
            ]
        ):
            if not server:
                server = await Server.get_or_create(server_id=message.guild.id)
            if server.do_responses and await check_channel_setupped(server, message.channel):
                try:
                    await message.reply(response_reply)
                except Exception:
                    pass
                logging.debug("Response sent: %s", response_reply)

    try:
        if message.author in message.mentions and message.type != discord.MessageType.poll_result and reactions_ratelimit.get(message.guild.id, 0) < 100:
            if not server:
                server = await Server.get_or_create(server_id=message.guild.id)
            if server.do_reactions and await check_channel_setupped(server, message.channel):
                await message.add_reaction(get_emoji("staring_cat"))
            react_count += 1
            reactions_ratelimit[message.guild.id] = reactions_ratelimit.get(message.guild.id, 0) + 1
            logging.debug("Reaction added: %s", "staring_cat")
    except Exception:
        pass

    if react_count >= 3:
        await achemb(message, "silly", "reply")

    if (":place_of_worship:" in text or "🛐" in text) and (":cat:" in text or ":staring_cat:" in text or "🐱" in text):
        await achemb(message, "worship", "reply")

    if text.lower() in ["testing testing 1 2 3", "cat!ach"]:
        if not server:
            server = await Server.get_or_create(server_id=message.guild.id)
        if server.do_responses and await check_channel_setupped(server, message.channel):
            try:
                await message.reply("test success")
            except Exception:
                # test failure
                pass
            logging.debug("Response sent: %s", "test success")
        await achemb(message, "test_ach", "reply")

    if text.lower() == "please do not the cat":
        user = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.author.id)
        if user.cat_Fine > 0:
            user.cat_Fine -= 1
            await user.save()
        if not server:
            server = await Server.get_or_create(server_id=message.guild.id)
        if server.do_responses and await check_channel_setupped(server, message.channel):
            try:
                personname = message.author.name.replace("_", "\\_")
                await message.reply(f"ok then\n{personname} lost 1 fine cat!!!1!\nYou now have {user.cat_Fine:,} cats of dat type!")
            except Exception:
                pass
            logging.debug("Response sent: %s", "please do not the cat")
        await achemb(message, "pleasedonotthecat", "reply")

    if text.lower() == "please do the cat":
        if not server:
            server = await Server.get_or_create(server_id=message.guild.id)
        if server.do_responses and await check_channel_setupped(server, message.channel):
            thing = discord.File("images/socialcredit.jpg", filename="socialcredit.jpg")
            try:
                await message.reply(file=thing)
            except Exception:
                pass
            logging.debug("Response sent: %s", "please do the cat")
        await achemb(message, "pleasedothecat", "reply")

    if text.lower() == "car":
        if not server:
            server = await Server.get_or_create(server_id=message.guild.id)
        if server.do_responses and await check_channel_setupped(server, message.channel):
            file = discord.File("images/car.png", filename="car.png")
            embed = discord.Embed(title="car!", color=Colors.brown).set_image(url="attachment://car.png")
            try:
                await message.reply(file=file, embed=embed)
            except Exception:
                pass
            logging.debug("Response sent: %s", "car")
        await achemb(message, "car", "reply")

    if text.lower() == "cart":
        if not server:
            server = await Server.get_or_create(server_id=message.guild.id)
        if server.do_responses and await check_channel_setupped(server, message.channel):
            file = discord.File("images/cart.png", filename="cart.png")
            embed = discord.Embed(title="cart!", color=Colors.brown).set_image(url="attachment://cart.png")
            try:
                await message.reply(file=file, embed=embed)
            except Exception:
                pass
            logging.debug("Response sent: %s", "cart")

    try:
        if (
            ("sus" in text.lower() or "amog" in text.lower() or "among" in text.lower() or "impost" in text.lower() or "report" in text.lower())
            and (channel := await Channel.get_or_none(channel_id=message.channel.id))
            and channel.cattype == "Sus"
        ):
            await achemb(message, "sussy", "reply")
    except Exception:
        pass

    # this is run whether someone says "cat" (very complex)
    if text.lower() == "cat":
        user = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.author.id)
        channel = await Channel.get_or_none(channel_id=message.channel.id)
        if not server:
            server = await Server.get_or_create(server_id=message.guild.id)
        if (
            not channel
            or not channel.cat
            or channel.cat in temp_catches_storage
            or user.timeout > time.time()
            or (server.anti_double_catch and user.last_catch_channel != message.channel.id and user.last_catch + ANTI_DOUBLE_CATCH_COOLDOWN > time.time())
        ):
            # laugh at this user
            # (except if rain is active, we dont have perms or channel isnt setupped, or we laughed way too much already)
            if channel and channel.cat_rains == 0 and pointlaugh_ratelimit.get(message.channel.id, 0) < 10:
                try:
                    if server.do_reactions and await check_channel_setupped(server, message.channel):
                        await message.add_reaction(get_emoji("pointlaugh"))
                    pointlaugh_ratelimit[message.channel.id] = pointlaugh_ratelimit.get(message.channel.id, 0) + 1
                except Exception:
                    pass

            # Catch-streak passive XP: a laughed-at miss breaks the streak.
            # Only resets if we'd actually have a streak going.
            if user.catch_streak > 0:
                user.catch_streak = 0
                await user.save()

            # belated battlepass
            if message.channel.id in temp_belated_storage:
                current_time = message.created_at.timestamp()
                belated = temp_belated_storage[message.channel.id]
                if (
                    channel
                    and "users" in belated
                    and "time" in belated
                    and belated.get("timestamp", 0) + 3 > current_time
                    and message.author.id not in belated["users"]
                ):
                    belated["users"].append(message.author.id)
                    temp_belated_storage[message.channel.id] = belated
                    if user.catnip_active >= time.time() or user.hibernation:
                        await bounty(message, user, channel.cattype)
                    quests = ["3cats"]
                    if channel.cattype == "Fine":
                        quests.append("2fine")
                    if channel.cattype == "Good":
                        quests.append("good")
                    belated_time = belated.get("time", 10) + current_time - belated.get("timestamp", 0)
                    if belated_time < 10:
                        quests.append("under10")
                    if belated_time < 3:
                        quests.append("under3")
                    # `slow` (>60s) can't physically fire here — belated catches
                    # only register when the original catch happened within 3s.
                    if random.randint(0, 1) == 0:
                        quests.append("even")
                    else:
                        quests.append("odd")
                    if channel.cattype and channel.cattype not in ["Fine", "Nice", "Good"]:
                        quests.append("rare+")
                    if channel.cattype in LEGENDARY_PLUS:
                        quests.append("legendary+")
                    if user.catnip_active > time.time():
                        quests.append("catnip_catch")
                    total_count = await Prism.count("guild_id = $1", message.guild.id)
                    user_count = await Prism.count("guild_id = $1 AND user_id = $2", message.guild.id, message.author.id)
                    prism_boost = PRISM_BOOST_GLOBAL_COEF * math.log(2 * total_count + 1) + PRISM_BOOST_USER_COEF * math.log(2 * user_count + 1)
                    if prism_boost > random.random():
                        quests.append("prism")
                    if user.catch_quest == "finenice":
                        # 0 none
                        # 1 fine
                        # 2 nice
                        # 3 both
                        if channel.cattype == "Fine" and user.catch_progress in [0, 2]:
                            quests.append("finenice")
                        elif channel.cattype == "Nice" and user.catch_progress in [0, 1]:
                            quests.append("finenice")
                            quests.append("finenice")
                    await multi_progress(message, user, quests, True)
        else:
            pls_remove_me_later_k_thanks = channel.cat
            temp_catches_storage.append(channel.cat)
            decided_time = random.uniform(channel.spawn_times_min, channel.spawn_times_max)

            cat_rain_end = False
            if channel.cat_rains > 0:
                channel.cat_rains -= 1
                if channel.cat_rains == 0:
                    cat_rain_end = True
                else:
                    decided_time = random.uniform(1, 2)
                    channel.rain_should_end = int(time.time() + decided_time)

            if channel.yet_to_spawn < time.time():
                # if there isnt already a scheduled spawn
                channel.yet_to_spawn = time.time() + decided_time + 10
            else:
                channel.yet_to_spawn = 0
                decided_time = 0
            force_rain_summary = None

            try:
                current_time = message.created_at.timestamp()
                channel.lastcatches = current_time
                cat_temp = channel.cat
                channel.cat = 0
                try:
                    if channel.cattype != "":
                        catchtime = discord.utils.snowflake_time(cat_temp)
                        le_emoji = channel.cattype
                    else:
                        var = await message.channel.fetch_message(cat_temp)
                        catchtime = var.created_at
                        catchcontents = var.content

                        partial_type = None
                        for v in allowedemojis:
                            if v in catchcontents:
                                partial_type = v
                                break

                        if not partial_type and "thetrashcellcat" in catchcontents:
                            partial_type = "trashcat"
                            le_emoji = "Trash"
                        else:
                            if not partial_type:
                                return

                            for i in cattypes:
                                if i.lower() in partial_type:
                                    le_emoji = i
                                    break
                except Exception:
                    try:
                        await message.channel.send(f"oopsie poopsie i cant access the original message but {message.author.mention} *did* catch a cat rn")
                    except Exception:
                        pass
                    return

                send_target = message.channel
                try:
                    # some math to make time look cool
                    then = catchtime.timestamp()
                    time_caught = round(abs(current_time - then), 3)  # cry about it
                    if time_caught >= 1:
                        time_caught = round(time_caught, 2)

                    days, time_left = divmod(time_caught, 86400)
                    hours, time_left = divmod(time_left, 3600)
                    minutes, seconds = divmod(time_left, 60)

                    caught_time = ""
                    if days:
                        caught_time = caught_time + str(int(days)) + " days "
                    if hours:
                        caught_time = caught_time + str(int(hours)) + " hours "
                    if minutes:
                        caught_time = caught_time + str(int(minutes)) + " minutes "
                    if seconds:
                        pre_time = round(seconds, 3)
                        if pre_time % 1 == 0:
                            # replace .0 with .00 basically
                            pre_time = str(int(pre_time)) + ".00"
                        caught_time = caught_time + str(pre_time) + " seconds "
                    do_time = True
                    if not caught_time:
                        caught_time = "0.000 seconds (woah) "
                    if time_caught <= 0:
                        do_time = False
                except Exception:
                    # if some of the above explodes just give up
                    do_time = False
                    caught_time = "undefined amounts of time "

                try:
                    if time_caught >= 0:
                        temp_belated_storage[message.channel.id] = {"time": time_caught, "users": [message.author.id], "timestamp": current_time}
                except Exception:
                    pass

                if channel.cat_rains > 0 or cat_rain_end:
                    do_time = False

                suffix_string = ""
                silly_amount = 1

                # Random pack drop on every catch, independent of catnip.
                # Tier is weighted (Wooden common, Celestial extremely rare).
                # When it fires we attach a tier-themed embed to the catch
                # confirmation rather than appending an inline line — the
                # drop is rare enough (~2%) to earn its own moment of drama.
                bonus_pack_embed = None
                if random.random() < PACK_DROP_CHANCE_ON_CATCH:
                    bonus_pack_name, _ = grant_bonus_pack(user)
                    bonus_pack_embed = build_bonus_pack_embed(user, bonus_pack_name)

                # perky!
                double_chance = 0
                triple_chance = 0
                single_chance = 100
                none_chance = 0
                double_boost_chance = 0
                rain_chance = 0
                purr_all_triple = False
                packs = []
                double_boost = False
                double_first = 0
                combo_per_stack = 0
                bp_xp_chance = 0
                respawn_chance = 0
                do_respawn = False
                packs_gained = []

                if user.perks:
                    if user.catnip_active < time.time():
                        if user.catnip_active != 1:
                            user.catnip_active = 1
                            suffix_string += f"\n{get_emoji('catnip_disabled')} Your catnip expired! Run /catnip to get more."
                        perks = []
                    elif _jobs_perks_suspended(user):
                        # Cat Police lockout — catnip's active but perks are
                        # suspended until perks_suspended_until.
                        suffix_string += f"\n🚓 Your perks are suspended by the Cat Police until <t:{int(user.perks_suspended_until)}:R>."
                        perks = []
                    else:
                        perks = user.perks
                    perks_info = catnip_list["perks"]
                    user.pack_attempts -= 1

                    if len(perks) > 0:
                        logging.debug("Catnip active with %d perks", len(perks))

                    for perk in perks:
                        h = perk.split("_")
                        rarity = int(h[0])
                        type = int(h[1])
                        id = perks_info[type - 1]["id"]

                        if id == "double":
                            double_chance += perks_info[0]["values"][rarity]
                            single_chance -= perks_info[0]["values"][rarity]
                        elif id == "triple_none":
                            triple_chance += perks_info[1]["values"][rarity]
                            none_chance += perks_info[1]["values"][rarity] / 2
                            single_chance -= perks_info[1]["values"][rarity] * (1.5)
                        elif "pack" in id and user.pack_attempts > 0:
                            for num, pack in enumerate(pack_data):
                                if pack["name"].lower() in id:
                                    packs.append((num, perks_info[type - 1]["values"][rarity]))
                                    break
                        elif id == "double_boost":
                            double_boost_chance += perks_info[8]["values"][rarity]
                        elif id == "triple_ach":
                            purr_all_triple = True
                        elif id == "rain_boost":
                            rain_chance += perks_info[12]["values"][rarity]
                        elif id == "double_first":
                            double_first += perks_info[13]["values"][rarity]
                        elif id == "combo":
                            combo_per_stack += perks_info[14]["values"][rarity]
                        elif id == "bp_xp":
                            bp_xp_chance += perks_info[15]["values"][rarity]
                        elif id == "respawn":
                            respawn_chance += perks_info[16]["values"][rarity]

                    for i in packs:
                        chance = random.random() * 100
                        if chance <= i[1]:
                            packs_gained.append(pack_data[i[0]]["name"])
                            user[f"pack_{pack_data[i[0]]['name'].lower()}"] += 1
                            suffix_string += f"\n{get_emoji(pack_data[i[0]]['name'].lower() + 'pack')} You got a {pack_data[i[0]]['name']} pack! You now have {user[f'pack_{pack_data[i[0]]["name"].lower()}']:,} packs of this type!"

                    chance = random.random() * 100
                    if chance <= double_boost_chance:
                        double_boost = True

                    # Snowballer: each consecutive catch grows the stack (cap 30);
                    # idle for >5 min resets to 1. Per-stack % feeds the double pool.
                    # user.last_catch still holds the PREVIOUS catch time at this point —
                    # it's not updated to "now" until later in this handler.
                    if combo_per_stack > 0:
                        if time.time() - user.last_catch > 300:
                            user.combo_stack = 1
                        else:
                            user.combo_stack = min(30, user.combo_stack + 1)
                        combo_chance = min(combo_per_stack * user.combo_stack, 100.0)
                        double_chance += combo_chance
                        single_chance -= combo_chance
                        if user.combo_stack >= 30 and not user.has_ach("snowballer_max"):
                            await achemb(message, "snowballer_max", "send")

                    # Battlepass Booster: % chance per catch of +5 XP nugget.
                    # grant_achievement_xp saves the user internally; if the proc
                    # happens to push a level, send the level-up embeds inline.
                    if bp_xp_chance > 0 and random.random() * 100 < bp_xp_chance:
                        bp_xp_embeds = await grant_achievement_xp(user, 5)
                        suffix_string += f"\n{get_emoji('catnip')} +5 battlepass XP!"
                        if bp_xp_embeds:
                            try:
                                await message.channel.send(f"<@{user.user_id}>", embeds=bp_xp_embeds)
                            except Exception:
                                logging.exception("bp_xp level-up embed send failed")
                        if not user.has_ach("bp_xp_proc"):
                            await achemb(message, "bp_xp_proc", "send")

                    # Bait & Switch: roll the chance now, fire the respawn after
                    # channel state has been persisted (see post-save hook below).
                    if respawn_chance > 0 and random.random() * 100 < respawn_chance and channel.cat_rains == 0:
                        do_respawn = True

                # Big Score perk: permanent +5% spawn-extra in this server.
                # Survives catnip expiry and pinch lockout — it's not a catnip
                # perk, it's a reward for clearing the Tier 5 capstone.
                if not do_respawn and bool(getattr(user, "big_score_perk_unlocked", False)) and channel.cat_rains == 0:
                    bs_chance = JOBS_BIG_SCORE.get("perk_spawn_extra_bonus", 0.05) * 100
                    if random.random() * 100 < bs_chance:
                        do_respawn = True
                        suffix_string += f"\n{get_emoji('catnip')} Bait & Switch! Another cat appears..."
                        if not user.has_ach("bait_switch_proc"):
                            await achemb(message, "bait_switch_proc", "send")

                    if double_first > user.catnip_total_cats:
                        user.catnip_total_cats += 1
                        double_chance = 100 - triple_chance
                        single_chance = 0
                        none_chance = 0

                    if time_caught > 0 and time_caught == int(time_caught):
                        user.perfection_count += 1
                        if purr_all_triple:
                            triple_chance = 100
                            double_chance = 0
                            single_chance = 0
                            none_chance = 0

                    if "undefined" not in caught_time and time_caught > 0:
                        raw_digits = "".join(char for char in caught_time[:-1] if char.isdigit())
                        if len(set(raw_digits)) == 1 and purr_all_triple:
                            triple_chance = 100
                            double_chance = 0
                            single_chance = 0
                            none_chance = 0

                    if single_chance < 0:
                        single_chance = 0
                        double_chance = 100 - triple_chance - none_chance
                    if double_chance < 0:
                        double_chance = 0
                        if 100 - triple_chance < 25:
                            none_chance = 25
                            triple_chance = 75
                    if none_chance < 0:
                        none_chance = 0

                    if random.random() * 100 < rain_chance:
                        if channel.cat_rains == 0 and server.do_rain:
                            force_rain_summary = config.cat_cought_rain.get(channel.channel_id, {}).copy()
                            channel.cat_rains = 10
                            decided_time = random.uniform(1, 2)
                            channel.rain_should_end = int(time.time() + decided_time)
                            channel.yet_to_spawn = 0
                            config.cat_cought_rain[channel.channel_id] = {}
                            config.rain_starter[channel.channel_id] = message.author.id
                            bot.loop.create_task(rain_recovery_loop(channel))
                            suffix_string += "\n☔ Catnip started a short rain! 10 cats will spawn."

                    chance = random.random() * 100
                    if chance <= triple_chance:
                        silly_amount *= 3
                        suffix_string += f"\n{get_emoji('catnip')}{get_emoji('catnip')} catnip worked! your cat was TRIPLED by catnip!1!!1!"
                        user.catnip_activations += 2
                    elif chance <= triple_chance + double_chance:
                        silly_amount *= 2
                        suffix_string += f"\n{get_emoji('catnip')} catnip worked! your cat was doubled by catnip!!1!"
                        user.catnip_activations += 1
                    elif chance <= triple_chance + double_chance + single_chance:
                        silly_amount *= 1
                    elif chance <= triple_chance + double_chance + single_chance + none_chance:
                        silly_amount *= 0
                        suffix_string += "\n🚫 catnip failed! your cat was uncought. tragic."

                # blessings
                bless_chance = await User.sum("rain_minutes_bought", "blessings_enabled = true") * 0.0001 * 0.01
                if bless_chance > random.random():
                    # woo we got blessed thats pretty cool
                    if silly_amount == 0:
                        silly_amount += 1
                    else:
                        silly_amount *= 2

                    blesser_l = await User.collect("blessings_enabled = true AND rain_minutes_bought > 0 ORDER BY -ln(random()) / rain_minutes_bought LIMIT 1")
                    blesser = blesser_l[0]
                    blesser.cats_blessed += 1
                    if not blesser.username:
                        blesser.username = (await bot.fetch_user(blesser.user_id)).name
                    asyncio.create_task(blesser.save())

                    logging.debug("Catch blessed")

                    if blesser.blessings_anonymous:
                        blesser_text = "💫 Anonymous Supporter"
                    else:
                        blesser_text = f"{blesser.emoji or '💫'} {blesser.username}"

                    if silly_amount > 1:
                        suffix_string += f"\n{blesser_text} blessed your catch and it got doubled!"
                    else:
                        suffix_string += f"\n{blesser_text} blessed your catch and it got saved!"

                # calculate prism boost
                total_count = await Prism.count("guild_id = $1", message.guild.id)
                user_count = await Prism.count("guild_id = $1 AND user_id = $2", message.guild.id, message.author.id)
                global_boost = PRISM_BOOST_GLOBAL_COEF * math.log(2 * total_count + 1)
                user_boost = global_boost + PRISM_BOOST_USER_COEF * math.log(2 * user_count + 1)
                did_boost = False
                le_old_emoji = le_emoji
                if user_boost > random.random():
                    # determine whodunnit
                    if random.uniform(0, user_boost) > global_boost:
                        # boost from our own prism
                        user_prisms = await Prism.collect("guild_id = $1 AND user_id = $2 ORDER BY random() LIMIT 1", message.guild.id, message.author.id)
                        prism_which_boosted = user_prisms[0]
                    else:
                        # boost from any prism
                        total_prisms = await Prism.collect("guild_id = $1 ORDER BY random() LIMIT 1", message.guild.id)
                        prism_which_boosted = total_prisms[0]

                    if prism_which_boosted.user_id == message.author.id:
                        boost_applied_prism = "Your prism " + prism_which_boosted.name
                    else:
                        boost_applied_prism = f"<@{prism_which_boosted.user_id}>'s prism " + prism_which_boosted.name

                    did_boost = True
                    user.boosted_catches += 1
                    prism_which_boosted.catches_boosted += 1
                    asyncio.create_task(prism_which_boosted.save())
                    # Passive XP: +20 to the prism owner when their prism
                    # boosts a different user's catch. Silent grant — the
                    # owner sees it next time they check /battlepass.
                    if prism_which_boosted.user_id != message.author.id:
                        async def _grant_prism_owner_xp(guild_id, owner_id):
                            try:
                                owner = await Profile.get_or_none(guild_id=guild_id, user_id=owner_id)
                                if owner is not None:
                                    await grant_achievement_xp(owner, 20)
                            except Exception:
                                logging.exception("prism owner XP grant failed")
                        asyncio.create_task(_grant_prism_owner_xp(message.guild.id, prism_which_boosted.user_id))
                    logging.debug("Boosted from %s", le_emoji)
                    idx_shift = 0
                    try:
                        le_old_emoji = le_emoji
                        if double_boost:
                            idx_shift = cattypes.index(le_emoji) + 2
                        else:
                            idx_shift = cattypes.index(le_emoji) + 1
                        le_emoji = cattypes[idx_shift]
                        normal_bump = True
                    except IndexError:
                        normal_bump = False
                        if not channel.forcespawned:
                            if idx_shift == len(cattypes) + 1:
                                rainboost = RAINBOOST_LONG
                            elif idx_shift == len(cattypes):
                                rainboost = RAINBOOST_SHORT
                            logging.debug("Boosted to rain: %d", rainboost)
                            channel.cat_rains += math.ceil(rainboost / 2.75)
                            if channel.cat_rains > math.ceil(rainboost / 2.75):
                                await message.channel.send(f"# ‼️‼️ RAIN EXTENDED BY {int(rainboost / 60)} MINUTES ‼️‼️")
                                await message.channel.send(f"# ‼️‼️ RAIN EXTENDED BY {int(rainboost / 60)} MINUTES ‼️‼️")
                                await message.channel.send(f"# ‼️‼️ RAIN EXTENDED BY {int(rainboost / 60)} MINUTES ‼️‼️")
                            elif server.do_rain:
                                force_rain_summary = config.cat_cought_rain.get(channel.channel_id, {}).copy()
                                decided_time = random.uniform(1, 2)
                                channel.rain_should_end = int(time.time() + decided_time)
                                channel.yet_to_spawn = 0
                                config.cat_cought_rain[channel.channel_id] = {}
                                config.rain_starter[channel.channel_id] = message.author.id
                                bot.loop.create_task(rain_recovery_loop(channel))

                    if normal_bump:
                        if double_boost:
                            suffix_string += f"\n{get_emoji('prism')}{get_emoji('prism')} {boost_applied_prism} boosted this catch twice from a {get_emoji(le_old_emoji.lower() + 'cat')} {le_old_emoji} cat!"
                        else:
                            suffix_string += f"\n{get_emoji('prism')} {boost_applied_prism} boosted this catch from a {get_emoji(le_old_emoji.lower() + 'cat')} {le_old_emoji} cat!"
                    elif not channel.forcespawned:
                        suffix_string += (
                            f"\n{get_emoji('prism')} {boost_applied_prism} tried to boost this catch, but failed! A {rainboost // 60}m rain will start!"
                        )

                icon = get_emoji(le_emoji.lower() + "cat")

                if channel.channel_id in config.cat_cought_rain:
                    if le_emoji not in config.cat_cought_rain[channel.channel_id]:
                        config.cat_cought_rain[channel.channel_id][le_emoji] = []
                    for _ in range(silly_amount):
                        config.cat_cought_rain[channel.channel_id][le_emoji].append(f"<@{user.user_id}>")
                    for i in packs_gained:
                        if i not in config.cat_cought_rain[channel.channel_id]:
                            config.cat_cought_rain[channel.channel_id][i] = []
                        config.cat_cought_rain[channel.channel_id][i].append(f"<@{user.user_id}>")

                if random.randint(0, 19) == 0:
                    # diplay a hint/fun fact
                    suffix_string += "\n💡 " + random.choice(hints)

                custom_cough_strings = {
                    "Corrupt": "{username} coought{type} c{emoji}at!!!!404!\nYou now BEEP {count} cats of dCORRUPTED!!\nthis fella wa- {time}!!!!",
                    "eGirl": "{username} cowought {emoji} {type} cat~~ ^^\nYou-u now *blushes* hawe {count} cats of dat tywe~!!!\nthis fella was <3 cought in {time}!!!!",
                    "Rickroll": "{username} cought {emoji} {type} cat!!!!1!\nYou will never give up {count} cats of dat type!!!\nYou wouldn't let them down even after {time}!!!!",
                    "Sus": "{username} cought {emoji} {type} cat!!!!1!\nYou have vented infront of {count} cats of dat type!!!\nthis sussy baka was cought in {time}!!!!",
                    "Professor": "{username} caught {emoji} {type} cat!\nThou now hast {count} cats of that type!\nThis fellow was caught 'i {time}!",
                    "8bit": "{username} c0ught {emoji} {type} cat!!!!1!\nY0u n0w h0ve {count} cats 0f dat type!!!\nth1s fe11a was c0ught 1n {time}!!!!",
                    "Reverse": "!!!!{time} in cought was fella this\n!!!type dat of cats {count} have now You\n!1!!!!cat {type} {emoji} cought {username}",
                }

                if channel.cought:
                    # custom spawn message
                    coughstring = channel.cought
                elif le_emoji in custom_cough_strings:
                    # custom type message
                    coughstring = custom_cough_strings[le_emoji]
                else:
                    # default
                    coughstring = "{username} cought {emoji} {type} cat!!!!1!\nYou now have {count} cats of dat type!!!\nthis fella was cought in {time}!!!!"

                view = None
                button = None

                async def dark_market_cutscene(interaction):
                    nonlocal message
                    if interaction.user != message.author:
                        await interaction.response.send_message(
                            "the shadow you saw runs away. perhaps you need to be the one to catch the cat.",
                            ephemeral=True,
                        )
                        return
                    if user.dark_market_active:
                        await interaction.response.send_message("the shadowy figure is nowhere to be found.", ephemeral=True)
                        return
                    user.dark_market_active = True
                    await user.save()
                    await interaction.response.send_message("is someone watching after you?", ephemeral=True)

                    dark_market_followups = [
                        "you walk up to them. the dark voice says:",
                        "**???**: Hello. We have a unique deal for you.",
                        "**???**: To access our services, run /catnip.",
                        "**???**: You won't be disappointed.",
                        "before you manage to process that, the figure disappears. will you figure out whats going on?",
                        "the only choice is to go to that place.",
                    ]

                    for phrase in dark_market_followups:
                        await asyncio.sleep(5)
                        await interaction.followup.send(phrase, ephemeral=True)

                    await achemb(message, "dark_market", "followup")

                vote_time_user = await User.get_or_create(user_id=message.author.id)
                if random.randint(0, 10) == 0 and user.total_catches > 50 and not user.dark_market_active:
                    button = Button(label="You see a shadow...", style=ButtonStyle.red)
                    button.callback = dark_market_cutscene
                elif config.VOTING_ENABLED and config.WEBHOOK_VERIFY and vote_time_user.vote_time_topgg + 43200 < time.time():
                    button = Button(
                        emoji=get_emoji("topgg"),
                        label=random.choice(vote_button_texts),
                        url="https://top.gg/bot/966695034340663367/vote",
                    )

                if button:
                    view = View(timeout=VIEW_TIMEOUT)
                    view.add_item(button)

                user[f"cat_{le_emoji}"] += silly_amount
                new_count = user[f"cat_{le_emoji}"]
                if silly_amount > 0:
                    await mark_discovered(user, le_emoji)

                async def delete_cat():
                    try:
                        cat_spawn = send_target.get_partial_message(cat_temp)
                        await cat_spawn.delete()
                    except Exception:
                        pass

                async def send_confirm():
                    try:
                        kwargs = {}
                        if view:
                            kwargs["view"] = view
                        if bonus_pack_embed is not None:
                            kwargs["embed"] = bonus_pack_embed

                        result = await send_target.send(
                            coughstring.replace("{username}", message.author.name.replace("_", "\\_"))
                            .replace("{emoji}", str(icon))
                            .replace("{type}", le_emoji)
                            .replace("{count}", f"{new_count:,}")
                            .replace("{time}", caught_time[:-1])
                            + suffix_string,
                            **kwargs,
                        )

                        if server.auto_delete_catches:
                            # button do stuff = button stay... for now-
                            delay = 30 if (button and button.callback) else 10
                            await result.delete(delay=delay)

                    except Exception:
                        # Silently fail if we can't send the confirmation message (e.g. permission issues)
                        pass

                await asyncio.gather(delete_cat(), send_confirm())

                logging.debug("Caught (pre-boost) %d %s", 1, channel.cattype)
                logging.debug("Caught (post-boost) %d %s", silly_amount, le_emoji)

                user.total_catches += 1
                user.last_catch = time.time()
                user.last_catch_channel = message.channel.id
                if do_time:
                    user.total_catch_time += time_caught

                # handle fastest and slowest catches
                if do_time and time_caught < user.time:
                    user.time = time_caught
                if do_time and time_caught > user.timeslow:
                    user.timeslow = time_caught

                if channel.cat_rains > 0:
                    user.rain_participations += 1

                await user.save()

                global_user_for_streak = await User.get_or_create(user_id=message.author.id)
                first_catch_of_day = await update_daily_catch_streak(global_user_for_streak)

                # Passive XP: first catch of the day = +50, every 10-catch
                # streak boundary = +20. We aggregate any resulting level-up
                # embeds and send them inline with the rest of the catch flow.
                passive_xp_embeds: list[discord.Embed] = []
                if first_catch_of_day:
                    passive_xp_embeds += await grant_first_catch_of_day_xp(user)
                passive_xp_embeds += await grant_catch_streak_xp(user)
                if passive_xp_embeds:
                    try:
                        await message.channel.send(f"<@{user.user_id}>", embeds=passive_xp_embeds)
                    except Exception:
                        logging.exception("passive XP embed send failed")

                # Streaker challenge quest: fires once when a streak crosses a
                # multiple of 10. progress() short-circuits on its own cooldown,
                # so streaks of 20/30/etc. don't re-complete the quest.
                if user.catch_streak > 0 and user.catch_streak % 10 == 0:
                    await progress(message, user, "streak10")

                if random.randint(0, 1000) == 69 and not user.lucky:
                    await achemb(message, "lucky", "send")
                if message.content == "CAT" and not user.loud_cat:
                    await achemb(message, "loud_cat", "send")
                if bot.user in message.mentions and message.reference.message_id == cat_temp and not user.ping_reply:
                    await achemb(message, "ping_reply", "send")
                if channel.cat_rains > 0 and not user.cat_rain:
                    await achemb(message, "cat_rain", "send")

                if not user.first:
                    await achemb(message, "first", "send")

                # Data-driven catch-event triggers. Fires any ach in aches.json
                # whose trigger.event == "catch" and condition is satisfied.
                # Hardcoded triggers below cover the legacy / unmigrated ones.
                await ach_engine.evaluate(
                    "catch",
                    user,
                    {
                        "time": user.time,
                        "timeslow": user.timeslow,
                        "total_catches": user.total_catches,
                        "cat_type": channel.cattype,
                        "rain_active": channel.cat_rains > 0,
                        "prism_boosted": did_boost,
                    },
                    message=message,
                    achemb=achemb,
                )

                if time_caught in [3.14, 31.41, 31.42, 194.15, 194.16, 1901.59, 11655.92, 11655.93] and not user.pie:
                    await achemb(message, "pie", "send")

                if time_caught > 0 and time_caught == int(time_caught) and not user.perfection:
                    await achemb(message, "perfection", "send")

                if did_boost and not user.boosted:
                    await achemb(message, "boosted", "send")

                if "undefined" not in caught_time and time_caught > 0 and not user.all_the_same:
                    raw_digits = "".join(char for char in caught_time[:-1] if char.isdigit())
                    if len(set(raw_digits)) == 1:
                        await achemb(message, "all_the_same", "send")

                if suffix_string.count("\n") >= 4 and not user.certified_yapper:
                    await achemb(message, "certified_yapper", "send")

                # handle battlepass
                quests = ["3cats"]
                if channel.cattype == "Fine":
                    quests.append("2fine")
                if channel.cattype == "Good":
                    quests.append("good")
                if time_caught >= 0 and time_caught < 10:
                    quests.append("under10")
                if time_caught >= 0 and time_caught < 3:
                    quests.append("under3")
                if time_caught >= 60:
                    quests.append("slow")
                if time_caught >= 0 and int(time_caught) % 2 == 0:
                    quests.append("even")
                if time_caught >= 0 and int(time_caught) % 2 == 1:
                    quests.append("odd")
                if channel.cattype and channel.cattype not in ["Fine", "Nice", "Good"]:
                    quests.append("rare+")
                if channel.cattype in LEGENDARY_PLUS:
                    quests.append("legendary+")
                if user.catnip_active > time.time():
                    quests.append("catnip_catch")
                if did_boost:
                    quests.append("prism")
                if user.catch_quest == "finenice":
                    # 0 none
                    # 1 fine
                    # 2 nice
                    # 3 both
                    if channel.cattype == "Fine" and user.catch_progress in [0, 2]:
                        quests.append("finenice")
                    elif channel.cattype == "Nice" and user.catch_progress in [0, 1]:
                        quests.append("finenice")
                        quests.append("finenice")

                # handle catnip bounties
                await bounty(message, user, channel.cattype)

                # handle quests
                await multi_progress(message, user, quests, False)
            finally:
                if decided_time:
                    if cat_rain_end:
                        await channel.save()
                        bot.loop.create_task(rain_end(message, channel, force_rain_summary))

                    # shift decided_time to reduce load
                    if decided_time > 10:
                        # ignore cat rains
                        start_time = channel.yet_to_spawn
                        shifts = [0] + [x for n in range(1, 11) for x in (n, -n)]
                        for shift in shifts:
                            c = await Channel.count("yet_to_spawn = $1", start_time + shift)
                            if c < 5:
                                channel.yet_to_spawn = start_time + shift
                                decided_time += shift
                                break

                    await channel.save()

                    # Bait & Switch: schedule an immediate respawn that races
                    # the normal post-cooldown spawn. spawn_cat is self-guarded
                    # against double-spawn, so whichever lands first wins.
                    if do_respawn:
                        bot.loop.create_task(spawn_cat(str(message.channel.id)))

                    await asyncio.sleep(decided_time)
                    try:
                        temp_catches_storage.remove(pls_remove_me_later_k_thanks)
                    except Exception:
                        pass
                    await spawn_cat(str(message.channel.id))
                else:
                    await channel.save()
                    if do_respawn:
                        bot.loop.create_task(spawn_cat(str(message.channel.id)))
                    try:
                        temp_catches_storage.remove(pls_remove_me_later_k_thanks)
                    except Exception:
                        pass

    # only letting the owner of the bot access anything past this point
    if message.author.id != OWNER_ID:
        return

    # those are "owner" commands which are not really interesting
    if text.lower().startswith("cat!sweep"):
        try:
            channel = await Channel.get_or_none(channel_id=message.channel.id)
            channel.cat = 0
            await channel.save()
            await message.reply("success")
        except Exception:
            pass
    if text.lower().startswith("cat!rain"):
        # syntax: cat!rain 553093932012011520 short
        things = text.split(" ")
        user = await User.get_or_create(user_id=int(things[1]))
        if not user.rain_minutes:
            user.rain_minutes = 0
        if things[2] == "short":
            user.rain_minutes += 2
        elif things[2] == "medium":
            user.rain_minutes += 10
        elif things[2] == "long":
            user.rain_minutes += 20
        else:
            user.rain_minutes += int(things[2])
        user.premium = True
        await user.save()
    if text.lower().startswith("cat!restart"):
        try:
            await message.reply("restarting!")
        except Exception:
            pass
        if config.WEBHOOK_VERIFY:
            await vote_server.cleanup()
        await bot.cat_bot_reload_hook("db" in text)  # pyright: ignore
    if text.lower().startswith("cat!print"):
        # just a simple one-line with no async (e.g. 2+3)
        try:
            await message.reply(eval(text[9:]))
        except Exception:
            try:
                await message.reply(traceback.format_exc())
            except Exception:
                pass
    if text.lower().startswith("cat!eval"):
        # complex eval, multi-line + async support
        # requires the full `await message.channel.send(2+3)` to get the result

        # async def go():
        #  <stuff goes here>
        #
        # try:
        #  bot.loop.create_task(go())
        # except Exception:
        #  await message.reply(traceback.format_exc())

        silly_billy = text[9:]

        spaced = ""
        for i in silly_billy.split("\n"):
            spaced += "  " + i + "\n"

        intro = "async def go(message, bot):\n try:\n"
        ending = "\n except Exception:\n  await message.reply(traceback.format_exc())\nbot.loop.create_task(go(message, bot))"

        complete = intro + spaced + ending
        exec(complete)
    if text.lower().startswith("cat!news"):
        async for i in Channel.all():
            try:
                channeley = bot.get_partial_messageable(int(i.channel_id))
                await channeley.send(text[8:])
            except Exception:
                pass
    if text.lower().startswith("cat!custom"):
        stuff = text.split(" ")
        if stuff[1][0] not in "1234567890":
            stuff.insert(1, message.channel.owner_id)
        user = await User.get_or_create(user_id=int(stuff[1]))
        cat_name = " ".join(stuff[2:])
        if stuff[2] != "None" and message.reference and message.reference.message_id:
            emoji_name = str(user.user_id) + "cat"
            if emoji_name in emojis.keys():
                await message.reply("emoji already exists")
                return
            og_msg = await message.channel.fetch_message(message.reference.message_id)
            if not og_msg or len(og_msg.attachments) == 0:
                await message.reply("no image found")
                return
            img_data = await og_msg.attachments[0].read()

            if og_msg.attachments[0].content_type.startswith("image/gif"):
                await bot.create_application_emoji(name=emoji_name, image=img_data)
            else:
                img = Image.open(io.BytesIO(img_data))
                img.thumbnail((128, 128))
                with io.BytesIO() as image_binary:
                    img.save(image_binary, format="PNG")
                    image_binary.seek(0)
                    await bot.create_application_emoji(name=emoji_name, image=image_binary.getvalue())
        user.custom = cat_name if cat_name != "None" else ""
        emojis = {emoji.name: str(emoji) for emoji in await bot.fetch_application_emojis()}
        await user.save()
        await message.reply("success")


# the message when cat gets added to a new server
async def on_guild_join(guild):
    def verify(ch):
        return ch and ch.permissions_for(guild.me).send_messages

    def find(patt, channels):
        for i in channels:
            if patt in i.name:
                return i

    logging.debug("Guild joined, member count %d", guild.member_count)

    # first to try a good channel, then whenever we cat atleast chat
    ch = find("cat", guild.text_channels)
    if not verify(ch):
        ch = find("bot", guild.text_channels)
    if not verify(ch):
        ch = find("commands", guild.text_channels)
    if not verify(ch):
        ch = find("general", guild.text_channels)

    found = False
    if not verify(ch):
        for ch in guild.text_channels:
            if verify(ch):
                found = True
                break
        if not found:
            ch = guild.owner

    # you are free to change/remove this, its just a note for general user letting them know
    unofficial_note = "**NOTE: This is an unofficial Cat Bot instance.**\n\n"
    if not bot.user or bot.user.id == 966695034340663367:
        unofficial_note = ""
    try:
        if ch.permissions_for(guild.me).send_messages:
            await ch.send(
                unofficial_note
                + "Thanks for adding me!\nTo start, use `/setup` and `/help` to learn more!\nJoin the support server here: https://discord.gg/staring\nHave a nice day :)"
            )
    except Exception:
        pass


@bot.tree.command(description="A guide of how to use the bot")
async def help(message):
    embed1 = discord.Embed(
        title="How to Setup",
        description="Server moderator (anyone with *Manage Server* permission) needs to run `/setup` in any channel. After that, cats will start to spawn in 1-10 minute intervals inside of that channel.\nYou can customize those intervals with `/changetimings` and change the spawn message with `/changemessage`.\nCat spawns can also be forced by moderators using `/forcespawn` command.\nYou can have unlimited amounts of setupped channels at once.\nYou can stop the spawning in a channel by running `/forget`.",
        color=Colors.brown,
    ).set_thumbnail(url="https://wsrv.nl/?url=raw.githubusercontent.com/milenakos/cat-bot/main/images/cat.png")

    embed2 = (
        discord.Embed(title="How to Play", color=Colors.brown)
        .add_field(
            name="Catch Cats",
            value='Whenever a cat spawns you will see a message along the lines of "a cat has appeared", which will also display it\'s type.\nCat types can have varying rarities from 25% for Fine to hundredths of percent for rarest types.\nSo, after saying "cat" the cat will be added to your inventory.',
            inline=False,
        )
        .add_field(
            name="Viewing Your Inventory",
            value="You can view your (or anyone elses!) inventory using `/inventory` command. It will display all the cats, along with other stats.\nIt is important to note that you have a separate inventory in each server and nothing carries over, to make the experience more fair and fun.\nCheck out the leaderboards for your server by using `/leaderboards` command.\nIf you want to transfer cats, you can use the simple `/gift` or more complex `/trade` commands.",
            inline=False,
        )
        .add_field(
            name="Let's get funky!",
            value='Cat Bot has various other mechanics to make fun funnier. You can collect various `/achievements`, for example saying "i read help", progress in the `/battlepass`, or have beef with the mafia over catnip addiction. The amount you worship is the limit!',
            inline=False,
        )
        .add_field(
            name="Other features",
            value="Cat Bot has extra fun commands which you will discover along the way.\nAnything unclear? Check out [our wiki](https://catbot.wiki) or drop us a line at our [Discord server](https://discord.gg/staring).",
            inline=False,
        )
        .set_footer(
            text=f"Cat Bot by Milenakos, {discord.utils.utcnow().year}",
            icon_url="https://wsrv.nl/?url=raw.githubusercontent.com/milenakos/cat-bot/main/images/cat.png",
        )
    )

    await message.response.send_message(embeds=[embed1, embed2])


@bot.tree.command(description="Roll the credits")
async def credits(message: discord.Interaction):
    global gen_credits

    if not gen_credits:
        await message.response.send_message(
            "credits not yet ready! this is a very rare error, congrats.",
            ephemeral=True,
        )
        return

    await message.response.defer()

    embedVar = discord.Embed(title="Cat Bot", color=Colors.brown, description=gen_credits).set_thumbnail(
        url="https://wsrv.nl/?url=raw.githubusercontent.com/milenakos/cat-bot/main/images/cat.png"
    )

    await message.followup.send(embed=embedVar)


def format_timedelta(start_timestamp, end_timestamp):
    delta = datetime.timedelta(seconds=end_timestamp - start_timestamp)
    days = delta.days
    seconds = delta.seconds
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    return f"{days}d {hours}h {minutes}m {seconds}s"


@bot.tree.command(description="View various info and stats about the bot")
async def info(message: discord.Interaction):
    embed = discord.Embed(title="Cat Bot Info", color=Colors.brown)
    try:
        git_timestamp = int(subprocess.check_output(["git", "show", "-s", "--format=%ct"]).decode("utf-8"))
    except Exception:
        git_timestamp = 0

    embed.description = f"""
**__System__**
OS Version: `{platform.system()} {platform.release()}`
Python Version: `{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}`
discord.py Version: `{discord.__version__}{"-catbot" if "localhost" in str(discord.gateway.DiscordWebSocket.DEFAULT_GATEWAY) else ""}`
CPU usage: `{psutil.cpu_percent():.1f}%`
RAM usage: `{psutil.virtual_memory().percent:.1f}%`

**__Tech__**
Hard uptime: `{format_timedelta(config.HARD_RESTART_TIME, time.time())}`
Soft uptime: `{format_timedelta(config.SOFT_RESTART_TIME, time.time())}`
Last code update: `{format_timedelta(git_timestamp, time.time()) if git_timestamp else "N/A"}`
Loops since soft restart: `{loop_count + 1:,}`
Shards: `{len(bot.shards):,}`
Guild shard: `{message.guild.shard_id:,}`

**__Global Stats__**
Guilds: `{len(bot.guilds):,}`
DB Profiles: `{await Profile.count():,}`
DB Users: `{await User.count():,}`
DB Channels: `{await Channel.count():,}`
"""

    await message.response.send_message(embed=embed)


@bot.tree.command(description="Confused? Check out the Cat Bot Wiki!")
async def wiki(message: discord.Interaction):
    embed = discord.Embed(title="Cat Bot Wiki", color=Colors.brown)
    embed.description = "\n".join(
        [
            "Main Page: https://catbot.wiki/",
            "",
            "[Cat Bot](https://catbot.wiki/cat-bot)",
            "[Cat Spawning](https://catbot.wiki/spawning)",
            "[Commands](https://catbot.wiki/commands)",
            "[Cat Types](https://catbot.wiki/cat-types)",
            "[Cattlepass](https://catbot.wiki/cattlepass)",
            "[Achievements](https://catbot.wiki/achievements)",
            "[Packs](https://catbot.wiki/packs)",
            "[Trading](https://catbot.wiki/trading)",
            "[Gambling](https://catbot.wiki/gambling)",
            "[Catnip](https://catbot.wiki/catnip)",
            "[Prisms](https://catbot.wiki/prisms)",
            "[Stocks](https://catbot.wiki/stocks)",
        ]
    )
    await message.response.send_message(embed=embed)
    profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    await progress(message, profile, "wiki")


@bot.tree.command(description="Read The Cat Bot Times™️")
async def news(message: discord.Interaction):
    embed = discord.Embed(
        title="📰 The Cat Bot Times",
        description="Coming soon.",
        color=Colors.brown,
    )
    await message.response.send_message(embed=embed)
    return
    # The original /news command is preserved below but unreachable.
    # Restore by removing the early-return above when you have news to publish.
    user = await User.get_or_create(user_id=message.user.id)
    buttons = []
    current_state = user.news_state.strip()

    async def send_news(interaction: discord.Interaction):
        news_id = int(interaction.data["custom_id"])
        if interaction.user != message.user:
            await do_funny(interaction)
            return

        async def go_back(back_interaction: discord.Interaction):
            if back_interaction.user != message.user:
                await do_funny(back_interaction)
                return
            await back_interaction.response.defer()
            await regen_buttons()
            await back_interaction.edit_original_response(view=generate_page(current_page))

        await interaction.response.defer()

        current_state = user.news_state.strip()
        if current_state[news_id] not in "123456789":
            user.news_state = current_state[:news_id] + "1" + current_state[news_id + 1 :]
            await user.save()

        profile = await Profile.get_or_create(guild_id=interaction.guild.id, user_id=interaction.user.id)
        await progress(interaction, profile, "news")

        view = LayoutView(timeout=VIEW_TIMEOUT)
        back_button = Button(emoji="⬅️", label="Back")
        back_button.callback = go_back
        back_row = ActionRow(back_button)

        logging.debug("Read news #%d", news_id)

        if news_id == 0:
            embed = Container(
                "## 📜 Cat Bot Survey",
                "Hello and welcome to The Cat Bot Times:tm:! I kind of want to learn more about your time with Cat Bot because I barely know about it lmao. This should only take a couple of minutes.\n\nGood high-quality responses will win FREE cat rain prizes.\n\nSurvey is closed!",
                "-# <t:1731168230>",
            )
            view.add_item(embed)
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)
        elif news_id == 1:
            embed = Container(
                "## ✨ New Cat Rains perks!",
                "Hey there! Buying Cat Rains now gives you access to `/editprofile` command! You can add an image, change profile color, and add an emoji next to your name. Additionally, you will now get a special role in our [discord server](https://discord.gg/staring).\nEveryone who ever bought rains and all future buyers will get it.\nAnyone who bought these abilities separately in the past (known as 'Cat Bot Supporter') have received 10 minutes of Rains as compensation.\n\nThis is a really cool perk and I hope you like it!",
                Button(label="Cat Bot Store", url="https://catbot.shop"),
                "-# <t:1732377932>",
            )
            view.add_item(embed)
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)
        elif news_id == 2:
            embed = Container(
                "## ☃️ Cat Bot Christmas",
                f"⚡ **Cat Bot Wrapped 2024**\nIn 2024 Cat Bot got...\n- 🖥️ *45777* new servers!\n- 👋 *286607* new profiles!\n- {get_emoji('staring_cat')} okay so funny story due to the new 2.1 billion per cattype limit i added a few months ago 4 with 832 zeros cats were deleted... oopsie... there are currently *64105220101255* cats among the entire bot rn though\n- {get_emoji('cat_throphy')} *1518096* achievements get!\nSee last year's Wrapped [here](<https://discord.com/channels/966586000417619998/1021844042654417017/1188573593408385074>).\n\n❓ **New Year Update**\nSomething is coming...",
                "-# <t:1734458962>",
            )
            view.add_item(embed)
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)
        elif news_id == 3:
            embed = Container(
                "## Cattlepass is getting an update!",
                """### qhar?
- Huge stuff!
- Cattlepass will now reset every month
- You will have 3 quests, including voting
- They refresh 12 hours after completing
- Quest reward is XP which goes towards progressing
- There are 30 cattlepass levels with much better rewards (even Ultimate cats and Rain minutes!)
- Prism crafting/true ending no longer require cattlepass progress.
- More fun stuff to do each day and better rewards!

### oh no what if i hate grinding?
Don't worry, quests are very easy and to complete the cattlepass you will need to complete less than 3 easy quests a day.

### will you sell paid cattlepass? its joever
There are currently no plans to sell a paid cattlepass.""",
                "-# <t:1735689601>",
            )
            view.add_item(embed)
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)
        elif news_id == 4:
            embed = Container(
                f"## {get_emoji('goldpack')} Packs!",
                f"""you want more gambling? we heard you!
instead of predetermined cat rewards you now unlock Packs! packs have different rarities and have a 30% chance to upgrade a rarity when opening, then 30% for one more upgrade and so on. this means even the most common packs have a small chance to upgrade to the rarest one!
the rarities are - Wooden {get_emoji("woodenpack")}, Stone {get_emoji("stonepack")}, Bronze {get_emoji("bronzepack")}, Silver {get_emoji("silverpack")}, Gold {get_emoji("goldpack")}, Platinum {get_emoji("platinumpack")}, Diamond {get_emoji("diamondpack")} and Celestial {get_emoji("celestialpack")}!
the extra reward is now a stone pack instead of 5 random cats too!
*LETS GO GAMBLING*""",
                "-# <t:1740787200>",
            )
            view.add_item(embed)
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)
        elif news_id == 5:
            embed = Container(
                "## Important Message from CEO of Cat Bot",
                """(April Fools 2025)

Dear Cat Bot users,

I hope this message finds you well. I want to take a moment to address some recent developments within our organization that are crucial for our continued success.

Our latest update has had a significant impact on our financial resources, resulting in an unexpected budget shortfall. In light of this situation, we have made the difficult decision to implement advertising on our platform to help offset these costs. We believe this strategy will not only stabilize our finances but also create new opportunities for growth.

Additionally, in our efforts to manage expenses more effectively, we have replaced all cat emojis with just the "Fine Cat" branding. This change will help us save on copyright fees while maintaining an acceptable user experience.

We are committed to resolving these challenges and aim to have everything back on track by **April 2nd**. Thank you for your understanding and continued dedication during this time. Together, we will navigate these changes and emerge stronger.

Best regards,
[Your Name]""",
                "-# <t:1743454803>",
            )
            view.add_item(embed)
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)
        elif news_id == 6:
            embed = Container(
                "## 🥳 Cat Bot Turns 3",
                """april 21st is a special day for cat bot! on this day is its birthday, and in 2025 its turning three!
happy birthda~~
...
hold on...
im recieving some news cats are starting to get caught with puzzle pieces in their teeth!
the puzzle pieces say something about having to collect a million of them...
how interesting!

update: the puzzle piece event has concluded""",
                "-# <t:1745242856>",
            )
            view.add_item(embed)
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)
        elif news_id == 7:
            embed = Container(
                "## 🎉 100,000 SERVERS WHAT",
                """wow! cat bot has reached 100,000 servers! this beyond insane i never thought this would happen thanks everyone
giving away a whole bunch of rain as celebration!

1. cat stand giveaway (ENDED)
[join our discord server](<https://discord.gg/FBkXDxjqSz>) and click the first reaction under the latest newspost to join in!
there will be a total of 10 winners who will get 40 minutes each! giveaway ends july 5th.

2. art contest (ENDED)
again in our [discord server](<https://discord.gg/zrYstPe3W6>) a new channel has opened for art submissions!
top 5 people who get the most community votes will get 250, 150, 100, 50 and 50 rain minutes respectively!

3. cat bot event (ENDED)
starting june 30th, for the next 5 days you will get points randomly on every catch! if you manage to collect 1,000 points before the time runs out you will get 2 minutes of rain!!

4. sale (ENDED)
starting june 30th, [catbot.shop](<https://catbot.shop>) will have a sale for the next 5 days! if everything above wasnt enough rain for your fancy you can buy some more with a discount!

aaaaaaaaaaaaaaa""",
                ActionRow(
                    Button(label="Join our Server", url="https://discord.gg/staring"),
                    Button(label="Cat Bot Store", url="https://catbot.shop"),
                ),
                "-# <t:1751252181>",
            )
            view.add_item(embed)
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)

        elif news_id == 8:
            embed = Container(
                "## Regarding recent instabilities",
                """hello!

stuff has been kinda broken the past few days, and the past 24 hours in paricular.

it was mostly my fault, but i worked hard to fix everything and i think its mostly working now.

as a compensation i will give everyone who voted in the past 3 days 2 free gold packs! you can press the button below to claim them. (note you can only claim it in 1 server, choose wisely)

thanks for using cat bot!""",
                Button(label="Expired!", disabled=True),
                "-# <t:1752689941>",
            )
            view.add_item(embed)
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)
        elif news_id == 9:
            # we hijack the cookie system to store the yippee count
            cookie_user = await Profile.get_or_create(guild_id=9, user_id=bot.user.id)

            async def add_yippee(interaction):
                nonlocal cookie_user
                await interaction.response.defer()
                cookie_user = await Profile.get(["cookies"], guild_id=9, user_id=bot.user.id)
                cookie_user.cookies += 1
                await cookie_user.save()
                await send_yippee(interaction)

            async def send_yippee(interaction):
                view = LayoutView(timeout=VIEW_TIMEOUT)
                btn = Button(label=f"yippee! ({cookie_user.cookies:,})", emoji=get_emoji("yippee"), style=ButtonStyle.primary)
                btn.callback = add_yippee
                embed = Container(
                    # RE-ENABLE WHEN VOTING IS PUBLIC: "## cat bot is now top 5 on top.gg",
                    # RE-ENABLE WHEN VOTING IS PUBLIC: "thanks for voting",
                    # RE-ENABLE WHEN VOTING IS PUBLIC: discord.ui.MediaGallery(discord.MediaGalleryItem("https://i.imgur.com/MSZF3ly.png")),
                    # RE-ENABLE WHEN VOTING IS PUBLIC: "also pls still [go vote](https://top.gg/bot/966695034340663367/vote) incase OwO will rebeat us!!",
                    "## yippee",
                    "===",
                    btn,
                    "-# <t:1757794211>",
                )
                view.add_item(embed)
                view.add_item(back_row)
                await interaction.edit_original_response(view=view)

            await send_yippee(interaction)
        elif news_id == 10:
            # RE-ENABLE WHEN VOTING IS PUBLIC: original news entry referenced top.gg awards / voting
            embed = Container(
                "## (this news entry is hidden on this self-hosted instance)",
                "-# <t:1759513848>",
            )
            view.add_item(embed)
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)
        elif news_id == 11:
            embed = Container(
                f"## {get_emoji('catnip')} Welcome to the Cat Mafia",
                f"""after the dog mafia got arrested, cats got inspired and started their own mafia!

- the dark market is being replaced by {get_emoji("catnip")} catnip
- the biggest update ever (probably)
- this is a new late-game complex mechanic with *leveling, bounties and perks*
- it can be accessed and managed via /catnip
- discover **10 new cats** - the members of the mafia who have tough challenges for you
- getting through all of it is a very tough challenge, **the hardest thing in cat bot**
- the old system is completely gone, all process you had in it will be reset

👉 okay now let me explain:
at each level you will have some bounties you have to complete within a time frame. if you complete the bounties and pay the price, you will be able to choose one of 3 different perks of random rarities {get_emoji("common")}{get_emoji("uncommon")}{get_emoji("rare")}{get_emoji("epic")}{get_emoji("legendary")}. the perks will stack while catnip is active! failing to complete the bounties will bring you one level down and you will lose your last perk. higher levels are harder but give you better perks!""",
                "-# <t:1761325200>",
            )
            view.add_item(embed)
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)
        elif news_id == 12:
            # RE-ENABLE WHEN VOTING IS PUBLIC: original news entry referenced top.gg awards / voting
            embed = Container(
                "## (this news entry is hidden on this self-hosted instance)",
                "-# <t:1765747278>",
            )
            view.add_item(embed)
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)
        elif news_id == 13:
            embed = Container(
                f"## {get_emoji('christmaspack')} Cat Bot Christmas 2025 (event over)",
                f"""Merry Christmas!

{get_emoji("christmaspack")} **Christmas Packs**
Christmas packs are a new pack type with a twist: when opening them the upgrade chances are 70% instead of 30%!
They start below Wooden with base value of 30. Their average value is ~225.
You can trade, gift, and open them as usual even after the event ends.
You will be able to collect them until <t:1767297600> using 2 methods:
- You get 1 when completing the Vote quest, or
- You get 1 for every 500 snowflakes you earn.

❄️ **Snowflakes**
You can get them by catching cats. The amount will be determined by the value of the catch (excluding all boosts), where 1 value = 1 ❄️.
This means catching an eGirl cat will give you 4 Christmas packs!

🎅 **Christmas Sale**
-20% sale starts now on the Cat Bot Store!
:point_right: **[catbot.shop](<https://catbot.shop>)**""",
                ActionRow(
                    Button(label="Cat Bot Store", url="https://catbot.shop"),
                ),
                "-# <t:1766433600>",
            )
            view.add_item(embed)
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)
        elif news_id == 14:
            embed = Container(
                "## 💝 Valentine's Day!",
                f"""💞 **Pick a Valentine** (event over)
Use `/valentine` to pick a valentine - your progress and rewards will be shared with them for the duration of the event.
You can't change this after you picked someone, so choose wisely!

{get_emoji("valentinepack")} **Valentine Packs**
Valentine packs are the new event pack type, with the upgrade chances being 70% instead of 30%!
Just like Christmas packs, they start below Wooden with base value of 30 and have average value of ~225.
You can trade, gift, and open them as usual even after the event ends.
You will be able to collect them until <t:1771437600> using 2 methods:
- You and your valentine both get 1 when either of you completes the Vote quest, and
- You and your valentine both get 1 for every 50 cats you collectively catch.

🥰 **Valentine's Sale** (over)
-20% sale starts now on the Cat Bot Store and will end on <t:1771437600>!
:point_right: **[catbot.shop](<https://catbot.shop>)**""",
                ActionRow(
                    Button(label="Cat Bot Store", url="https://catbot.shop"),
                ),
                "-# <t:1771005600>",
            )
            view.add_item(embed)
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)
        elif news_id == 15:
            embed = Container(
                "## 📈 Welcome to the Stock Market",
                """ever wanted to invest your cats into stocks? no? well now you can!
- /stocks and /portfolio
- deposit packs to get coins
- trade shares of stocks with other cat bot users globally
- earn random rewards (dividends) from time to time
- withdraw back to packs with a 20% fee

i understand this might be overwhelming which is why i added a ton of help buttons throughout the thing! those have much better explanations than this brief overview

ummm good luck and let the line go up!""",
                "-# <t:1772308800>",
            )
            view.add_item(embed)
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)
        elif news_id == 16:
            embed = Container(
                "## PackOrRain Event",
                "everyone *who votes below* will earn a prize! the prize type will be **whatever option gets most votes**, and the prize amount will be **how many millions of catches** everyone does until the event ends!",
                "-# the prize will be given to everyone who votes, even if their vote wasn't the winning option.",
                "===",
                "**Final Prize**: 2 ☔ Rain Minutes",
                "**Event ended** <t:1773856800>",
                "===",
                "-# <t:1773424800>",
            )
            view.add_item(embed)
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)
        elif news_id == 17:
            embed = Container(
                f"## {get_emoji('insane')} cat bot has reached 200k servers!",
                "wow big number!!",
                "to celebrate im ~~doing a 200 rain minute giveaway~~ ended!! in our [discord server](https://discord.com/channels/966586000417619998/1021844042654417017/1492510874458394655)",
                ActionRow(
                    Button(label="Join the server", url="https://discord.gg/staring"),
                ),
                "-# <t:1775913490>",
            )
            view.add_item(embed)
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)
        elif news_id == 18:
            embed = Container(
                f"## {get_emoji('b_babycat')} It's Cat Bot's 4th birthday!!",
                Section(
                    f"### {get_emoji('b_babycat')} Baby cat becomes an adult 🥳",
                    "Help decide Baby cat's new name via a poll in our [Discord server](https://discord.com/channels/966586000417619998/1021844042654417017)!",
                    Button(label="Vote!", url="https://discord.com/channels/966586000417619998/1021844042654417017"),
                ),
                f"### {get_emoji('birthdaypack')} Birthday Packs [ended]",
                f"For the next 5 days, you will get a {get_emoji('birthdaypack')} Birthday Pack for every {get_emoji('b_babycat')} Baby cat you catch!\nCollect 10 of them to get ☔ **2 free Rain Minutes**!",
                Section(
                    "### 🎨 Birthday Art Contest",
                    "Join our [Discord server](https://discord.gg/staring) to participate in the Birthday Art Contest! 3 winners will get ☔ **100 Rain Minutes** each.",
                    Button(label="Join the server", url="https://discord.gg/staring"),
                ),
                Section(
                    f"### {get_emoji('insane')} -50% off Sale [ended]",
                    "This is **much higher** than normal sale amounts!!",
                    Button(label="catbot.shop", emoji="☔", url="https://catbot.shop"),
                ),
                "-# <t:1776778856>",
            )
            view.add_item(embed)
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)
        elif news_id == 19:
            view.add_item(
                Container(
                    "## (disabled on this self-hosted instance)",
                )
            )
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)

    async def regen_buttons():
        nonlocal buttons
        await user.refresh_from_db()
        buttons = []
        current_state = user.news_state.strip()
        for num, article in enumerate(news_list):
            try:
                have_read_this = current_state[num] != "0"
            except Exception:
                have_read_this = False
            button = Button(
                label=article["title"],
                emoji=get_emoji(article["emoji"]),
                custom_id=str(num),
                style=ButtonStyle.green if not have_read_this else ButtonStyle.gray,
            )
            button.callback = send_news
            buttons.append(button)
        buttons = buttons[::-1]  # reverse the list so the first button is the most recent article

    await regen_buttons()

    if len(news_list) > len(current_state):
        user.news_state = current_state + "0" * (len(news_list) - len(current_state))
        await user.save()

    current_page = 0

    async def prev_page(interaction):
        nonlocal current_page
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        current_page -= 1
        await interaction.response.edit_message(view=generate_page(current_page))

    async def next_page(interaction):
        nonlocal current_page
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        current_page += 1
        await interaction.response.edit_message(view=generate_page(current_page))

    async def mark_all_as_read(interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        user.news_state = "1" * len(news_list)
        await user.save()
        await regen_buttons()
        await interaction.response.edit_message(view=generate_page(current_page))

    def generate_page(number):
        view = LayoutView(timeout=VIEW_TIMEOUT)
        view.add_item(TextDisplay("Choose an article:"))

        # article buttons
        if current_page == 0:
            end = (number + 1) * 4
        else:
            end = len(buttons)
            row = ActionRow()
        for num, button in enumerate(buttons[number * 4 : end]):
            if current_page == 0:
                view.add_item(ActionRow(button))
            else:
                if len(row.children) == 5:
                    view.add_item(row)
                    row = ActionRow()
                row.add_item(button)

        if current_page != 0 and len(row.children) > 0:
            view.add_item(row)

        last_row = ActionRow()

        # pages buttons
        if current_page != 0:
            button = Button(label="Back")
            button.callback = prev_page
            last_row.add_item(button)

        button = Button(label="Mark all as read")
        button.callback = mark_all_as_read
        last_row.add_item(button)

        if current_page == 0:
            button = Button(label="Archive")
            button.callback = next_page
            last_row.add_item(button)

        view.add_item(last_row)

        return view

    await message.response.send_message(view=generate_page(current_page))
    await achemb(message, "news", "followup")


@bot.tree.command(description="Read text as TikTok TTS woman")
@discord.app_commands.describe(text="The text to be read! (300 characters max)")
async def tiktok(message: discord.Interaction, text: str):
    # detect n-words
    for i in NONOWORDS:
        if i in text.lower():
            await message.response.send_message("Do not.", ephemeral=True)
            return

    await message.response.defer()
    profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)

    if text == "bwomp":
        file = discord.File("bwomp.mp3", filename="bwomp.mp3")
        await message.followup.send(file=file)
        await achemb(message, "bwomp", "followup")
        await progress(message, profile, "tiktok")
        return

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                "https://tiktok-tts.weilnet.workers.dev/api/generation",
                json={"text": text, "voice": "en_us_001"},
                headers={"User-Agent": "CatBot/1.0 https://github.com/milenakos/cat-bot"},
            ) as response:
                stuff = await response.json()
                with io.BytesIO() as f:
                    ba = "data:audio/mpeg;base64," + stuff["data"]
                    f.write(base64.b64decode(ba))
                    f.seek(0)
                    await message.followup.send(file=discord.File(fp=f, filename="output.mp3"))
        except discord.NotFound:
            pass
        except Exception:
            await message.followup.send("i dont speak guacamole (remove non-english characters, make sure the message is below 300 characters)")

    await progress(message, profile, "tiktok")


@bot.tree.command(description="(ADMIN) Prevent someone from catching cats for a certain time period")
@discord.app_commands.default_permissions(manage_guild=True)
@discord.app_commands.describe(person="A person to timeout!", timeout="How many seconds? (0 to reset, -1 for infinity)")
async def preventcatch(message: discord.Interaction, person: discord.User, timeout: int):
    user = await Profile.get_or_create(guild_id=message.guild.id, user_id=person.id)
    if timeout == 0:
        timestamp = 0
        suffix = " can now catch cats again."
    elif timeout == -1:
        timestamp = 9223372036854775806  # :wyphsmall:
        suffix = " can't catch cats until the year 292277026596"
        # You finally wake up from your coma. It's the year 292,277,026,596.
        # After the events of the World War 239, 99% of the humanity was wiped.
        # Only a few people are preserved in cryogenic sleep.
        # The AIs are waking them up to let them know of a high likelihood of a catastrophic failure
        # caused by the 64 bit integer limit for unix timestamps.
        # You realize it is out of your control, and decide to spend your last moments in a fun way.
        # You open a completely random app using your brainchip - it lands on Discord.
        # Due to the technological breakthroughs of the 22nd century all computers can run without electricity,
        # and the internet connection can't break due to quantum entanglement, which means abandoned apps can work forever.
        # No one has touched this app in thousands of milleniums, and it was abandoned by the developers back in 2126.
        # You open a random server your account happens to be in.
        # "A Fine cat has appeared. Type "cat" to catch it!". You do as instructed.
        # The catch fails, but seconds later you get a notification that your /preventcatch expired.
        # All the memories come back. You break down crying.
        # "this fella was caught in 292277024570 years 69 days 21 hours 42 minutes 6.7 seconds"
        # This gotta be a record.
    else:
        timestamp = round(time.time()) + timeout
        suffix = f" can't catch cats until <t:{timestamp}:R>"
    user.timeout = timestamp
    await user.save()
    await message.response.send_message(person.name.replace("_", r"\_") + suffix)


@bot.tree.command(description="(ADMIN) Change Cat Bot avatar")
@discord.app_commands.default_permissions(manage_guild=True)
@discord.app_commands.describe(avatar="The avatar to use (leave empty to reset)")
async def changeavatar(message: discord.Interaction, avatar: Optional[discord.Attachment]):
    await message.response.defer()

    if avatar and avatar.content_type not in ["image/png", "image/jpeg", "image/gif", "image/webp"]:
        await message.followup.send("Invalid file type! Please upload a PNG, JPEG, GIF, or WebP image.", ephemeral=True)
        return

    if avatar:
        avatar_value = discord.utils._bytes_to_base64_data(await avatar.read())
    else:
        avatar_value = None

    try:
        # this isnt supported by discord.py yet
        await bot.http.request(discord.http.Route("PATCH", f"/guilds/{message.guild.id}/members/@me"), json={"avatar": avatar_value})
        await message.followup.send("Avatar changed successfully!")
    except Exception:
        await message.followup.send("Failed to change avatar! Your image is too big or you are changing avatars too quickly.", ephemeral=True)
        return


@bot.tree.command(description="(ADMIN) Change the cat spawn/appear times")
@discord.app_commands.default_permissions(manage_guild=True)
@discord.app_commands.describe(
    minimum_time="In seconds, minimum possible time between spawns (leave both empty to reset)",
    maximum_time="In seconds, maximum possible time between spawns (leave both empty to reset)",
)
async def changetimings(
    message: discord.Interaction,
    minimum_time: Optional[int],
    maximum_time: Optional[int],
):
    channel = await Channel.get_or_none(channel_id=message.channel.id)
    if not channel:
        await message.response.send_message("This channel isnt setupped. Please select a valid channel.", ephemeral=True)
        return

    if not minimum_time and not maximum_time:
        # reset
        channel.spawn_times_min = 60
        channel.spawn_times_max = 600
        await channel.save()
        await message.response.send_message("Success! This channel is now reset back to usual spawning intervals.")
    elif minimum_time and maximum_time:
        if minimum_time < 20:
            await message.response.send_message("Sorry, but minimum time must be above 20 seconds.", ephemeral=True)
            return
        if maximum_time < minimum_time:
            await message.response.send_message(
                "Sorry, but maximum time must not be less than minimum time.",
                ephemeral=True,
            )
            return

        channel.spawn_times_min = minimum_time
        channel.spawn_times_max = maximum_time
        await channel.save()

        await message.response.send_message(
            f"Success! The spawn times are now {minimum_time} to {maximum_time} seconds. Please note the changes will only apply after the next spawn."
        )
    else:
        await message.response.send_message("Please input all times.", ephemeral=True)


@bot.tree.command(description="(ADMIN) Change the cat appear and cought message texts")
@discord.app_commands.default_permissions(manage_guild=True)
async def changemessage(message: discord.Interaction):
    caller = message.user
    channel = await Channel.get_or_none(channel_id=message.channel.id)
    if not channel:
        await message.response.send_message("pls setup this channel first", ephemeral=True)
        return

    # this is the silly popup when you click the button
    class InputModal(Modal):
        def __init__(self, type):
            super().__init__(
                title=f"Change {type} Message",
                timeout=VIEW_TIMEOUT,
            )

            self.type = type

            if self.type == "Appear":
                default = channel.appear if channel.appear else '{emoji} {type} cat has appeared! Type "cat" to catch it!'
            else:
                default = (
                    channel.cought
                    if channel.cought
                    else "{username} cought {emoji} {type} cat!!!!1!\nYou now have {count} cats of dat type!!!\nthis fella was cought in {time}!!!!"
                )

            self.input = TextInput(
                min_length=0,
                max_length=1000,
                label="Input",
                style=discord.TextStyle.long,
                required=False,
                default=default,
            )
            self.add_item(self.input)

        async def on_submit(self, interaction: discord.Interaction):
            await channel.refresh_from_db()
            if not channel:
                await message.response.send_message("this channel is not /setup-ed", ephemeral=True)
                return
            input_value = self.input.value

            # check if all placeholders are there
            if input_value != "":
                check = ["{emoji}", "{type}"] + (["{username}", "{count}", "{time}"] if self.type == "Cought" else [])

                for i in check:
                    if i not in input_value:
                        await interaction.response.send_message(
                            f"nuh uh! you are missing `{i}`.\nyou must include the placeholders exactly like they are shown, the values will be replaced by cat bot when it uses them.",
                            ephemeral=True,
                        )
                        return
                    elif input_value.count(i) > 10:
                        await interaction.response.send_message(f"nuh uh! you are using too much of `{i}`.", ephemeral=True)
                        return

                # check there are no emojis as to not break catching
                for i in allowedemojis:
                    if i in input_value:
                        await interaction.response.send_message(f"nuh uh! you cant use `{i}`. sorry!", ephemeral=True)
                        return

                icon = get_emoji("finecat")
                await interaction.response.send_message(
                    "Success! Here is a preview:\n"
                    + input_value.replace("{emoji}", str(icon))
                    .replace("{type}", "Fine")
                    .replace("{username}", "Cat Bot")
                    .replace("{count}", "1")
                    .replace("{time}", "69 years 420 days")
                )
            else:
                await interaction.response.send_message("Reset to defaults.")

            if self.type == "Appear":
                channel.appear = input_value
            else:
                channel.cought = input_value

            await channel.save()

    # helper to make the above popup appear
    async def ask_appear(interaction):
        nonlocal caller

        if interaction.user != caller:
            await do_funny(interaction)
            return

        modal = InputModal("Appear")
        await interaction.response.send_modal(modal)

    async def ask_catch(interaction):
        nonlocal caller

        if interaction.user != caller:
            await do_funny(interaction)
            return

        modal = InputModal("Cought")
        await interaction.response.send_modal(modal)

    embed = discord.Embed(
        title="Change appear and cought messages",
        description="""below are buttons to change them.
they are required to have all placeholders somewhere in them.
you must include the placeholders exactly like they are shown below, the values will be replaced by cat bot when it uses them.
that being:

for appear:
`{emoji}`, `{type}`

for cought:
`{emoji}`, `{type}`, `{username}`, `{count}`, `{time}`

missing any of these will result in a failure.
how to do mentions: `@everyone`, `@here`, `<@userid>`, `<@&roleid>`
to get ids, run `/getid` with the thing you want to mention.
if it doesnt work make sure the bot has mention permissions.
leave blank to reset.""",
        color=Colors.brown,
    )

    button1 = Button(label="Appear Message", style=ButtonStyle.blurple)
    button1.callback = ask_appear

    button2 = Button(label="Catch Message", style=ButtonStyle.blurple)
    button2.callback = ask_catch

    view = View(timeout=VIEW_TIMEOUT)
    view.add_item(button1)
    view.add_item(button2)

    await message.response.send_message(embed=embed, view=view)


@bot.tree.command(description="Get ID of a thing")
async def getid(message: discord.Interaction, thing: discord.User | discord.Role):
    await message.response.send_message(f"The ID of {thing.mention} is {thing.id}\nyou can use it in /changemessage like this: `{thing.mention}`")


@bot.tree.command(description="(ADMIN) tune various cat bot things")
@discord.app_commands.default_permissions(manage_guild=True)
async def settings(message: discord.Interaction):
    server = await Server.get_or_create(server_id=message.guild.id)

    async def toggle_parameter(interaction: discord.Interaction):
        if interaction.user != message.user:
            await do_funny(interaction)
            return
        await interaction.response.defer()
        parameter = interaction.data["custom_id"]
        server[parameter] = not server[parameter]
        await server.save()
        await interaction.edit_original_response(view=await settings_view())

    def make_button(parameter):
        if server[parameter]:
            button = Button(label="Disable", style=ButtonStyle.red, custom_id=parameter)
        else:
            button = Button(label="Enable", style=ButtonStyle.green, custom_id=parameter)
        button.callback = toggle_parameter
        return button

    async def settings_view():
        await server.refresh_from_db()
        view = LayoutView(timeout=VIEW_TIMEOUT)
        view.add_item(
            Container(
                f"## Cat Bot Settings for {message.guild.name}",
                Section(
                    "### Only in Setupped",
                    "If enabled, mutes reactions, responses, achievements and cattlepass progress outside of setupped channels",
                    make_button("only_setupped_channels"),
                ),
                Section("### Reactions", "Controls all Cat Bot reactions", make_button("do_reactions")),
                Section("### Responses", "Controls Cat Bot easter egg responses to specific messages sent", make_button("do_responses")),
                Section("### Mute Achievements", 'If enabled, will hide all Cat Bot "achievement get" messages', make_button("mute_achievements")),
                Section(
                    "### Auto-Delete Achievements",
                    'If enabled, will delete all "achievement get" messages after 10 seconds',
                    make_button("auto_delete_achievements"),
                ),
                Section(
                    "### Auto-Delete Catches",
                    'If enabled, will delete all "user cought" messages after ~10 seconds',
                    make_button("auto_delete_catches"),
                ),
                "===",
                Section("### Cat Rains", "Controls whether Cat Rains can happen", make_button("do_rain")),
                Section("### Catnip", "Controls whether catnip is accessible", make_button("do_catnip")),
                "===",
                Section(
                    "### Anti-Double Catch",
                    "If enabled, users must wait 5 minutes after catching in one channel to catch in another",
                    make_button("anti_double_catch"),
                ),
            )
        )
        return view

    await message.response.send_message(view=await settings_view())


@bot.tree.command(description="Get Daily cats")
async def daily(message: discord.Interaction):
    await message.response.send_message("there is no daily cats why did you even try this")
    await achemb(message, "daily", "followup")


@bot.tree.command(description="View when the last cat was caught in this channel, and when the next one might spawn")
async def last(message: discord.Interaction):
    channel = await Channel.get_or_none(channel_id=message.channel.id)
    nextpossible = ""

    try:
        lasttime = channel.lastcatches
        if int(lasttime) == 0:  # unix epoch check
            displayedtime = "forever ago"
        else:
            displayedtime = f"<t:{int(lasttime)}:R>"
    except Exception:
        displayedtime = "forever ago"

    if channel and not channel.cat:
        times = [channel.spawn_times_min, channel.spawn_times_max]
        nextpossible = f"\nthe next cat will spawn between <t:{int(lasttime) + times[0]}:R> and <t:{int(lasttime) + times[1]}:R>"

    if channel and channel.cat_rains:
        nextpossible += f"\ncat rain! {channel.cat_rains} cats remaining..."

    await message.response.send_message(f"the last cat in this channel was caught {displayedtime}.{nextpossible}")


@bot.tree.command(description="View all the juicy numbers and info behind cat types")
async def catalogue(message: discord.Interaction):
    embed = discord.Embed(title=f"{get_emoji('staring_cat')} The Catalogue", color=Colors.brown)
    for cat_type in cattypes:
        in_server = await Profile.sum(f"cat_{cat_type}", f'guild_id = $1 AND "cat_{cat_type}" > 0', message.guild.id)
        title = f"{get_emoji(cat_type.lower() + 'cat')} {cat_type}"
        if in_server == 0 or not in_server:
            in_server = 0
            title = f"{get_emoji('mysterycat')} ???"

        title += f" ({round((type_dict[cat_type] / sum(type_dict.values())) * 100, 2)}%)"

        embed.add_field(
            name=title,
            value=f"{round(sum(type_dict.values()) / type_dict[cat_type], 2)} value\n{in_server:,} in this server",
        )

    await message.response.send_message(embed=embed)


async def gen_stats(profile, star):
    stats = []
    user = await User.get_or_create(user_id=profile.user_id)

    # catching
    stats.append([get_emoji("staring_cat"), "Catching"])
    stats.append(["catches", "🐈", f"Catches: {profile.total_catches:,}{star}"])
    catch_time = "---" if profile.time >= 99999999999999 else round(profile.time, 3)
    slow_time = "---" if profile.timeslow == 0 else round(profile.timeslow / 3600, 2)
    stats.append(["time_records", "⏱️", f"Fastest: {catch_time}s, Slowest: {slow_time}h"])
    if profile.total_catches - profile.rain_participations != 0:
        stats.append(
            ["average_time", "⏱️", f"Average catch time: {profile.total_catch_time / (profile.total_catches - profile.rain_participations):,.2f}s{star}"]
        )
    else:
        stats.append(["average_time", "⏱️", f"Average catch time: N/A{star}"])
    stats.append(["purrfect_catches", "✨", f"Purrfect catches: {profile.perfection_count:,}{star}"])

    # catching boosts
    stats.append([get_emoji("prism"), "Prisms & Catnip"])
    prisms_crafted = await Prism.count("guild_id = $1 AND user_id = $2", profile.guild_id, profile.user_id)
    boosts_done = await Prism.sum("catches_boosted", "guild_id = $1 AND user_id = $2", profile.guild_id, profile.user_id)
    stats.append(["prism_crafted", get_emoji("prism"), f"Prisms crafted: {prisms_crafted:,}"])
    stats.append(["boosts_done", get_emoji("prism"), f"Boosts by owned prisms: {boosts_done:,}{star}"])
    stats.append(["boosted_catches", get_emoji("prism"), f"Prism-boosted catches: {profile.boosted_catches:,}{star}"])
    stats.append(["catnip_activations", get_emoji("catnip"), f"Cats gained from catnip: {profile.catnip_activations:,}"])
    stats.append(["catnip_bought", get_emoji("catnip"), f"Catnip levels reached: {profile.catnip_bought:,}"])
    stats.append(["highest_catnip_level", "⬆️", f"Highest catnip level: {profile.highest_catnip_level:,}"])
    stats.append(["bounties_complete", "🎯", f"Mafia bounties completed: {profile.bounties_complete:,}"])

    # battlepass
    stats.append(["⬆️", "Cattlepass & Voting"])
    stats.append(["total_votes", get_emoji("topgg"), f"Total votes: {user.total_votes:,}{star}"])
    stats.append(["current_daily_catch_streak", "🔥", f"Current daily catch streak: {user.daily_catch_streak} (max {max(user.daily_catch_streak, user.max_daily_streak):,}){star}"])
    seasons_complete = 0
    levels_complete = 0
    max_level = 0
    total_xp = 0
    # past seasons
    for season in profile.bp_history.split(";"):
        if not season:
            break
        season_num, season_lvl, season_progress = map(int, season.split(","))
        if season_num == 0:
            continue
        levels_complete += season_lvl
        total_xp += season_progress
        if season_lvl > 30:
            seasons_complete += 1
            total_xp += 1500 * (season_lvl - 31)
        if season_lvl > max_level:
            max_level = season_lvl

        for num, level in enumerate(config.battle["seasons"][str(season_num)]):
            if num >= season_lvl:
                break
            total_xp += level["xp"]
    # current season
    if profile.season != 0:
        levels_complete += profile.battlepass
        total_xp += profile.progress
        if profile.battlepass > 30:
            seasons_complete += 1
            total_xp += 1500 * (profile.battlepass - 31)
        if profile.battlepass > max_level:
            max_level = profile.battlepass

        for num, level in enumerate(config.battle["seasons"][str(profile.season)]):
            if num >= profile.battlepass:
                break
            total_xp += level["xp"]
    current_packs = 0
    for pack in pack_data:
        current_packs += profile[f"pack_{pack['name'].lower()}"]
    stats.append(["quests_completed", "✅", f"Quests completed: {profile.quests_completed:,}{star}"])
    stats.append(["seasons_completed", "🏅", f"Cattlepass seasons completed: {seasons_complete:,}"])
    stats.append(["levels_completed", "✅", f"Cattlepass levels completed: {levels_complete:,}"])
    stats.append(["packs_in_inventory", get_emoji("woodenpack"), f"Packs in inventory: {current_packs:,}"])
    stats.append(["packs_opened", get_emoji("goldpack"), f"Packs opened: {profile.packs_opened:,}"])
    stats.append(["pack_upgrades", get_emoji("diamondpack"), f"Pack upgrades: {profile.pack_upgrades:,}"])
    stats.append(["highest_ever_level", "🏆", f"Highest ever Cattlepass level: {max_level:,}"])
    stats.append(["total_xp_earned", "🧮", f"Total Cattlepass XP earned: {total_xp:,}"])

    # rains & supporter
    stats.append(["☔", "Rains & Blessings"])
    stats.append(["current_rain_minutes", "☔", f"Current rain minutes: {user.rain_minutes:,}"])
    stats.append(["rain_minutes_bought", "☔", f"Rain minutes bought: {user.rain_minutes_bought:,}"])
    stats.append(["cats_caught_during_rains", "☔", f"Cats caught during rains: {profile.rain_participations:,}{star}"])
    stats.append(["rain_minutes_started", "☔", f"Rain minutes started: {profile.rain_minutes_started:,}{star}"])
    stats.append(["cats_blessed", "🌠", f"Cats blessed: {user.cats_blessed:,}"])

    # misc
    stats.append(["❓", "Misc"])
    portfolio_value = 0
    for stock in stock_data:
        stock_price = await get_stock_price(stock["ticker"])
        amount_owned = profile[f"stock_{stock['ticker'].lower()}"]
        item_value = stock_price * amount_owned
        portfolio_value += item_value
    if profile.ttt_played != 0:
        stats.append(
            ["ttc_win_rate", "⭕", f"Tic Tac Toe wins: {profile.ttt_won:,} (winrate: {(profile.ttt_won + profile.ttt_draws) / profile.ttt_played * 100:.2f}%)"]
        )
    else:
        stats.append(["ttc_win_rate", "⭕", "Tic Tac Toe wins: 0 (winrate: 0%)"])
    stats.append(["casino_spins", "🎰", f"Casino spins: {profile.gambles:,}"])
    stats.append(["slot_spins", "🎰", f"Slot spins: {profile.slot_spins:,}, wins: {profile.slot_wins:,}, big wins: {profile.slot_big_wins:,}"])
    stats.append(["roulette_spins", "💰", f"Roulette spins: {profile.roulette_spins:,}, wins: {profile.roulette_wins:,}"])
    stats.append(["portfolio_value", "🪙", f"Portfolio value: {portfolio_value:,}"])
    stats.append(["cookies", "🍪", f"Cookies clicked: {profile.cookies:,}"])
    stats.append(["pig_high_score", "🎲", f"Pig high score: {profile.best_pig_score:,}"])
    stats.append(["cats_gifted", "🎁", f"Cats gifted: {profile.cats_gifted:,}{star}"])
    stats.append(["cats_received_as_gift", "🎁", f"Cats received as gift: {profile.cat_gifts_recieved:,}{star}"])
    stats.append(["trades_completed", "💱", f"Trades completed: {profile.trades_completed}{star}"])
    stats.append(["cats_traded", "💱", f"Cats traded: {profile.cats_traded:,}{star}"])
    if profile.user_id == 553093932012011520:
        stats.append(["owner", get_emoji("neocat"), "a cute catgirl :3"])
    return stats


@bot.tree.command(name="stats", description="View some advanced stats")
@discord.app_commands.rename(person_id="user")
@discord.app_commands.describe(person_id="Person to view the stats of!")
async def stats_command(message: discord.Interaction, person_id: Optional[discord.User]):
    await message.response.defer()
    if not person_id:
        person_id = message.user
    profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=person_id.id)
    star = "*" if not profile.new_user else ""

    stats = await gen_stats(profile, star)
    embedVar = discord.Embed(
        title=f"{person_id.name}'s Stats",
        color=Colors.brown,
    )

    current_category = None
    current_lines = []

    for stat in stats:
        if len(stat) == 2:
            # remove prev cat
            if current_category:
                embedVar.add_field(name=current_category, value="\n".join(current_lines), inline=True)

            # start new cat
            current_category = f"{stat[0]} {stat[1]}"
            current_lines = []

        elif len(stat) == 3:
            current_lines.append(stat[2])

    # add last cat
    if current_category:
        embedVar.add_field(name=current_category, value="\n".join(current_lines), inline=True)

    if star:
        embedVar.set_footer(text="* this stat is only tracked since February 2025")

    await message.followup.send(embed=embedVar)


async def gen_inventory(message, person_id):
    # check if we are viewing our own inv or some other person
    if person_id is None:
        person_id = message.user
    me = bool(person_id == message.user)
    person = await Profile.get_or_create(guild_id=message.guild.id, user_id=person_id.id)
    user = await User.get_or_create(user_id=person_id.id)

    # around here we count aches
    unlocked = 0
    minus_achs = 0
    minus_achs_count = 0
    for k in ach_names:
        is_ach_hidden = ach_list[k]["category"] == "Hidden"
        if is_ach_hidden:
            minus_achs_count += 1
        # has_ach checks the JSONB unlocked_aches array first, then falls back
        # to the legacy boolean column. New aches (catstore_*, mafia_*, etc.)
        # only exist in the JSONB array — probing them via person[k] raises
        # KeyError because no column was ever created for them.
        if person.has_ach(k):
            if is_ach_hidden:
                minus_achs += 1
            else:
                unlocked += 1
    total_achs = len(ach_list) - minus_achs_count
    minus_achs = "" if minus_achs == 0 else f" + {minus_achs}"

    # count prism stuff
    prisms = await Prism.collect_limit(["name"], "guild_id = $1 AND user_id = $2", message.guild.id, person_id.id)
    total_count = await Prism.count("guild_id = $1", message.guild.id)
    user_count = len(prisms)
    global_boost = PRISM_BOOST_GLOBAL_COEF * math.log(2 * total_count + 1)
    prism_boost = round((global_boost + PRISM_BOOST_USER_COEF * math.log(2 * user_count + 1)) * 100, 3)
    if len(prisms) == 0:
        prism_list = "None"
    elif len(prisms) <= 3:
        prism_list = ", ".join([i.name for i in prisms])
    else:
        prism_list = f"{prisms[0].name}, {prisms[1].name}, {len(prisms) - 2} more..."

    emoji_prefix = str(user.emoji) + " " if user.emoji else ""

    if user.color:
        color = user.color
    else:
        color = "#6E593C"

    await refresh_quests(person)
    try:
        needed_xp = config.battle["seasons"][str(person.season)][person.battlepass]["xp"]
    except Exception:
        needed_xp = 1500

    stats = await gen_stats(person, "")
    highlighted_stat = None
    for stat in stats:
        if stat[0] == person.highlighted_stat:
            highlighted_stat = stat
            break
    if not highlighted_stat:
        for stat in stats:
            if stat[0] == "time_records":
                highlighted_stat = stat
                break

    embedVar = discord.Embed(
        title=f"{emoji_prefix}{person_id.name.replace('_', r'\_')}",
        description=f"{highlighted_stat[1]} {highlighted_stat[2]}\n{get_emoji('ach')} Achievements: {unlocked}/{total_achs}{minus_achs}\n⬆️ Cattlepass Level {person.battlepass} ({person.progress}/{needed_xp} XP)",
        color=discord.Colour.from_str(color),
    )

    debt = False
    give_collector = True
    total = 0
    valuenum = 0

    # for every cat
    cat_desc = ""
    for i in cattypes:
        icon = get_emoji(i.lower() + "cat")
        cat_num = person[f"cat_{i}"]
        if cat_num < 0:
            debt = True
        if cat_num != 0:
            total += cat_num
            valuenum += (sum(type_dict.values()) / type_dict[i]) * cat_num
            cat_desc += f"{icon} **{i}** {cat_num:,}\n"
        else:
            give_collector = False

    if user.custom:
        icon = get_emoji(str(user.user_id) + "cat")
        cat_desc += f"{icon} **{user.custom}** {user.custom_num:,}"

    if len(cat_desc) == 0:
        cat_desc = f"u hav no cats {get_emoji('cat_cry')}"

    if embedVar.description:
        embedVar.description += f"\n{get_emoji('staring_cat')} Cats: {total:,}, Value: {round(valuenum):,}\n{get_emoji('prism')} Prisms: {prism_list} ({prism_boost}%)\n\n{cat_desc}"

    if user.image.startswith("https://cdn.discordapp.com/attachments/"):
        embedVar.set_thumbnail(url=user.image)

    give_achs = []
    if me:
        # give some aches if we are vieweing our own inventory
        if len(news_list) > len(user.news_state.strip()) or "0" in user.news_state.strip()[-4:]:
            embedVar.set_author(name="You have unread news! /news")

        if give_collector:
            give_achs.append("collecter")

        if person.time <= 5:
            give_achs.append("fast_catcher")
        if person.timeslow >= 3600:
            give_achs.append("slow_catcher")

        if total >= 100:
            give_achs.append("second")
        if total >= 1000:
            give_achs.append("third")
        if total >= 10000:
            give_achs.append("fourth")

        if unlocked >= 15:
            give_achs.append("achiever")

        if debt:
            bot.loop.create_task(debt_cutscene(message, person))

    return embedVar, give_achs


@bot.tree.command(description="View your inventory")
@discord.app_commands.rename(person_id="user")
@discord.app_commands.describe(person_id="Person to view the inventory of!")
async def inventory(message: discord.Interaction, person_id: Optional[discord.User]):
    await message.response.defer()
    if not person_id:
        person_id = message.user
    person = await Profile.get_or_create(guild_id=message.guild.id, user_id=person_id.id)
    user = await User.get_or_create(user_id=message.user.id)
    stats = await gen_stats(person, "")

    async def edit_profile(interaction: discord.Interaction):
        if interaction.user.id != person_id.id:
            await do_funny(interaction)
            return

        def stat_select(category):
            options = [discord.SelectOption(emoji="⬅️", label="Back", value="back")]
            track = False
            for stat in stats:
                if len(stat) == 2:
                    track = bool(stat[1] == category)
                if len(stat) == 3 and track:
                    options.append(discord.SelectOption(value=stat[0], emoji=stat[1], label=stat[2]))

            select = discord.ui.Select(placeholder="Edit highlighted stat... (2/2)", options=options)

            async def select_callback(interaction: discord.Interaction):
                await interaction.response.defer()
                if select.values[0] == "back":
                    view = View(timeout=VIEW_TIMEOUT)
                    view.add_item(category_select())
                    await interaction.edit_original_response(view=view)
                else:
                    # update the stat
                    person.highlighted_stat = select.values[0]
                    await person.save()
                    await interaction.edit_original_response(content="Highlighted stat updated!", embed=None, view=None)

            select.callback = select_callback
            return select

        def category_select():
            options = []
            for stat in stats:
                if len(stat) != 2:
                    continue
                options.append(discord.SelectOption(emoji=stat[0], label=stat[1], value=stat[1]))

            select = discord.ui.Select(placeholder="Edit highlighted stat... (1/2)", options=options)

            async def select_callback(interaction: discord.Interaction):
                # im 13 and this is deep (nesting)
                # and also please dont think about the fact this is async inside of sync :3
                await interaction.response.defer()
                view = View(timeout=VIEW_TIMEOUT)
                view.add_item(stat_select(select.values[0]))
                await interaction.edit_original_response(view=view)

            select.callback = select_callback
            return select

        highlighted_stat = None
        for stat in stats:
            if stat[0] == person.highlighted_stat:
                highlighted_stat = stat
                break
        if not highlighted_stat:
            for stat in stats:
                if stat[0] == "time_records":
                    highlighted_stat = stat
                    break

        view = View(timeout=VIEW_TIMEOUT)
        view.add_item(category_select())

        if user.premium:
            if not user.color:
                user.color = "#6E593C"
            description = f"""👑 __Supporter Settings__
Global, change with `/editprofile`.
**Color**: {user.color.lower() if user.color.upper() not in ["", "#6E593C"] else "Default"}
**Emoji**: {user.emoji if user.emoji else "None"}
**Image**: {"Yes" if user.image.startswith("https://cdn.discordapp.com/attachments/") else "No"}

__Highlighted Stat__
{highlighted_stat[1]} {highlighted_stat[2]}"""

            embed = discord.Embed(
                title=f"{(user.emoji + ' ') if user.emoji else ''}Edit Profile", description=description, color=discord.Colour.from_str(user.color)
            )
            if user.image.startswith("https://cdn.discordapp.com/attachments/"):
                embed.set_thumbnail(url=user.image)

        else:
            description = f"""👑 __Supporter Settings__
Global, buy anything from [the store](https://catbot.shop) to unlock.
👑 **Color**
👑 **Emoji**
👑 **Image**

__Highlighted Stat__
{highlighted_stat[1]} {highlighted_stat[2]}"""

            embed = discord.Embed(title="Edit Profile", description=description, color=Colors.brown)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    embedVar, give_achs = await gen_inventory(message, person_id)

    embedVar.set_footer(text=rain_shill)

    if person_id.id == message.user.id:
        view = View(timeout=VIEW_TIMEOUT)
        btn = Button(emoji="📝", label="Edit", style=ButtonStyle.blurple)
        btn.callback = edit_profile
        view.add_item(btn)
        await message.followup.send(embed=embedVar, view=view)
    else:
        await message.followup.send(embed=embedVar)

    for ach in give_achs:
        await achemb(message, ach, "followup")


async def rain_recovery_loop(channel):
    logging.debug("Rain started, cats %d", channel.cat_rains)
    while True:
        await asyncio.sleep(5)
        await channel.refresh_from_db()
        if channel.cat_rains <= 0:
            break
        if channel.cat_rains and not channel.cat and time.time() - channel.rain_should_end > 5:
            await spawn_cat(str(channel.channel_id))
            channel.cat_rains -= 1
            await channel.save()


async def rain_end(message, channel, force_summary=None):
    try:
        for _ in range(3):
            await message.channel.send("# :bangbang: cat rain has ended")
            await asyncio.sleep(0.4)
    except Exception:
        pass

    guild = await bot.fetch_guild(message.guild.id)
    if isinstance(message.channel, discord.Thread):
        api_channel = await guild.fetch_channel(message.channel.parent_id)
    else:
        api_channel = await guild.fetch_channel(message.channel.id)

    lock_success = False
    try:
        me_overwrites = api_channel.overwrites_for(message.guild.me)
        me_overwrites.send_messages = True

        everyone_overwrites = api_channel.overwrites_for(guild.default_role)
        current_perm = everyone_overwrites.send_messages
        everyone_overwrites.send_messages = False

        await asyncio.gather(
            api_channel.set_permissions(guild.default_role, overwrite=everyone_overwrites),
            api_channel.set_permissions(message.guild.me, overwrite=me_overwrites),
        )
        lock_success = True
    except Exception:
        pass

    # rain summary
    try:
        rain_server = force_summary
        if not rain_server:
            if channel.channel_id not in config.rain_starter or channel.channel_id not in config.cat_cought_rain:
                return
            rain_server = config.cat_cought_rain[channel.channel_id]

        # you can throw out the name of the emoji to save on characters
        pack_names = ["Wooden", "Stone", "Bronze", "Silver", "Gold", "Platinum", "Diamond", "Celestial"]
        pack_yeah = {"Wooden": 1, "Stone": 0.9, "Bronze": 0.8, "Silver": 0.7, "Gold": 0.6, "Platinum": 0.5, "Diamond": 0.4, "Celestial": 0.3}
        rain_packs = []
        rain_cats = []

        for key in rain_server.keys():
            if key in cattypes:
                rain_cats.append(key)
            if key in pack_names:
                rain_packs.append(key)

        funny_cat_emojis = {k: re.sub(r":[A-Za-z0-9_]*:", ":i:", get_emoji(k.lower() + "cat"), count=1) for k in rain_cats}
        funny_pack_emojis = {k: re.sub(r":[A-Za-z0-9_]*:", ":i:", get_emoji(k.lower() + "pack"), count=1) for k in rain_packs}

        funny_emojis = funny_cat_emojis | funny_pack_emojis

        reverse_mapping = {}

        for thing_type, user_ids in rain_server.items():
            for user_id in user_ids:
                if user_id not in reverse_mapping:
                    reverse_mapping[user_id] = []
                reverse_mapping[user_id].append(thing_type)

        evil_types = []
        epic_fail = False
        thingtypes = cattypes + pack_names
        for cat_type in thingtypes:
            part_one = "## Rain Summary\n"

            for user_id, cat_types in sorted(reverse_mapping.items(), key=lambda item: len(item[1]), reverse=True):
                show_cats = ""
                shortened_types = False
                dictdict = type_dict | pack_yeah
                cat_types.sort(reverse=True, key=lambda x: dictdict[x])
                pack_amount = 0
                for cat_type_two in cat_types:
                    if cat_type_two in evil_types:
                        shortened_types = True
                        continue
                    if cat_type_two in pack_names:
                        pack_amount += 1
                    show_cats += funny_emojis[cat_type_two]
                if show_cats != "":
                    if shortened_types:
                        show_cats = ": ..." + show_cats
                    else:
                        show_cats = ": " + show_cats
                if str(config.rain_starter[channel.channel_id]) in str(user_id):
                    part_one += "☔ "
                disambig = f"({len(cat_types)})"
                if pack_amount:
                    disambig = f"({len(cat_types) - pack_amount} {get_emoji('finecat')}, {pack_amount} {get_emoji('woodenpack')})"
                part_one += f"{user_id} {disambig}{show_cats}\n"

            if not lock_success and not epic_fail:
                part_one += "-# 💡 Cat Bot will automatically lock the channel for a few seconds after a rain if you give it `Manage Permissions`"

            if len(part_one) > 4000:
                evil_types.append(cat_type)
                epic_fail = True
                continue

            parts = [part_one]

            if epic_fail:
                part_two = ""
                for cat_type in thingtypes:
                    if cat_type not in rain_server.keys():
                        continue
                    if len(rain_server[cat_type]) > 5:
                        part_two += f"{funny_emojis[cat_type]} *{len(rain_server[cat_type])} catches*\n"
                    else:
                        part_two += f"{funny_emojis[cat_type]} {' '.join(rain_server[cat_type])}\n"

                if not lock_success:
                    part_two += "-# 💡 Cat Bot will automatically lock the channel for a few seconds after a rain if you give it `Manage Permissions`"

                parts.append(part_two)

            for rain_msg in parts:
                if ":i:" not in rain_msg:
                    continue
                # this is to bypass character limit up to 4k
                v = LayoutView()
                v.add_item(TextDisplay(rain_msg))
                try:
                    await message.channel.send(view=v)
                except Exception:
                    pass

            break

        del config.cat_cought_rain[channel.channel_id]
        del config.rain_starter[channel.channel_id]

        await asyncio.sleep(2)
    except discord.Forbidden:
        pass
    finally:
        if lock_success:
            everyone_overwrites = api_channel.overwrites_for(guild.default_role)
            everyone_overwrites.send_messages = current_perm
            await api_channel.set_permissions(guild.default_role, overwrite=everyone_overwrites)


@bot.tree.command(description="(disabled on this self-hosted instance)")
async def plush(message: discord.Interaction):
    await message.response.send_message("This command is disabled on this self-hosted instance.", ephemeral=True)


@bot.tree.command(description="its raining cats")
async def rain(message: discord.Interaction):
    user = await User.get_or_create(user_id=message.user.id)
    profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    server = await Server.get_or_create(server_id=message.guild.id)

    if not user.rain_minutes:
        user.rain_minutes = 0
        await user.save()

    if not user.claimed_free_rain:
        user.rain_minutes += 2
        user.claimed_free_rain = True
        await user.save()

    server_rains = ""
    server_minutes = profile.rain_minutes
    if server_minutes > 0:
        server_rains = f" (+**{server_minutes}** bonus minutes)"

    embed = discord.Embed(
        title="☔ Cat Rains",
        description=f"""Cat Rains are power-ups which spawn cats super fast for a limited amounts of time in a channel of your choice.

You can get those by buying them at our [store](<https://catbot.shop>) or by winning them in an event.
This bot is developed by a single person so buying one would be very appreciated.
As a bonus, you will get access to /editprofile and /customcat commands!
Fastest times are not saved during rains.

You currently have **{user.rain_minutes:,}** minutes of rains{server_rains}.""",
        color=Colors.brown,
    )

    # this is the silly popup when you click the button
    class RainModal(Modal):
        def __init__(self, type):
            super().__init__(
                title="Start a Cat Rain!",
                timeout=VIEW_TIMEOUT,
            )

            self.input = TextInput(
                min_length=1,
                max_length=5,
                label="Duration in minutes",
                style=discord.TextStyle.short,
                required=True,
                placeholder="2",
            )
            self.add_item(self.input)

        async def on_submit(self, interaction: discord.Interaction):
            try:
                duration = int(self.input.value)
            except Exception:
                await interaction.response.send_message("number pls", ephemeral=True)
                return
            await do_rain(interaction, duration)

    async def do_rain(interaction, rain_length):
        # i LOOOOVE checks
        user = await User.get_or_create(user_id=interaction.user.id)
        profile = await Profile.get_or_create(guild_id=interaction.guild.id, user_id=interaction.user.id)
        channel = await Channel.get_or_none(channel_id=interaction.channel.id)
        await server.refresh_from_db()

        if not user.rain_minutes:
            user.rain_minutes = 0
            await user.save()

        if not user.claimed_free_rain:
            user.rain_minutes += 2
            user.claimed_free_rain = True
            await user.save()

        if not server.do_rain:
            await interaction.response.send_message("rain is disabled in this server.", ephemeral=True)
            return

        if rain_length < 1:
            await interaction.response.send_message("last time i checked weather can not change for a negative amount of time", ephemeral=True)
            return

        if rain_length > user.rain_minutes + profile.rain_minutes or user.rain_minutes < 0:
            await interaction.response.send_message(
                "you dont have enough rain! buy some more [here](<https://catbot.shop>)",
                ephemeral=True,
            )
            return

        if not channel:
            await interaction.response.send_message("please run this in a setupped channel.", ephemeral=True)
            return

        if channel.cat:
            await interaction.response.send_message("please catch the cat in this channel first.", ephemeral=True)
            return

        if channel.cat_rains > 0:
            await interaction.response.send_message("there is already a rain running!", ephemeral=True)
            return

        profile.rain_minutes_started += rain_length
        channel.cat_rains = math.ceil(rain_length * 60 / 2.75)
        channel.yet_to_spawn = 0
        await channel.save()
        if profile.rain_minutes:
            if rain_length > profile.rain_minutes:
                user.rain_minutes -= rain_length - profile.rain_minutes
                profile.rain_minutes = 0
            else:
                profile.rain_minutes -= rain_length
        else:
            user.rain_minutes -= rain_length
        await user.save()
        await profile.save()
        await interaction.response.send_message(f"{rain_length:,}m cat rain was started by {interaction.user.mention}!")
        try:
            ch = bot.get_partial_messageable(config.RAIN_CHANNEL_ID)
            await ch.send(f"{interaction.user.id} started {rain_length}m rain in {interaction.channel.id} ({user.rain_minutes} left)")
        except Exception:
            pass

        config.cat_cought_rain[channel.channel_id] = {}
        config.rain_starter[channel.channel_id] = interaction.user.id
        await spawn_cat(str(interaction.channel.id))
        await rain_recovery_loop(channel)

    async def rain_modal(interaction):
        modal = RainModal(interaction.user)
        await interaction.response.send_modal(modal)

    button = Button(label="Rain!", style=ButtonStyle.blurple, disabled=not server.do_rain)
    button.callback = rain_modal

    shopbutton = Button(
        emoji="🛒",
        label="Store",
        url="https://catbot.shop",
    )

    view = View(timeout=VIEW_TIMEOUT)
    view.add_item(button)
    view.add_item(shopbutton)

    await message.response.send_message(embed=embed, view=view)


@bot.tree.command(description="Buy Cat Rains!")
async def store(message: discord.Interaction):
    await message.response.send_message("☔ Cat rains make cats spawn instantly! Make your server active, get more cats and have fun!\n<https://catbot.shop>")


if config.DONOR_CHANNEL_ID:

    @bot.tree.command(description="(SUPPORTER) Get a cosmetic custom cat! (non-tradeable, doesn't count towards anything)")
    @discord.app_commands.describe(
        name='The name of your custom cat. ("None" to remove)',
        image="Static/animated GIF, PNG, JPEG, WEBP, AVIF below 256 KB. Static images will be auto-compressed.",
        amount="The amount of your custom cat you want.",
    )
    async def customcat(message: discord.Interaction, name: Optional[str], image: Optional[discord.Attachment], amount: Optional[int]):
        global emojis
        user = await User.get_or_create(user_id=message.user.id)
        if not user.premium:
            await message.response.send_message(
                "👑 This feature is supporter-only!\nBuy anything from Cat Bot Store to unlock custom cats!\n<https://catbot.shop>",
                ephemeral=True,
            )
            return

        if image and image.content_type not in ["image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"]:
            await message.response.send_message("Invalid file type! Please upload a PNG, JPEG, GIF, WebP, or AVIF image.", ephemeral=True)
            return

        await message.response.defer(ephemeral=True)

        em_name = str(user.user_id) + "cat"

        if name:
            user.custom = name if name.lower() != "none" else ""
        if amount:
            user.custom_num = amount
        if image:
            if customcatcooldown.get(message.user.id, 0) + 300 > time.time():
                await message.followup.send("You can only upload a new custom cat image every 5 minutes.", ephemeral=True)
                return
            customcatcooldown[message.user.id] = time.time()
            try:
                emojiss = {emoji.name: emoji for emoji in await bot.fetch_application_emojis()}
                if em_name in emojiss:
                    await emojiss[em_name].delete()
                data = await image.read()
                if image.content_type.startswith("image/gif"):
                    new_em = await bot.create_application_emoji(name=em_name, image=data)
                else:
                    img = Image.open(io.BytesIO(data))
                    img.thumbnail((128, 128))
                    with io.BytesIO() as image_binary:
                        img.save(image_binary, format="PNG")
                        image_binary.seek(0)
                        new_em = await bot.create_application_emoji(name=em_name, image=image_binary.getvalue())
                emojiss[em_name] = new_em
                emojis = {k: str(v) for k, v in emojiss.items()}
            except Exception:
                await message.followup.send("Error creating emoji. Make sure your image is valid and below 256KB.", ephemeral=True)
                return
        await user.save()
        embedVar, _ = await gen_inventory(message, message.user)
        await message.followup.send("Success! Here is a preview:", embed=embedVar, ephemeral=True)

    @bot.tree.command(description="(SUPPORTER) Bless random Cat Bot users with doubled cats!")
    async def bless(message: discord.Interaction):
        user = await User.get_or_create(user_id=message.user.id)
        do_edit = False

        if user.blessings_enabled and user.username != message.user.name:
            user.username = message.user.name
            await user.save()

        async def toggle_bless(interaction):
            if interaction.user.id != message.user.id:
                await do_funny(interaction)
                return
            nonlocal do_edit, user
            do_edit = True
            await interaction.response.defer()
            await user.refresh_from_db()
            if not user.premium:
                return
            user.blessings_enabled = not user.blessings_enabled
            user.username = message.user.name
            await user.save()
            await regen(interaction)

        async def toggle_anon(interaction):
            if interaction.user.id != message.user.id:
                await do_funny(interaction)
                return
            nonlocal do_edit, user
            do_edit = True
            await interaction.response.defer()
            await user.refresh_from_db()
            user.blessings_anonymous = not user.blessings_anonymous
            await user.save()
            await regen(interaction)

        async def regen(interaction):
            if user.blessings_anonymous:
                blesser = "💫 Anonymous Supporter"
            else:
                blesser = f"{user.emoji or '💫'} {message.user.name}"

            user_bless_chance = user.rain_minutes_bought * 0.0001
            global_bless_chance = await User.sum("rain_minutes_bought", "blessings_enabled = true") * 0.0001

            view = View(timeout=VIEW_TIMEOUT)
            if not user.premium:
                bbutton = Button(label="Supporter Required!", url="https://catbot.shop", emoji="👑")
            else:
                bbutton = Button(
                    emoji="🌟",
                    label=f"{'Disable' if user.blessings_enabled else 'Enable'} Blessings",
                    style=ButtonStyle.red if user.blessings_enabled else ButtonStyle.green,
                )
                bbutton.callback = toggle_bless

            view = LayoutView(timeout=VIEW_TIMEOUT)
            container = Container(
                "## :stars: Cat Blessings",
                "When enabled, random Cat Bot users will have their cats blessed by you - and their catches will be doubled! Your bless chance increases by *0.0001%* per minute of rain bought.",
                "===",
                f"Cats you blessed: **{user.cats_blessed:,}**\nYour bless chance is **{user_bless_chance:.4f}%**\nGlobal bless chance is **{global_bless_chance:.4f}%**",
                "===",
                Section(bbutton, f"Your blessings are currently **{'enabled' if user.blessings_enabled else 'disabled'}**."),
            )

            if user.premium:
                abutton = Button(
                    emoji="🕵️",
                    label=f"{'Disable' if user.blessings_anonymous else 'Enable'} Anonymity",
                    style=ButtonStyle.red if user.blessings_anonymous else ButtonStyle.green,
                )
                abutton.callback = toggle_anon

                container.add_item(Section(abutton, f"{'' if user.blessings_enabled else '*(disabled)* '}{blesser} blessed your catch and it got doubled!"))

            view.add_item(container)

            if do_edit:
                await message.edit_original_response(view=view)
            else:
                await message.response.send_message(view=view)

        await regen(message)

    @bot.tree.command(description="(SUPPORTER) Customize your profile!")
    @discord.app_commands.rename(provided_emoji="emoji")
    @discord.app_commands.describe(
        color="Color for your profile in hex form (e.g. #6E593C)",
        provided_emoji="A default Discord emoji to show near your username.",
        image="A square image to show in top-right corner of your profile.",
    )
    async def editprofile(
        message: discord.Interaction,
        color: Optional[str],
        provided_emoji: Optional[str],
        image: Optional[discord.Attachment],
    ):
        if not config.DONOR_CHANNEL_ID:
            return

        user = await User.get_or_create(user_id=message.user.id)
        if not user.premium:
            await message.response.send_message(
                "👑 This feature is supporter-only!\nBuy anything from Cat Bot Store to unlock profile customization!\n<https://catbot.shop>"
            )
            return

        if provided_emoji and discord_emoji.to_discord(provided_emoji.strip(), get_all=False, put_colons=False):
            user.emoji = provided_emoji.strip()

        if color:
            match = re.search(r"^#(?:[0-9a-fA-F]{3}){1,2}$", color)
            if match:
                user.color = match.group(0)
        if image and image.content_type in ["image/png", "image/jpeg", "image/gif", "image/webp"]:
            # reupload image
            channeley = bot.get_partial_messageable(config.DONOR_CHANNEL_ID)
            file = await image.to_file()
            if "." in file.filename:
                ext = file.filename[file.filename.rfind(".") :]
                file.filename = "i" + ext
            else:
                file.filename = "i"
            msg = await channeley.send(file=file)
            user.image = msg.attachments[0].url
        await user.save()
        embedVar, _ = await gen_inventory(message, message.user)
        await message.response.send_message("Success! Here is a preview:", embed=embedVar)


@bot.tree.command(description="View and open packs")
async def packs(message: discord.Interaction):
    async def process_pack_opening(limit=None):
        await user.refresh_from_db()

        pack_names = [pack["name"] for pack in pack_data]
        total_pack_count = sum(user[f"pack_{pack_id.lower()}"] for pack_id in pack_names)

        if total_pack_count < 1:
            return None

        real_to_open = total_pack_count
        if limit:
            real_to_open = min(limit, total_pack_count)

        display_cats = real_to_open >= 50
        results_header = []
        results_detail = []
        results_percat = {cat: 0 for cat in cattypes}
        total_upgrades = 0
        opened_so_far = 0

        for level, pack in enumerate(pack_names):
            if opened_so_far >= real_to_open:
                break
            logging.debug("Opened pack %s", pack)
            pack_id = f"pack_{pack.lower()}"
            this_packs_count = user[pack_id]
            if this_packs_count < 1:
                continue

            opening_this = min(this_packs_count, real_to_open - opened_so_far)

            results_header.append(f"{opening_this:,}x {get_emoji(pack.lower() + 'pack')}")
            for _ in range(opening_this):
                chosen_type, cat_amount, upgrades, rewards = get_pack_rewards(level, is_single=False)
                total_upgrades += upgrades
                if not display_cats:
                    results_detail.append(rewards)
                results_percat[chosen_type] += cat_amount

            user[pack_id] -= opening_this
            opened_so_far += opening_this

        user.packs_opened += opened_so_far
        user.pack_upgrades += total_upgrades
        for cat_type, cat_amount in results_percat.items():
            user[f"cat_{cat_type}"] += cat_amount
        await user.save()
        for cat_type, cat_amount in results_percat.items():
            if cat_amount > 0:
                await mark_discovered(user, cat_type)

        final_header = f"Opened {opened_so_far:,} packs!"
        pack_list = "**" + ", ".join(results_header) + "**"
        final_result = "\n".join(results_detail)

        if display_cats or len(final_result) > 4000 - len(pack_list):
            cat_summary = []
            for cat in cattypes:
                if results_percat[cat] > 0:
                    cat_summary.append(f"{get_emoji(cat.lower() + 'cat')} x{results_percat[cat]:,}")
            final_result = "\n".join(cat_summary)

        if len(final_result) > 0:
            final_result = "\n\n" + final_result

        return discord.Embed(title=final_header, description=f"{pack_list}{final_result}", color=Colors.brown)

    async def confirm_open_all(interaction: discord.Interaction):
        if interaction.user != message.user:
            await do_funny(interaction)
            return

        async def do_it(interaction):
            await interaction.response.defer()
            await interaction.delete_original_response()
            await open_all_packs(interaction)

        confirm_view = View(timeout=VIEW_TIMEOUT)
        yes_btn = Button(label="Yes, Open All", style=ButtonStyle.green)
        yes_btn.callback = do_it
        confirm_view.add_item(yes_btn)

        await interaction.response.send_message("Are you sure you want to open ALL your packs?", view=confirm_view, ephemeral=True)

    def gen_view(user):
        view = View(timeout=VIEW_TIMEOUT)
        empty = True
        has_special = False
        total_amount = 0
        for pack in pack_data:
            if user[f"pack_{pack['name'].lower()}"] < 1:
                continue
            empty = False
            amount = user[f"pack_{pack['name'].lower()}"]
            total_amount += amount
            button = Button(
                emoji=get_emoji(pack["name"].lower() + "pack"),
                label=f"{pack['name']} ({amount:,})",
                style=ButtonStyle.blurple if not pack["special"] else ButtonStyle.green,
                custom_id=pack["name"],
            )
            button.callback = open_pack
            view.add_item(button)
            if pack["special"]:
                has_special = True
        if empty:
            view.add_item(Button(label="No packs left!", disabled=True))
        if total_amount > 5:
            button = Button(label=f"Open all! ({total_amount:,})", style=ButtonStyle.gray)
            button.callback = confirm_open_all
            view.add_item(button)
        return view, has_special

    def get_pack_rewards(level: int, is_single=True, _cascade_depth=0):
        # returns cat_type, cat_amount, upgrades, verbal_output
        #
        # _cascade_depth tracks how many fail-cascades have already happened.
        # 0 = original open. 1 = post-cascade (or post-Wooden-re-roll). At
        # depth >= 1, a sub-1 fail goes straight to "3 Fine cats" consolation
        # with no further retry (per "fails more than once → 3 Fine cats").
        reward_texts = []
        build_string = ""
        upgrades = 0
        if not is_single:
            build_string = get_emoji(pack_data[level]["name"].lower() + "pack")

        is_special = pack_data[level]["special"]
        bump_boost = 7 / 3 if is_special else 1
        first_boost = 1
        if is_special:
            # find first non-special level
            while pack_data[level + first_boost]["special"]:
                first_boost += 1

        # bump rarity
        while random.uniform(1, 100) <= pack_data[level]["upgrade"] * bump_boost:
            if is_single:
                reward_texts.append(f"{get_emoji(pack_data[level]['name'].lower() + 'pack')} {pack_data[level]['name']}\n" + build_string)
                build_string = f"Upgraded from {get_emoji(pack_data[level]['name'].lower() + 'pack')} {pack_data[level]['name']}!\n" + build_string
            else:
                build_string += f" -> {get_emoji(pack_data[level + first_boost]['name'].lower() + 'pack')}"
            level += first_boost
            first_boost = 1
            upgrades += 1
        final_level = pack_data[level]
        if is_single:
            reward_texts.append(f"{get_emoji(final_level['name'].lower() + 'pack')} {final_level['name']}\n" + build_string)

        # select cat type
        goal_value = final_level["value"]
        chosen_type = random.choice(cattypes)
        cat_emoji = get_emoji(chosen_type.lower() + "cat")
        pre_cat_amount = goal_value / (sum(type_dict.values()) / type_dict[chosen_type])
        if pre_cat_amount % 1 > random.random():
            cat_amount = math.ceil(pre_cat_amount)
        else:
            cat_amount = math.floor(pre_cat_amount)
        # If the cascade/re-roll path overrides the result, skip the trailing
        # "You got X cats!" line (the cascade's animation already shows it).
        skip_final_line = False
        if pre_cat_amount < 1:
            if is_single:
                reward_texts.append(
                    reward_texts[-1] + f"\n{round(pre_cat_amount * 100, 2)}% chance for a {get_emoji(chosen_type.lower() + 'cat')} {chosen_type} cat"
                )
                reward_texts.append(reward_texts[-1] + ".")
                reward_texts.append(reward_texts[-1] + ".")
                reward_texts.append(reward_texts[-1] + ".")
            else:
                build_string += f" {round(pre_cat_amount * 100, 2)}% {cat_emoji}? "
            if cat_amount == 1:
                # success
                if is_single:
                    reward_texts.append(reward_texts[-1] + "\n✅ Success!")
                else:
                    build_string += f"✅ -> {cat_emoji} 1"
            else:
                # fail — branch on cascade depth and tier
                if is_single:
                    reward_texts.append(reward_texts[-1] + "\n❌ Miss!")
                else:
                    build_string += "❌"

                if _cascade_depth >= 1:
                    # already retried once — final consolation
                    chosen_type = "Fine"
                    cat_amount = 3
                    if is_single:
                        reward_texts.append(reward_texts[-1] + f"\nConsolation: {get_emoji('finecat')} 3 Fine cats")
                    else:
                        build_string += f" -> {get_emoji('finecat')} 3"
                elif level <= 4:
                    # Wooden (or a special pack that somehow never upgraded) —
                    # re-roll the cat type once, run the lottery again.
                    if is_single:
                        reward_texts.append(reward_texts[-1] + "\n🎲 Re-rolling cat type...")
                    new_type = random.choice(cattypes)
                    new_pre = goal_value / (sum(type_dict.values()) / type_dict[new_type])
                    if new_pre % 1 > random.random():
                        new_amount = math.ceil(new_pre)
                    else:
                        new_amount = math.floor(new_pre)
                    new_emoji = get_emoji(new_type.lower() + "cat")
                    if new_amount >= 1:
                        chosen_type = new_type
                        cat_amount = new_amount
                        if is_single:
                            reward_texts.append(
                                reward_texts[-1] + f"\n✅ Got {new_emoji} {new_amount:,} {new_type} cats!"
                            )
                            skip_final_line = True
                        else:
                            build_string += f" -> 🎲 {new_emoji} {new_amount:,}"
                    else:
                        # re-roll lottery also missed
                        chosen_type = "Fine"
                        cat_amount = 3
                        if is_single:
                            reward_texts.append(
                                reward_texts[-1] + f"\n❌ Re-roll missed. Consolation: {get_emoji('finecat')} 3 Fine cats"
                            )
                        else:
                            build_string += f" -> 🎲 ❌ {get_emoji('finecat')} 3"
                else:
                    # Stone+ — cascade: open one tier lower as consolation
                    cascade_level = level - 1
                    if is_single:
                        reward_texts.append(
                            reward_texts[-1]
                            + f"\n📦 Cascade! Opening a {pack_data[cascade_level]['name']} pack as consolation..."
                        )
                    else:
                        build_string += f" -> 📦 {pack_data[cascade_level]['name']}"
                    cascade_type, cascade_amount, cascade_upgrades, cascade_text = get_pack_rewards(
                        cascade_level, is_single, _cascade_depth + 1
                    )
                    chosen_type = cascade_type
                    cat_amount = cascade_amount
                    upgrades += cascade_upgrades
                    if is_single:
                        # cascade_text entries are cumulative snapshots of the
                        # cascade pack's animation — append them as-is so the
                        # user sees the smaller pack open from its title down.
                        for ct in cascade_text:
                            reward_texts.append(ct)
                        skip_final_line = True
                    else:
                        build_string += " " + cascade_text
                        skip_final_line = True
        elif not is_single:
            build_string += f" {cat_emoji} {cat_amount:,}"
        if is_single:
            if not skip_final_line:
                reward_texts.append(reward_texts[-1] + f"\nYou got {get_emoji(chosen_type.lower() + 'cat')} {cat_amount:,} {chosen_type} cats!")
            return chosen_type, cat_amount, upgrades, reward_texts
        return chosen_type, cat_amount, upgrades, build_string

    async def open_pack(interaction: discord.Interaction):
        if interaction.user != message.user:
            await do_funny(interaction)
            return

        await interaction.response.defer()
        pack = interaction.data["custom_id"]
        await user.refresh_from_db()
        if user[f"pack_{pack.lower()}"] < 1:
            return
        level = next((i for i, p in enumerate(pack_data) if p["name"] == pack), 0)

        chosen_type, cat_amount, upgrades, reward_texts = get_pack_rewards(level)
        user[f"cat_{chosen_type}"] += cat_amount
        user.pack_upgrades += upgrades
        user.packs_opened += 1
        user[f"pack_{pack.lower()}"] -= 1
        await user.save()
        if cat_amount > 0 and chosen_type in cattypes:
            await mark_discovered(user, chosen_type)

        logging.debug("Opened pack %s", pack)

        embed = discord.Embed(title=reward_texts[0], color=Colors.brown)
        await interaction.edit_original_response(embed=embed, view=None)
        for reward_text in reward_texts[1:]:
            await asyncio.sleep(1)
            things = reward_text.split("\n", 1)
            embed = discord.Embed(title=things[0], description=things[1], color=Colors.brown)
            await interaction.edit_original_response(embed=embed)
        await asyncio.sleep(1)
        view, _ = gen_view(user)
        await interaction.edit_original_response(view=view)

    async def open_all_packs(interaction: discord.Interaction):
        embed = await process_pack_opening(10000)
        if not embed:
            return

        await message.edit_original_response(embed=embed, view=None)
        await asyncio.sleep(1)
        view, _ = gen_view(user)
        await message.edit_original_response(view=view)

    user = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    view, has_special = gen_view(user)
    description = "Each pack starts at one of eight tiers of increasing value - Wooden, Stone, Bronze, Silver, Gold, Platinum, Diamond, or Celestial - and can repeatedly move up tiers with a 30% chance per upgrade. This means that even a pack starting at Wooden, through successive upgrades, can reach the Celestial tier.\n[Chance Info](<https://catbot.minkos.lol/packs>)"
    if has_special:
        description += "\n\n**Special Packs** are packs highlighted in green. Their upgrade chance is 70% instead of 30% and they start below Wooden."
    description += "\n\nClick the buttons below to start opening packs!"
    embed = discord.Embed(title=f"{get_emoji('goldpack')} Packs", description=description, color=Colors.brown)
    await message.response.send_message(embed=embed, view=view)


@bot.tree.command(description="why would anyone think a cattlepass would be a good idea (bp)")
async def battlepass(message: discord.Interaction):
    current_mode = ""
    user = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    global_user = await User.get_or_create(user_id=message.user.id)

    async def toggle_reminders(interaction: discord.Interaction):
        nonlocal current_mode
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        await interaction.response.defer()
        await user.refresh_from_db()
        if not user.reminders_enabled:
            try:
                dm_channel = await fetch_dm_channel(global_user)
                await dm_channel.send(
                    f"You have enabled reminders in {interaction.guild.name}. You can disable them in the /battlepass command in that server or by saying `disable {interaction.guild.id}` here any time."
                )
            except Exception:
                await interaction.followup.send(
                    "Failed. Ensure you have DMs open by going to Server > Privacy Settings > Allow direct messages from server members."
                )
                return

        user.reminders_enabled = not user.reminders_enabled
        await user.save()

        view = View(timeout=VIEW_TIMEOUT)
        button = Button(emoji="🔄", label="Refresh", style=ButtonStyle.blurple)
        button.callback = gen_main
        view.add_item(button)

        if user.reminders_enabled:
            button = Button(emoji="🔕", style=ButtonStyle.blurple)
        else:
            button = Button(label="Enable Reminders", emoji="🔔", style=ButtonStyle.green)
        button.callback = toggle_reminders
        view.add_item(button)

        await interaction.followup.send(
            f"Reminders are now {'enabled' if user.reminders_enabled else 'disabled'}.",
            ephemeral=True,
        )
        await interaction.edit_original_response(view=view)

    async def gen_main(interaction, first=False):
        nonlocal current_mode
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        await interaction.response.defer()
        current_mode = "Main"

        await refresh_quests(user)

        await global_user.refresh_from_db()
        if global_user.vote_time_topgg + QUEST_COOLDOWN > time.time():
            await progress(message, user, "vote")
            await global_user.refresh_from_db()

        await user.refresh_from_db()

        # season end
        now = discord.utils.utcnow() + datetime.timedelta(hours=4)

        if now.month == 12:
            next_month = datetime.datetime(now.year + 1, 1, 1)
        else:
            next_month = datetime.datetime(now.year, now.month + 1, 1)

        next_month -= datetime.timedelta(hours=4)

        timestamp = int(time.mktime(next_month.timetuple()))

        description = f"Season ends <t:{timestamp}:R>\n\n"

        # vote
        if config.VOTING_ENABLED:
            streak_string = ""
            if global_user.daily_catch_streak >= 5:
                streak_string = f" (🔥 {global_user.daily_catch_streak}x streak)"
            if user.vote_cooldown != 0:
                description += f"✅ ~~Vote on Top.gg~~\n- Refreshes <t:{int(user.vote_cooldown + QUEST_COOLDOWN)}:R>{streak_string}\n"
            else:
                # inform double vote xp during weekends
                is_weekend = now.weekday() >= 4

                if is_weekend:
                    description += "-# *Double Vote XP During Weekends*\n"

                description += f"{get_emoji('topgg')} [Vote on Top.gg](https://top.gg/bot/966695034340663367/vote)\n"

                if is_weekend:
                    description += f"- Reward: ~~{user.vote_reward}~~ **{user.vote_reward * 2}** XP"
                else:
                    description += f"- Reward: {user.vote_reward} XP"

                next_streak_data = get_streak_reward(global_user.daily_catch_streak + 1)
                if next_streak_data["reward"] and global_user.vote_time_topgg + 24 * 3600 > time.time():
                    description += f" + {next_streak_data['emoji']} 1 {next_streak_data['reward'].capitalize()} pack"

                description += f"{streak_string}\n"
        elif global_user.daily_catch_streak >= 5:
            description += f"🔥 {global_user.daily_catch_streak}-day catch streak\n"

        # catch
        catch_quest = config.battle["quests"]["catch"][user.catch_quest]
        if user.catch_cooldown != 0:
            description += f"✅ ~~{catch_quest['title']}~~\n- Refreshes <t:{int(user.catch_cooldown + QUEST_COOLDOWN if user.catch_cooldown + QUEST_COOLDOWN < timestamp else timestamp)}:R>\n"
        else:
            progress_string = ""
            if catch_quest["progress"] != 1:
                if user.catch_quest == "finenice":
                    try:
                        real_progress = ["need both", "need Nice", "need Fine", "done"][user.catch_progress]
                    except IndexError:
                        real_progress = "error"
                    progress_string = f" ({real_progress})"
                else:
                    progress_string = f" ({user.catch_progress}/{catch_quest['progress']})"
            description += f"{get_emoji(catch_quest['emoji'])} {catch_quest['title']}{progress_string}\n- Reward: {user.catch_reward} XP\n"

        # misc
        misc_quest = config.battle["quests"]["misc"][user.misc_quest]
        if user.misc_cooldown != 0:
            description += f"✅ ~~{misc_quest['title']}~~\n- Refreshes <t:{int(user.misc_cooldown + QUEST_COOLDOWN if user.misc_cooldown + QUEST_COOLDOWN < timestamp else timestamp)}:R>\n"
        else:
            progress_string = ""
            if misc_quest["progress"] != 1:
                progress_string = f" ({user.misc_progress}/{misc_quest['progress']})"
            description += f"{get_emoji(misc_quest['emoji'])} {misc_quest['title']}{progress_string}\n- Reward: {user.misc_reward} XP\n"

        # extra (third slot)
        extra_quest = config.battle["quests"]["extra"][user.extra_quest]
        if user.extra_cooldown != 0:
            description += f"✅ ~~{extra_quest['title']}~~\n- Refreshes <t:{int(user.extra_cooldown + QUEST_COOLDOWN if user.extra_cooldown + QUEST_COOLDOWN < timestamp else timestamp)}:R>\n"
        else:
            progress_string = ""
            if extra_quest["progress"] != 1:
                progress_string = f" ({user.extra_progress}/{extra_quest['progress']})"
            if extra_quest.get("dynamic_reward"):
                reward_line = "- Reward: depends on the cat 🎲"
            else:
                reward_line = f"- Reward: {user.extra_reward} XP"
            description += f"{get_emoji(extra_quest['emoji'])} {extra_quest['title']}{progress_string}\n{reward_line}\n"

        # challenge (fifth slot — harder catch-condition quests)
        challenge_quest = config.battle["quests"]["challenge"][user.challenge_quest]
        if user.challenge_cooldown != 0:
            description += f"✅ ~~{challenge_quest['title']}~~\n- Refreshes <t:{int(user.challenge_cooldown + QUEST_COOLDOWN if user.challenge_cooldown + QUEST_COOLDOWN < timestamp else timestamp)}:R>\n\n"
        else:
            progress_string = ""
            if challenge_quest["progress"] != 1:
                progress_string = f" ({user.challenge_progress}/{challenge_quest['progress']})"
            description += f"{get_emoji(challenge_quest['emoji'])} {challenge_quest['title']}{progress_string}\n- Reward: {user.challenge_reward} XP\n\n"

        if user.battlepass >= len(config.battle["seasons"][str(user.season)]):
            description += f"**Extra Rewards** [{user.progress}/1500 XP]\n"
            colored = int(user.progress / 150)
            description += get_emoji("staring_square") * colored + "⬛" * (10 - colored) + "\nReward: " + get_emoji("stonepack") + " Stone pack\n\n"
        else:
            level_data = config.battle["seasons"][str(user.season)][user.battlepass]
            description += f"**Level {user.battlepass + 1}/30** [{user.progress}/{level_data['xp']} XP]\n"
            colored = int(user.progress / level_data["xp"] * 10)
            description += f"**{user.battlepass}** " + get_emoji("staring_square") * colored + "⬛" * (10 - colored) + f" **{user.battlepass + 1}**\n"

            if level_data["reward"] == "Rain":
                description += f"Reward: ☔ {level_data['amount']} minutes of rain\n\n"
            elif level_data["reward"] in cattypes:
                description += f"Reward: {get_emoji(level_data['reward'].lower() + 'cat')} {level_data['amount']} {level_data['reward']} cats\n\n"
            else:
                description += f"Reward: {get_emoji(level_data['reward'].lower() + 'pack')} {level_data['reward']} pack\n\n"

        # next reward
        levels = config.battle["seasons"][str(user.season)]
        for num, level_data in enumerate(levels):
            claimed_suffix = "_claimed" if num < user.battlepass else ""
            if level_data["reward"] == "Rain":
                description += get_emoji(str(level_data["amount"]) + "rain" + claimed_suffix)
            elif level_data["reward"] in cattypes:
                description += get_emoji(level_data["reward"].lower() + "cat" + claimed_suffix)
            else:
                description += get_emoji(level_data["reward"].lower() + "pack" + claimed_suffix)
            if num % 10 == 9:
                description += "\n"
        if user.battlepass >= len(config.battle["seasons"][str(user.season)]) - 1:
            description += f"*Extra:* {get_emoji('stonepack')} per 1500 XP"

        embedVar = discord.Embed(
            title=f"Cattlepass Season {user.season}",
            description=description,
            color=Colors.brown,
        ).set_footer(text=rain_shill)
        view = View(timeout=VIEW_TIMEOUT)

        button = Button(emoji="🔄", label="Refresh", style=ButtonStyle.blurple)
        button.callback = gen_main
        view.add_item(button)

        # /news is currently stubbed to "Coming soon", so the unread-news indicator is suppressed.
        # if len(news_list) > len(global_user.news_state.strip()) or "0" in global_user.news_state.strip()[-4:]:
        #     embedVar.set_author(name="You have unread news! /news")

        if first:
            await interaction.followup.send(embed=embedVar, view=view)
        else:
            await interaction.edit_original_response(embed=embedVar, view=view)

    await gen_main(message, True)


if config.VOTING_ENABLED:
    @bot.tree.command(description="vote for cat bot")
    async def vote(message: discord.Interaction):
        view = View(timeout=1)
        button = Button(label="Vote!", url="https://top.gg/bot/966695034340663367/vote", emoji=get_emoji("topgg"))
        view.add_item(button)
        await message.response.send_message(view=view)


async def stock_help(message):
    text = """Let's break this down!

At the top is the name of the stock. Each stock has a 4 letter "ticker" its identified by.
This is also where the reward will be displayed if there is one upcoming, more on them a bit later.

Below that is the price graph over the last 3 days.
**Stock price** is determined by the last coin amount the stocks were bought for (will be explained shortly).

After this you can view the open sell and buy orders. Let's explain this with an example:

- You create a buy order for 5 stocks priced at 40 coins. This means you spend 200 coins hoping to buy 5 stocks.
- After you create your order its placed into the *Buy Orders* list.
- Then, all of the orders try to cancel out - if there is a sell order for the same (or less) amount of coins as yours in the *Sell Orders* list, it will fulfill your order.
- If such a match isn't found or wasn't enough to fully fulfill your order, then your order will stay in the *Buy Orders* list until someone creates a matching *Sell Order*.
- Whenever an exchange such as this happens, this is set to be the **stock price**, as displayed on the graph and in overviews.
- This proccess is symmetrical for buy and sell orders."""

    view = View(timeout=VIEW_TIMEOUT)
    button = Button(label="Continue")
    button.callback = rewards_help
    view.add_item(button)
    await message.response.send_message(text, view=view, ephemeral=True)


async def rewards_help(message):
    text = """Rewards are random events which happen every couple of days. You will know of when an award is about to be given out **48 hours** in advance to prepare and buy the stock if you want it.
Rewards have a *random* chance to give you a *random* amount of :coin: **coins** per *stock* you own.
For example, if the reward is "50% chance to get :coin: 10/stock" and you have 5 of that stock, then when the time comes you will either get 50 or 0 coins added to your balance.

These rewards are global and equal for everyone, and whether you get the reward or not is also the same for everyone (if your chance failed, everyone else's did as well!)
To spice it up, sometimes the chance percentage or the reward amount will be randomly hidden. Be more careful when trading such a stock.
The reward can also sometimes be negative but I'm sure you don't have to worry about that :)"""
    await message.response.send_message(text, ephemeral=True)


async def portfolio_help(message):
    text = """Welcome to your portfolio!

First of all comes your combined portfolio value. This is a sum of all of your stocks priced at their current **stock price**, plus your current coin balance. You can also see your lifetime portfolio growth percentage and cancel your open orders.

Next, the portfolio value from before is broken down. You can see how much of each stock you have, how much they are worth, and how many :coin: **coins** you have left.

What follows are your open orders. These are orders you created which haven't been fulfilled yet. In other words, they are currently sitting in the *Buy/Sell Orders* lists.

Lastly, there is your portfolio history. This is a history of everything which happened to your portfolio, including rewards, deposits, withdrawals, as well as buy and sell orders."""
    await message.response.send_message(text, ephemeral=True)


async def view_portfolio(interaction, person, refresh=False, hidden=None):
    if not hidden:
        hidden = False
    await interaction.response.defer(ephemeral=hidden)
    profile = await Profile.get_or_create(user_id=person.id, guild_id=interaction.guild.id)
    user = await User.get_or_create(user_id=person.id)

    view = LayoutView(timeout=VIEW_TIMEOUT)

    portfolio_value = profile.coins
    share_strs = [f"🪙 {profile.coins:,}"]

    for stock in stock_data:
        stock_price = await get_stock_price(stock["ticker"])
        emoji = get_emoji(stock["emoji"])
        amount_owned = profile[f"stock_{stock['ticker'].lower()}"]
        item_value = stock_price * amount_owned
        portfolio_value += item_value
        if amount_owned > 0:
            share_strs.append(f"{emoji} {amount_owned:,}x (🪙 *{item_value:,}*)")

    shares_display = "\n".join(share_strs)

    open_orders = []
    async for order in Order.filter("user_id = $1", profile.id):
        open_orders.append(
            f"{'BUY' if order.type_buy else 'SELL'}ING {order.quantity:,}x **{order.ticker}**, 🪙 {order.price:,}/share, expires <t:{order.time + 3600 * 24 * 7}:R>"
        )

    portfolio_history = []
    async for history in PortfolioHistory.filter("user_id = $1 ORDER BY time DESC LIMIT 13", profile.id):
        if history.type == "d":
            portfolio_history.append(f"📥 Deposited 🪙 {history.price:,} coins <t:{history.time}:R>")
        elif history.type == "w":
            portfolio_history.append(f"📤 Withdrew 🪙 {history.price:,} coins <t:{history.time}:R>")
        elif history.type == "s":
            portfolio_history.append(f"🔴 Created SELL for {history.quantity:,}x {history.ticker} at 🪙 {history.price:,}/share <t:{history.time}:R>")
        elif history.type == "b":
            portfolio_history.append(f"🟢 Created BUY for {history.quantity:,}x {history.ticker} at 🪙 {history.price:,}/share <t:{history.time}:R>")
        elif history.type == "r":
            portfolio_history.append(f"⭐ Got rewarded 🪙 {history.quantity:,} by {history.ticker} <t:{history.time}:R>")
        elif history.type == "c":
            portfolio_history.append(f":x: Cancelled BUY, refunded 🪙 {history.quantity:,} <t:{history.time}:R>")
        elif history.type == "C":
            portfolio_history.append(f":x: Cancelled SELL, refunded {history.quantity:,}x {history.ticker} shares <t:{history.time}:R>")

    deposits = await PortfolioHistory.sum("price", "user_id = $1 AND type = $2", profile.id, "d")
    deposits -= await PortfolioHistory.sum("price", "user_id = $1 AND type = $2", profile.id, "w")

    try:
        value_diff = (portfolio_value / deposits - 1) * 100
    except ZeroDivisionError:
        value_diff = 0
    growth_emoji = "📈" if value_diff >= 0 else "📉"
    emoji_prefix = (user.emoji + " ") if user.emoji else ""

    first_lines = (f"## {emoji_prefix}{person}", f"### 🪙 {portfolio_value:,}", f"{growth_emoji} {value_diff:+.2f}% *(Lifetime)*")

    async def refresh_portfolio(interaction):
        await view_portfolio(interaction, person, refresh=True, hidden=False)

    help_button = Button(label="Help", style=ButtonStyle.gray, emoji="💡")
    help_button.callback = portfolio_help

    cancel_button = Button(label="Cancel orders...", style=ButtonStyle.red)
    cancel_button.callback = cancel_orders

    refresh_button = Button(label="Refresh", style=ButtonStyle.gray, emoji="🔄")
    refresh_button.callback = refresh_portfolio

    container = Container(
        Section(*first_lines, Thumbnail(user.image)) if user.image else first_lines,
        "===",
        shares_display or "No portfolio",
        "===",
        "### Open Orders",
        "\n".join(open_orders) or "No open orders",
        "===",
        "### Portfolio History",
        "\n".join(portfolio_history) or "No portfolio history",
        "===",
        ActionRow(refresh_button, cancel_button, help_button),
        accent_color=Colors.brown if not user.color else discord.Colour.from_str(user.color),
    )

    view.add_item(container)
    if not refresh:
        await interaction.followup.send(view=view, ephemeral=hidden)
    else:
        await interaction.edit_original_response(view=view)

    if not profile.rugpulled and await PortfolioHistory.count("user_id = $1 AND type = $2 AND quantity < 0", profile.id, "r") > 0:
        await achemb(interaction, "rugpulled", "followup", person)


@bot.tree.command(description="View your stock portfolio")
@discord.app_commands.rename(person_id="user")
@discord.app_commands.describe(person_id="Person to view the inventory of!", hidden="Whether the response will only be seen by you.")
async def portfolio(message: discord.Interaction, person_id: Optional[discord.User], hidden: Optional[bool]):
    if not person_id:
        person_id = message.user
    if not hidden:
        hidden = False
    await view_portfolio(message, person_id, refresh=False, hidden=hidden)


async def cancel_orders(interaction):
    await interaction.response.defer()
    profile = await Profile.get_or_create(user_id=interaction.user.id, guild_id=interaction.guild.id)
    view = View(timeout=VIEW_TIMEOUT)
    open_orders = []
    async for order in Order.filter("user_id = $1 AND time < $2", profile.id, time.time() - 43200):
        open_orders.append(
            discord.SelectOption(label=f"{'BUY' if order.type_buy else 'SELL'}ING {order.quantity:,}x {order.ticker}, 🪙 {order.price:,}/share", value=order.id)
        )
    if not open_orders:
        await interaction.followup.send("No open orders\n-# you can only cancel orders older than 12 hours", ephemeral=True)
        return
    cancel_select = Select(
        "cancel_order_dd",
        placeholder="Select an order to cancel",
        opts=open_orders,
        on_select=the_order_canceller,
    )
    view.add_item(cancel_select)
    await interaction.followup.send("Select orders to cancel...\n(you can only cancel orders after 12 hours)", view=view, ephemeral=True)


async def the_order_canceller(interaction, choices):
    if not choices:
        await interaction.response.send_message("No orders selected", ephemeral=True)
        return
    await interaction.response.defer()
    profile = await Profile.get_or_create(user_id=interaction.user.id, guild_id=interaction.guild.id)
    if not isinstance(choices, list):
        choices = [choices]
    for choice in choices:
        order = await Order.get_or_none(id=int(choice))
        if not order or order.user_id != profile.id or order.time > time.time() - 43200:
            continue
        if order.type_buy:
            profile.coins += order.price * order.quantity
            await PortfolioHistory.create(user_id=profile.id, type="c", quantity=order.price * order.quantity, time=int(time.time()))
        else:
            profile[f"stock_{order.ticker.lower()}"] += order.quantity
            await PortfolioHistory.create(user_id=profile.id, type="C", quantity=order.quantity, time=int(time.time()), ticker=order.ticker)
        await order.delete()
    await profile.save()
    await interaction.edit_original_response(content="Orders cancelled!", view=None)


async def resolve_orders(order: Order):
    remaining_quantity = order.quantity
    display_price = None
    if order.type_buy:
        # buy order
        seller_coin_deltas = {}
        async for eligible_order in Order.filter(
            "ticker = $1 AND type_buy = $2 AND price <= $3 ORDER BY price ASC, time ASC", order.ticker, False, order.price
        ):
            if remaining_quantity == 0:
                break

            buy_quantity = min(remaining_quantity, eligible_order.quantity)
            remaining_quantity -= buy_quantity
            eligible_order.quantity -= buy_quantity

            seller_coin_deltas[eligible_order.user_id] = seller_coin_deltas.get(eligible_order.user_id, 0) + (buy_quantity * eligible_order.price)

            display_price = eligible_order.price

            if eligible_order.quantity == 0:
                await eligible_order.delete()
            else:
                await eligible_order.save()
                break

        if seller_coin_deltas:
            updates = []
            for user_id, delta in seller_coin_deltas.items():
                u = await Profile.get(id=user_id)
                u.coins += delta
                updates.append(u)
            await Profile.bulk_update(updates, "coins")

        profile = await Profile.get(id=order.user_id)
        profile[f"stock_{order.ticker.lower()}"] += order.quantity - remaining_quantity
        await profile.save()
    else:
        # sell order
        buyer_stock_deltas = {}
        async for eligible_order in Order.filter(
            "ticker = $1 AND type_buy = $2 AND price >= $3 ORDER BY price DESC, time ASC", order.ticker, True, order.price
        ):
            if remaining_quantity == 0:
                break

            sell_quantity = min(remaining_quantity, eligible_order.quantity)
            remaining_quantity -= sell_quantity
            eligible_order.quantity -= sell_quantity

            buyer_stock_deltas[eligible_order.user_id] = buyer_stock_deltas.get(eligible_order.user_id, 0) + sell_quantity

            display_price = eligible_order.price

            if eligible_order.quantity == 0:
                await eligible_order.delete()
            else:
                await eligible_order.save()
                break

        if buyer_stock_deltas:
            updates = []
            for user_id, delta in buyer_stock_deltas.items():
                u = await Profile.get(id=user_id)
                u[f"stock_{order.ticker.lower()}"] += delta
                updates.append(u)
            await Profile.bulk_update(updates, f"stock_{order.ticker.lower()}")

        profile = await Profile.get(id=order.user_id)
        profile.coins += (order.quantity - remaining_quantity) * order.price
        await profile.save()

    if display_price:
        await PriceHistory.create(ticker=order.ticker, price=display_price, time=int(time.time()))
        temp_stock_prices[order.ticker] = display_price

    if remaining_quantity > 0:
        order.quantity = remaining_quantity
        await order.save()
    else:
        await order.delete()
    return remaining_quantity


@bot.tree.command(description="the stonk market")
async def stocks(message: discord.Interaction):
    profile = await Profile.get_or_create(user_id=message.user.id, guild_id=message.guild.id)
    profile.last_ran_stocks = int(time.time())
    await profile.save()

    async def deposit_pack(interaction):
        await interaction.response.defer()
        await profile.refresh_from_db()
        pack_name = interaction.data["custom_id"]
        if profile[f"pack_{pack_name.lower()}"] < 1:
            await interaction.followup.send("u dont have any packs of such type", ephemeral=True)
            return
        profile[f"pack_{pack_name.lower()}"] -= 1
        og = profile.coins
        if pack_name not in ["Wooden", "Stone", "Bronze", "Silver", "Gold", "Platinum", "Diamond", "Celestial"]:
            return
        for pack in pack_data:
            if pack["name"].lower() == pack_name.lower():
                profile.coins += pack["totalvalue"]
                break
        await profile.save()
        embedVar = discord.Embed(title="📥 Deposit Packs", description=f"You currently have 🪙 **{profile.coins:,}** coins.", color=Colors.brown)
        await interaction.edit_original_response(embed=embedVar, view=deposit_msg(profile))
        await PortfolioHistory.create(user_id=profile.id, time=int(time.time()), type="d", price=profile.coins - og)

    async def deposit(interaction):
        await profile.refresh_from_db()
        profile.seen_deposit = True
        if profile.battlepass < 3 and not profile.bp_history.strip().replace("0,0,0;", ""):
            await interaction.response.send_message("you need to reach atleast cattlepass level 3 to deposit packs.", ephemeral=True)
            return
        embedVar = discord.Embed(title="📥 Deposit Packs", description=f"You currently have 🪙 **{profile.coins:,}** coins.", color=Colors.brown)
        await interaction.response.send_message(embed=embedVar, view=deposit_msg(profile), ephemeral=True)
        await profile.save()

    def deposit_msg(profile):
        view = View(timeout=VIEW_TIMEOUT)
        empty = True
        for pack in pack_data:
            if pack["name"] not in ["Wooden", "Stone", "Bronze", "Silver", "Gold", "Platinum", "Diamond", "Celestial"]:
                continue
            if profile[f"pack_{pack['name'].lower()}"] < 1:
                continue
            empty = False
            amount = profile[f"pack_{pack['name'].lower()}"]
            button = Button(
                emoji=get_emoji(pack["name"].lower() + "pack"),
                label=f"{pack['name']} ({amount:,})",
                style=ButtonStyle.blurple,
                custom_id=pack["name"],
            )
            button.callback = deposit_pack
            view.add_item(button)
        if empty:
            view.add_item(Button(label="No packs left!", disabled=True))
        return view

    async def withdraw(interaction):
        await profile.refresh_from_db()
        embedVar = discord.Embed(
            title="📤 Withdraw Coins",
            description=f"You currently have 🪙 **{profile.coins:,}** coins.\n\nThere is a **25%** withdrawal fee - You will get {get_emoji('woodenpack')} **1 Wooden Pack** for every 🪙 **{COIN_PER_PACK}** coins you withdraw.",
            color=Colors.brown,
        )
        view = View(timeout=VIEW_TIMEOUT)
        button = Button(label="Continue")
        button.callback = send_withdrawal_modal
        view.add_item(button)
        await interaction.response.send_message(embed=embedVar, view=view, ephemeral=True)

    async def send_withdrawal_modal(interaction):
        await profile.refresh_from_db()
        max_packs = profile.coins // COIN_PER_PACK
        if max_packs < 0:
            max_packs = 0
        await interaction.response.send_modal(WithdrawalModal(max_packs))

    class WithdrawalModal(Modal):
        def __init__(self, max_packs):
            super().__init__(
                title="Withdraw...",
                timeout=VIEW_TIMEOUT,
            )

            self.input = TextInput(
                min_length=1,
                max_length=5,
                label=f"Wooden packs to withdraw (max {max_packs})",
                style=discord.TextStyle.short,
                required=True,
                placeholder="2",
            )
            self.add_item(self.input)

        async def on_submit(self, interaction: discord.Interaction):
            try:
                packs = int(self.input.value)
                if packs <= 0:
                    raise ValueError
            except Exception:
                await interaction.response.send_message("number pls", ephemeral=True)
                return

            await profile.refresh_from_db()
            max_packs = profile.coins // COIN_PER_PACK
            if max_packs < 0:
                max_packs = 0
            if packs > max_packs:
                await interaction.response.send_message("u dont have enough coins", ephemeral=True)
                return

            profile.coins -= packs * COIN_PER_PACK
            profile.pack_wooden += packs
            await profile.save()
            await PortfolioHistory.create(user_id=profile.id, time=int(time.time()), type="w", price=packs * COIN_PER_PACK)
            await interaction.response.send_message(f"📤 You withdrew {packs} wooden packs! 🪙 -{packs * COIN_PER_PACK} coins.", ephemeral=True)

    class OrderModal(Modal):
        def __init__(self, ticker, type, recommended_price, max_shares=None):
            super().__init__(title=f"{type.capitalize()}ing {ticker}")

            self.ticker = ticker
            self.type = type
            self.max_shares = max_shares

            self.quantity = TextInput(
                label="Quantity",
                placeholder=f"Amt. of shares to {type}" + (f" (max {max_shares})" if type == "sell" else f" (balance: {max_shares})"),
                min_length=1,
                max_length=6,
                required=True,
                style=discord.TextStyle.short,
            )
            self.add_item(self.quantity)

            self.price = TextInput(
                label="Price per share",
                placeholder=f"Recommended: {recommended_price}",
                default=recommended_price,
                min_length=1,
                max_length=6,
                required=True,
                style=discord.TextStyle.short,
            )
            self.add_item(self.price)

        async def on_submit(self, interaction: discord.Interaction):
            await profile.refresh_from_db()
            # price checking
            try:
                price = int(self.price.value)
                if price <= 0:
                    raise Exception
            except Exception:
                await interaction.response.send_message("your price looks funny (it must be a positive integer)", ephemeral=True)
                return

            # quantity checking
            try:
                quantity = int(self.quantity.value)
                if quantity <= 0:
                    raise Exception
            except Exception:
                await interaction.response.send_message("your quantity looks funny (it must be a positive integer)", ephemeral=True)
                return

            # open orders checking
            if await Order.count("user_id = $1", profile.id) > 25:
                await interaction.response.send_message("you have too many open orders. please cancel some before placing new ones.", ephemeral=True)
                return

            if self.type == "sell" and quantity > profile[f"stock_{self.ticker.lower()}"]:
                await interaction.response.send_message("you don't have enough shares", ephemeral=True)
                return

            if self.type == "buy" and quantity * price > profile.coins:
                await interaction.response.send_message("you don't have enough coins", ephemeral=True)
                return

            if self.type == "buy":
                profile.coins -= quantity * price
            if self.type == "sell":
                profile[f"stock_{self.ticker.lower()}"] -= quantity
            await profile.save()

            curr_time = int(time.time())
            order = await Order.create(
                user_id=profile.id,
                ticker=self.ticker,
                type_buy=self.type == "buy",
                quantity=quantity,
                price=price,
                time=curr_time,
            )
            await PortfolioHistory.create(
                user_id=profile.id,
                ticker=self.ticker,
                type="b" if self.type == "buy" else "s",
                quantity=quantity,
                price=price,
                time=curr_time,
            )
            await interaction.response.send_message(f"☑️ Order to {self.type} {quantity} shares of {self.ticker} placed!", ephemeral=True)
            remaining_quantity = await resolve_orders(order)
            if remaining_quantity == 0:
                await interaction.followup.send("✅ Order fully fulfilled!", ephemeral=True)
            elif remaining_quantity != quantity:
                await interaction.followup.send(f"✅ Order partially fulfilled. {remaining_quantity}/{self.quantity} shares remaining", ephemeral=True)
            await achemb(interaction, "buy_stock" if self.type == "buy" else "sell_stock", "followup")

    async def buy_stock(interaction):
        profile = await Profile.get_or_create(user_id=interaction.user.id, guild_id=message.guild.id)
        ticker = interaction.data["custom_id"].split("_")[0]
        try:
            recommended_price = await Order.min("price", "ticker = $1 AND type_buy = $2", ticker, False)
            if not recommended_price:
                recommended_price = 40
        except Exception:
            recommended_price = 40
        await interaction.response.send_modal(OrderModal(ticker, "buy", recommended_price, profile.coins))

    async def sell_stock(interaction):
        profile = await Profile.get_or_create(user_id=interaction.user.id, guild_id=message.guild.id)
        ticker = interaction.data["custom_id"].split("_")[0]
        try:
            recommended_price = await Order.max("price", "ticker = $1 AND type_buy = $2", ticker, True)
            if not recommended_price:
                recommended_price = 40
        except Exception:
            recommended_price = 40
        await interaction.response.send_modal(OrderModal(ticker, "sell", recommended_price, profile[f"stock_{ticker.lower()}"]))

    async def view_stock(interaction):
        await interaction.response.defer()
        view = LayoutView(timeout=VIEW_TIMEOUT)

        stock_ticker = interaction.data["custom_id"]
        for i in stock_data:
            if i["ticker"] == stock_ticker:
                stock = i
                break

        data = []
        async for i in PriceHistory.filter("ticker = $1 AND time > $2", stock_ticker, int(time.time() - 3600 * 49)):
            data.append((i.time, i.price))

        buffer = await bot.loop.run_in_executor(None, graph.make_graph, data, 10, 3)
        file = discord.File(fp=buffer, filename="output.png")

        reward = await Reward.get_or_create(ticker=stock["ticker"])
        reward_suffix = ""
        if reward and reward.active:
            reward_suffix = f"\n⭐ {reward.chance if not reward.chance_hidden else '???'}% to get 🪙 {reward.amount if not reward.amount_hidden else '???'}/stock <t:{reward.end_time}:R>"

        container = Container(
            f"## {get_emoji(stock['emoji'])} {stock['name']} ({stock['ticker']}){reward_suffix}",
            "===",
            discord.ui.MediaGallery(discord.MediaGalleryItem(file)),
            "===",
        )

        button = Button(label="Buy", style=ButtonStyle.green, custom_id=stock_ticker + "_buy")
        button.callback = buy_stock
        top_3 = await Order.collect_limit(
            ["price", RawSQL("SUM(quantity) as total_quantity")],
            "type_buy = $1 AND ticker = $2 GROUP BY price ORDER BY price DESC LIMIT 5",
            True,
            stock_ticker,
            add_primary_key=False,
        )
        container.add_item(
            Section(
                "### Buy Orders",
                "\n".join([f"🪙 **{item.price:,}** - *{item.total_quantity:,}x*" for item in top_3]) if top_3 else "No buy orders",
                button,
            )
        )

        button = Button(label="Sell", style=ButtonStyle.red, custom_id=stock_ticker + "_sell")
        button.callback = sell_stock
        top_3 = await Order.collect_limit(
            ["price", RawSQL("SUM(quantity) as total_quantity")],
            "type_buy = $1 AND ticker = $2 GROUP BY price ORDER BY price ASC LIMIT 5",
            False,
            stock_ticker,
            add_primary_key=False,
        )
        container.add_item(
            Section(
                "### Sell Orders",
                "\n".join([f"🪙 **{item.price:,}** - *{item.total_quantity:,}x*" for item in top_3]) if top_3 else "No sell orders",
                button,
            )
        )

        view.add_item(container)

        back_button = Button(style=ButtonStyle.gray, emoji="⬅️")
        back_button.callback = go_back

        refresh_button = Button(label="Refresh", style=ButtonStyle.gray, emoji="🔄", custom_id=stock_ticker)
        refresh_button.callback = view_stock

        help_button = Button(label="Help", style=ButtonStyle.gray, emoji="💡")
        help_button.callback = stock_help

        container.add_item(Separator())
        container.add_item(ActionRow(back_button, refresh_button, help_button))

        await interaction.edit_original_response(view=view, attachments=[file])

    async def main_page():
        await profile.refresh_from_db()

        view = LayoutView(timeout=VIEW_TIMEOUT)

        portfolio_value = profile.coins
        share_strs = [f"🪙 {profile.coins:,}"]

        for stock in stock_data:
            stock_price = await get_stock_price(stock["ticker"])
            emoji = get_emoji(stock["emoji"])
            amount_owned = profile[f"stock_{stock['ticker'].lower()}"]
            item_value = stock_price * amount_owned
            portfolio_value += item_value
            if amount_owned > 0:
                share_strs.append(f"{emoji} {amount_owned:,}x (🪙 *{item_value:,}*)")

        deposits = await PortfolioHistory.sum("price", "user_id = $1 AND type = $2", profile.id, "d")
        deposits -= await PortfolioHistory.sum("price", "user_id = $1 AND type = $2", profile.id, "w")

        container = Container(
            "## 📈 Stock Market",
            "Buy stocks representing Cat Bot mechanics.\nEarn rewards if they perform well!",
            "===",
        )

        for item in stock_data:
            button = Button(label="View", style=ButtonStyle.blurple, custom_id=item["ticker"])

            button.callback = view_stock

            price = await get_stock_price(item["ticker"])

            reward = await Reward.get_or_create(ticker=item["ticker"])
            reward_suffix = ""
            if reward and reward.active:
                reward_suffix = f"\n⭐ {reward.chance if not reward.chance_hidden else '???'}% to get 🪙 {reward.amount if not reward.amount_hidden else '???'}/stock <t:{reward.end_time}:R>"

            to_buy = await Order.sum("quantity", "ticker = $1 AND type_buy = $2", item["ticker"], True)
            to_sell = await Order.sum("quantity", "ticker = $1 AND type_buy = $2", item["ticker"], False)

            container.add_item(
                Section(
                    f"### {get_emoji(item['emoji'])} {item['ticker']} - 🪙 {price:,}",
                    f"*{to_buy:,}* wanted, *{to_sell:,}* offered{reward_suffix}",
                    button,
                )
            )

        row = ActionRow()

        button = Button(label="Deposit", style=ButtonStyle.green)
        button.callback = deposit
        row.add_item(button)

        button = Button(label="Withdraw", style=ButtonStyle.red)
        button.callback = withdraw
        row.add_item(button)

        button = Button(label="Your Portfolio", style=ButtonStyle.blurple)
        button.callback = view_user_portfolio
        row.add_item(button)

        container.add_item(Separator())
        container.add_item(row)
        view.add_item(container)
        return view

    async def view_user_portfolio(interaction):
        await view_portfolio(interaction, interaction.user, refresh=False, hidden=True)

    async def go_back(interaction):
        await interaction.response.defer()
        await interaction.edit_original_response(view=await main_page(), attachments=[])

    await message.response.send_message(view=await main_page(), ephemeral=True)

    if not profile.seen_deposit:
        text = f"""Welcome!

**Cat Bot Stock Market** is a recreation of real-life stock market made to be as simple as possible while still being functional. There are 5 stocks you can trade with other Cat Bot users *globally*. To sell and buy stocks you use :coin: **coins**, which you can get by depositing {get_emoji("goldpack")} __Packs__. You can withdraw :coin: **coins** back into __Packs__ with a 25% fee.

Select any stock and click `💡 Help` to learn more, or click `Deposit` to start."""
        await message.followup.send(text, ephemeral=True)


@bot.tree.command(description="buy and sell cats with the cat mafia")
async def catstore(message: discord.Interaction):
    """Cat Store — the late-game coin sink. Players spend coins on cats they've
    personally discovered, gated by catnip-level discount. Sells are always at
    face value (cat_value), so the discount is buy-side only."""

    # ----- per-invocation state (closure-scoped) -----
    profile = await Profile.get_or_create(user_id=message.user.id, guild_id=message.guild.id)
    current_page = 0
    current_cat: Optional[str] = None  # None = on main page, else on detail page
    last_toast: Optional[str] = None  # one-line "✅ Bought ..." banner, cleared on navigation
    PAGE_SIZE = 6

    def _container_color(discount: int) -> int:
        if discount >= 5:
            return Colors.green
        if discount <= -5:
            return Colors.red
        return Colors.brown

    def _signed_pct(discount: int) -> str:
        if discount > 0:
            return f"+{discount}%"
        if discount < 0:
            # Unicode minus sign (U+2212) reads better than ASCII hyphen here.
            return f"−{abs(discount)}%"
        return "0%"

    def _rank_name(level: int) -> str:
        try:
            return catnip_list["levels"][level]["name"]
        except (IndexError, KeyError):
            return "?"

    def _discovered_list(p: Profile) -> list[str]:
        # Iterate type_dict so the ordering is "most common first" — matches the
        # spec and feels natural to browse.
        owned = set(_coerce_array(p.discovered_cats))
        return [t for t in cattypes if t in owned]

    async def show_help(interaction: discord.Interaction):
        await interaction.response.send_message(
            "**🛒 Cat Store — quick guide**\n"
            "- Cats cost coins. Their value comes from how rare they are.\n"
            "- Your Cat Mafia level changes the price. Levels 5–10 give you a discount, levels 0–3 charge a tax, level 4 is even.\n"
            "- You can only buy or sell cats you've personally discovered in this server. Catch one to discover it.\n"
            "- Sell prices also scale with Cat Mafia level — Newbies sell at 50% of face value, mid-ranks peak around 80%, and the rate stays below the buy price at every level so round-trips always lose money. You can't farm the store.",
            ephemeral=True,
        )

    # ----- buy/sell modals -----
    class BuyModal(Modal):
        def __init__(self, cat_type: str, max_affordable: int):
            super().__init__(title=f"Buy {cat_type} cats", timeout=VIEW_TIMEOUT)
            self.cat_type = cat_type
            self.input = TextInput(
                min_length=1,
                max_length=6,
                label=f"How many? (max {max_affordable:,})",
                style=discord.TextStyle.short,
                required=True,
                placeholder="1",
            )
            self.add_item(self.input)

        async def on_submit(self, interaction: discord.Interaction):
            nonlocal last_toast
            try:
                qty = int(self.input.value)
                if qty <= 0:
                    raise ValueError
            except Exception:
                await interaction.response.send_message("invalid quantity", ephemeral=True)
                return

            await interaction.response.defer()
            async with transaction() as conn:
                fresh = await Profile.get_or_create(conn, user_id=message.user.id, guild_id=message.guild.id)
                if self.cat_type not in _coerce_array(fresh.discovered_cats):
                    await interaction.followup.send("you haven't discovered that cat in this server", ephemeral=True)
                    return
                unit_price = store_buy_price(self.cat_type, fresh.catnip_level)
                unit_value = cat_value(self.cat_type)
                total_cost = unit_price * qty
                if fresh.coins < total_cost:
                    await interaction.followup.send(
                        f"not enough coins — need 🪙 {total_cost:,}, have 🪙 {fresh.coins:,}",
                        ephemeral=True,
                    )
                    return
                fresh.coins -= total_cost
                fresh[f"cat_{self.cat_type}"] += qty
                # discovered_cats is already set (gate above), but mark_discovered
                # is idempotent and also handles the case where someone hand-edits
                # the JSONB array.
                discovered = _coerce_array(fresh.discovered_cats)
                if self.cat_type not in discovered:
                    fresh.discovered_cats = discovered + [self.cat_type]
                purchased = _coerce_array(fresh.store_purchased_rarities)
                if self.cat_type not in purchased:
                    fresh.store_purchased_rarities = purchased + [self.cat_type]
                await fresh.save()
                # rebind closure profile so the re-render reflects the new state
                nonlocal profile
                profile = fresh

            # Build the toast.
            savings = unit_value - unit_price
            if savings > 0:
                last_toast = f"✅ Bought {qty}× {self.cat_type} for 🪙 {total_cost:,} (saved 🪙 {savings * qty:,})"
            elif savings < 0:
                last_toast = f"✅ Bought {qty}× {self.cat_type} for 🪙 {total_cost:,} (paid 🪙 {-savings * qty:,} tax)"
            else:
                last_toast = f"✅ Bought {qty}× {self.cat_type} for 🪙 {total_cost:,}"

            # Achievements (fire after transaction commits).
            if not profile.has_ach("catstore_first_buy"):
                await achemb(interaction, "catstore_first_buy", "followup")
            if total_cost >= 10000 and not profile.has_ach("catstore_whale"):
                await achemb(interaction, "catstore_whale", "followup")
            if store_discount_pct(profile.catnip_level) >= 30 and not profile.has_ach("mafia_discount_max"):
                await achemb(interaction, "mafia_discount_max", "followup")
            if profile.catnip_level == 0 and not profile.has_ach("mafia_tax_payer"):
                await achemb(interaction, "mafia_tax_payer", "followup")
            if (
                len(set(_coerce_array(profile.store_purchased_rarities))) == len(type_dict)
                and not profile.has_ach("catstore_collector")
            ):
                await achemb(interaction, "catstore_collector", "followup")

            await gen_detail(interaction, self.cat_type, use_followup=False)

    class SellModal(Modal):
        def __init__(self, cat_type: str, max_owned: int):
            super().__init__(title=f"Sell {cat_type} cats", timeout=VIEW_TIMEOUT)
            self.cat_type = cat_type
            self.input = TextInput(
                min_length=1,
                max_length=6,
                label=f"How many? (max {max_owned:,})",
                style=discord.TextStyle.short,
                required=True,
                placeholder="1",
            )
            self.add_item(self.input)

        async def on_submit(self, interaction: discord.Interaction):
            nonlocal last_toast
            try:
                qty = int(self.input.value)
                if qty <= 0:
                    raise ValueError
            except Exception:
                await interaction.response.send_message("invalid quantity", ephemeral=True)
                return

            await interaction.response.defer()
            async with transaction() as conn:
                fresh = await Profile.get_or_create(conn, user_id=message.user.id, guild_id=message.guild.id)
                if self.cat_type not in _coerce_array(fresh.discovered_cats):
                    await interaction.followup.send("you haven't discovered that cat in this server", ephemeral=True)
                    return
                owned = fresh[f"cat_{self.cat_type}"]
                if owned < qty:
                    await interaction.followup.send(
                        f"you only own {owned:,} {self.cat_type} cats in this server", ephemeral=True
                    )
                    return
                unit_price = store_sell_price(self.cat_type, fresh.catnip_level)
                total = unit_price * qty
                fresh[f"cat_{self.cat_type}"] -= qty
                fresh.coins += total
                await fresh.save()
                nonlocal profile
                profile = fresh

            face_total = cat_value(self.cat_type) * qty
            cut = face_total - total
            if cut > 0:
                last_toast = f"✅ Sold {qty}× {self.cat_type} for 🪙 {total:,} (mafia took 🪙 {cut:,})"
            else:
                last_toast = f"✅ Sold {qty}× {self.cat_type} for 🪙 {total:,} (full value)"
            if not profile.has_ach("catstore_first_sell"):
                await achemb(interaction, "catstore_first_sell", "followup")
            await gen_detail(interaction, self.cat_type, use_followup=False)

    # ----- callbacks wired to buttons -----
    async def on_view(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        nonlocal current_cat, last_toast
        last_toast = None
        current_cat = interaction.data["custom_id"].removeprefix("view_")
        await interaction.response.defer()
        await gen_detail(interaction, current_cat, use_followup=False)

    async def on_back(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        nonlocal current_cat, last_toast
        last_toast = None
        current_cat = None
        await interaction.response.defer()
        await gen_main(interaction, use_followup=False)

    async def on_prev(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        nonlocal current_page
        current_page = max(0, current_page - 1)
        await interaction.response.defer()
        await gen_main(interaction, use_followup=False)

    async def on_next(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        nonlocal current_page
        current_page += 1
        await interaction.response.defer()
        await gen_main(interaction, use_followup=False)

    async def on_help(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        await show_help(interaction)

    async def on_buy(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        if not current_cat:
            return
        await profile.refresh_from_db()
        unit_price = store_buy_price(current_cat, profile.catnip_level)
        max_affordable = profile.coins // unit_price if unit_price > 0 else 0
        if max_affordable < 1:
            await interaction.response.send_message(
                f"you need 🪙 {unit_price - profile.coins:,} more coins to buy one {current_cat}",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(BuyModal(current_cat, max_affordable))

    async def on_sell(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        if not current_cat:
            return
        await profile.refresh_from_db()
        owned = profile[f"cat_{current_cat}"]
        if owned < 1:
            await interaction.response.send_message(
                f"you don't have any {current_cat} cats to sell", ephemeral=True
            )
            return
        await interaction.response.send_modal(SellModal(current_cat, owned))

    # ----- renderers -----
    async def gen_main(interaction: discord.Interaction, use_followup: bool):
        await profile.refresh_from_db()
        discount = store_discount_pct(profile.catnip_level)
        rank = _rank_name(profile.catnip_level)
        discovered = _discovered_list(profile)

        view = LayoutView(timeout=VIEW_TIMEOUT)
        items: list = [
            "## 🛒 Cat Store",
            f"🪙 {profile.coins:,} · Mafia Lv {profile.catnip_level} ({rank}) · {_signed_pct(discount)}",
        ]
        if last_toast:
            items.append(last_toast)

        if not discovered:
            items.append(
                "You haven't discovered any cats here yet! Catch one in this server first, then come back."
            )
            help_btn = Button(label="💡 Help", style=ButtonStyle.gray, custom_id="catstore_help")
            help_btn.callback = on_help
            items.append(ActionRow(help_btn))
            container = Container(*items)
            try:
                container.accent_color = _container_color(discount)
            except Exception:
                pass
            view.add_item(container)
            if use_followup:
                await interaction.followup.send(view=view, ephemeral=True)
            else:
                await interaction.edit_original_response(view=view)
            return

        # Pagination math.
        total_pages = max(1, (len(discovered) + PAGE_SIZE - 1) // PAGE_SIZE)
        nonlocal current_page
        if current_page >= total_pages:
            current_page = total_pages - 1
        start = current_page * PAGE_SIZE
        page_cats = discovered[start : start + PAGE_SIZE]

        for cat_type in page_cats:
            owned = profile[f"cat_{cat_type}"]
            price = store_buy_price(cat_type, profile.catnip_level)
            owned_line = f"Owned: {owned:,}" if owned > 0 else f"*Owned: 0*"
            body = f"{owned_line}  ·  🪙 {price:,}"
            btn = Button(label="View", style=ButtonStyle.blurple, custom_id=f"view_{cat_type}")
            btn.callback = on_view
            items.append(
                Section(
                    f"### {get_emoji(cat_type.lower() + 'cat')} {cat_type}",
                    body,
                    btn,
                )
            )

        # Footer: pagination only when multi-page, plus Help always.
        action_buttons: list = []
        if total_pages > 1:
            prev_btn = Button(label="← Prev", style=ButtonStyle.gray, custom_id="catstore_prev")
            prev_btn.callback = on_prev
            prev_btn.disabled = current_page == 0
            next_btn = Button(label="Next →", style=ButtonStyle.gray, custom_id="catstore_next")
            next_btn.callback = on_next
            next_btn.disabled = current_page >= total_pages - 1
            action_buttons.append(prev_btn)
            action_buttons.append(next_btn)
            items.append(f"-# Page {current_page + 1}/{total_pages}")
        help_btn = Button(label="💡 Help", style=ButtonStyle.gray, custom_id="catstore_help")
        help_btn.callback = on_help
        action_buttons.append(help_btn)
        items.append(ActionRow(*action_buttons))

        container = Container(*items)
        try:
            container.accent_color = _container_color(discount)
        except Exception:
            pass
        view.add_item(container)
        if use_followup:
            await interaction.followup.send(view=view, ephemeral=True)
        else:
            await interaction.edit_original_response(view=view)

    async def gen_detail(interaction: discord.Interaction, cat_type: str, use_followup: bool):
        await profile.refresh_from_db()
        discount = store_discount_pct(profile.catnip_level)
        sell_pct = store_sell_pct(profile.catnip_level)
        unit_value = cat_value(cat_type)
        unit_buy = store_buy_price(cat_type, profile.catnip_level)
        unit_sell = store_sell_price(cat_type, profile.catnip_level)
        owned = profile[f"cat_{cat_type}"]
        coins = profile.coins
        can_afford = coins // unit_buy if unit_buy > 0 else 0

        if discount > 0:
            buy_note = f"  (saving 🪙 {unit_value - unit_buy:,} at +{discount}%)"
        elif discount < 0:
            buy_note = f"  (adding 🪙 {unit_buy - unit_value:,} tax 💸)"
        else:
            buy_note = ""

        if sell_pct < 100:
            sell_note = f"  (mafia takes 🪙 {unit_value - unit_sell:,} — {sell_pct}% of face)"
        else:
            sell_note = "  (full face value)"

        body_lines = [
            f"Buy:  🪙 {unit_buy:,}{buy_note}",
            f"Sell: 🪙 {unit_sell:,}{sell_note}",
            "",
            f"You own: {owned:,} in this server",
            f"You have: 🪙 {coins:,}  (can afford {can_afford:,})",
        ]
        body = "\n".join(body_lines)

        thumb_url = (
            f"https://wsrv.nl/?url=raw.githubusercontent.com/milenakos/cat-bot/"
            f"refs/heads/main/images/spawn/{cat_type.lower()}_cat.png"
        )

        view = LayoutView(timeout=VIEW_TIMEOUT)
        section_items: list = [
            Section(
                f"### {get_emoji(cat_type.lower() + 'cat')} {cat_type} Cat",
                body,
                Thumbnail(thumb_url),
            ),
        ]
        if last_toast:
            section_items.append(last_toast)

        # Buy / Sell buttons — disabled with a helpful label if unavailable.
        buy_btn = Button(label="Buy", style=ButtonStyle.green, custom_id="catstore_buy")
        if can_afford < 1:
            buy_btn.disabled = True
            buy_btn.label = f"Need 🪙 {unit_buy - coins:,} more"
        buy_btn.callback = on_buy

        sell_btn = Button(label="Sell", style=ButtonStyle.red, custom_id="catstore_sell")
        if owned < 1:
            sell_btn.disabled = True
            sell_btn.label = "None to sell"
        sell_btn.callback = on_sell

        back_btn = Button(label="← Back", style=ButtonStyle.gray, custom_id="catstore_back")
        back_btn.callback = on_back

        help_btn = Button(label="💡 Help", style=ButtonStyle.gray, custom_id="catstore_help")
        help_btn.callback = on_help

        section_items.append(ActionRow(buy_btn, sell_btn, back_btn, help_btn))
        container = Container(*section_items)
        try:
            container.accent_color = _container_color(discount)
        except Exception:
            pass
        view.add_item(container)
        if use_followup:
            await interaction.followup.send(view=view, ephemeral=True)
        else:
            await interaction.edit_original_response(view=view)

    # ----- entry point -----
    await message.response.defer(ephemeral=True)
    await gen_main(message, use_followup=True)


@bot.tree.command(description="take contracts from the cat mafia")
async def jobs(message: discord.Interaction):
    """Cat Mafia jobs board. Multi-screen flow:
       board → send screen → result (with 30s cancel grace).
    Acceptance criterion of Phase 1 (deterministic window seeding) is preserved;
    Phase 2 adds the commit path with atomic escrow + roll + reward grant."""
    profile = await Profile.get_or_create(user_id=message.user.id, guild_id=message.guild.id)

    # ----- closure state -----
    mode: dict = {"screen": "board", "job_id": None}
    send_state: dict = {}  # cat_type -> count being sent
    last_toast: list[str] = []  # one short status line carried across renders

    async def _attach_view(interaction: discord.Interaction, view, use_followup: bool):
        """Render the view via the right channel depending on what's already
        happened to this interaction. Use_followup is True only for the entry
        point (where we deferred before any user action). Otherwise: if the
        interaction has been responded to (e.g. via defer), edit the original;
        else edit the message the button lives on."""
        if use_followup:
            await interaction.followup.send(view=view, ephemeral=True)
        elif interaction.response.is_done():
            await interaction.edit_original_response(view=view)
        else:
            await interaction.response.edit_message(view=view)

    async def show_board(interaction: discord.Interaction, use_followup: bool = False):
        await profile.refresh_from_db()
        now = int(time.time())
        # Lazy heat decay on every board open.
        _jobs_apply_heat_decay(profile, now)
        level = int(profile.catnip_level or 0)

        async def _say(text: str):
            if use_followup:
                await interaction.followup.send(text, ephemeral=True)
            elif interaction.response.is_done():
                await interaction.edit_original_response(content=text, view=None)
            else:
                await interaction.response.edit_message(content=text, view=None)

        if level < 2:
            await _say("You're not in the family yet. Catch more cats and climb the catnip ranks — "
                       "you can start running errands at Lv2.")
            return

        offers = await _jobs_refresh_offers_if_needed(profile, now)
        if not offers:
            await _say("the family doesn't want anything to do with you right now. "
                       "lay low, fix your reputation, and check back later.")
            return

        rank_name = catnip_list["levels"][level]["name"] if level < len(catnip_list["levels"]) else "?"
        window_idx = _jobs_window_index(now)
        _, win_end = _jobs_window_bounds(window_idx)
        heat = int(getattr(profile, "heat", 0) or 0)
        heat_band = "🟢" if heat <= 30 else ("🟡" if heat <= 70 else "🔴")
        suspended_until = int(getattr(profile, "perks_suspended_until", 0) or 0)
        pinch_active = suspended_until > now
        today_count = await _jobs_commits_today(int(profile.user_id), int(profile.guild_id), now)
        day_end = _jobs_start_of_utc_day(now) + 86400

        view = LayoutView(timeout=VIEW_TIMEOUT)
        items: list = [
            "## 📋 Jobs Board",
            (f"Mafia Lv {level} ({rank_name})  ·  {heat_band} Heat: {heat}/100  ·  "
             f"Jobs today: {today_count}/{JOBS_MAX_DAILY_COMMITS}  ·  Refreshes <t:{win_end}:R>"),
        ]
        if today_count >= JOBS_MAX_DAILY_COMMITS:
            items.append(f"-# 🛑 **Daily limit hit.** Resets <t:{day_end}:R>.")
        if pinch_active:
            items.append(f"-# 🚓 **Pinched.** Catnip perks suspended until <t:{suspended_until}:R>.")
        if level < 4:
            items.append("-# *Tutorial errand only. Reach Capo (Lv4) for the full board.*")
        for line in last_toast:
            items.append(line)
        last_toast.clear()

        for row in offers:
            reward = _jobs_coerce_dict(row.reward_snapshot)
            tier_info = JOBS_TIERS.get(str(row.tier), {})
            tier_name = tier_info.get("name", f"Tier {row.tier}")
            category_label = row.category.title() if row.category else ""
            section_body = (
                f"*{row.narrative}*\n"
                f"🎯 Target: **{_jobs_npc_display(row.target_faction)}**\n"
                f"💪 Difficulty: **{row.difficulty} SP**\n"
                f"💰 Reward: {_jobs_reward_summary(reward)}\n"
                f"🚨 Heat cost: +{row.heat_cost}"
            )

            accept_btn = Button(label="Accept", style=ButtonStyle.green, custom_id=f"jobs_accept_{row.id}")
            accept_btn.callback = make_on_accept(int(row.id))
            decline_btn = Button(label="Decline", style=ButtonStyle.gray, custom_id=f"jobs_decline_{row.id}")
            decline_btn.callback = make_on_decline(int(row.id))

            items.append(
                Section(
                    f"### {_jobs_npc_display(row.offered_by)}  ·  Tier {row.tier} ({tier_name})  ·  {category_label}",
                    section_body,
                    Thumbnail(
                        f"https://wsrv.nl/?url=raw.githubusercontent.com/milenakos/cat-bot/"
                        f"refs/heads/main/images/spawn/fine_cat.png"
                    ),
                ),
            )
            items.append(ActionRow(accept_btn, decline_btn))

        help_btn = Button(label="💡 Help", style=ButtonStyle.gray, custom_id="jobs_help")
        help_btn.callback = on_help
        items.append(ActionRow(help_btn))

        container = Container(*items)
        try:
            container.accent_color = Colors.brown
        except Exception:
            pass
        view.add_item(container)
        await _attach_view(interaction, view, use_followup)

    async def show_send(interaction: discord.Interaction):
        await profile.refresh_from_db()
        job = await JobInstance.get_or_none(id=mode["job_id"])
        if not job or job.state != "offered" or int(job.user_id) != int(message.user.id):
            mode["screen"] = "board"
            send_state.clear()
            last_toast.append("⚠️ That offer is no longer available.")
            await show_board(interaction)
            return

        send_total = _jobs_send_total(send_state)
        rep_bonus = _jobs_offerer_rep_bonus(job.offered_by, _jobs_faction_rep(profile))
        # Pending aftermath effects from a previous commit get applied to THIS
        # send. Surface them inline so the player isn't surprised at commit time.
        pending_diff_mult = float(_jobs_col(profile, "jobs_pending_difficulty_mult", 1.0))
        pending_heat_bonus = int(_jobs_col(profile, "jobs_pending_heat_bonus", 0))
        effective_difficulty = max(1, math.ceil(int(job.difficulty) * pending_diff_mult))
        effective_heat_cost = int(job.heat_cost or 0) + pending_heat_bonus
        chance = _jobs_success_chance(send_total, effective_difficulty, rep_bonus)
        ratio = send_total / max(1, effective_difficulty)
        # Complication preview (informational — the actual roll happens on commit).
        offerer_rep_for_comp = int(_jobs_faction_rep(profile).get(job.offered_by, 0))
        comp_chance = _jobs_complication_chance(
            int(job.tier), int(getattr(profile, "heat", 0) or 0), offerer_rep_for_comp
        )
        near_miss_band = JOBS_PROB["near_miss_band"]
        feel, color = _jobs_feel_label(chance)
        gauge = _jobs_gauge(chance)
        near_miss_chance = max(0.0, min(near_miss_band, JOBS_PROB["ceiling"] - chance))
        wipe_chance = max(0.0, 1.0 - chance - near_miss_chance)
        sent_count = _jobs_send_count(send_state)
        expected_loss_sp = 0
        if sent_count:
            avg_sp = send_total / max(1, sent_count)
            losses = wipe_chance * sent_count + near_miss_chance * math.ceil(sent_count / 2)
            expected_loss_sp = round(losses * avg_sp)

        tier_info = JOBS_TIERS.get(str(job.tier), {})
        tier_name = tier_info.get("name", f"Tier {job.tier}")
        crew_lines = []
        for t in cattypes:
            c = int(send_state.get(t, 0) or 0)
            if c <= 0:
                continue
            sp_each = JOBS_SEND_POWER.get(t, 0)
            raw_sp = sp_each * c
            eff_sp = int(round(_jobs_effective_sp_for_type(t, c)))
            emoji = get_emoji(t.lower() + "cat") or get_emoji(t.lower()) or ""
            # Show effective SP with the raw value in parens when they differ
            # (i.e. count > 1 — the diminishing-returns dampening kicked in).
            sp_str = f"{eff_sp} SP" if eff_sp == raw_sp else f"{eff_sp} SP  (raw {raw_sp})"
            crew_lines.append(f"{c:>3} × {emoji} {t}{' ' * max(0, 18 - len(t))} {sp_str}")
        if not crew_lines:
            crew_lines.append("(no cats in crew yet)")

        reward = _jobs_coerce_dict(job.reward_snapshot)

        items: list = [
            f"## 🎯 {_jobs_npc_display(job.offered_by)} — Tier {job.tier} ({tier_name})",
            f"*{job.narrative}*",
            f"🎯 Target: **{_jobs_npc_display(job.target_faction)}**",
            Separator(),
            "**👥 Your Crew**",
            "```\n" + "\n".join(crew_lines) + f"\nTotal: {send_total} SP\n```",
            (f"💪 Difficulty: **{effective_difficulty} SP**"
             f"{' (witness: +' + str(round((pending_diff_mult - 1) * 100)) + '%)' if pending_diff_mult > 1.0 else ''}"
             f"  ·  Ratio: **{ratio:.2f}×**"),
            f"📊 Success Chance: **{chance * 100:.0f}%**",
            f"`{gauge}` {feel}",
            f"⚠️ Complication chance: **{comp_chance * 100:.0f}%** — heat raids, rivals, jackpots",
            f"💰 Reward (success): {_jobs_reward_summary(reward)}",
            f"🩹 Near-miss ({near_miss_chance * 100:.0f}%): half your crew survives, no reward",
            f"💀 Total failure ({wipe_chance * 100:.0f}%): all cats destroyed",
        ]
        if sent_count:
            items.append(f"📉 Expected loss: ~{expected_loss_sp} SP across many attempts")
        heat_str = f"🚨 Heat cost: +{effective_heat_cost}"
        if pending_heat_bonus:
            heat_str += f" (+{pending_heat_bonus} loose end)"
        items.append(heat_str)
        for line in last_toast:
            items.append(line)
        last_toast.clear()

        add_btn = Button(label="➕ Add", style=ButtonStyle.green, custom_id="jobs_send_add")
        add_btn.callback = on_send_add
        remove_btn = Button(label="➖ Remove", style=ButtonStyle.gray, custom_id="jobs_send_remove")
        remove_btn.callback = on_send_remove
        remove_btn.disabled = sent_count == 0
        reset_btn = Button(label="Reset", style=ButtonStyle.gray, custom_id="jobs_send_reset")
        reset_btn.callback = on_send_reset
        reset_btn.disabled = sent_count == 0
        send_btn = Button(
            label="Send Crew" if sent_count else "Add cats first",
            style=ButtonStyle.green,
            custom_id="jobs_send_commit",
        )
        send_btn.callback = on_send_commit
        cancel_btn = Button(label="← Back", style=ButtonStyle.red, custom_id="jobs_send_back")
        cancel_btn.callback = on_send_back
        help_btn = Button(label="💡 Help", style=ButtonStyle.gray, custom_id="jobs_send_help")
        help_btn.callback = on_help

        items.append(ActionRow(add_btn, remove_btn, reset_btn))
        items.append(ActionRow(send_btn, cancel_btn, help_btn))

        view = LayoutView(timeout=VIEW_TIMEOUT)
        container = Container(*items)
        try:
            container.accent_color = color
        except Exception:
            pass
        view.add_item(container)
        await _attach_view(interaction, view, use_followup=False)

    async def show_result(interaction: discord.Interaction):
        logging.info("jobs: show_result entry job_id=%s", mode.get("job_id"))
        await profile.refresh_from_db()
        job = await JobInstance.get_or_none(id=mode["job_id"])
        logging.info("jobs: show_result fetched job=%s outcome=%s state=%s",
                     getattr(job, "id", None) if job else None,
                     getattr(job, "outcome", None) if job else None,
                     getattr(job, "state", None) if job else None)
        if not job or int(job.user_id) != int(message.user.id):
            mode["screen"] = "board"
            await show_board(interaction)
            return

        outcome = job.outcome
        send_snapshot = _jobs_coerce_dict(job.send_snapshot)
        cats_destroyed = _jobs_coerce_dict(job.cats_destroyed)
        reward = _jobs_coerce_dict(job.reward_snapshot)
        sent_count = _jobs_send_count(send_snapshot)
        destroyed_count = _jobs_send_count(cats_destroyed)
        survived = sent_count - destroyed_count

        if outcome == "success":
            header = "## ✅ Success"
            outcome_color = Colors.green
            body_lines = [
                f"All {sent_count} cats came home.",
                f"💰 Reward: {_jobs_reward_summary(reward)}",
            ]
        elif outcome == "near_miss":
            header = "## 🩹 Near-miss"
            outcome_color = Colors.brown
            body_lines = [
                f"You got out, but **{destroyed_count}** cats didn't.",
                f"Survivors returned: {survived}.",
                "No reward this time.",
            ]
        else:
            header = "## 💀 Total failure"
            outcome_color = Colors.red
            body_lines = [
                f"The whole crew is gone. **{sent_count}** cats lost.",
                "No reward.",
            ]

        items: list = [
            header,
            f"Rolled **{job.roll * 100:.1f}%** vs threshold **{job.success_chance * 100:.0f}%**",
            Separator(),
            *body_lines,
        ]
        # Complication block — sits between outcome body and heat summary so
        # it reads as "what happened beyond the dice."
        comp_id = (_jobs_col(job, "complication", "") or "").strip()
        if comp_id:
            flavor = _jobs_complication_flavor(comp_id, random.Random(int(job.committed_at or 0) ^ int(job.id or 0)))
            comp_pretty = comp_id.replace("_", " ").title()
            items.append(Separator())
            items.append(f"⚠️ **Complication: {comp_pretty}**")
            if flavor:
                items.append(f"*{flavor}*")
        items.append(Separator())
        items.append(f"🚨 Heat: +{job.heat_cost}")
        if destroyed_count:
            lost_summary = ", ".join(f"{c}× {t}" for t, c in cats_destroyed.items())
            items.append(f"💀 Lost: {lost_summary}")
        # Pinch follow-up — only shown on the commit that crossed the threshold.
        rep_changes = _jobs_coerce_dict(job.rep_changes)
        if rep_changes.get("pinched"):
            suspended_until = int(getattr(profile, "perks_suspended_until", 0) or 0)
            items.append(
                f"\n🚓 **Pinched.** Your heat hit 100. The Cat Police caught up with your crew.\n"
                f"Catnip perks are suspended until <t:{suspended_until}:R>. Heat reset to {JOBS_PINCH_RESET}."
            )

        # One cat from the crew gets the last word. Seeded off the job id + outcome
        # so the line is stable across re-renders of the same result.
        voice_rng = random.Random(int(job.id or 0) ^ hash(outcome) ^ int(job.committed_at or 0))
        send_for_voice = _jobs_coerce_dict(job.send_snapshot)
        voice_line = _jobs_pick_cat_voice(send_for_voice, cats_destroyed, outcome, comp_id, voice_rng)
        if voice_line:
            items.append(Separator())
            items.append(voice_line)

        # Outcomes are final — the roll is locked the moment Send Crew is clicked.
        # No Undo (strategic re-rolling defeats the gamble).
        back_btn = Button(label="📋 Back to board", style=ButtonStyle.gray, custom_id="jobs_result_back")
        back_btn.callback = on_result_back
        result_help_btn = Button(label="💡 Help", style=ButtonStyle.gray, custom_id="jobs_result_help")
        result_help_btn.callback = on_help
        items.append(ActionRow(back_btn, result_help_btn))

        view = LayoutView(timeout=VIEW_TIMEOUT)
        container = Container(*items)
        try:
            container.accent_color = outcome_color
        except Exception:
            pass
        view.add_item(container)
        logging.info("jobs: show_result about to send view (items=%d)", len(items))
        await _attach_view(interaction, view, use_followup=False)
        logging.info("jobs: show_result view sent")

    # ----- callback factories -----

    def make_on_accept(job_id: int):
        async def cb(interaction: discord.Interaction):
            mode["screen"] = "send"
            mode["job_id"] = job_id
            send_state.clear()
            # Public thematic embed. Fire-and-forget — don't block the send
            # screen render on it.
            try:
                job_for_announce = await JobInstance.get_or_none(id=job_id)
                if job_for_announce:
                    bot.loop.create_task(_jobs_announce_accept(
                        message.channel, job_for_announce, message.user.mention
                    ))
            except Exception:
                logging.exception("jobs: accept announce dispatch failed")
            await show_send(interaction)
        return cb

    def make_on_decline(job_id: int):
        async def cb(interaction: discord.Interaction):
            job = await JobInstance.get_or_none(id=job_id)
            if job and job.state == "offered" and int(job.user_id) == int(message.user.id):
                job.state = "declined"
                await job.save()
                last_toast.append(f"-# Declined offer from {_jobs_npc_display(job.offered_by)}.")
            await show_board(interaction)
        return cb

    async def on_help(interaction: discord.Interaction):
        """Board/Send help button → paginated help. Jumps to the most relevant
        page for the current screen; falls back to page 1 if not found."""
        if mode.get("screen") == "send":
            start = _jobs_help_index_by_title(profile, "sending")
        elif mode.get("screen") == "result":
            start = _jobs_help_index_by_title(profile, "three outcomes")
        else:
            start = 0
        await _jobs_send_help(interaction, profile, start_page=start)

    # ----- send screen modals + callbacks -----

    class AddCatsModal(Modal):
        def __init__(self):
            super().__init__(title="Add cats to crew", timeout=VIEW_TIMEOUT)
            self.rarity = TextInput(
                label="Rarity (e.g. Fine, Legendary)",
                style=discord.TextStyle.short,
                required=True,
                min_length=1,
                max_length=20,
                placeholder="Fine",
            )
            self.count = TextInput(
                label="How many?",
                style=discord.TextStyle.short,
                required=True,
                min_length=1,
                max_length=6,
                placeholder="1",
            )
            self.add_item(self.rarity)
            self.add_item(self.count)

        async def on_submit(self, interaction: discord.Interaction):
            t = self.rarity.value.strip()
            t_normalized = next((x for x in cattypes if x.lower() == t.lower()), None)
            if t_normalized is None:
                last_toast.append(f"⚠️ Unknown rarity: {t}")
                await show_send(interaction)
                return
            try:
                c = int(self.count.value)
                if c <= 0:
                    raise ValueError
            except Exception:
                last_toast.append("⚠️ Count must be a positive integer.")
                await show_send(interaction)
                return
            await profile.refresh_from_db()
            owned = int(profile[f"cat_{t_normalized}"] or 0)
            in_crew = int(send_state.get(t_normalized, 0) or 0)
            available = owned - in_crew
            if available <= 0:
                last_toast.append(f"⚠️ No spare {t_normalized} cats to send.")
                await show_send(interaction)
                return
            actual = min(c, available)
            send_state[t_normalized] = in_crew + actual
            logging.info("jobs: added %d× %s to send (total now %s)",
                         actual, t_normalized, dict(send_state))
            if actual < c:
                last_toast.append(f"-# Added {actual}× {t_normalized} (only {available} available).")
            await show_send(interaction)

    class RemoveCatsModal(Modal):
        def __init__(self):
            super().__init__(title="Remove cats from crew", timeout=VIEW_TIMEOUT)
            self.rarity = TextInput(
                label="Rarity",
                style=discord.TextStyle.short,
                required=True,
                min_length=1,
                max_length=20,
            )
            self.count = TextInput(
                label="How many? (use 'all' to remove all)",
                style=discord.TextStyle.short,
                required=True,
                min_length=1,
                max_length=6,
            )
            self.add_item(self.rarity)
            self.add_item(self.count)

        async def on_submit(self, interaction: discord.Interaction):
            t = self.rarity.value.strip()
            t_normalized = next((x for x in cattypes if x.lower() == t.lower()), None)
            if t_normalized is None or t_normalized not in send_state:
                last_toast.append(f"⚠️ {t} is not in your crew.")
                await show_send(interaction)
                return
            val = self.count.value.strip().lower()
            if val == "all":
                send_state.pop(t_normalized, None)
            else:
                try:
                    c = int(val)
                    if c <= 0:
                        raise ValueError
                except Exception:
                    last_toast.append("⚠️ Count must be a positive integer or 'all'.")
                    await show_send(interaction)
                    return
                new_val = max(0, int(send_state[t_normalized]) - c)
                if new_val == 0:
                    send_state.pop(t_normalized, None)
                else:
                    send_state[t_normalized] = new_val
            await show_send(interaction)

    async def on_send_add(interaction: discord.Interaction):
        await interaction.response.send_modal(AddCatsModal())

    async def on_send_remove(interaction: discord.Interaction):
        await interaction.response.send_modal(RemoveCatsModal())

    async def on_send_reset(interaction: discord.Interaction):
        send_state.clear()
        await show_send(interaction)

    async def on_send_back(interaction: discord.Interaction):
        mode["screen"] = "board"
        send_state.clear()
        await show_board(interaction)

    async def on_send_commit(interaction: discord.Interaction):
        logging.info("jobs: send_commit fired (user=%s job=%s send=%s)",
                     message.user.id, mode.get("job_id"), dict(send_state))
        if not send_state:
            await interaction.response.send_message("Add at least one cat first.", ephemeral=True)
            return
        # Daily cap check before doing any work. Cancelled commits don't count
        # (committed_at is zeroed on cancel), so misclicks aren't punished.
        now_check = int(time.time())
        today_count = await _jobs_commits_today(int(message.user.id), int(message.guild.id), now_check)
        if today_count >= JOBS_MAX_DAILY_COMMITS:
            day_end = _jobs_start_of_utc_day(now_check) + 86400
            await interaction.response.send_message(
                f"You've hit your daily limit of **{JOBS_MAX_DAILY_COMMITS}** jobs. "
                f"Resets <t:{day_end}:R>. Come back tomorrow.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        # Atomic: lock profile, lock offer (FOR UPDATE), verify inventory, escrow,
        # roll, apply outcome. The whole thing in one transaction.
        try:
            async with transaction() as conn:
                fresh = await Profile.get_or_create(
                    connection=conn,
                    user_id=int(message.user.id),
                    guild_id=int(message.guild.id),
                )
                job = await JobInstance.get_or_none(id=mode["job_id"])
                if not job or job.state != "offered" or int(job.user_id) != int(message.user.id):
                    last_toast.append("⚠️ That offer is no longer available.")
                    mode["screen"] = "board"
                    send_state.clear()
                    await show_board(interaction)
                    return

                for t, c in send_state.items():
                    if int(fresh[f"cat_{t}"] or 0) < c:
                        last_toast.append(f"⚠️ Not enough {t} cats.")
                        await show_send(interaction)
                        return

                # Escrow: decrement first. Survivors get re-added below per outcome.
                for t, c in send_state.items():
                    if not _jobs_subtract_cat(fresh, t, int(c)):
                        last_toast.append(f"⚠️ Escrow failed on {t}.")
                        await show_send(interaction)
                        return

                send_total = _jobs_send_total(send_state)
                rep_bonus_now = _jobs_offerer_rep_bonus(job.offered_by, _jobs_faction_rep(fresh))
                rng = random.Random()

                # --- Consume pending aftermath from the previous commit ---
                pending_diff_mult = float(_jobs_col(fresh, "jobs_pending_difficulty_mult", 1.0))
                pending_heat_bonus = int(_jobs_col(fresh, "jobs_pending_heat_bonus", 0))
                # Reset immediately so this commit's aftermath (if any) starts
                # from a clean slate. Tolerate the columns being absent (pre-009).
                try:
                    fresh.jobs_pending_difficulty_mult = 1.0
                    fresh.jobs_pending_heat_bonus = 0
                except KeyError:
                    pass
                effective_difficulty = max(1, math.ceil(int(job.difficulty) * pending_diff_mult))
                # Heat cost compounds: base × scrutiny × pending_heat_bonus, then
                # complications may add more in post_roll.
                committed_heat_cost = int(job.heat_cost or 0) + pending_heat_bonus

                # --- Roll the complication die (independent of success die) ---
                offerer_rep_for_comp = int(_jobs_faction_rep(fresh).get(job.offered_by, 0))
                comp_chance = _jobs_complication_chance(
                    int(job.tier), int(getattr(fresh, "heat", 0) or 0), offerer_rep_for_comp
                )
                comp_event = _jobs_roll_complication(int(job.tier), comp_chance, rng)
                comp_id = ""
                comp_meaningful = False

                # --- pre_roll: mutate difficulty or short-circuit outcome ---
                forced_outcome = None
                if comp_event and comp_event.get("phase") == "pre_roll":
                    effective_difficulty, forced_outcome = _jobs_apply_pre_roll(
                        comp_event, effective_difficulty, send_total
                    )
                    comp_id = comp_event["id"]
                    comp_meaningful = True

                # --- Success die ---
                if forced_outcome == "near_miss":
                    outcome_dict = {
                        "outcome": "near_miss",
                        "roll": 0.0,
                        "success_chance": _jobs_success_chance(send_total, effective_difficulty, rep_bonus_now),
                        "cats_destroyed": _jobs_select_near_miss_casualties(send_state, rng),
                    }
                else:
                    outcome_dict = _jobs_resolve_outcome_with_rep(
                        send_total, effective_difficulty, dict(send_state), rep_bonus_now, rng
                    )

                # --- post_roll: mutate reward or downgrade outcome, add heat ---
                reward_snapshot = _jobs_coerce_dict(job.reward_snapshot)
                if comp_event and comp_event.get("phase") == "post_roll":
                    outcome_dict, reward_snapshot, comp_extra_heat, fired = _jobs_apply_post_roll(
                        comp_event, outcome_dict, reward_snapshot, int(job.tier), dict(send_state), rng
                    )
                    if fired:
                        comp_id = comp_event["id"]
                        comp_meaningful = True
                        committed_heat_cost += comp_extra_heat

                # Persist the (possibly-mutated) reward + heat back onto the job
                # so _jobs_apply_outcome grants the modified values.
                job.reward_snapshot = reward_snapshot
                job.heat_cost = committed_heat_cost
                job.complication = comp_id if comp_meaningful else ""

                # --- aftermath: persist to profile for next commit ---
                if comp_event and comp_event.get("phase") == "aftermath":
                    _jobs_apply_aftermath(comp_event, fresh)
                    job.complication = comp_event["id"]
                    comp_meaningful = True

                job.send_snapshot = dict(send_state)
                job.send_total = send_total
                job.state = "committed"
                job.committed_at = int(time.time())
                # Also remember the effective_difficulty actually faced (handy
                # for the result screen narrative).
                if not isinstance(job.rep_changes, dict):
                    job.rep_changes = {}

                await _jobs_apply_outcome(fresh, job, outcome_dict, rng)

                # Re-credit surviving cats. (Success returns the entire send;
                # near-miss returns send-minus-destroyed; total failure returns none.)
                if outcome_dict["outcome"] == "success":
                    for t, c in send_state.items():
                        _jobs_add_cat(fresh, t, int(c))
                elif outcome_dict["outcome"] == "near_miss":
                    destroyed = outcome_dict["cats_destroyed"]
                    for t, c in send_state.items():
                        survived_c = int(c) - int(destroyed.get(t, 0))
                        if survived_c > 0:
                            _jobs_add_cat(fresh, t, survived_c)

                job.state = "resolved"
                job.resolved_at = int(time.time())
                logging.info("jobs: pre-save outcome=%s job_id=%s", outcome_dict["outcome"], job.id)
                await job.save()
                logging.info("jobs: job.save() ok")
                await fresh.save()
                logging.info("jobs: fresh.save() ok")
                # NOTE: don't refresh the outer `profile` reference here.
                # catpg's refresh_from_db calls _get() with no connection, which
                # auto-applies FOR UPDATE and tries to relock the profile row —
                # but `conn` already holds that lock, causing a self-deadlock.
                # show_result() does its own refresh outside this block.
        except Exception:
            logging.exception("jobs: commit transaction failed")
            last_toast.append("⚠️ Something went wrong. Your cats are safe — try again.")
            try:
                await show_send(interaction)
            except Exception:
                logging.exception("jobs: show_send after commit-failure also failed")
            return

        logging.info("jobs: transaction committed cleanly")

        # Battlepass extra-quest progress for jobs. Only successful resolutions
        # count toward the BP quests; near-miss/wipe don't progress them.
        try:
            committed_job = await JobInstance.get_or_none(id=mode["job_id"])
            if committed_job and committed_job.outcome == "success":
                try:
                    await progress(interaction, profile, "job_easy")
                except Exception:
                    logging.exception("jobs: job_easy progress failed")
                if int(committed_job.tier or 0) >= 4:
                    try:
                        await progress(interaction, profile, "job_hard")
                    except Exception:
                        logging.exception("jobs: job_hard progress failed")
        except Exception:
            logging.exception("jobs: post-commit BP progress wrapper failed")

        logging.info("jobs: about to call show_result")
        mode["screen"] = "result"
        try:
            await show_result(interaction)
            logging.info("jobs: show_result returned")
        except Exception:
            logging.exception("jobs: show_result failed")

        # Public thematic embed. Fire-and-forget so a channel-send failure
        # never affects the result screen the player already saw.
        try:
            resolved_job = await JobInstance.get_or_none(id=mode["job_id"])
            if resolved_job and resolved_job.outcome:
                bot.loop.create_task(_jobs_announce_outcome(
                    message.channel, resolved_job, profile, message.user.mention
                ))
        except Exception:
            logging.exception("jobs: outcome announce dispatch failed")

    async def on_result_back(interaction: discord.Interaction):
        mode["screen"] = "board"
        send_state.clear()
        await show_board(interaction)

    # ----- entry point -----
    await message.response.defer(ephemeral=True)
    await show_board(message, use_followup=True)


@bot.tree.command(description="view your reputation with the cat mafia families")
async def rep(message: discord.Interaction):
    """Per-server reputation with each Mafia NPC. Built up by completing
    contracts for them, tanked by working against them. Effects: offerer rep
    boosts success chance up to ±12%; target rep tunes difficulty up to +25%."""
    profile = await Profile.get_or_create(user_id=message.user.id, guild_id=message.guild.id)
    rep_data = _jobs_faction_rep(profile)
    refuse = JOBS_REP["refuse_threshold"]
    hostile = JOBS_REP["hostile_threshold"]
    unlock = JOBS_REP["unlock_threshold"]

    rows = []
    for key, npc in JOBS_NPCS.items():
        score = int(rep_data.get(key, 0))
        name = npc.get("display_name", key)
        if score >= unlock:
            band = "🌟 Premium"
        elif score >= 50:
            band = "💚 Friendly"
        elif score > refuse:
            band = "—"
        elif score > hostile:
            band = "🚫 Refuses"
        else:
            band = "🔥 Hostile"
        rows.append(f"`{score:>+5}`  **{name}**  {band}")
    # Also show top targets they're notable against:
    for key, t in config.jobs.get("targets_only", {}).items():
        if key == "commoners":
            continue
        score = int(rep_data.get(key, 0))
        name = t.get("display_name", key)
        rows.append(f"`{score:>+5}`  *{name}* (target)")

    body = "\n".join(rows) if rows else "No reputation yet."

    async def on_rep_help(interaction: discord.Interaction):
        await _jobs_send_help(interaction, profile, start_page=_jobs_help_index_by_title(profile, "reputation"))

    help_btn = Button(label="💡 Help", style=ButtonStyle.gray, custom_id="jobsrep_help")
    help_btn.callback = on_rep_help

    view = LayoutView(timeout=VIEW_TIMEOUT)
    container = Container(
        "## 🤝 Reputation",
        "Per-server. Built up by working *for* an NPC, tanked by working *against* one.",
        Separator(),
        body,
        Separator(),
        f"-# Refusal threshold: {refuse}  ·  Hostile: {hostile}  ·  Unlock: +{unlock}",
        ActionRow(help_btn),
    )
    try:
        container.accent_color = Colors.brown
    except Exception:
        pass
    view.add_item(container)
    await message.response.send_message(view=view, ephemeral=True)


@bot.tree.command(description="cat prisms are a special power up")
@discord.app_commands.describe(person="Person to view the prisms of")
async def prism(message: discord.Interaction, person: Optional[discord.User]):
    icon = get_emoji("prism")
    page_number = 0

    if not person:
        person_id = message.user
    else:
        person_id = person

    user_prisms = await Prism.collect("guild_id = $1 AND user_id = $2", message.guild.id, person_id.id)
    all_prisms = await Prism.collect("guild_id = $1", message.guild.id)
    total_count = len(all_prisms)
    user_count = len(user_prisms)
    global_boost = PRISM_BOOST_GLOBAL_COEF * math.log(2 * total_count + 1)
    user_boost = round((global_boost + PRISM_BOOST_USER_COEF * math.log(2 * user_count + 1)) * 100, 3)
    prism_texts = []

    if person_id == message.user and user_count != 0:
        await achemb(message, "prism", "followup")
        # Data-driven prism-event triggers (UI-added aches).
        try:
            prism_profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
            await ach_engine.evaluate(
                "prism",
                prism_profile,
                {"user_prism_count": user_count, "server_prism_count": total_count},
                message=message,
                achemb=achemb,
                send_type="followup",
            )
        except Exception:
            logging.exception("ach_engine prism event failed")

    order_map = {name: index for index, name in enumerate(prism_names)}
    prisms = all_prisms if not person else user_prisms
    prisms.sort(key=lambda p: order_map.get(p.name, float("inf")))

    for prism in prisms:
        prism_texts.append(f"{icon} **{prism.name}** {f'Owner: <@{prism.user_id}>' if not person else ''}\n<@{prism.creator}> crafted <t:{prism.time}:D>")

    if len(prisms) == 0:
        prism_texts.append("No prisms found!")

    async def confirm_craft(interaction: discord.Interaction):
        await interaction.response.defer()
        user = await Profile.get_or_create(guild_id=interaction.guild.id, user_id=interaction.user.id)

        # check we still can craft
        for i in cattypes:
            if user["cat_" + i] < 1:
                await interaction.followup.send("You don't have enough cats. Nice try though.", ephemeral=True)
                return

        if await Prism.count("guild_id = $1", interaction.guild.id) >= len(prism_names):
            await interaction.followup.send("This server has reached the prism limit.", ephemeral=True)
            return

        # determine the next name
        for selected_name in prism_names:
            if not await Prism.get_or_none(guild_id=message.guild.id, name=selected_name):
                break

        if await Prism.get_or_none(guild_id=message.guild.id, name=selected_name) or await Prism.count("guild_id = $1", message.guild.id) >= len(prism_names):
            await interaction.followup.send("This server has reached the prism limit.", ephemeral=True)
            return

        youngest_prism = await Prism.collect("guild_id = $1 ORDER BY time DESC LIMIT 1", message.guild.id)
        if youngest_prism:
            selected_time = max(round(time.time()), youngest_prism[0].time + 1)
        else:
            selected_time = round(time.time())

        # actually take away cats
        for i in cattypes:
            user["cat_" + i] -= 1
        await user.save()

        # create the prism
        await Prism.create(
            guild_id=interaction.guild.id,
            user_id=interaction.user.id,
            creator=interaction.user.id,
            time=selected_time,
            name=selected_name,
        )

        logging.debug("Created prism")

        await message.followup.send(f"{icon} {interaction.user.mention} has created prism {selected_name}!")
        await achemb(interaction, "prism", "followup")
        await achemb(interaction, "collecter", "followup")

    async def craft_prism(interaction: discord.Interaction):
        user = await Profile.get_or_create(guild_id=interaction.guild.id, user_id=interaction.user.id)

        found_cats = await cats_in_server(interaction.guild.id)
        missing_cats = []
        unknowns = 0
        for i in cattypes:
            if user[f"cat_{i}"] > 0:
                continue
            if i in found_cats:
                missing_cats.append(get_emoji(i.lower() + "cat"))
            else:
                unknowns += 1

        unknown_suffix = ""
        if unknowns:
            unknown_suffix = f" + {unknowns} unknown cat types (see /catalogue)"

        if len(missing_cats) == 0:
            view = View(timeout=VIEW_TIMEOUT)
            confirm_button = Button(label="Craft!", style=ButtonStyle.blurple, emoji=icon)
            confirm_button.callback = confirm_craft
            description = "The crafting recipe is __ONE of EVERY cat type__.\nContinue crafting?"
        else:
            view = View(timeout=VIEW_TIMEOUT)
            confirm_button = Button(label="Not enough cats!", style=ButtonStyle.red, disabled=True)
            description = "The crafting recipe is __ONE of EVERY cat type__.\nYou are missing " + "".join(missing_cats) + unknown_suffix

        view.add_item(confirm_button)
        await interaction.response.send_message(description, view=view, ephemeral=True)

    async def prev_page(interaction):
        nonlocal page_number
        page_number -= 1
        embed, view = gen_page()
        await interaction.response.edit_message(embed=embed, view=view)

    async def next_page(interaction):
        nonlocal page_number
        page_number += 1
        embed, view = gen_page()
        await interaction.response.edit_message(embed=embed, view=view)

    def gen_page():
        target = "" if not person else f"{person_id.name}'s"

        embed = discord.Embed(
            title=f"{icon} {target} Cat Prisms",
            color=Colors.brown,
            description="Prisms are a tradeable power-up which occasionally bumps cat rarity up by one. Each prism crafted gives the entire server an increased chance to get upgraded, plus additional chance for prism owner.\n\n",
        ).set_footer(
            text=f"{total_count} Total Prisms | Server boost: {round(global_boost * 100, 3)}%\n{person_id.name}'s prisms | Owned: {user_count} | Personal boost: {user_boost}%"
        )

        embed.description += "\n".join(prism_texts[page_number * 26 : (page_number + 1) * 26])

        view = View(timeout=VIEW_TIMEOUT)

        craft_button = Button(label="Craft!", style=ButtonStyle.blurple, emoji=icon)
        craft_button.callback = craft_prism
        view.add_item(craft_button)

        prev_button = Button(label="<-", disabled=bool(page_number == 0))
        prev_button.callback = prev_page
        view.add_item(prev_button)

        next_button = Button(label="->", disabled=bool(page_number == (len(prism_texts) + 1) // 26))
        next_button.callback = next_page
        view.add_item(next_button)

        return embed, view

    embed, view = gen_page()
    await message.response.send_message(embed=embed, view=view)


@bot.tree.command(description="Pong")
async def ping(message: discord.Interaction):
    try:
        latency = round(bot.latency * 1000)
    except Exception:
        latency = "infinite"
    if latency == 0:
        # probably using gateway proxy, try fetching latency from metrics
        async with aiohttp.ClientSession() as session:
            shard_latency = 0
            try:
                async with session.get("http://localhost:7878/metrics") as response:
                    data = await response.text()
                    total_latencies = 0
                    total_shards = 0
                    for line in data.split("\n"):
                        if line.startswith("gateway_shard_latency{shard="):
                            if "NaN" in line:
                                continue
                            if f'shard="{message.guild.shard_id}"' in line:
                                shard_latency = int(float(line.split(" ")[1]) * 1000)
                            try:
                                total_latencies += float(line.split(" ")[1])
                                total_shards += 1
                            except Exception:
                                pass
                    latency = round((total_latencies / total_shards) * 1000)
            except Exception:
                pass
        postfix = ""
        if shard_latency:
            postfix = f"\nthe neuron for this server has a delay of {shard_latency} ms {get_emoji('staring_cat')}{get_emoji('staring_cat')}"
        await message.response.send_message(f"🏓 cat has global brain delay of {latency} ms {get_emoji('staring_cat')}{postfix}")
    else:
        await message.response.send_message(f"🏓 cat has brain delay of {latency} ms {get_emoji('staring_cat')}")
    user = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    await progress(message, user, "ping")


@bot.tree.command(description="the most useful command ever")
async def bruh(message: discord.Interaction):
    await message.response.defer()
    await message.delete_original_response()


@bot.tree.command(description="play a relaxing game of tic tac toe")
@discord.app_commands.describe(person="who do you want to play with? (choose Cat Bot for ai)")
async def tictactoe(message: discord.Interaction, person: discord.Member):
    do_edit = False
    board = [None, None, None, None, None, None, None, None, None]

    players = [message.user, person]
    random.shuffle(players)
    bot_is_playing = person == bot.user
    current_turn = 0

    def check_win(board):
        combinations = [
            # rows
            [0, 1, 2],
            [3, 4, 5],
            [6, 7, 8],
            # columns
            [0, 3, 6],
            [1, 4, 7],
            [2, 5, 8],
            # diagonals
            [0, 4, 8],
            [2, 4, 6],
        ]

        for combination in combinations:
            if board[combination[0]] == board[combination[1]] == board[combination[2]] and board[combination[0]] is not None:
                return combination

        return [-1]

    def minimax(board, depth, is_maximizing, alpha, beta, bot_symbol, human_symbol):
        wins = check_win(board)
        if wins != [-1]:
            if board[wins[0]] == bot_symbol:
                return 10 - depth  # Bot wins (good for bot)
            elif board[wins[0]] == human_symbol:
                return -10 + depth  # Human wins (bad for bot)

        if all(cell is not None for cell in board):
            return 0

        if is_maximizing:
            max_eval = float("-inf")
            for i in range(9):
                if board[i] is None:
                    board[i] = bot_symbol
                    eval = minimax(board, depth + 1, False, alpha, beta, bot_symbol, human_symbol)
                    board[i] = None
                    max_eval = max(max_eval, eval)
                    alpha = max(alpha, eval)
                    if beta <= alpha:
                        break
            return max_eval
        else:
            min_eval = float("inf")
            for i in range(9):
                if board[i] is None:
                    board[i] = human_symbol
                    eval = minimax(board, depth + 1, True, alpha, beta, bot_symbol, human_symbol)
                    board[i] = None
                    min_eval = min(min_eval, eval)
                    beta = min(beta, eval)
                    if beta <= alpha:
                        break
            return min_eval

    def get_best_move(board):
        best_score = float("-inf")
        best_move = None

        bot_turn = None
        human_turn = None
        for i, player in enumerate(players):
            if player.bot:
                bot_turn = i
            else:
                human_turn = i

        bot_symbol = "❌" if bot_turn == 0 else "⭕"
        human_symbol = "❌" if human_turn == 0 else "⭕"

        for i in range(9):
            if board[i] is None:
                board[i] = bot_symbol
                score = minimax(board, 0, False, float("-inf"), float("inf"), bot_symbol, human_symbol)
                board[i] = None

                if score > best_score:
                    best_score = score
                    best_move = i

        return best_move

    async def finish_turn():
        nonlocal do_edit, current_turn

        view = LayoutView(timeout=VIEW_TIMEOUT)
        wins = check_win(board)
        tie = True
        rows = [ActionRow(), ActionRow(), ActionRow()]
        for cell_num, cell in enumerate(board):
            if cell is None:
                tie = False
                button = Button(emoji=get_emoji("empty"), custom_id=str(cell_num), disabled=wins != [-1])
            else:
                button = Button(emoji=cell, disabled=True, style=ButtonStyle.green if cell_num in wins else ButtonStyle.gray)
            button.callback = play
            rows[cell_num // 3].add_item(button)

        if wins != [-1]:
            if board[wins[0]] == "❌":
                second_line = f"{players[0].mention} (X) won!"
                await end_game(0)
            elif board[wins[0]] == "⭕":
                second_line = f"{players[1].mention} (O) won!"
                await end_game(1)
        elif tie:
            second_line = "its a tie!"
            await end_game(-1)
        else:
            second_line = f"{players[current_turn].mention}'s turn ({'X' if current_turn == 0 else 'O'})"

        container = Container(f"## {players[0].mention} (X) vs {players[1].mention} (O)", second_line, rows[0], rows[1], rows[2])
        view.add_item(container)

        if do_edit:
            await message.edit_original_response(view=view)
        else:
            await message.response.send_message(view=view)
            do_edit = True

        if bot_is_playing and players[current_turn].bot and wins == [-1] and not tie:
            await asyncio.sleep(1)
            best_move = get_best_move(board)
            if best_move is not None:
                board[best_move] = "❌" if current_turn == 0 else "⭕"
                current_turn = 1 - current_turn
                await finish_turn()

    async def play(interaction):
        nonlocal current_turn
        cell_num = int(interaction.data["custom_id"])
        if board[cell_num] is not None:
            await interaction.response.send_message("That spot is already taken!", ephemeral=True)
            return
        if players[current_turn] != interaction.user:
            await interaction.response.send_message("It's not your turn!", ephemeral=True)
            return
        await interaction.response.defer()
        board[cell_num] = "❌" if current_turn == 0 else "⭕"
        current_turn = 1 - current_turn
        await finish_turn()

    async def end_game(winner):
        if players[0] == players[1]:
            # self-play
            user = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
            await progress(message, user, "ttc")
            return
        users = [
            await Profile.get_or_create(guild_id=message.guild.id, user_id=players[0].id),
            await Profile.get_or_create(guild_id=message.guild.id, user_id=players[1].id),
        ]
        users[0].ttt_played += 1
        users[1].ttt_played += 1
        if winner != -1:
            users[winner].ttt_won += 1
            await achemb(message, "ttt_win", "followup", players[winner])
        else:
            users[0].ttt_draws += 1
            users[1].ttt_draws += 1
        await users[0].save()
        await users[1].save()
        await progress(message, users[0], "ttc")
        await progress(message, users[1], "ttc")

    await finish_turn()


@bot.tree.command(description="dont select a person to make an everyone vs you game")
@discord.app_commands.describe(person="Who do you want to play with?")
async def rps(message: discord.Interaction, person: Optional[discord.Member]):
    clean_name = message.user.name.replace("_", "\\_")
    picks = {"Rock": [], "Paper": [], "Scissors": []}
    mappings = {"Rock": ["Paper", "Rock", "Scissors"], "Paper": ["Scissors", "Paper", "Rock"], "Scissors": ["Rock", "Scissors", "Paper"]}
    vs_picks = {}
    players = []

    async def pick(interaction):
        nonlocal players
        if person and interaction.user.id not in [message.user.id, person.id]:
            await do_funny(interaction)
            return

        await interaction.response.defer()

        thing = interaction.data["custom_id"]
        if person or interaction.user != message.user:
            if interaction.user.id in players:
                return
            if person:
                vs_picks[interaction.user.name.replace("_", "\\_")] = thing
            else:
                picks[thing].append(interaction.user.name.replace("_", "\\_"))
            players.append(interaction.user.id)
            if person and person.id == bot.user.id:
                players.append(bot.user.id)
                vs_picks[bot.user.name.replace("_", "\\_")] = mappings[thing][0]
            if not person or len(players) == 1:
                await interaction.edit_original_response(content=f"Players picked: {len(players)}")
                return

        result = mappings[thing]

        if not person:
            description = f"{clean_name} picked: __{thing}__\n\n"
            for num, i in enumerate(["Winners", "Tie", "Losers"]):
                if picks[result[num]]:
                    peoples = "\n".join(picks[result[num]])
                else:
                    peoples = "No one"
                description += f"**{i}** ({result[num]})\n{peoples}\n\n"
        else:
            description = f"{clean_name} picked: __{vs_picks[clean_name]}__\n\n{clean_name_2} picked: __{vs_picks[clean_name_2]}__\n\n"
            result = mappings[vs_picks[clean_name]].index(vs_picks[clean_name_2])
            if result == 0:
                description += f"**Winner**: {clean_name_2}!"
            elif result == 1:
                description += "It's a **Tie**!"
            else:
                description += f"**Winner**: {clean_name}!"

        embed = discord.Embed(
            title=f"{clean_name_2} vs {clean_name}",
            description=description,
            color=Colors.brown,
        )
        await interaction.edit_original_response(content=None, embed=embed, view=None)

    if person:
        clean_name_2 = person.name.replace("_", "\\_")
    else:
        clean_name_2 = "Rock Paper Scissors"

    if person:
        description = "Pick what to play!"
    else:
        description = "Any amount of users can play. The game ends when the person who ran the command picks. Max time is 24 hours."
    embed = discord.Embed(
        title=f"{clean_name_2} vs {clean_name}",
        description=description,
        color=Colors.brown,
    )
    view = View(timeout=24 * 3600)
    for i in ["Rock", "Paper", "Scissors"]:
        button = Button(label=i, custom_id=i)
        button.callback = pick
        view.add_item(button)
    await message.response.send_message("Players picked: 0", embed=embed, view=view)


@bot.tree.command(description="you feel like making cookies")
async def cookie(message: discord.Interaction):
    user = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)

    async def bake(interaction):
        nonlocal user
        if interaction.user != message.user:
            await do_funny(interaction)
            return
        await interaction.response.defer()
        user = await Profile.get(["cookies"], guild_id=message.guild.id, user_id=message.user.id)
        user.cookies += 1
        await user.save()
        view.children[0].label = f"{user.cookies:,}"
        await interaction.edit_original_response(view=view)
        if user.cookies < 5:
            await achemb(interaction, "cookieclicker", "followup")
        if 5100 > user.cookies >= 5000:
            await achemb(interaction, "cookiesclicked", "followup")
        # casino quest: clicking the cookie counts as the cookieclicker game
        await progress_casino_quest(interaction, user, "cookieclicker")

    view = View(timeout=VIEW_TIMEOUT)
    button = Button(emoji="🍪", label=f"{user.cookies:,}", style=ButtonStyle.blurple)
    button.callback = bake
    view.add_item(button)
    await message.response.send_message(view=view)


@bot.tree.command(description="donate (give) cats now")
@discord.app_commands.rename(gift_type="type")
@discord.app_commands.describe(
    person="Whom to gift?",
    gift_type="im gonna airstrike your house from orbit",
    amount="And how much?",
)
@discord.app_commands.autocomplete(gift_type=gift_autocomplete)
async def gift(
    message: discord.Interaction,
    person: discord.User,
    gift_type: str,
    amount: Optional[int],
):
    if amount is None:
        # default the amount to 1
        amount = 1
    person_id = person.id

    if amount <= 0 or amount >= 2147483647 or message.user.id == person_id:
        # haha skill issue
        await message.response.send_message("no", ephemeral=True)
        if message.user.id == person_id:
            await achemb(message, "lonely", "followup")
        return

    async with transaction() as conn:
        if gift_type.lower() == "rain":
            if person_id == bot.user.id:
                await message.response.send_message("you can't sacrifice rains", ephemeral=True)
                return
            user = await User.get_or_create(conn, user_id=message.user.id)
            reciever = await User.get_or_create(conn, user_id=person_id)
        else:
            user = await Profile.get_or_create(conn, guild_id=message.guild.id, user_id=message.user.id)
            reciever = await Profile.get_or_create(conn, guild_id=message.guild.id, user_id=person_id)

        if gift_type.lower() == "rain":
            key = "rain_minutes"
            thing = "Rain Minutes"
        elif gift_type.lower() in [cattype.lower() for cattype in cattypes]:
            gift_type = cattype_lc_dict[gift_type.lower()]
            key = f"cat_{gift_type}"
            thing = f"{gift_type} cats"
        elif gift_type.lower() in [i["name"].lower() for i in pack_data]:
            key = f"pack_{gift_type.lower()}"
            thing = f"{gift_type.capitalize()} packs"
            if user.battlepass < 3 and not user.bp_history.strip().replace("0,0,0;", ""):
                await message.response.send_message("you need to reach atleast cattlepass level 3 to gift packs.", ephemeral=True)
                return
        else:
            await message.response.send_message("bro what", ephemeral=True)
            return

        # if enough
        if user[key] >= amount:
            user[key] -= amount
            reciever[key] += amount
            if key.startswith("cat_"):
                user.cats_gifted += amount
                reciever.cat_gifts_recieved += amount
            await user.save()
            await reciever.save()
            if key.startswith("cat_"):
                # gift_type is the cat type (Fine/Nice/...) at this point.
                await mark_discovered(reciever, gift_type)
        else:
            await message.response.send_message("no", ephemeral=True)
            return

    content = f"Successfully transfered {amount:,} {thing} from {message.user.mention} to {person.mention}!"

    # handle tax
    if key.startswith("cat_") and amount >= 5:
        tax_amount = round(amount * 0.2)
        tax_debounce = False

        async def pay(interaction):
            nonlocal tax_debounce
            if tax_debounce:
                return
            if interaction.user.id != message.user.id:
                await do_funny(interaction)
                return

            tax_debounce = True
            await interaction.response.defer()

            user = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
            user[f"cat_{gift_type}"] -= tax_amount
            await user.save()

            await interaction.edit_original_response(view=None)
            await interaction.followup.send(f"You paid the tax of {tax_amount:,} {gift_type} cats!")
            await achemb(message, "good_citizen", "followup")
            if user[f"cat_{gift_type}"] < 0:
                bot.loop.create_task(debt_cutscene(interaction, user))

        async def evade(interaction):
            if interaction.user.id != message.user.id:
                await do_funny(interaction)
                return

            await interaction.response.defer()
            await interaction.edit_original_response(view=None)
            await interaction.followup.send(f"You evaded the tax of {tax_amount:,} {gift_type} cats.")
            await achemb(message, "secret", "followup")

        button = Button(label="Pay 20% tax", style=ButtonStyle.green)
        button.callback = pay

        button2 = Button(label="Evade the tax", style=ButtonStyle.red)
        button2.callback = evade

        myview = View(timeout=VIEW_TIMEOUT)

        myview.add_item(button)
        myview.add_item(button2)

        await message.response.send_message(content, view=myview, allowed_mentions=discord.AllowedMentions(users=True))
    else:
        await message.response.send_message(content, allowed_mentions=discord.AllowedMentions(users=True))

    # handle aches
    user = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    await achemb(message, "donator", "followup")
    await achemb(message, "anti_donator", "followup", person)
    # Data-driven gift-event triggers (sender side).
    try:
        await ach_engine.evaluate(
            "gift",
            user,
            {"amount": int(amount) if str(amount).isdigit() else 0, "gift_type": gift_type, "recipient_is_bot": person_id == bot.user.id},
            message=message,
            achemb=achemb,
            send_type="followup",
        )
    except Exception:
        logging.exception("ach_engine gift event failed")
    if person_id == bot.user.id and gift_type == "Ultimate":
        user.ultimates_gifted = min(32766, user.ultimates_gifted + int(amount))
        await user.save()
        if user.ultimates_gifted >= 5:
            await achemb(message, "rich", "followup")
    if person_id == bot.user.id:
        await achemb(message, "sacrifice", "followup")
    if gift_type == "Nice" and int(amount) == 69:
        await achemb(message, "nice", "followup")

    await progress(message, user, "gift")
    # Social quest: any /gift to another player (not the bot) counts.
    if person_id != bot.user.id:
        await progress(message, user, "social")
    # Generous quest (`gift3`): each unique non-bot recipient ticks progress
    # once per quest cycle. We persist the recipient list as comma-separated
    # IDs on the profile; it's cleared on quest completion and on season roll.
    if person_id != bot.user.id and user.extra_quest == "gift3" and user.extra_cooldown == 0:
        recipients = [r for r in user.gift3_recipients.split(",") if r]
        if str(person_id) not in recipients:
            recipients.append(str(person_id))
            user.gift3_recipients = ",".join(recipients)
            await user.save()
            await progress(message, user, "gift3")
    # Sacrifice quest: gifting cats to the bot. XP depends on the cat type
    # (hidden from the user — the quest text only says "depends on the cat"),
    # multiplied by amount and capped at 300.
    if person_id == bot.user.id and key.startswith("cat_") and user.extra_quest == "sacrifice" and user.extra_cooldown == 0:
        per_cat_xp = SACRIFICE_XP.get(gift_type, 25)
        user.extra_reward = min(300, per_cat_xp * int(amount))
        await user.save()
        await progress(message, user, "sacrifice")

    if key == "rain_minutes":
        try:
            ch = bot.get_partial_messageable(config.RAIN_CHANNEL_ID)
            await ch.send(f"{message.user.id} gave {amount}m to {person_id}")
        except Exception:
            pass


@bot.tree.command(description="Trade stuff!")
@discord.app_commands.rename(person_id="user")
@discord.app_commands.describe(person_id="why would you need description")
async def trade(message: discord.Interaction, person_id: discord.User):
    person1 = message.user
    person2 = person_id

    blackhole = False

    person1accept = False
    person2accept = False

    person1value = 0
    person2value = 0

    person1gives = {}
    person2gives = {}

    user1 = await Profile.get_or_create(guild_id=message.guild.id, user_id=person1.id)
    user2 = await Profile.get_or_create(guild_id=message.guild.id, user_id=person2.id)

    if not bot.user:
        return

    # do the funny
    if person2.id == bot.user.id:
        person2gives["eGirl"] = 9999999

    # this is the deny button code
    async def denyb(interaction):
        nonlocal person1, person2, person1accept, person2accept, person1gives, person2gives, blackhole
        if interaction.user != person1 and interaction.user != person2:
            await do_funny(interaction)
            return

        await interaction.response.defer()
        blackhole = True
        person1gives = {}
        person2gives = {}
        try:
            await interaction.edit_original_response(
                content=f"{interaction.user.mention} has cancelled the trade.",
                embed=None,
                view=None,
            )
        except Exception:
            pass

    # this is the accept button code
    async def acceptb(interaction):
        nonlocal person1, person2, person1accept, person2accept, person1gives, person2gives, person1value, person2value, user1, user2, blackhole
        if interaction.user != person1 and interaction.user != person2:
            await do_funny(interaction)
            return

        # clicking accept again would make you un-accept
        if interaction.user == person1:
            person1accept = not person1accept
        elif interaction.user == person2:
            person2accept = not person2accept

        await interaction.response.defer()
        await update_trade_embed(interaction)

        if person1accept and person2 == bot.user:
            await achemb(message, "desperate", "followup")

        if blackhole:
            await update_trade_embed(interaction)
            return

        if person1accept and person2accept:
            blackhole = True
            await user1.refresh_from_db()
            await user2.refresh_from_db()
            actual_user1 = await User.get_or_create(user_id=person1.id)
            actual_user2 = await User.get_or_create(user_id=person2.id)

            # check if we have enough things (person could have moved them during the trade)
            error = False
            person1prismgive = 0
            person2prismgive = 0
            for k, v in person1gives.items():
                if k in prism_names:
                    person1prismgive += 1
                    prism = await Prism.get_or_none(guild_id=interaction.guild.id, name=k)
                    if not prism or prism.user_id != person1.id:
                        error = True
                        break
                    continue
                elif k == "rains":
                    if actual_user1.rain_minutes < v:
                        error = True
                        break
                elif k in cattypes:
                    if user1[f"cat_{k}"] < v:
                        error = True
                        break
                elif user1[f"pack_{k.lower()}"] < v:
                    error = True
                    break

            for k, v in person2gives.items():
                if k in prism_names:
                    person2prismgive += 1
                    prism = await Prism.get_or_none(guild_id=interaction.guild.id, name=k)
                    if not prism or prism.user_id != person2.id:
                        error = True
                        break
                    continue
                elif k == "rains":
                    if actual_user2.rain_minutes < v:
                        error = True
                        break
                elif k in cattypes:
                    if user2[f"cat_{k}"] < v:
                        error = True
                        break
                elif user2[f"pack_{k.lower()}"] < v:
                    error = True
                    break

            if error:
                try:
                    await interaction.edit_original_response(
                        content="Uh oh - some of the cats/prisms/packs/rains disappeared while trade was happening",
                        embed=None,
                        view=None,
                    )
                except Exception:
                    await interaction.followup.send("Uh oh - some of the cats/prisms/packs/rains disappeared while trade was happening")
                return

            # exchange
            cat_count = 0
            user2_discovered_via_trade: list[str] = []
            user1_discovered_via_trade: list[str] = []
            for k, v in person1gives.items():
                if k in prism_names:
                    move_prism = await Prism.get_or_none(guild_id=message.guild.id, name=k)
                    move_prism.user_id = person2.id
                    await move_prism.save()
                elif k == "rains":
                    actual_user1.rain_minutes -= v
                    actual_user2.rain_minutes += v
                    try:
                        ch = bot.get_partial_messageable(config.RAIN_CHANNEL_ID)
                        await ch.send(f"{actual_user1.user_id} traded {v}m to {actual_user2.user_id}")
                    except Exception:
                        pass
                elif k in cattypes:
                    cat_count += v
                    user1[f"cat_{k}"] -= v
                    user2[f"cat_{k}"] += v
                    user2_discovered_via_trade.append(k)
                else:
                    user1[f"pack_{k.lower()}"] -= v
                    user2[f"pack_{k.lower()}"] += v

            for k, v in person2gives.items():
                if k in prism_names:
                    move_prism = await Prism.get_or_none(guild_id=message.guild.id, name=k)
                    move_prism.user_id = person1.id
                    await move_prism.save()
                elif k == "rains":
                    actual_user2.rain_minutes -= v
                    actual_user1.rain_minutes += v
                    try:
                        ch = bot.get_partial_messageable(config.RAIN_CHANNEL_ID)
                        await ch.send(f"{actual_user2.user_id} traded {v}m to {actual_user1.user_id}")
                    except Exception:
                        pass
                elif k in cattypes:
                    cat_count += v
                    user1[f"cat_{k}"] += v
                    user2[f"cat_{k}"] -= v
                    user1_discovered_via_trade.append(k)
                else:
                    user1[f"pack_{k.lower()}"] += v
                    user2[f"pack_{k.lower()}"] -= v

            user1.cats_traded += cat_count
            user2.cats_traded += cat_count
            user1.trades_completed += 1
            user2.trades_completed += 1

            await user1.save()
            await user2.save()
            await actual_user1.save()
            await actual_user2.save()

            for k in user1_discovered_via_trade:
                await mark_discovered(user1, k)
            for k in user2_discovered_via_trade:
                await mark_discovered(user2, k)

            try:
                await interaction.edit_original_response(content="Trade finished!", view=None)
            except Exception:
                await interaction.followup.send()

            await achemb(message, "extrovert", "followup")
            await achemb(message, "extrovert", "followup", person2)

            # Data-driven trade-event triggers (for both sides).
            try:
                trade_ctx = {
                    "cat_count": cat_count,
                    "person1_value": person1value,
                    "person2_value": person2value,
                    "total_value": person1value + person2value,
                }
                await ach_engine.evaluate(
                    "trade", user1, trade_ctx,
                    message=message, achemb=achemb, send_type="followup",
                )
                await ach_engine.evaluate(
                    "trade", user2, trade_ctx,
                    message=message, achemb=achemb, send_type="followup",
                    author_string=person2,
                )
            except Exception:
                logging.exception("ach_engine trade event failed")

            if cat_count >= 1000:
                await achemb(message, "capitalism", "followup")
                await achemb(message, "capitalism", "followup", person2)

            if person2value + person1value == 0:
                await achemb(message, "absolutely_nothing", "followup")
                await achemb(message, "absolutely_nothing", "followup", person2)

            if person2value - person1value >= 100:
                await achemb(message, "profit", "followup")
            if person1value - person2value >= 100:
                await achemb(message, "profit", "followup", person2)

            if person1value > person2value:
                await achemb(message, "scammed", "followup")
            if person2value > person1value:
                await achemb(message, "scammed", "followup", person2)

            if person1value == person2value and person1gives != person2gives:
                await achemb(message, "perfectly_balanced", "followup")
                await achemb(message, "perfectly_balanced", "followup", person2)

            await progress(message, user1, "trade")
            await progress(message, user2, "trade")
            await progress(message, user1, "social")
            await progress(message, user2, "social")

    # add cat code
    async def addb(interaction):
        nonlocal person1, person2, person1accept, person2accept, person1gives, person2gives
        if interaction.user != person1 and interaction.user != person2:
            await do_funny(interaction)
            return

        currentuser = 1 if interaction.user == person1 else 2

        # all we really do is spawn the modal
        modal = TradeModal(currentuser)
        await interaction.response.send_modal(modal)

    # this is ran like everywhere when you do anything
    # it updates the embed
    async def gen_embed():
        nonlocal person1, person2, person1accept, person2accept, person1gives, person2gives, blackhole, person1value, person2value

        if blackhole:
            # no way thats fun
            await achemb(message, "blackhole", "followup")
            await achemb(message, "blackhole", "followup", person2)
            return discord.Embed(color=Colors.brown, title="Blackhole", description="How Did We Get Here?"), None

        view = View(timeout=VIEW_TIMEOUT)

        accept = Button(label="Accept", style=ButtonStyle.green)
        accept.callback = acceptb

        deny = Button(label="Deny", style=ButtonStyle.red)
        deny.callback = denyb

        add = Button(label="Offer...", style=ButtonStyle.blurple)
        add.callback = addb

        view.add_item(accept)
        view.add_item(deny)
        view.add_item(add)

        person1name = person1.name.replace("_", "\\_")
        person2name = person2.name.replace("_", "\\_")
        coolembed = discord.Embed(
            color=Colors.brown,
            title=f"{person1name} and {person2name} trade",
            description="no way",
        )

        # a single field for one person
        def field(personaccept, persongives, person, number):
            nonlocal coolembed, person1value, person2value
            icon = "⬜"
            if personaccept:
                icon = "✅"
            valuestr = ""
            valuenum = 0
            total = 0
            for k, v in persongives.items():
                if v == 0:
                    continue
                if k in prism_names:
                    # prisms
                    valuestr += f"{get_emoji('prism')} {k}\n"
                    for v2 in type_dict.values():
                        valuenum += sum(type_dict.values()) / v2
                elif k == "rains":
                    # rains
                    valuestr += f"☔ {v:,}m of Cat Rains\n"
                    valuenum += 900 * v
                elif k in cattypes:
                    # cats
                    valuenum += (sum(type_dict.values()) / type_dict[k]) * v
                    total += v
                    aicon = get_emoji(k.lower() + "cat")
                    valuestr += f"{aicon} {k} {v:,}\n"
                else:
                    # packs
                    valuenum += sum([i["totalvalue"] if i["name"] == k else 0 for i in pack_data]) * v
                    aicon = get_emoji(k.lower() + "pack")
                    valuestr += f"{aicon} {k} {v:,}\n"
            if not valuestr:
                valuestr = "Nothing offered!"
            else:
                valuestr += f"*Total value: {round(valuenum):,}\nTotal cats: {round(total):,}*"
                if number == 1:
                    person1value = round(valuenum)
                else:
                    person2value = round(valuenum)
            personname = person.name.replace("_", "\\_")
            coolembed.add_field(name=f"{icon} {personname}", inline=True, value=valuestr)

        field(person1accept, person1gives, person1, 1)
        field(person2accept, person2gives, person2, 2)

        return coolembed, view

    # this is wrapper around gen_embed() to edit the mesage automatically
    async def update_trade_embed(interaction):
        embed, view = await gen_embed()
        try:
            await interaction.edit_original_response(embed=embed, view=view)
        except Exception:
            await achemb(message, "blackhole", "followup")
            await achemb(message, "blackhole", "followup", person2)

    # lets go add cats modal thats fun
    class TradeModal(Modal):
        def __init__(self, currentuser):
            super().__init__(
                title="Add to the trade",
                timeout=VIEW_TIMEOUT,
            )
            self.currentuser = currentuser

            self.cattype = TextInput(
                label='Cat or Pack Type, Prism Name or "Rain"',
                placeholder="Fine / Wooden / Alpha / Rain",
            )
            self.add_item(self.cattype)

            self.amount = TextInput(label="Amount to offer", placeholder="1", required=False)
            self.add_item(self.amount)

        # this is ran when user submits
        async def on_submit(self, interaction: discord.Interaction):
            nonlocal person1, person2, person1accept, person2accept, person1gives, person2gives
            value = self.amount.value if self.amount.value else 1
            await user1.refresh_from_db()
            await user2.refresh_from_db()

            try:
                if int(value) < 0:
                    person1accept = False
                    person2accept = False
            except Exception:
                await interaction.response.send_message("invalid amount", ephemeral=True)
                return

            # handle prisms
            if (pname := " ".join(i.capitalize() for i in self.cattype.value.split())) in prism_names:
                try:
                    prism = await Prism.get_or_none(guild_id=interaction.guild.id, name=pname)
                    if not prism:
                        raise Exception
                except Exception:
                    await interaction.response.send_message("this prism doesnt exist", ephemeral=True)
                    return
                if prism.user_id != interaction.user.id:
                    await interaction.response.send_message("this is not your prism", ephemeral=True)
                    return
                if (self.currentuser == 1 and pname in person1gives.keys()) or (self.currentuser == 2 and pname in person2gives.keys()):
                    await interaction.response.send_message("you already added this prism", ephemeral=True)
                    return

                if self.currentuser == 1:
                    person1gives[pname] = 1
                else:
                    person2gives[pname] = 1
                await interaction.response.defer()
                await update_trade_embed(interaction)
                return

            # handle packs
            if self.cattype.value.capitalize() in [i["name"] for i in pack_data]:
                pname = self.cattype.value.capitalize()
                if self.currentuser == 1:
                    if user1.battlepass < 3 and not user1.bp_history.strip().replace("0,0,0;", ""):
                        await interaction.response.send_message("you need to reach atleast cattlepass level 3 to trade packs.", ephemeral=True)
                        return
                    if user1[f"pack_{pname.lower()}"] < int(value):
                        await interaction.response.send_message("you dont have enough packs", ephemeral=True)
                        return
                    new_val = person1gives.get(pname, 0) + int(value)
                    if new_val >= 0:
                        person1gives[pname] = new_val
                    else:
                        await interaction.response.send_message("skibidi toilet", ephemeral=True)
                        return
                else:
                    if user2.battlepass < 3 and not user2.bp_history.strip().replace("0,0,0;", ""):
                        await interaction.response.send_message("you need to reach atleast cattlepass level 3 to trade packs.", ephemeral=True)
                        return
                    if user2[f"pack_{pname.lower()}"] < int(value):
                        await interaction.response.send_message("you dont have enough packs", ephemeral=True)
                        return
                    new_val = person2gives.get(pname, 0) + int(value)
                    if new_val >= 0:
                        person2gives[pname] = new_val
                    else:
                        await interaction.response.send_message("skibidi toilet", ephemeral=True)
                        return
                await interaction.response.defer()
                await update_trade_embed(interaction)
                return

            # handle rains
            if "rain" in self.cattype.value.lower():
                user = await User.get_or_create(user_id=interaction.user.id)
                try:
                    if user.rain_minutes < int(value) or int(value) < 1:
                        await interaction.response.send_message("you dont have enough rains", ephemeral=True)
                        return
                except Exception:
                    await interaction.response.send_message("please enter a number for amount", ephemeral=True)
                    return

                if self.currentuser == 1:
                    try:
                        person1gives["rains"] += int(value)
                    except Exception:
                        person1gives["rains"] = int(value)
                else:
                    try:
                        person2gives["rains"] += int(value)
                    except Exception:
                        person2gives["rains"] = int(value)
                await interaction.response.defer()
                await update_trade_embed(interaction)
                return

            lc_input = self.cattype.value.lower()

            # loop through the cat types and find the correct one using lowercased user input.
            cname = cattype_lc_dict.get(lc_input, None)

            # if no cat type was found, the user input was invalid. as cname is still `None`
            if cname is None:
                await interaction.response.send_message("add a valid cat/pack/prism name 💀💀💀", ephemeral=True)
                return

            try:
                if self.currentuser == 1:
                    currset = person1gives[cname]
                else:
                    currset = person2gives[cname]
            except Exception:
                currset = 0

            try:
                if int(value) + currset < 0 or int(value) == 0:
                    raise Exception
            except Exception:
                await interaction.response.send_message("plz number?", ephemeral=True)
                return

            if (self.currentuser == 1 and user1[f"cat_{cname}"] < int(value) + currset) or (
                self.currentuser == 2 and user2[f"cat_{cname}"] < int(value) + currset
            ):
                await interaction.response.send_message(
                    "hell naww dude you dont even have that many cats 💀💀💀",
                    ephemeral=True,
                )
                return

            # OKE SEEMS GOOD LETS ADD CATS TO THE TRADE
            if self.currentuser == 1:
                try:
                    person1gives[cname] += int(value)
                    if person1gives[cname] == 0:
                        person1gives.pop(cname)
                except Exception:
                    person1gives[cname] = int(value)
            else:
                try:
                    person2gives[cname] += int(value)
                    if person2gives[cname] == 0:
                        person2gives.pop(cname)
                except Exception:
                    person2gives[cname] = int(value)

            await interaction.response.defer()
            await update_trade_embed(interaction)

    embed, view = await gen_embed()
    if not view:
        await message.response.send_message(embed=embed)
    else:
        await message.response.send_message(person2.mention, embed=embed, view=view, allowed_mentions=discord.AllowedMentions(users=True))

    if person1 == person2:
        await achemb(message, "introvert", "followup")


@bot.tree.command(description="Get Cat Image, does not add a cat to your inventory")
@discord.app_commands.rename(cat_type="type")
@discord.app_commands.describe(cat_type="select a cat type ok")
@discord.app_commands.autocomplete(cat_type=cat_command_autocomplete)
async def cat(message: discord.Interaction, cat_type: Optional[str]):
    if cat_type and cat_type not in cattypes:
        await message.response.send_message("bro what", ephemeral=True)
        return

    # check the user has the cat if required
    user = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    if cat_type and user[f"cat_{cat_type}"] <= 0:
        await message.response.send_message("you dont have that cat", ephemeral=True)
        return

    image = f"images/spawn/{cat_type.lower()}_cat.png" if cat_type else "images/cat.png"
    file = discord.File(image, filename=image)
    await message.response.send_message(file=file)


@bot.tree.command(description="Get Cursed Cat")
async def cursed(message: discord.Interaction):
    file = discord.File("images/cursed.jpg", filename="cursed.jpg")
    await message.response.send_message(file=file)


@bot.tree.command(description="Get Your balance")
async def bal(message: discord.Interaction):
    file = discord.File("images/money.png", filename="money.png")
    embed = discord.Embed(title="cat coins", color=Colors.brown).set_image(url="attachment://money.png")
    await message.response.send_message(file=file, embed=embed)


@bot.tree.command(description="Brew some coffee to catch cats more efficiently")
async def brew(message: discord.Interaction):
    user = await Profile.get_or_create(user_id=message.user.id, guild_id=message.guild.id)
    retry_counter = 2

    async def brew_coffee(interaction: discord.Interaction):
        nonlocal user, retry_counter, view
        if interaction.user != message.user:
            await do_funny(interaction)
            return

        await interaction.response.defer()

        if retry_counter != 0:
            retry_counter -= 1
            return

        user = await Profile.get(["coffees"], guild_id=message.guild.id, user_id=message.user.id)
        user.coffees += 1
        await user.save()

        view.children[0].label = f"{user.coffees:,}"
        await interaction.edit_original_response(content="ugh fine", view=view)

    view = View(timeout=VIEW_TIMEOUT)
    button = Button(emoji="☕", label="Retry", style=ButtonStyle.blurple)
    button.callback = brew_coffee
    view.add_item(button)
    await message.response.send_message("HTTP 418: I'm a teapot. <https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/418>", view=view)
    await achemb(message, "coffee", "followup")


def get_current_week():
    epoch_monday = datetime.datetime(1970, 1, 5, tzinfo=datetime.timezone.utc).date()
    today = discord.utils.utcnow().date()
    return (today - epoch_monday).days // 7


def get_timestamp_of_next_week():
    today = discord.utils.utcnow().date()
    days_until_next_monday = (7 - today.weekday()) % 7
    if days_until_next_monday == 0:
        days_until_next_monday = 7
    next_monday_date = today + datetime.timedelta(days=days_until_next_monday)
    next_monday_dt = datetime.datetime(next_monday_date.year, next_monday_date.month, next_monday_date.day, tzinfo=datetime.timezone.utc)
    return int(next_monday_dt.timestamp())


@bot.tree.command(description="Deliver orders from your bakery to get Cat Eggs and Packs!")
async def bakery(message: discord.Interaction):
    user = await User.get_or_create(user_id=message.user.id)
    profile = await Profile.get_or_create(user_id=message.user.id, guild_id=message.guild.id)
    if user.queued_chef_pack:
        profile.pack_chef += 1
        user.queued_chef_pack = False
        await user.save()
        await profile.save()
        try:
            await message.channel.send(f"{message.user.mention} got +1 {get_emoji('chefpack')} Chef Pack from Bake.gg!")
        except Exception:
            pass

    if user.last_bakegg_send == get_current_week():
        # order already delivered for this week
        await message.response.send_message(f"You already delivered this order. Next order is <t:{get_timestamp_of_next_week()}:R>.", ephemeral=True)
        return

    async def deliver(interaction: discord.Interaction):
        if interaction.user != message.user:
            await do_funny(interaction)
            return

        await interaction.response.defer()

        await profile.refresh_from_db()
        await user.refresh_from_db()
        if profile.cookies < BAKERY_COST_COOKIES or profile.coffees < BAKERY_COST_COFFEES or profile.cat_Nice < BAKERY_COST_NICE:
            await interaction.followup.send("Your order is not ready yet.", ephemeral=True)
            return
        if user.last_bakegg_send == get_current_week():
            await interaction.followup.send("You've already delivered this order.", ephemeral=True)
            return

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    "https://auth.bake.gg:2053/reward/catbot",
                    headers={"Authorization": os.environ.get("BAKE_GG_TOKEN", "")},  # i dont believe anyone would ever need to change this
                    json={"user": str(interaction.user.id)},
                ) as response:
                    if response.status != 200:
                        print(response.status, await response.text())
                        raise ValueError

                    profile.cookies -= BAKERY_COST_COOKIES
                    profile.coffees -= BAKERY_COST_COFFEES
                    profile.cat_Nice -= BAKERY_COST_NICE
                    profile.pack_silver += 1
                    await profile.save()

                    user.last_bakegg_send = get_current_week()
                    await user.save()

                    view = LayoutView(timeout=1)
                    view.add_item(
                        Container(
                            "## ✅ Order Delivered!",
                            f"+1 {get_emoji('silverpack')} Silver pack, +1 {get_emoji('bakegg_egg')} Bake.gg Cat Egg",
                            f"Next order <t:{get_timestamp_of_next_week()}:R>",
                            "===",
                            f"➡️ Opening any {get_emoji('bakegg_egg')} Cat Egg in Bake.gg will give you an **exclusive {get_emoji('chefpack')} Chef Pack** in Cat Bot, so head over to not miss out!",
                            "-# 1 Chef Pack per user per week",
                            "===",
                            Button(label="Bake.gg", url="https://bake.gg/"),
                        )
                    )
                    await interaction.edit_original_response(view=view)
                    await achemb(message, "baker", "followup")
            except Exception:
                await interaction.followup.send("Failed! Try again later.", ephemeral=True)
                raise

    view = LayoutView(timeout=VIEW_TIMEOUT)
    order_complete = profile.cookies >= BAKERY_COST_COOKIES and profile.coffees >= BAKERY_COST_COFFEES and profile.cat_Nice >= BAKERY_COST_NICE
    button = Button(label="Deliver!", style=ButtonStyle.green, disabled=not order_complete)
    button.callback = deliver
    embed = Container(
        "## 📝 Bakery Order",
        "In collaboration with [Bake.gg](https://bake.gg)",
        "__Order Details__",
        f"""{get_emoji("bakegg_cookie")} {min(profile.cookies, BAKERY_COST_COOKIES)}/{BAKERY_COST_COOKIES} {"✅" if profile.cookies >= BAKERY_COST_COOKIES else "(`/cookie`)"}
{get_emoji("bakegg_coffee")} {min(profile.coffees, BAKERY_COST_COFFEES)}/{BAKERY_COST_COFFEES} {"✅" if profile.coffees >= BAKERY_COST_COFFEES else "(`/brew`)"}
{get_emoji("nicecat")} {min(profile.cat_Nice, BAKERY_COST_NICE)}/{BAKERY_COST_NICE} {"✅" if profile.cat_Nice >= BAKERY_COST_NICE else ""}""",
        "===",
        "__Order Reward__",
        f"""{get_emoji("bakegg_egg")} 1 Bake.gg Cat Egg
{get_emoji("silverpack")} 1 Silver Pack""",
        "-# orders can only be done once a week per user",
        "===",
        button,
    )
    view.add_item(embed)
    await message.response.send_message(view=view)


@bot.tree.command(description="Gamble your life savings away in our totally-not-rigged catsino!")
async def casino(message: discord.Interaction):
    if message.user.id + message.guild.id in casino_lock:
        await message.response.send_message(
            "you get kicked out of the catsino because you are already there, and two of you playing at once would cause a glitch in the universe",
            ephemeral=True,
        )
        await achemb(message, "paradoxical_gambler", "followup")
        return

    profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    # funny global gamble counter cus funny
    total_sum = await Profile.sum("gambles", "gambles > 0")
    embed = discord.Embed(
        title="🎲 The Catsino",
        description=f"One spin costs 5 {get_emoji('finecat')} Fine cats\nSo far you gambled {profile.gambles} times.\nAll Cat Bot users gambled {total_sum:,} times.",
        color=Colors.maroon,
    )

    async def spin(interaction):
        nonlocal message
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        if message.user.id + message.guild.id in casino_lock:
            await interaction.response.send_message(
                "you get kicked out of the catsino because you are already there, and two of you playing at once would cause a glitch in the universe",
                ephemeral=True,
            )
            return

        await profile.refresh_from_db()
        if profile.cat_Fine < 5:
            await interaction.response.send_message("you are too broke now", ephemeral=True)
            await achemb(interaction, "broke", "followup")
            return

        await interaction.response.defer()
        amount = random.randint(1, 5)
        casino_lock.append(message.user.id + message.guild.id)
        profile.cat_Fine += amount - 5
        profile.gambles += 1
        await profile.save()

        if profile.gambles >= 10:
            await achemb(message, "gambling_one", "followup")
        if profile.gambles >= 50:
            await achemb(message, "gambling_two", "followup")

        variants = [
            f"{get_emoji('egirlcat')} 1 eGirl cats",
            f"{get_emoji('egirlcat')} 3 eGirl cats",
            f"{get_emoji('ultimatecat')} 2 Ultimate cats",
            f"{get_emoji('corruptcat')} 7 Corrupt cats",
            f"{get_emoji('divinecat')} 4 Divine cats",
            f"{get_emoji('epiccat')} 10 Epic cats",
            f"{get_emoji('professorcat')} 5 Professor cats",
            f"{get_emoji('realcat')} 2 Real cats",
            f"{get_emoji('legendarycat')} 5 Legendary cats",
            f"{get_emoji('mythiccat')} 2 Mythic cats",
            f"{get_emoji('8bitcat')} 7 8bit cats",
        ]

        random.shuffle(variants)
        icon = "🎲"

        for i in variants:
            embed = discord.Embed(title=f"{icon} The Catsino", description=f"**{i}**", color=Colors.maroon)
            try:
                await interaction.edit_original_response(embed=embed, view=None)
            except Exception:
                pass
            await asyncio.sleep(1)

        embed = discord.Embed(
            title=f"{icon} The Catsino",
            description=f"You won:\n**{get_emoji('finecat')} {amount} Fine cats**",
            color=Colors.maroon,
        )

        button = Button(label="Spin", style=ButtonStyle.blurple)
        button.callback = spin

        myview = View(timeout=VIEW_TIMEOUT)
        myview.add_item(button)

        casino_lock.remove(message.user.id + message.guild.id)

        try:
            await interaction.edit_original_response(embed=embed, view=myview)
        except Exception:
            await interaction.followup.send(embed=embed, view=myview)

    button = Button(label="Spin", style=ButtonStyle.blurple)
    button.callback = spin

    myview = View(timeout=VIEW_TIMEOUT)
    myview.add_item(button)

    await message.response.send_message(embed=embed, view=myview)


@bot.tree.command(description="oh no")
async def slots(message: discord.Interaction):
    if message.user.id + message.guild.id in slots_lock:
        await message.response.send_message(
            "you get kicked from the slot machine because you are already there, and two of you playing at once would cause a glitch in the universe",
            ephemeral=True,
        )
        await achemb(message, "paradoxical_gambler", "followup")
        return

    await message.response.defer()

    profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    total_spins, total_wins, total_big_wins = (
        await Profile.sum("slot_spins", "slot_spins > 0"),
        await Profile.sum("slot_wins", "slot_wins > 0"),
        await Profile.sum("slot_big_wins", "slot_big_wins > 0"),
    )
    embed = discord.Embed(
        title=":slot_machine: The Slot Machine",
        description=f"__Your stats__\n{profile.slot_spins:,} spins\n{profile.slot_wins:,} wins\n{profile.slot_big_wins:,} big wins\n\n__Global stats__\n{total_spins:,} spins\n{total_wins:,} wins\n{total_big_wins:,} big wins",
        color=Colors.maroon,
    )

    async def remove_debt(interaction):
        nonlocal message
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        await profile.refresh_from_db()

        # remove debt
        for i in cattypes:
            profile[f"cat_{i}"] = max(0, profile[f"cat_{i}"])

        await profile.save()
        await interaction.response.send_message("You have removed your debts! Life is wonderful!", ephemeral=True)
        await achemb(interaction, "debt", "followup")

    async def spin(interaction):
        nonlocal message
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        if message.user.id + message.guild.id in slots_lock:
            await interaction.response.send_message(
                "you get kicked from the slot machine because you are already there, and two of you playing at once would cause a glitch in the universe",
                ephemeral=True,
            )
            return
        await profile.refresh_from_db()

        await interaction.response.defer()
        slots_lock.append(message.user.id + message.guild.id)
        profile.slot_spins += 1
        await profile.save()

        try:
            await achemb(interaction, "slots", "followup")
            await progress(message, profile, "slots")
            await progress(message, profile, "slots2")
            await progress_casino_quest(message, profile, "slots")
        except Exception:
            pass

        variants = ["🍒", "🍋", "🍇", "🔔", "⭐", ":seven:"]
        reel_durations = [random.randint(9, 12), random.randint(15, 22), random.randint(25, 28)]
        random.shuffle(reel_durations)

        # the k number is much cycles it will go before stopping + 1
        col1 = random.choices(variants, k=reel_durations[0])
        col2 = random.choices(variants, k=reel_durations[1])
        col3 = random.choices(variants, k=reel_durations[2])

        if message.user.id in rigged_users:
            col1[len(col1) - 2] = ":seven:"
            col2[len(col2) - 2] = ":seven:"
            col3[len(col3) - 2] = ":seven:"

        blank_emoji = get_emoji("empty")
        for slot_loop_ind in range(1, max(reel_durations) - 1):
            current1 = min(len(col1) - 2, slot_loop_ind)
            current2 = min(len(col2) - 2, slot_loop_ind)
            current3 = min(len(col3) - 2, slot_loop_ind)
            desc = ""
            for offset in [-1, 0, 1]:
                if offset == 0:
                    desc += f"➡️ {col1[current1 + offset]} {col2[current2 + offset]} {col3[current3 + offset]} ⬅️\n"
                else:
                    desc += f"{blank_emoji} {col1[current1 + offset]} {col2[current2 + offset]} {col3[current3 + offset]} {blank_emoji}\n"
            embed = discord.Embed(
                title=":slot_machine: The Slot Machine",
                description=desc,
                color=Colors.maroon,
            )
            try:
                await interaction.edit_original_response(embed=embed, view=None)
            except Exception:
                pass
            await asyncio.sleep(0.125)

        await profile.refresh_from_db()
        big_win = False
        if col1[current1] == col2[current2] == col3[current3]:
            profile.slot_wins += 1
            if col1[current1] == ":seven:":
                desc = "**BIG WIN!**\n\n" + desc
                profile.slot_big_wins += 1
                big_win = True
                await profile.save()
                await achemb(interaction, "big_win_slots", "followup")
            else:
                desc = "**You win!**\n\n" + desc
                await profile.save()
            await achemb(interaction, "win_slots", "followup")
        else:
            desc = "**You lose!**\n\n" + desc

        button = Button(label="Spin", style=ButtonStyle.blurple)
        button.callback = spin

        myview = View(timeout=VIEW_TIMEOUT)
        myview.add_item(button)

        if big_win:
            # check if user has debt in any cat type
            has_debt = False
            for i in cattypes:
                if profile[f"cat_{i}"] < 0:
                    has_debt = True
                    break
            if has_debt:
                desc += "\n\n**You can remove your debt!**"
                button = Button(label="Remove Debt", style=ButtonStyle.blurple)
                button.callback = remove_debt
                myview.add_item(button)

        slots_lock.remove(message.user.id + message.guild.id)

        embed = discord.Embed(title=":slot_machine: The Slot Machine", description=desc, color=Colors.maroon)

        try:
            await interaction.edit_original_response(embed=embed, view=myview)
        except Exception:
            await interaction.followup.send(embed=embed, view=myview)

    button = Button(label="Spin", style=ButtonStyle.blurple)
    button.callback = spin

    myview = View(timeout=VIEW_TIMEOUT)
    myview.add_item(button)

    await message.followup.send(embed=embed, view=myview)


@bot.tree.command(description="what")
async def roulette(message: discord.Interaction):
    user = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)

    # this is the silly popup when you click the button
    class RouletteModel(Modal):
        def __init__(self):
            super().__init__(
                title="place a bet idfk",
                timeout=VIEW_TIMEOUT,
            )

            self.bettype = TextInput(
                min_length=1,
                max_length=5,
                label="choose a bet",
                style=discord.TextStyle.short,
                required=True,
                placeholder="red / black / green / 0 / 1 / 2 / 3 / ... / 36",
            )
            self.add_item(self.bettype)

            self.betamount = TextInput(
                min_length=1,
                label="bet amount (in coins)",
                style=discord.TextStyle.short,
                required=True,
                placeholder="69",
            )
            self.add_item(self.betamount)

        async def on_submit(self, interaction: discord.Interaction):
            await user.refresh_from_db()

            valids = ["red", "black", "green"] + [str(i) for i in range(37)]
            if self.bettype.value.lower() not in valids:
                await interaction.response.send_message("invalid bet", ephemeral=True)
                return

            try:
                bet_amount = int(self.betamount.value)
                if bet_amount <= 0:
                    await interaction.response.send_message("bet amount must be greater than 0", ephemeral=True)
                    return
                if bet_amount > max(user.coins, 100):
                    await interaction.response.send_message(f"your max bet is {max(user.coins, 100)}", ephemeral=True)
                    return
            except ValueError:
                await interaction.response.send_message("invalid bet amount", ephemeral=True)
                return

            await interaction.response.defer()

            # mapping of colors to numbers by indexes
            colors = [
                "green",
                "red",
                "black",
                "red",
                "black",
                "red",
                "black",
                "red",
                "black",
                "red",
                "black",
                "black",
                "red",
                "black",
                "red",
                "black",
                "red",
                "black",
                "red",
                "red",
                "black",
                "red",
                "black",
                "red",
                "black",
                "red",
                "black",
                "red",
                "black",
                "black",
                "red",
                "black",
                "red",
                "black",
                "red",
                "black",
                "red",
            ]

            emoji_map = {
                "red": "🔴",
                "black": "⚫",
                "green": "🟢",
            }

            final_choice = random.randint(0, 36)
            user.coins -= bet_amount
            user.roulette_spins += 1
            win = False
            funny_win = False
            if str(final_choice) == self.bettype.value or colors[final_choice] == self.bettype.value.lower():
                if self.bettype.value in [str(i) for i in range(37)] or self.bettype.value.lower() == "green":
                    user.coins += bet_amount * 36
                    funny_win = True
                else:
                    user.coins += bet_amount * 2
                user.roulette_wins += 1
                win = True
            user.coins = int(round(user.coins))
            await user.save()

            for wait_time in [0.025, 0.05, 0.075, 0.1, 0.125, 0.15, 0.175, 0.2, 0.225, 0.25, 0.275, 0.3, 0.375]:
                choice = random.randint(0, 36)
                color = colors[choice]
                embed = discord.Embed(
                    color=Colors.maroon,
                    title="woo its spinnin",
                    description=f"your bet is {int(self.betamount.value):,} coins on {self.bettype.value.capitalize()}\n\n{emoji_map[color]} **{choice}**",
                )
                await interaction.edit_original_response(embed=embed, view=None)
                await asyncio.sleep(wait_time)

            color = colors[final_choice]

            broke_suffix = ""
            if user.coins <= 0:
                broke_suffix = "\ndebt is allowed - you can still gamble up to **100** coins"

            embed = discord.Embed(
                color=Colors.maroon,
                title="winner!!!" if win else "womp womp",
                description=f"your bet was {int(self.betamount.value):,} coins on {self.bettype.value.capitalize()}\n\n{emoji_map[color]} **{final_choice}**\n\nyour new balance is **{user.coins:,}** coins{broke_suffix}",
            )
            view = View(timeout=VIEW_TIMEOUT)
            b = Button(label="spin", style=ButtonStyle.blurple)
            b.callback = modal_select
            view.add_item(b)
            await interaction.edit_original_response(embed=embed, view=view)

            if win:
                await progress(message, user, "roulette")
                await achemb(interaction, "roulette_winner", "followup")
            # casino quest counts every roulette spin (win or lose)
            await progress_casino_quest(message, user, "roulette")
            if funny_win:
                await achemb(interaction, "roulette_prodigy", "followup")
            if user.coins < 0:
                await achemb(interaction, "failed_gambler", "followup")

    async def modal_select(interaction: discord.Interaction):
        if interaction.user != message.user:
            await do_funny(interaction)
            return

        await interaction.response.send_modal(RouletteModel())

    broke_suffix = ""
    if user.coins <= 0:
        broke_suffix = "\n\ndebt is allowed - you can still gamble up to **100** coins"

    embed = discord.Embed(
        color=Colors.maroon,
        title="hecking roulette table",
        description=f"your balance is **{user.coins:,}** coins{broke_suffix}",
    )

    view = View(timeout=VIEW_TIMEOUT)
    b = Button(label="spin", style=ButtonStyle.blurple)
    b.callback = modal_select
    view.add_item(b)

    await message.response.send_message(embed=embed, view=view)

    if user.coins < 0:
        await achemb(message, "failed_gambler", "followup")


@bot.tree.command(description="roll a dice")
async def roll(message: discord.Interaction, sides: Optional[int]):
    if sides is None:
        sides = 6

    if sides < 0:
        await message.response.send_message("???", ephemeral=True)
        return

    user = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)

    if sides == 0:
        # ???
        family_guy_funny_moments = [
            "your sphere doesn't land",
            "your sphere floats in air",
            "your sphere lands and bounces forever",
            "your sphere breaks",
            "your sphere gets turned inside out",
            "your sphere lands in a dumpster",
            "your sphere gets eaten",
            "your sphere lands in an active volcano",
            "your house gets striked down from orbit before your sphere lands",
            "your sphere lands on the bottom of the Mariana trench",
            "your sphere lands inside of a frying pan and burns",
            "your sphere breaks into 0 pieces and the universe throws a runtime error",
            "your sphere is getting married",
            "your sphere turns into a pentagonal bipyramid because it's bored",
            "your sphere defies gravity and floats into the space never to be seen again",
            "your sphere lands in honey and gets sticky",
            "your sphere gets compressed into a blackhole",
            "your sphere became sentient and refused to land",
            "your sphere lands a pretty good job",
            "your sphere lands on a 7 somehow",
            "you try to pick up your sphere but its just a hallucination",
            'your sphere lands on "WAKE UP"',
            "your sphere is in a superposition of having landed on 0 and not landed",
            "your sphere lands on pi (get it?)",
            "your sphere fell into sulfuric acid and dissolved",
            "your sphere used slightly a wrong pi and therefore is just barely not a sphere",
            "your sphere is too fast to be seen",
            "your sphere's landing is delayed because of poor visibility at the airport",
            "your sphere turns into a tesseract",
            "your sphere opens a macdonalds franchise",
            "your sphere lands in crippling debt",
            "your sphere lands in court",
            "your sphere lands in prison",
            "your sphere has been sentenced to lifetime slavery",
            "your sphere is a sphere trying its best to become a cube with no avail because of the discrimination of society",
            "your mom is a sphere",
            "everything in the world is sphere its a matter of perspective",
            "did you notice most emojis are spheres?",
            "why are you still here",
            "your sphere ran out of jokes",
            "your sphere finally peacefully lands on the table. you shed a (spherical) tear of happiness.",
        ]

        if user.sphere_easter_egg < len(family_guy_funny_moments):
            await message.response.send_message(family_guy_funny_moments[user.sphere_easter_egg], ephemeral=True)
            user.sphere_easter_egg += 1
            await user.save()

            if user.sphere_easter_egg == len(family_guy_funny_moments):
                await achemb(message, "sphere_ach", "followup")
        else:
            await message.response.send_message(random.choice(family_guy_funny_moments), ephemeral=True)

        return

    # loosely based on this wikipedia article
    # https://en.wikipedia.org/wiki/Dice
    dice_names = {
        1: '"dice"',
        2: "coin",
        4: "tetrahedron",
        5: "triangular prism",
        6: "cube",
        7: "pentagonal prism",
        8: "octahedron",
        9: "hexagonal prism",
        10: "pentagonal trapezohedron",
        12: "dodecahedron",
        14: "heptagonal trapezohedron",
        16: "octagonal bipyramid",
        18: "rounded rhombicuboctahedron",
        20: "icosahedron",
        24: "triakis octahedron",
        30: "rhombic triacontahedron",
        34: "heptadecagonal trapezohedron",
        48: "disdyakis dodecahedron",
        50: "icosipentagonal trapezohedron",
        60: "deltoidal hexecontahedron",
        100: "zocchihedron",
        120: "disdyakis triacontahedron",
    }

    if sides in dice_names.keys():
        dice = dice_names[sides]
    else:
        dice = f"d{sides}"

    if sides == 2:
        coinflipresult = random.randint(1, 2)
        if coinflipresult == 2:
            side = "tails"
        else:
            side = "heads"
        await message.response.send_message(f"🪙 your coin lands on **{side}** ({coinflipresult})")
    else:
        await message.response.send_message(f"🎲 your {dice} lands on **{random.randint(1, sides)}**")
    await progress(message, user, "roll")


@bot.tree.command(description="get a super accurate rating of something")
@discord.app_commands.describe(thing="The thing or person to check", stat="The stat to check")
async def rate(message: discord.Interaction, thing: str, stat: str):
    if len(thing) > 100 or len(stat) > 100:
        await message.response.send_message("thats kinda long", ephemeral=True)
        return
    if thing.lower() == "/rate" and stat.lower() == "correct":
        await message.response.send_message("/rate is 100% correct")
    else:
        await message.response.send_message(f"{thing} is {random.randint(0, 100)}% {stat}")
    user = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    await progress(message, user, "rate")


@bot.tree.command(name="8ball", description="ask the magic catball")
@discord.app_commands.describe(question="your question to the catball")
async def eightball(message: discord.Interaction, question: str):
    if len(question) > 300:
        await message.response.send_message("thats kinda long", ephemeral=True)
        return

    catball_responses = [
        # positive
        "it is certain",
        "it is decidedly so",
        "without a doubt",
        "yes definitely",
        "you may rely on it",
        "as i see it, yes",
        "most likely",
        "outlook good",
        "yes",
        "signs point to yes",
        # negative
        "dont count on it",
        "my reply is no",
        "my sources say no",
        "outlook not so good",
        "very doubtful",
        "most likely not",
        "unlikely",
        "no definitely",
        "no",
        "signs point to no",
        # neutral
        "reply hazy, try again",
        "ask again later",
        "better not tell you now",
        "cannot predict now",
        "concetrate and ask again",
    ]

    await message.response.send_message(f"{question}\n:8ball: **{random.choice(catball_responses)}**")
    user = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    await progress(message, user, "catball")
    await achemb(message, "balling", "followup")


@bot.tree.command(description="the most engaging boring game")
async def pig(message: discord.Interaction):
    score = 0

    profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)

    async def roll(interaction: discord.Interaction):
        nonlocal score
        if interaction.user != message.user:
            await do_funny(interaction)
            return

        await interaction.response.defer()

        if score == 0:
            # dont roll 1 on first roll
            roll_result = random.randint(2, 6)
        else:
            roll_result = random.randint(1, 6)

        if roll_result == 1:
            # gg
            last_score = score
            score = 0
            view = View(timeout=VIEW_TIMEOUT)
            button = Button(label="Play Again", emoji="🎲", style=ButtonStyle.blurple)
            button.callback = roll
            view.add_item(button)
            await interaction.edit_original_response(
                content=f"*Oops!* You rolled a **1** and lost your {last_score} score...\nFinal score: 0\nBetter luck next time!", view=view
            )
        else:
            score += roll_result
            view = View(timeout=VIEW_TIMEOUT)
            button = Button(label="Roll", emoji="🎲", style=ButtonStyle.blurple)
            button.callback = roll
            button2 = Button(label="Save & Finish")
            button2.callback = finish
            view.add_item(button)
            view.add_item(button2)
            await interaction.edit_original_response(content=f"🎲 +{roll_result}\nCurrent score: {score:,}", view=view)

    async def finish(interaction: discord.Interaction):
        nonlocal score
        if interaction.user != message.user:
            await do_funny(interaction)
            return

        await interaction.response.defer()

        await profile.refresh_from_db()

        if score > profile.best_pig_score:
            profile.best_pig_score = score
            await profile.save()

        if score >= 20:
            await progress(message, profile, "pig")
        # casino quest: any /pig round counts, even sub-20 scores
        await progress_casino_quest(message, profile, "pig")
        if score >= 50:
            await achemb(interaction, "pig50", "followup")
        if score >= 100:
            await achemb(interaction, "pig100", "followup")

        # Data-driven pig-play triggers (UI-added aches with stat_threshold on score).
        try:
            await ach_engine.evaluate(
                "pig_play",
                profile,
                {"score": score},
                message=interaction,
                achemb=achemb,
                send_type="followup",
            )
        except Exception:
            logging.exception("ach_engine pig_play event failed")

        last_score = score
        score = 0
        view = View(timeout=VIEW_TIMEOUT)
        button = Button(label="Play Again", emoji="🎲", style=ButtonStyle.blurple)
        button.callback = roll
        view.add_item(button)
        await interaction.edit_original_response(content=f"*Congrats!*\nYou finished with {last_score} score!", view=view)

    view = View(timeout=VIEW_TIMEOUT)
    button = Button(label="Play!", emoji="🎲", style=ButtonStyle.blurple)
    button.callback = roll
    view.add_item(button)
    await message.response.send_message(
        f"🎲 Pig is a simple dice game. You repeatedly roll a die. The number it lands on gets added to your score, then you can either roll the die again, or finish and save your current score. However, if you roll a 1, you lose and your score gets voided.\n\nYour current best score is **{profile.best_pig_score:,}**.",
        view=view,
    )


@bot.tree.command(description="get a reminder in the future (+- 5 minutes)")
@discord.app_commands.describe(
    days="in how many days",
    hours="in how many hours",
    minutes="in how many minutes (+- 5 minutes)",
    text="what to remind",
)
async def remind(
    message: discord.Interaction,
    days: Optional[int],
    hours: Optional[int],
    minutes: Optional[int],
    text: Optional[str],
):
    if not days:
        days = 0
    if not hours:
        hours = 0
    if not minutes:
        minutes = 0
    if not text:
        text = "Reminder!"

    goal_time = int(time.time() + (days * 86400) + (hours * 3600) + (minutes * 60))
    if goal_time > time.time() + (86400 * 365 * 20):
        await message.response.send_message("cats do not live for that long", ephemeral=True)
        return
    if len(text) > 1900:
        await message.response.send_message("thats too long", ephemeral=True)
        return
    if goal_time < 0:
        await message.response.send_message("cat cant time travel (yet)", ephemeral=True)
        return
    await message.response.send_message(f"🔔 ok, <t:{goal_time}:R> (+- 5 min) ill remind you of:\n{text}")
    msg = await message.original_response()
    message_link = msg.jump_url
    text += f"\n\n*This is a [reminder](<{message_link}>) you set.*"
    await Reminder.create(user_id=message.user.id, text=text, time=goal_time)
    profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    profile.reminders_set += 1
    await profile.save()
    await achemb(message, "reminder", "followup")  # the ai autocomplete thing suggested this and its actually a cool ach
    await progress(message, profile, "reminder")  # the ai autocomplete thing also suggested this though profile wasnt defined


@bot.tree.command(name="random", description="Get a random cat")
async def random_cat(message: discord.Interaction):
    await message.response.defer()
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                "https://api.thecatapi.com/v1/images/search", headers={"User-Agent": "CatBot/1.0 https://github.com/milenakos/cat-bot"}
            ) as response:
                data = await response.json()
                await message.followup.send(data[0]["url"])
                await achemb(message, "randomizer", "followup")
        except Exception:
            await message.followup.send("no cats :(")


if config.WORDNIK_API_KEY:

    @bot.tree.command(description="define a word")
    async def define(message: discord.Interaction, word: str):
        word = word.lower()
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    f"https://api.wordnik.com/v4/word.json/{word}/definitions?api_key={config.WORDNIK_API_KEY}&useCanonical=true&includeTags=false&includeRelated=false&limit=69",
                    headers={"User-Agent": "CatBot/1.0 https://github.com/milenakos/cat-bot"},
                ) as response:
                    data = await response.json()

                    # lazily filter some things
                    text = (await response.text()).lower()

                    # sometimes the api returns results without definitions, so we search for the first one which has a definition
                    for i in data:
                        if "text" in i.keys():
                            clean_data = re.sub(re.compile("<.*?>"), "", i["text"])
                            await message.response.send_message(
                                f"__{word}__\n{clean_data}\n-# [{i['attributionText']}](<{i['attributionUrl']}>) Powered by [Wordnik](<{i['wordnikUrl']}>)",
                                ephemeral=any([test in text for test in ["vulgar", "slur", "offensive", "profane", "insult", "abusive", "derogatory"]]),
                            )
                            await achemb(message, "define", "followup")
                            profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
                            await progress(message, profile, "define")
                            return

                    raise Exception
            except Exception:
                await message.response.send_message("no definition found", ephemeral=True)


@bot.tree.command(name="fact", description="get a random cat fact")
async def cat_fact(message: discord.Interaction):
    facts = [
        "you love cats",
        f"cat bot is in {len(bot.guilds):,} servers",
        "cat",
        "cats are the best",
    ]

    # give a fact from the list or the file
    if random.randint(0, 10) == 0:
        await message.response.send_message(random.choice(facts))
    else:
        await message.response.send_message(random.choice(cat_facts_list))

    user = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    user.facts += 1
    await user.save()
    if user.facts >= 10:
        await achemb(message, "fact_enjoyer", "followup")

    try:
        channel = await Channel.get_or_none(channel_id=message.channel.id)
        if channel and channel.cattype == "Professor":
            await achemb(message, "nerd_battle", "followup")
    except Exception:
        pass


async def bounty(message, user, cattype):
    if user.hibernation:
        return
    complete = 0
    completed = 0
    title = []
    colored = 0
    for i in range(user.bounties):
        if i == 0:
            id = user.bounty_id_one
            progress = user.bounty_progress_one
            total = user.bounty_total_one
            type = user.bounty_type_one
        if i == 1:
            id = user.bounty_id_two
            progress = user.bounty_progress_two
            total = user.bounty_total_two
            type = user.bounty_type_two
        if i == 2:
            id = user.bounty_id_three
            progress = user.bounty_progress_three
            total = user.bounty_total_three
            type = user.bounty_type_three
        if progress < total:
            if id == 0:
                progress += 1
                if progress == total:
                    complete += 1
                    title.append(f"Catch {total} cats")
            if id == 1:
                if cattype == type:
                    progress += 1
                    if progress == total:
                        complete += 1
                        title.append(f"Catch {total} {type} cats")
            if id == 2:
                if cattypes.index(cattype) >= cattypes.index(type):
                    progress += 1
                    if progress == total:
                        complete += 1
                        title.append(f"Catch {total} {type} or rarer cats")
        if i == 0:
            user.bounty_progress_one = progress
            if progress == total:
                completed += 1
        if i == 1:
            user.bounty_progress_two = progress
            if progress == total:
                completed += 1
        if i == 2:
            user.bounty_progress_three = progress
            if progress == total:
                completed += 1
    await user.save()
    if catnip_list["levels"][user.catnip_level]["bonus"]:
        bonus_title = ""
        if user.bounty_progress_bonus < user.bounty_total_bonus:
            if user.bounty_id_bonus == 0:
                user.bounty_progress_bonus += 1
                bonus_title = f"Catch {user.bounty_total_bonus} cats"
            elif user.bounty_id_bonus == 1:
                if cattype == user.bounty_type_bonus:
                    user.bounty_progress_bonus += 1
                bonus_title = f"Catch {user.bounty_total_bonus} {cattype} cats"
            else:
                if cattypes.index(cattype) >= cattypes.index(user.bounty_type_bonus):
                    user.bounty_progress_bonus += 1
                bonus_title = f"Catch {user.bounty_total_bonus} {user.bounty_type_bonus} or rarer cats"
            if user.bounty_progress_bonus == user.bounty_total_bonus:
                description = "Bonus Bounty Complete!\nGo to `/catnip` to reroll a perk!"
                embed = discord.Embed(title=f"✅ {bonus_title}", color=Colors.green, description=description).set_author(
                    name="Mafia Level " + str(user.catnip_level)
                )
                await message.channel.send(f"<@{user.user_id}>", embed=embed)
                user.reroll = False
                user.reroll_level = 0
            await user.save()
    for i in range(complete):
        logging.debug("Completed bounties %d", completed)
        level = user.catnip_level
        colored = int(completed / user.bounties * 10)
        progress_line = f"\n{level} " + get_emoji("staring_square") * int(colored) + "⬛" * int(10 - colored) + f" {level + 1}"
        if completed == user.bounties:
            description = f"{progress_line}\nAll Bounties Complete!\nGo to `/catnip` to pay up and pick a perk!"
        else:
            description = f"{progress_line}\n{completed}/{user.bounties} Bounties Complete"
        embed = discord.Embed(title=f"✅ {title[i]}", color=Colors.green, description=description).set_author(name="Mafia Level " + str(level))
        user.bounties_complete += 1
        if user.bounties_complete >= 5:
            await achemb(message, "bounty_novice", "followup")
        if user.bounties_complete >= 19:  # we do a little trolling (???)
            await achemb(message, "bounty_hunter", "followup")
        if user.bounties_complete >= 100:
            await achemb(message, "bounty_lord", "followup")
        await message.channel.send(f"<@{user.user_id}>", embed=embed)
        await user.save()


async def set_mafia_offer(level, user):
    if user.catnip_level == 0:
        user.catnip_amount = 0
        return
    level_data = catnip_list["levels"][level]
    vt = level_data["cost"]
    cattype = "Fine"
    for _ in range(100):
        cattype = random.choice(cattypes)
        value = sum(type_dict.values()) / type_dict[cattype]
        if value <= vt:
            break
    amount = max(1, round(vt / value))
    user.catnip_price = cattype
    user.catnip_amount = amount
    await user.save()


async def set_bounties(level, user):
    if user.catnip_level == 0:
        user.bounties = 0
        return
    bounties = await get_bounties(level)
    bonus_check = catnip_list["levels"][level + 1]["bonus"]
    if level == 10 and user.bounty_progress_bonus != user.bounty_total_bonus and user.catnip_active > 86400:
        bonus_check = False
    if bonus_check:
        bonus = bounties.pop()
        user.bounty_id_bonus = bonus["id"]
        user.bounty_type_bonus = bonus["cat_type"]
        user.bounty_total_bonus = bonus["amount"]
        user.bounty_progress_bonus = bonus["progress"]
    else:
        bounties = bounties[:-1]
    user.bounties = len(bounties)

    user.bounty_id_one = bounties[0]["id"] if bounties else None
    user.bounty_id_two = bounties[1]["id"] if len(bounties) > 1 else None
    user.bounty_id_three = bounties[2]["id"] if len(bounties) > 2 else None

    user.bounty_type_one = bounties[0]["cat_type"] if bounties else None
    user.bounty_type_two = bounties[1]["cat_type"] if len(bounties) > 1 else None
    user.bounty_type_three = bounties[2]["cat_type"] if len(bounties) > 2 else None

    user.bounty_total_one = bounties[0]["amount"] if bounties else 1
    user.bounty_total_two = bounties[1]["amount"] if len(bounties) > 1 else 1
    user.bounty_total_three = bounties[2]["amount"] if len(bounties) > 2 else 1

    user.bounty_progress_one = bounties[0]["progress"] if bounties else 0
    user.bounty_progress_two = bounties[1]["progress"] if len(bounties) > 1 else 0
    user.bounty_progress_three = bounties[2]["progress"] if len(bounties) > 2 else 0

    await user.save()


async def get_bounties(level):
    level_data = catnip_list["levels"][level + 1]
    bounties = []
    num_bounties = level_data["bounty_amount"]
    avg_cats_needed = level_data["bounty_difficulty"]
    num_max = level_data["max_amount"]

    used_types = set()
    used_rarities = set()
    tries = 0
    max_tries = 1000 * num_bounties
    while len(bounties) < num_bounties + 1 and tries < max_tries:
        tries += 1
        bounty_type = random.choice(["rarity", "specific", "any"])

        # to add a bit of randomness
        variation = random.uniform(0.85, 1.15)
        if len(bounties) == num_bounties:
            variation *= 1.5
            if level == 10:
                variation *= 10
        if bounty_type == "rarity":
            margin = 0.2
            rarity_i = random.randint(2, len(cattypes) - 2)

            while True:
                rarity = cattypes[rarity_i]
                eligible_types = cattypes[rarity_i:]

                prob = sum(type_dict[t] for t in eligible_types) / sum(type_dict.values())
                base_amount = max(1, round(avg_cats_needed * prob))
                expected_total = base_amount / prob if prob > 0 else float("inf")

                if abs(expected_total - avg_cats_needed) / avg_cats_needed <= margin or rarity_i == 0:
                    break
                rarity_i -= 1

            if rarity_i in used_rarities:
                continue

            used_rarities.add(rarity_i)
            amount = max(1, round(base_amount * variation))

            if amount > num_max:
                continue

            bounties.append({"id": 2, "progress": 0, "cat_type": rarity, "amount": amount, "desc": f"Catch {amount} cats of {rarity} rarity and above"})
        elif bounty_type == "any":
            if any(b["id"] == 0 for b in bounties):
                continue

            amount = max(1, round(avg_cats_needed * variation / 2))

            if amount > num_max:
                continue

            bounties.append({"id": 0, "progress": 0, "cat_type": "", "amount": amount, "desc": f"Catch {amount} cats of any kind"})
        else:
            # pick a specific cat type not already used
            available_types = [cat for cat in cattypes if cat not in used_types]
            if not available_types:
                continue

            available_types1 = available_types.copy()
            for i in available_types:
                cat_type = random.choices(available_types1)[0]
                prob = type_dict[cat_type] / sum(type_dict.values())
                base_amount = avg_cats_needed * prob
                available_types1.remove(cat_type)
                if base_amount > 0.8:
                    break

            amount = max(1, round(base_amount * variation))

            if amount > num_max:
                continue

            used_types.add(cat_type)
            bounties.append(
                {
                    "id": 1,
                    "progress": 0,
                    "cat_type": cat_type,
                    "amount": amount,
                    "desc": f"Catch {amount} {get_emoji(cat_type.lower() + 'cat')} cat{'s' if amount > 1 else ''}",
                }
            )

    return bounties


async def get_perks(level, user):
    level_data = catnip_list["levels"][level]
    rarities = [r for r in level_data["weights"].keys()]
    weights = {rarity: level_data["weights"][rarity] for rarity in rarities}
    perks = catnip_list["perks"]

    current_perks = []
    used_ids = set()
    thelist = []
    if user.perks:
        for perk in user.perks:
            p = perk.split("_")
            thelist.append(perks[int(p[1]) - 1]["id"])

    for _ in range(3):
        luck = random.randint(1, 1000) / 10
        total_weight = 0
        current_rarity = "common"
        for rarity, weight in weights.items():
            total_weight += weight
            if luck <= total_weight:
                current_rarity = rarity
                break

        tries = 0
        selected_perk = None

        while tries < 100:
            luck = random.randint(1, 100)
            total_weight = 0
            i = 0
            for perk in perks:
                i += 1
                total_weight += perk["weight"]

                if perk["id"] in used_ids or (perk["exclusive"] == 1 and perk["id"] in thelist):  # me when im in thelist
                    continue

                if all("pack" in p["id"] for p in current_perks) and "pack" in perk["id"]:
                    continue

                if luck <= total_weight:
                    effect = perk["values"][list(weights.keys()).index(current_rarity)]
                    if effect == 0:
                        continue

                    selected_perk = {
                        "id": perk["id"],
                        "name": perk["name"],
                        "values": perk["values"],
                        "rarity": current_rarity,
                        "uuid": f"{list(weights.keys()).index(current_rarity)}_{i}",
                        "effect": effect,
                    }

                    break
            if selected_perk:
                break
            tries += 1

        if selected_perk:
            used_ids.add(selected_perk["id"])
            current_perks.append(selected_perk)

    return current_perks


async def level_down(user, message, ephemeral=False):
    if user.catnip_level == 0:
        return

    user.catnip_level -= 1
    user.catnip_active = 0

    user.hibernation = True

    for number in ["one", "two", "three"]:
        user[f"bounty_id_{number}"] = 0
        user[f"bounty_type_{number}"] = ""
        user[f"bounty_total_{number}"] = 1
        user[f"bounty_progress_{number}"] = 0

    user.catnip_total_cats = 0

    user.bounty_active = False
    user.first_quote_seen = False

    if user.perks:
        h = list(user.perks)
        removed_perk = h.pop()
        user.perks = h[:]

    await set_bounties(user.catnip_level, user)
    await set_mafia_offer(user.catnip_level, user)
    await user.save()

    name = catnip_list["quotes"][user.catnip_level]["name"]
    quote = catnip_list["quotes"][user.catnip_level]["quotes"]["leveldown"].replace("jeremysus", get_emoji("jeremysus"))
    removed_line = ""

    if user.perks and removed_perk:
        rarities = ["Common", "Uncommon", "Rare", "Epic", "Legendary"]
        perk_rarity = int(removed_perk.split("_")[0])
        perk_type = int(removed_perk.split("_")[1])
        perk_data = catnip_list["perks"][perk_type - 1]

        removed_line = f"\nYou lost your **{perk_data['name']} ({rarities[perk_rarity]})** perk."

    embed = discord.Embed(
        title="❌ Mafia Level Failed",
        color=Colors.red,
        description=f"**{name}**: *{quote}*\n\nLevel {user.catnip_level + 1} bounties failed!\nYou're now on level {user.catnip_level}.{removed_line}",
    )

    logging.debug("Levelled down to %d", user.catnip_level)

    if ephemeral:
        return embed

    await message.channel.send(f"<@{user.user_id}>", embed=embed)


async def mafia_cutscene(interaction: discord.Interaction, user):
    # YAPPATRON
    text1 = """You feel satisfied with yourself. I just defeated the Godfather, Bailey! I'm on top of the world now!
Little did you know, it was foolish to believe it was over just yet.
You stare Bailey down, and realize just how bizarre he is. He's very large for a cat… he wags his tail… he just feels wrong. But then, you hear it.
*Bark! Bark!*
Oh no."""
    text2 = """You immediately run. You know that he will probably be able to outpace you, but you do have a bit of a head start.
There's a split in the alley.
Left would lead to the hideout, but you'll never get there in time.
Right, however, leads to a dead end.
Which way do you go?"""
    text3a = """You dash to the left. You can see the cat door ahead, but you'll never make it out in time.
You call out for help, and think back to all of those people you defeated.
Whiskers, the Lucians, Jinx, Jeremy, Sofia.
Would any of them be willing to save you?"""
    text3b = """You dash to the right. As you turn the corner and approach the dead end, you realize that while he may go faster, you can jump higher.
You back up against the wall, wait for him to approach… and jump.
You get over him, and run the other way. With a head start, you can get into the hideout.
But Bailey isn't done yet.
He's trying to break in. You think back to all of those people you defeated.
Whiskers, the Lucians, Jinx, Jeremy, Sofia.
Would any of them be willing to save you?"""
    text4 = """You see Jinx come out first. Whiskers is just behind him.
Jeremy doesn't take much longer. The Lucians come out too, though reluctantly.
Finally, Sofia scowls and approaches.
Bailey knew he could take down one cat. Two wouldn't be that hard. But seven..?
\"This isn't the end of this...\"
Bailey puts his head down, and scampers off. But you aren't done.
You and your crew chase after him. He runs, until you corner him. He goes into the building behind him… but it's the Cat Police Station.
As you return to your hideout, you hear a howl in the distance."""

    async def button3_callback(interaction: discord.Interaction):
        await interaction.response.defer()
        await interaction.edit_original_response(content=text4, view=None)
        user.thanksforplaying = False
        user.cutscene = 1
        await user.save()
        await achemb(interaction, "thanksforplaying", "followup")

    async def button2a_callback(interaction: discord.Interaction):
        myview3 = View(timeout=VIEW_TIMEOUT)
        button3 = Button(label="Next", style=ButtonStyle.blurple)
        button3.callback = button3_callback
        myview3.add_item(button3)
        await interaction.response.defer()
        await interaction.edit_original_response(content=text3a, view=myview3)

    async def button2b_callback(interaction: discord.Interaction):
        myview3 = View(timeout=VIEW_TIMEOUT)
        button3 = Button(label="Next", style=ButtonStyle.blurple)
        button3.callback = button3_callback
        myview3.add_item(button3)
        await interaction.response.defer()
        await interaction.edit_original_response(content=text3b, view=myview3)

    async def button1_callback(interaction: discord.Interaction):
        myview2 = View(timeout=VIEW_TIMEOUT)
        button2a = Button(label="Left", style=ButtonStyle.red)
        button2b = Button(label="Right", style=ButtonStyle.green)
        button2a.callback = button2a_callback
        button2b.callback = button2b_callback
        myview2.add_item(button2a)
        myview2.add_item(button2b)
        await interaction.response.defer()
        await interaction.edit_original_response(content=text2, view=myview2)

    user.thanksforplaying = True
    await user.save()

    myview1 = View(timeout=VIEW_TIMEOUT)
    button1 = Button(label="RUN!", style=ButtonStyle.blurple)
    button1.callback = button1_callback
    myview1.add_item(button1)
    await interaction.followup.send(content=text1, view=myview1, ephemeral=True)


async def mafia_cutscene2(interaction: discord.Interaction, user):
    text1 = """Why? What do you gain from this? What's the point?
You've gone too far. You defeated Bailey, and I was proud of you for that.
But you kept going. Just for slightly more cats.
You never cared about the people. It was all for you."""
    text2 = """I got too greedy myself. I took over the mafia far too young.
I wanted more, and more, and more. But I never went as far as you did.
I took over catnip production, and took so much for myself.
Eventually, though, someone took away my catnip.
And I realized how I had taken so much catnip, that the whole world was limited to about 4 doses a week."""
    text3 = """But you. You've left nothing for the others. You've made the most powerful catnip, but at what cost?
I can't stop you. No one can. I guess the only question is: will you stay here to torment us? Or fight on, against the world itself?
[More content coming soon! Congrats on actually making it to level 10, that's quite a feat.]"""
    text4a = """...Really? I thought you would continue your path of destruction.
So fine. Continue to torment us. You've won. Are you happy now?"""
    text4b = """woa you looked at the code! crazy. btw stella is cute"""

    async def button3a_callback(interaction: discord.Interaction):
        await interaction.response.defer()
        await interaction.edit_original_response(content=text4a, view=None)
        user.mafia_win = False
        user.cutscene = 2
        await user.save()
        await achemb(interaction, "mafia_win", "followup")

    async def button3b_callback(interaction: discord.Interaction):
        await interaction.response.defer()
        await interaction.edit_original_response(content=text4b, view=None)

    async def button2_callback(interaction: discord.Interaction):
        myview3 = View(timeout=VIEW_TIMEOUT)
        button3a = Button(label="Stay", style=ButtonStyle.green)
        button3b = Button(label="Continue", style=ButtonStyle.red, disabled=True)
        button3a.callback = button3a_callback
        button3b.callback = button3b_callback
        myview3.add_item(button3a)
        myview3.add_item(button3b)
        await interaction.response.defer()
        await interaction.edit_original_response(content=text3, view=myview3)

    async def button1_callback(interaction: discord.Interaction):
        myview2 = View(timeout=VIEW_TIMEOUT)
        button2 = Button(label="Next", style=ButtonStyle.blurple)
        button2.callback = button2_callback
        myview2.add_item(button2)
        await interaction.response.defer()
        await interaction.edit_original_response(content=text2, view=myview2)

    user.mafia_win = True
    await user.save()

    myview1 = View(timeout=VIEW_TIMEOUT)
    button1 = Button(label="'uhhhh'", style=ButtonStyle.blurple)
    button1.callback = button1_callback
    myview1.add_item(button1)
    await interaction.followup.send(content=text1, view=myview1, ephemeral=True)


@bot.tree.command(description="..?")
async def catnip(message: discord.Interaction):
    await message.response.defer(ephemeral=True)
    user = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    server = await Server.get_or_create(server_id=message.guild.id)

    if not server.do_catnip:
        await message.followup.send("catnip is disabled in this server.", ephemeral=True)
        return

    if not user.dark_market_active:
        await message.followup.send("You don't have access to the catnip yet. Catch more cats to unlock it!")
        return

    if user.catnip_active < time.time() and not user.hibernation and user.catnip_level > 0:
        embed = await level_down(user, message, True)
        await message.followup.send(f"<@{user.user_id}>", embed=embed, ephemeral=True)

    if user.catnip_amount == 0:
        await set_mafia_offer(user.catnip_level, user)

    if user.bounties == 0:
        await set_bounties(user.catnip_level, user)

    await achemb(message, "dark_market", "followup")

    if user.cutscene >= 1:
        await achemb(message, "thanksforplaying", "followup")
    if user.cutscene == 2:
        await achemb(message, "mafia_win", "followup")

    if len(user.perks) + 1 < user.catnip_level:
        user.perk_selected = False
        await user.save()

    if len(user.perks) + 1 > user.catnip_level:
        user.perks = user.perks[:-1]
        await user.save()

    level = user.catnip_level
    cat_type = user.catnip_price
    amount = user.catnip_amount

    async def pay_catnip(interaction):
        nonlocal user, cat_type, amount
        await user.refresh_from_db()
        if not interaction.response.is_done():
            await interaction.response.defer()
        if level != user.catnip_level:
            await interaction.followup.send("nice try", ephemeral=True)
            return
        for i in range(user.bounties):
            if (
                (i == 0 and user.bounty_progress_one < user.bounty_total_one)
                or (i == 1 and user.bounty_progress_two < user.bounty_total_two)
                or (i == 2 and user.bounty_progress_three < user.bounty_total_three)
            ):
                await interaction.followup.send("You haven't completed your bounties yet!", ephemeral=True)
                return
        if user.catnip_price:
            if user[f"cat_{user.catnip_price}"] < user.catnip_amount:
                need_more = user.catnip_amount - user[f"cat_{user.catnip_price}"]
                await interaction.followup.send(f"You don't have enough cats to pay up!\nYou need {need_more} more {user.catnip_price} cats.", ephemeral=True)
                return
            user[f"cat_{user.catnip_price}"] -= user.catnip_amount
        if not user.perk_selected:
            await interaction.followup.send("You haven't selected a perk from your previous level yet!", ephemeral=True)
            return

        trigger_cutscene = False
        if user.catnip_level != 10:
            user.catnip_level += 1
            user.hibernation = True
            if user.catnip_level == 1:
                user.catnip_active = int(time.time()) + 3600
                user.perk_selected = True  # we do a bit of lying
            else:
                user.perk_selected = False
        else:
            user.catnip_active += 86400
            trigger_cutscene = True
        user.catnip_bought += 1
        user.catnip_total_cats = 0
        user.first_quote_seen = False
        user.reroll = True

        if user.catnip_level > user.highest_catnip_level:
            user.highest_catnip_level = user.catnip_level

        await user.save()
        await set_bounties(user.catnip_level, user)
        await set_mafia_offer(user.catnip_level, user)

        await progress(interaction, user, "catnip_session")
        # Passive XP for catnip level-ups (capped to 1000 per season).
        level_up_embeds = await grant_catnip_levelup_xp(user)
        if level_up_embeds:
            try:
                await interaction.followup.send(embeds=level_up_embeds, ephemeral=True)
            except Exception:
                logging.exception("catnip level-up XP embed send failed")

        logging.debug("Levelled up to %d", user.catnip_level)

        if user.catnip_level == 8 and user.cutscene == 0:
            await mafia_cutscene(interaction, user)
        elif user.catnip_level == 10 and not trigger_cutscene:
            text = """The point of catnip IS NOT TO KEEP LEVELLING UP FOREVER.
You are meant to go up and down levels.
You get absolutely no benefit from completing level 10.
You can stop. That's okay. Seriously.
"""
            await interaction.followup.send(content=text, ephemeral=True)
        elif trigger_cutscene and user.cutscene <= 1:
            await mafia_cutscene2(interaction, user)
        elif user.catnip_level > 1:
            await perk_screen(interaction)
        else:
            await interaction.followup.send("Catnip started!", ephemeral=True)
            await main_message.edit(view=await gen_main())

    async def reroll(interaction):
        global_user = await User.get_or_create(user_id=interaction.user.id)
        user = await Profile.get_or_create(guild_id=interaction.guild.id, user_id=interaction.user.id)
        await user.refresh_from_db()
        perks = catnip_list["perks"]
        rarities = ["Common", "Uncommon", "Rare", "Epic", "Legendary"]
        rarity_colors = [get_emoji("common"), get_emoji("uncommon"), get_emoji("rare"), get_emoji("epic"), get_emoji("legendary")]
        emojied_options = {}
        user_perks = user.perks
        full_desc = ""

        for index, perk in enumerate(user_perks):
            perk_rarity = int(perk.split("_")[0])
            perk_data = perks[int(perk.split("_")[1]) - 1]
            effect = perk_data["values"][int(perk.split("_")[0])]
            desc = (
                perk_data.get("desc", "")
                .replace("percent", f"{effect:,}")
                .replace("triple_none", f"{effect / 2:g}")
                .replace("daily_catch_streak", f"{global_user.daily_catch_streak:,}")
            )
            full_desc += f"{rarity_colors[perk_rarity]} {perk_data.get('name', '')} ({rarities[perk_rarity]})\n{desc}\n\n"
            emojied_options[index + 1] = (f"{perk_data.get('name', '')} ({rarities[perk_rarity]})", rarity_colors[perk_rarity], desc.replace("**", ""))

        myview = LayoutView(timeout=VIEW_TIMEOUT)
        options = [Option(label=f"Lv{k}: {t}", emoji=e, description=d, value=str(k)) for k, (t, e, d) in emojied_options.items()]
        perk_select = Select(
            "rr_type",
            placeholder="Select a perk to reroll",
            opts=options,
            on_select=lambda interaction, level: perk_screen(interaction, int(level), True),
        )
        perk_embed = Container("# Your Perks", full_desc)
        myview.add_item(perk_embed)
        action_row = ActionRow(perk_select)
        myview.add_item(action_row)
        await main_message.edit(view=myview)

    async def view_perks(interaction):
        global_user = await User.get_or_create(user_id=interaction.user.id)
        user = await Profile.get_or_create(guild_id=interaction.guild.id, user_id=interaction.user.id)
        await user.refresh_from_db()
        perks = catnip_list["perks"]
        rarities = ["Common", "Uncommon", "Rare", "Epic", "Legendary"]
        rarity_colors = [get_emoji("common"), get_emoji("uncommon"), get_emoji("rare"), get_emoji("epic"), get_emoji("legendary")]
        user_perks = user.perks
        full_desc = ""

        for perk in user_perks:
            perk_rarity = int(perk.split("_")[0])
            perk_data = perks[int(perk.split("_")[1]) - 1]
            effect = perk_data["values"][int(perk.split("_")[0])]
            desc = (
                perk_data.get("desc", "")
                .replace("percent", f"{effect:,}")
                .replace("triple_none", f"{effect / 2:g}")
                .replace("daily_catch_streak", f"{global_user.daily_catch_streak:,}")
            )
            full_desc += f"{rarity_colors[perk_rarity]} {perk_data.get('name', '')} ({rarities[perk_rarity]})\n{desc}\n\n"

        if not user_perks:
            full_desc = "You have no perks!"
        myview = LayoutView(timeout=VIEW_TIMEOUT)
        perk_embed = Container("# Your Perks", full_desc)
        myview.add_item(perk_embed)
        await interaction.response.send_message(view=myview, ephemeral=True)

    async def perk_screen(interaction, level=0, reroll=False):
        if not interaction.response.is_done():
            await interaction.response.defer()
        global_user = await User.get_or_create(user_id=interaction.user.id)
        user = await Profile.get_or_create(guild_id=interaction.guild.id, user_id=interaction.user.id)

        async def select_perk(interaction):
            await user.refresh_from_db()
            await interaction.response.defer()

            if user.perk_selected and not reroll:
                await interaction.followup.send("You have already selected a perk.", ephemeral=True)
                return
            if reroll and user.reroll:
                await interaction.followup.send("your die rerolls through the floor", ephemeral=True)
                return
            if reroll and user.reroll_level and user.reroll_level != level:
                await interaction.followup.send(f"you already chose to reroll level {user.reroll_level}", ephemeral=True)
                return

            h = list(user.perks) if user.perks else []
            if reroll:
                # We use level-1 because level is 1-based (Lv1, Lv2, etc) defined in the UI
                if 0 <= level - 1 < len(h):
                    h[level - 1] = interaction.data["custom_id"]
                else:
                    await interaction.followup.send(f"Failed to reroll! Perk slot {level} not found. (Count: {len(h)})", ephemeral=True)
                    return
                # Mark reroll as consumed
                user.reroll = True
            else:
                user.perk_selected = True
                h.append(interaction.data["custom_id"])
            user.perks = h[:]  # black magic

            user.perk1 = ""
            user.perk2 = ""
            user.perk3 = ""
            await user.save()

            logging.debug("Selected perk on level %d", user.catnip_level)

            await main_message.edit(view=await gen_main())

        if user.perk_selected and not reroll:
            await interaction.followup.send("You have already selected a perk.", ephemeral=True)
            return
        if reroll and user.reroll:
            await interaction.followup.send("your die rerolls through the floor", ephemeral=True)
            return

        perks_data = catnip_list["perks"]
        rarities = ["Common", "Uncommon", "Rare", "Epic", "Legendary"]
        rarity_colors = [get_emoji("common"), get_emoji("uncommon"), get_emoji("rare"), get_emoji("epic"), get_emoji("legendary")]

        myview = LayoutView(timeout=VIEW_TIMEOUT)

        perk_embed = Container("# Select one of these perks!")

        if user.perk1 and user.perk2 and user.perk3:
            perks = [user.perk1, user.perk2, user.perk3]
        elif level:
            perks = [p["uuid"] for p in await get_perks(level, user)]
        else:
            perks = [p["uuid"] for p in await get_perks(user.catnip_level, user)]

        for i, perk in enumerate(perks):
            perk_data = perks_data[int(perk.split("_")[1]) - 1]
            effect = perk_data["values"][int(perk.split("_")[0])]

            button = Button(label="Select", style=ButtonStyle.blurple, custom_id=perk)
            button.callback = select_perk

            perk_embed.add_item(
                Section(
                    f"## {rarity_colors[int(perk.split('_')[0])]} {perk_data.get('name', '')} ({rarities[int(perk.split('_')[0])]})",
                    f"{perk_data.get('desc', '')}".replace("percent", str(effect))
                    .replace("triple_none", str(effect / 2))
                    .replace("daily_catch_streak", str(global_user.daily_catch_streak)),
                    button,
                )
            )
            perks[i] = {
                "uuid": perk,
                "name": perk_data.get("name", ""),
                "desc": perk_data.get("desc", ""),
                "rarity": perk_data.get("rarity", ""),
                "effect": effect,
            }

        user.perk1 = perks[0]["uuid"] if len(perks) > 0 else None
        user.perk2 = perks[1]["uuid"] if len(perks) > 1 else None
        user.perk3 = perks[2]["uuid"] if len(perks) > 2 else None
        if reroll:
            user.reroll_level = level
        await user.save()

        perk_embed.add_item(TextDisplay("-# The catnip timer will not start until you begin your bounties."))
        myview.add_item(perk_embed)
        await main_message.edit(view=myview)

    async def help_screen(interaction):
        desc = "Catnip is a prestige system where you pay cats to join your mafia and get perks and bounties!"
        desc += "\n\n❓ **How it works:**"
        desc += '\n- Press the "Begin" button to join the mafia and get your first perk and bounties.'
        desc += "\n- Complete your bounties and pay the fee again to level up and get more perks and better bounties!"
        desc += "\n- If you fail to pay in time, you will level down and lose your most recent perk."
        desc += "\n- The timer only starts after you press 'Begin Bounties'."
        desc += "\n\n⭐ **Perks:**"
        desc += "\nPerks give you various bonuses like a chance to double cats cought, a chance of getting packs, etc. You can view your current perks with the 'View Perks' button."
        desc += "\n\n⬆️ **Bounties:**"
        desc += "\nBounties are tasks you need to complete before you can level up. They involve catching a certain number of cats of specific types or rarities. You can view your current bounties in the catnip menu."
        help_embed = discord.Embed(title="Catnip Help", color=Colors.brown, description=desc)
        await interaction.response.send_message(embed=help_embed, ephemeral=True)

    async def begin_bounties(interaction, override=False):
        if not override:
            await interaction.response.defer()

        if not user.hibernation:
            await interaction.followup.send("nice try", ephemeral=True)
            return

        async def callbacks_are_so_fun(interaction2):
            nonlocal interaction
            await interaction2.response.defer()
            await begin_bounties(interaction, override=True)
            await interaction2.delete_original_response()

        if user.catnip_active > time.time() and user.catnip_level >= 2 and not override:
            myview = View(timeout=VIEW_TIMEOUT)
            button = Button(label="Begin Anyway", style=ButtonStyle.red)
            button.callback = callbacks_are_so_fun
            myview.add_item(button)
            await interaction.followup.send(
                f"Your catnip expires <t:{user.catnip_active}:R>.\nAre you sure you want to start your bounties now?\nThis will remove the remaining catnip time you have.",
                view=myview,
                ephemeral=True,
            )
            return

        level_data = catnip_list["levels"][user.catnip_level]
        duration = level_data["duration"]
        user.hibernation = False
        duration_bonus = 0
        perks = catnip_list["perks"]

        if user.perks:
            for perk in user.perks:
                perk_data = perks[int(perk.split("_")[1]) - 1]
                if perk_data["id"] == "loyalty_streak":
                    global_user = await User.get_or_create(user_id=interaction.user.id)
                    duration_bonus = 0
                    for i in range(int(global_user.daily_catch_streak / 100)):
                        i = i + 1
                        duration_bonus += 6000 / i
                    duration_bonus += 60 * (global_user.daily_catch_streak % 100) / (int(global_user.daily_catch_streak / 100) + 1)

        user.catnip_active = int(time.time()) + 3600 * duration + duration_bonus
        user.pack_attempts = (3600 * duration + duration_bonus) // 60
        await user.save()

        logging.debug("Started bounties on level %d", user.catnip_level)

        await main_message.edit(view=await gen_main())

    async def gen_main():
        await user.refresh_from_db()
        level = user.catnip_level
        level_data = catnip_list["levels"][level]
        rank = level_data["name"]
        change = level_data["change"]
        duration = level_data["duration"]
        bonus = level_data["bonus"]
        bounty_data = catnip_list["bounties"]
        cat_type = user.catnip_price
        amount = user.catnip_amount
        quote_list = catnip_list["quotes"][level - 1]["quotes"]
        all_complete = True
        bounties_complete = 0
        bonus_complete = False
        name = ""

        desc = "\n"
        if user.hibernation:
            desc += "\nThe timer for leveling up will **not start** until you begin your bounties.\n"

        if user.catnip_level > 0 and user.catnip_level < 11:

            def format_bounty(bounty_numstr, single=False):
                nonlocal desc, all_complete, bonus_complete, bounties_complete
                bounty_id = user[f"bounty_id_{bounty_numstr}"]
                bounty_type = user[f"bounty_type_{bounty_numstr}"]
                bounty_total = user[f"bounty_total_{bounty_numstr}"]
                bounty_progress = user[f"bounty_progress_{bounty_numstr}"]

                desc += "\n- "
                if bounty_progress == bounty_total:
                    desc += "✅ "
                    if bounty_numstr == "bonus":
                        bonus_complete = True
                    else:
                        bounties_complete += 1
                elif bounty_numstr != "bonus":
                    all_complete = False

                if bounty_progress == 0:
                    desc += f"{bounty_data[bounty_id]['desc']}".replace("X", str(bounty_total))
                else:
                    desc += f"{bounty_data[bounty_id]['desc']}".replace("X", str(bounty_total - bounty_progress) + " more")

                if bounty_total - bounty_progress == 1:
                    desc = desc.replace("cats", "cat")

                desc = desc.replace("type", f"{get_emoji(bounty_type.lower() + 'cat')} {bounty_type}")

            if not user.hibernation:
                if user.bounties == 1:
                    desc += "\n**__Bounty:__**"
                else:
                    desc += "\n**__Bounties:__**"
                for i in range(user.bounties):
                    if i == 0:
                        format_bounty("one")
                    if i == 1:
                        format_bounty("two")
                    if i == 2:
                        format_bounty("three")
                if bonus:
                    desc += "\n**__Bonus Bounty:__**"
                    format_bounty("bonus")
                desc += "\n"
                if not all_complete:
                    desc += f"\n**Pay Up!** {amount} {get_emoji(cat_type.lower() + 'cat')} {cat_type} after completing your bounties"
                else:
                    desc += f"\n**Pay Up!** {amount} {get_emoji(cat_type.lower() + 'cat')} {cat_type} to proceed"
            else:
                desc += "\nPress **Begin Bounties** to view your bounties and cost!"
                if user.catnip_active > time.time():
                    desc += f"\nPerks expire <t:{user.catnip_active}:R>"
                all_complete = False

            colored = int(bounties_complete / user.bounties * 10)
            desc += f"\n\n**Level {level}** - {change}"
            desc += f"\n{level} " + get_emoji("staring_square") * colored + "⬛" * (10 - colored) + f" {level + 1}"
        if not level == 0 and not user.hibernation:
            if user.catnip_active - int(time.time()) < 1800:
                desc += f"\n\n**Hurry!** Levels down <t:{user.catnip_active}:R> ({duration}h total)"
            elif user.catnip_active > time.time():
                desc += f"\n\nLevels down <t:{user.catnip_active}:R> ({duration}h total)"

        if user.catnip_level:
            if not user.first_quote_seen:
                quote = quote_list["first"]
                user.first_quote_seen = True
                await user.save()
            elif all_complete:
                quote = random.choice(quote_list["levelup"])
            else:
                quote = random.choice(quote_list["normal"])
            name = catnip_list["quotes"][level - 1]["name"]
            desc = f"**{name}**: *{quote}*" + desc

        myview = LayoutView(timeout=VIEW_TIMEOUT)

        if name == "Lucian Jr":
            name = "LucianJr"  # i hate file name conventions
        filename = f"images/mafia/{name}.png"

        if name == "Whiskers" and user.catnip_level == 10:
            filename = "images/mafia/WhiskersII.png"
        if name == "Jeremy" and random.randint(1, 100) == 69:
            filename = "images/mafia/sus.png"

        filename = "https://wsrv.nl/?url=raw.githubusercontent.com/milenakos/cat-bot/refs/heads/main/" + filename

        if not desc or desc == "\n":
            embed = Container(f"# Mafia - {rank} (Lv{level})")
        else:
            embed = Container(Section(f"# Mafia - {rank} (Lv{level})", desc, Thumbnail(filename)))
        action_row = ActionRow()

        if not user.perk_selected:
            button3 = Button(label="Select Perk", style=ButtonStyle.red)
            button3.callback = perk_screen
            action_row.add_item(button3)

        if bonus_complete and not user.reroll:
            button4 = Button(label="Reroll Perk!", style=ButtonStyle.green)
            button4.callback = reroll
            action_row.add_item(button4)
        if user.catnip_level == 0:
            button = Button(label="Begin.", style=ButtonStyle.blurple)
            button.callback = pay_catnip
            action_row.add_item(button)
        elif user.hibernation:
            button = Button(label="Begin Bounties", style=ButtonStyle.blurple)
            button.callback = begin_bounties
            action_row.add_item(button)
        elif user.catnip_level < 11:

            async def reroll_warning(interaction2):
                async def continue_pay_catnip(interaction3):
                    await interaction3.response.defer()
                    await interaction3.delete_original_response()
                    await pay_catnip(interaction2)

                view2 = View(timeout=VIEW_TIMEOUT)
                button = Button(label="Yes")
                button.callback = continue_pay_catnip
                view2.add_item(button)
                await interaction2.response.send_message(
                    "Warning: You will lose your reroll if you level up now. Use it first.\nStill continue?", view=view2, ephemeral=True
                )

            button = Button(label="Pay Up!", style=ButtonStyle.blurple)
            if user.bounty_progress_bonus == user.bounty_total_bonus and user.catnip_level >= 7 and not user.reroll:
                button.callback = reroll_warning
            else:
                button.callback = pay_catnip
            button.disabled = not all_complete
            action_row.add_item(button)

        if user.catnip_level > 0:
            button1 = Button(label="View Perks", style=ButtonStyle.gray)
            button1.callback = view_perks
            action_row.add_item(button1)

        button2 = Button(emoji="💡", label="Help", style=ButtonStyle.gray)
        button2.callback = help_screen
        action_row.add_item(button2)

        embed.add_item(action_row)
        myview.add_item(embed)
        return myview

    main_message = await message.followup.send(view=await gen_main(), ephemeral=True, wait=True)


@bot.tree.command(description="View your achievements (achs)")
async def achievements(message: discord.Interaction):
    # this is very close to /inv's ach counter
    user = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    if user.funny >= 50:
        await achemb(message, "its_not_working", "followup")

    unlocked = 0
    minus_achs = 0
    minus_achs_count = 0
    for k in ach_names:
        is_ach_hidden = ach_list[k]["category"] == "Hidden"
        if is_ach_hidden:
            minus_achs_count += 1
        if user.has_ach(k):
            if is_ach_hidden:
                minus_achs += 1
            else:
                unlocked += 1
    total_achs = len(ach_list) - minus_achs_count
    minus_achs = "" if minus_achs == 0 else f" + {minus_achs}"

    hidden_counter = 0

    # this is a single page of the achievement list
    async def gen_new(category):
        nonlocal message, unlocked, total_achs, hidden_counter

        unlocked = 0
        minus_achs = 0
        minus_achs_count = 0

        for k in ach_names:
            is_ach_hidden = ach_list[k]["category"] == "Hidden"
            if is_ach_hidden:
                minus_achs_count += 1
            if user.has_ach(k):
                if is_ach_hidden:
                    minus_achs += 1
                else:
                    unlocked += 1

        total_achs = len(ach_list) - minus_achs_count

        if minus_achs != 0:
            minus_achs = f" + {minus_achs}"
        else:
            minus_achs = ""

        hidden_suffix = ""

        if category == "Hidden":
            hidden_suffix = '\n\nThis is a "Hidden" category. Achievements here only show up after you complete them.'
            hidden_counter += 1
        else:
            hidden_counter = 0

        newembed = discord.Embed(
            title=category,
            description=f"Achievements unlocked (total): {unlocked}/{total_achs}{minus_achs}{hidden_suffix}",
            color=Colors.brown,
        ).set_footer(text=rain_shill)

        global_user = await User.get_or_create(user_id=message.user.id)
        if len(news_list) > len(global_user.news_state.strip()) or "0" in global_user.news_state.strip()[-4:]:
            newembed.set_author(name="You have unread news! /news")

        for k, v in ach_list.items():
            if v["category"] == category:
                if k == "thanksforplaying":
                    if user[k]:
                        newembed.add_field(
                            name=str(get_emoji("demonic_ach")) + " Catnip Addict",
                            value="uncover the mafia's truth",
                            inline=True,
                        )
                    else:
                        newembed.add_field(
                            name=str(get_emoji("no_demonic_ach")) + " Thanks For Playing",
                            value="complete the story",
                            inline=True,
                        )
                    continue

                icon = str(get_emoji("no_ach")) + " "
                if user[k]:
                    newembed.add_field(
                        name=str(get_emoji("ach")) + " " + v["title"],
                        value=v["description"],
                        inline=True,
                    )
                elif category != "Hidden":
                    newembed.add_field(
                        name=icon + v["title"],
                        value="???" if v["is_hidden"] else v["description"],
                        inline=True,
                    )

        return newembed

    # creates buttons at the bottom of the full view
    def insane_view_generator(category):
        myview = View(timeout=VIEW_TIMEOUT)

        options = [
            discord.SelectOption(label="Cat Hunt", emoji=get_emoji("staring_cat")),
            discord.SelectOption(label="Commands", emoji="🤖"),
            discord.SelectOption(label="Random", emoji="🙃"),
            discord.SelectOption(label="Silly", emoji=get_emoji("sillycat")),
            discord.SelectOption(label="Hard", emoji=get_emoji("demonic_ach")),
            discord.SelectOption(label="Hidden", emoji="❓", description="Hidden achievements only show up after you complete them."),
        ]
        select = discord.ui.Select(placeholder=category, options=options)

        async def callback_hell(interaction):
            thing = select.values[0]
            await interaction.response.defer()
            try:
                await interaction.edit_original_response(embed=await gen_new(thing), view=insane_view_generator(thing))
            except Exception:
                pass

            if hidden_counter == 3:
                await interaction.followup.send("catnip is now located in /catnip.", ephemeral=True)
            if hidden_counter == 5:
                await interaction.followup.send("catnip is now located in /catnip.", ephemeral=True)
            if hidden_counter == 10:
                await interaction.followup.send("catnip is now located in /catnip.", ephemeral=True)
            if hidden_counter == 15:
                await interaction.followup.send("I meant it. catnip is now located in /catnip.", ephemeral=True)
            if hidden_counter == 20:
                await interaction.followup.send("I really meant it. catnip is now located in /catnip.\nOh wait, did you want that achievement?", ephemeral=True)
                await achemb(message, "darkest_market", "followup")
            if hidden_counter == 50:
                await interaction.followup.send("I really, really meant it. catnip is now located in /catnip.", ephemeral=True)
            if hidden_counter == 100:
                await interaction.followup.send("Just go away.", ephemeral=True)
            if hidden_counter == 1000:
                await interaction.followup.send("911 theres a person who knocked on my door 1000 times get them out please", ephemeral=True)

        select.callback = callback_hell
        myview.add_item(select)
        return myview

    await message.response.send_message(
        embed=await gen_new("Cat Hunt"),
        ephemeral=True,
        view=insane_view_generator("Cat Hunt"),
    )

    if unlocked >= 15:
        await achemb(message, "achiever", "followup")

    await finale(message, user)


@bot.tree.command(name="catch", description="Catch someone in 4k")
async def catch_tip(message: discord.Interaction):
    await message.response.send_message(
        f'Nope, that\'s the wrong way to do this.\nRight Click/Long Hold a message you want to catch > Select `Apps` in the popup > "{get_emoji("staring_cat")} catch"',
        ephemeral=True,
    )


async def catch(message: discord.Interaction, msg: discord.Message):
    if message.user.id in catchcooldown and catchcooldown[message.user.id] + 6 > time.time():
        await message.response.send_message("your phone is overheating bro chill", ephemeral=True)
        return
    await message.response.defer()

    event_loop = asyncio.get_event_loop()
    try:
        member = await message.guild.fetch_member(msg.author.id)
    except Exception:
        member = msg.author
    result = await event_loop.run_in_executor(None, msg2img.msg2img, msg, member)

    try:
        await message.followup.send("cought in 4k", file=result)
    except Exception:
        try:
            await message.followup.send("failed")
        except Exception:
            pass

    catchcooldown[message.user.id] = time.time()

    await achemb(message, "4k", "followup")

    if msg.author.id == bot.user.id and "cought in 4k" in msg.content:
        await achemb(message, "8k", "followup")

    try:
        is_cat = (await Channel.get_or_none(channel_id=message.channel.id)).cat
    except Exception:
        is_cat = False

    if int(is_cat) == int(msg.id):
        await achemb(message, "not_like_that", "followup")


@bot.tree.command(description="View the leaderboards (lbs)")
@discord.app_commands.rename(leaderboard_type="type")
@discord.app_commands.describe(
    leaderboard_type="The leaderboard type to view!",
    cat_type="The cat type to view (only for the Cats leaderboard)",
    locked="Whether to remove page switch buttons to prevent tampering",
)
@discord.app_commands.autocomplete(cat_type=lb_type_autocomplete)
async def leaderboards(
    message: discord.Interaction,
    leaderboard_type: Optional[Literal["Cats", "Value", "Fast", "Slow", "Cattlepass", "Cookies", "Pig", "Coins", "Prisms", "Mafia", "Heists", "Job Coins", "Biggest Score"]],
    cat_type: Optional[str],
    locked: Optional[bool],
):
    if not leaderboard_type:
        leaderboard_type = "Cats"
    if not locked:
        locked = False
    if cat_type and cat_type not in cattypes + ["All"]:
        await message.response.send_message("invalid cattype", ephemeral=True)
        return

    # this fat function handles a single page
    async def lb_handler(interaction, type, do_edit=None, specific_cat="All"):
        if not specific_cat:
            specific_cat = "All"

        nonlocal message
        if do_edit is None:
            do_edit = True
        await interaction.response.defer()

        messager = None
        interactor = None

        # leaderboard top amount
        show_amount = 15

        string = ""
        if type == "Cats":
            unit = "cats"

            if specific_cat != "All":
                result = await Profile.collect_limit(
                    ["user_id", f"cat_{specific_cat}"], f'guild_id = $1 AND "cat_{specific_cat}" > 0 ORDER BY "cat_{specific_cat}" DESC', message.guild.id
                )
                final_value = f"cat_{specific_cat}"
            else:
                # dynamically generate sum expression, cast each value to bigint first to handle large totals
                cat_columns = [f'CAST("cat_{c}" AS BIGINT)' for c in cattypes]
                sum_expression = RawSQL("(" + " + ".join(cat_columns) + ") AS final_value")
                result = await Profile.collect_limit(["user_id", sum_expression], "guild_id = $1 ORDER BY final_value DESC", message.guild.id)
                final_value = "final_value"

                # find rarest
                rarest = None
                for i in cattypes[::-1]:
                    non_zero_count = await Profile.collect_limit("user_id", f'guild_id = $1 AND "cat_{i}" > 0', message.guild.id)
                    if len(non_zero_count) != 0:
                        rarest = i
                        rarest_holder = non_zero_count
                        break

                if rarest and specific_cat != rarest:
                    catmoji = get_emoji(rarest.lower() + "cat")
                    rarest_holder = [f"<@{i.user_id}>" for i in rarest_holder]
                    joined = ", ".join(rarest_holder)
                    if len(rarest_holder) > 10:
                        joined = f"{len(rarest_holder)} people"
                    string = f"Rarest cat: {catmoji} ({joined}'s)\n\n"
        elif type == "Value":
            unit = "value"
            sums = []
            for cat_type in cattypes:
                if not cat_type:
                    continue
                weight = sum(type_dict.values()) / type_dict[cat_type]
                sums.append(f'({weight}) * "cat_{cat_type}"')
            total_sum_expr = RawSQL("(" + " + ".join(sums) + ") AS final_value")
            result = await Profile.collect_limit(["user_id", total_sum_expr], "guild_id = $1 ORDER BY final_value DESC", message.guild.id)
            final_value = "final_value"
        elif type == "Fast":
            unit = "sec"
            result = await Profile.collect_limit(["user_id", "time"], "guild_id = $1 AND time < 99999999999999 ORDER BY time ASC", message.guild.id)
            final_value = "time"
        elif type == "Slow":
            unit = "h"
            result = await Profile.collect_limit(["user_id", "timeslow"], "guild_id = $1 AND timeslow > 0 ORDER BY timeslow DESC", message.guild.id)
            final_value = "timeslow"
        elif type == "Cattlepass":
            start_date = datetime.datetime(2026, 4, 1)
            current_date = discord.utils.utcnow() + datetime.timedelta(hours=4)
            full_months_passed = (current_date.year - start_date.year) * 12 + (current_date.month - start_date.month)
            bp_season = config.battle["seasons"][str(full_months_passed)]
            if current_date.day < start_date.day:
                full_months_passed -= 1
            result = await Profile.collect_limit(
                ["user_id", "battlepass", "progress"],
                "guild_id = $1 AND season = $2 AND (battlepass > 0 OR progress > 0) ORDER BY battlepass DESC, progress DESC",
                message.guild.id,
                full_months_passed,
            )
            final_value = "battlepass"
        elif type == "Cookies":
            unit = "cookies"
            result = await Profile.collect_limit(["user_id", "cookies"], "guild_id = $1 AND cookies > 0 ORDER BY cookies DESC", message.guild.id)
            final_value = "cookies"
        elif type == "Pig":
            unit = "score"
            result = await Profile.collect_limit(
                ["user_id", "best_pig_score"], "guild_id = $1 AND best_pig_score > 0 ORDER BY best_pig_score DESC", message.guild.id
            )
            final_value = "best_pig_score"
        elif type == "Coins":
            unit = "coins"
            # Anyone with a non-zero balance shows up — including debtors from
            # gambling, since the previous "≤ 0 is still ranked" behavior is
            # preserved for this category by the special-case below.
            result = await Profile.collect_limit(
                ["user_id", "coins"], "guild_id = $1 AND coins != 0 ORDER BY coins DESC", message.guild.id
            )
            final_value = "coins"
        elif type == "Prisms":
            unit = "prisms"
            result = await Prism.collect_limit(
                ["user_id", RawSQL("COUNT(*) as prism_count")],
                "guild_id = $1 GROUP BY user_id ORDER BY prism_count DESC",
                message.guild.id,
                add_primary_key=False,
            )
            final_value = "prism_count"
        elif type == "Mafia":
            # Cat Mafia (catnip) level — Newbies (level 0) are excluded since
            # that's the default for anyone who's never touched /catnip.
            unit = "Lv"
            result = await Profile.collect_limit(
                ["user_id", "catnip_level"], "guild_id = $1 AND catnip_level > 0 ORDER BY catnip_level DESC", message.guild.id
            )
            final_value = "catnip_level"
        elif type == "Heists":
            unit = "jobs"
            result = await Profile.collect_limit(
                ["user_id", "jobs_completed"],
                "guild_id = $1 AND jobs_completed > 0 ORDER BY jobs_completed DESC",
                message.guild.id,
            )
            final_value = "jobs_completed"
        elif type == "Job Coins":
            unit = "coins"
            result = await Profile.collect_limit(
                ["user_id", "job_coins_won"],
                "guild_id = $1 AND job_coins_won > 0 ORDER BY job_coins_won DESC",
                message.guild.id,
            )
            final_value = "job_coins_won"
        elif type == "Biggest Score":
            unit = "value"
            result = await Profile.collect_limit(
                ["user_id", "biggest_score_value"],
                "guild_id = $1 AND biggest_score_value > 0 ORDER BY biggest_score_value DESC",
                message.guild.id,
            )
            final_value = "biggest_score_value"
        else:
            # qhar
            raise ValueError("Invalid leaderboard type")

        # find the placement of the person who ran the command and optionally the person who pressed the button
        interactor_placement = 0
        messager_placement = 0
        for index, position in enumerate(result):
            if position["user_id"] == interaction.user.id:
                interactor_placement = index + 1
                interactor = position[final_value]
                if type == "Cattlepass":
                    if position[final_value] >= len(bp_season):
                        lv_xp_req = 1500
                    else:
                        lv_xp_req = bp_season[int(position[final_value]) - 1]["xp"]
                    interactor_perc = math.floor((100 / lv_xp_req) * position["progress"])
            if interaction.user != message.user and position["user_id"] == message.user.id:
                messager_placement = index + 1
                messager = position[final_value]
                if type == "Cattlepass":
                    if position[final_value] >= len(bp_season):
                        lv_xp_req = 1500
                    else:
                        lv_xp_req = bp_season[int(position[final_value]) - 1]["xp"]
                    messager_perc = math.floor((100 / lv_xp_req) * position["progress"])

        if type == "Slow":
            if interactor:
                interactor = round(interactor / 3600, 2)
            if messager:
                messager = round(messager / 3600, 2)

        if type == "Fast":
            if interactor:
                interactor = round(interactor, 3)
            if messager:
                messager = round(messager, 3)

        # dont show placements if they arent defined
        if interactor and type != "Fast":
            if interactor <= 0 and type != "Coins":
                interactor_placement = 0
            interactor = round(interactor)
        elif interactor and type == "Fast" and interactor >= 99999999999999:
            interactor_placement = 0

        if messager and type != "Fast":
            if messager <= 0 and type != "Coins":
                messager_placement = 0
            messager = round(messager)
        elif messager and type == "Fast" and messager >= 99999999999999:
            messager_placement = 0

        emoji = ""
        if type == "Cats" and specific_cat != "All":
            emoji = get_emoji(specific_cat.lower() + "cat")

        # the little place counter
        current = 1
        leader = False
        for i in result[:show_amount]:
            num = i[final_value]

            if type == "Cattlepass":
                if i[final_value] >= len(bp_season):
                    lv_xp_req = 1500
                else:
                    lv_xp_req = bp_season[int(i[final_value]) - 1]["xp"]
                prog_perc = math.floor((100 / lv_xp_req) * i["progress"])
                string += f"{current}. Level **{num}** *({prog_perc}%)*: <@{i['user_id']}>\n"
            else:
                if type == "Value":
                    if num <= 0:
                        break
                    num = round(num)
                elif type == "Fast" or type == "Slow":
                    if num >= 99999999999999 or num <= 0:
                        break
                    if num >= 31536000:
                        num = round(num / 31536000, 2)
                        unit = "yrs"
                    elif num >= 86400:
                        num = round(num / 86400, 2)
                        unit = "days"
                    elif num >= 3600:
                        num = round(num / 3600, 2)
                        unit = "hrs"
                    elif num >= 60:
                        num = round(num / 60, 2)
                        unit = "mins"
                    elif num >= 1:
                        num = round(num, 2)
                        unit = "sec"
                    else:
                        num = round(num, 3)
                        unit = "sec"
                elif type in ["Cookies", "Cats", "Pig", "Prisms"] and num <= 0:
                    break
                elif type == "Coins" and num == 0:
                    break
                string = string + f"{current}. {emoji} **{num:,}** {unit}: <@{i['user_id']}>\n"

            if message.user.id == i["user_id"] and current <= 5:
                leader = True
            current += 1

        # add the messager and interactor
        if messager_placement > show_amount or interactor_placement > show_amount:
            string = string + "...\n"

            # setting up names
            include_interactor = interactor_placement > show_amount and str(interaction.user.id) not in string
            include_messager = messager_placement > show_amount and str(message.user.id) not in string
            interactor_line = ""
            messager_line = ""
            if include_interactor:
                if type == "Cattlepass":
                    interactor_line = f"{interactor_placement}\\. Level **{interactor}** *({interactor_perc}%)*: {interaction.user.mention}\n"
                else:
                    interactor_line = f"{interactor_placement}\\. {emoji} **{interactor:,}** {unit}: {interaction.user.mention}\n"
            if include_messager:
                if type == "Cattlepass":
                    messager_line = f"{messager_placement}\\. Level **{messager}** *({messager_perc}%)*: {message.user.mention}\n"
                else:
                    messager_line = f"{messager_placement}\\. {emoji} **{messager:,}** {unit}: {message.user.mention}\n"

            # sort them correctly!
            if messager_placement > interactor_placement:
                # interactor should go first
                string += interactor_line
                string += messager_line
            else:
                # messager should go first
                string += messager_line
                string += interactor_line

        title = type + " Leaderboard"
        if type == "Cats":
            title = f"{specific_cat} {title}"
        title = "🏅 " + title

        embedVar = discord.Embed(title=title, description=string.rstrip(), color=Colors.brown).set_footer(text=rain_shill)

        global_user = await User.get_or_create(user_id=message.user.id)

        if len(news_list) > len(global_user.news_state.strip()) or "0" in global_user.news_state.strip()[-4:]:
            embedVar.set_author(name=f"{message.user} has unread news! /news")

        # handle funny buttons
        myview = View(timeout=VIEW_TIMEOUT)

        if type == "Cats":
            dd_opts = [Option(label="All", emoji=get_emoji("staring_cat"), value="All")]

            for i in await cats_in_server(message.guild.id):
                dd_opts.append(Option(label=i, emoji=get_emoji(i.lower() + "cat"), value=i))

            dropdown = Select(
                "cat_type_dd",
                placeholder="Select a cat type",
                opts=dd_opts,
                selected=specific_cat,
                on_select=lambda interaction, option: lb_handler(interaction, type, True, option),
                disabled=locked,
            )

        emojied_options = {
            "Cats": "🐈",
            "Value": "🧮",
            "Fast": "⏱️",
            "Slow": "💤",
            "Cattlepass": "⬆️",
            "Cookies": "🍪",
            "Pig": "🎲",
            "Coins": "🪙",
            "Prisms": get_emoji("prism"),
            "Mafia": get_emoji("catnip"),
            "Heists": "🏆",
            "Job Coins": "💰",
            "Biggest Score": "💎",
        }
        options = [Option(label=k, emoji=v) for k, v in emojied_options.items()]
        lb_select = Select(
            "lb_type",
            placeholder=type,
            opts=options,
            on_select=lambda interaction, type: lb_handler(interaction, type, True),
        )

        if not locked:
            myview.add_item(lb_select)
            if type == "Cats":
                myview.add_item(dropdown)

        # just send if first time, otherwise edit existing
        try:
            if not do_edit:
                raise Exception
            await interaction.edit_original_response(embed=embedVar, view=myview)
        except Exception:
            await interaction.followup.send(embed=embedVar, view=myview)

        if leader:
            await achemb(message, "leader", "followup")

    await lb_handler(message, leaderboard_type, False, cat_type)


@bot.tree.command(description="(ADMIN) Give cats to people")
@discord.app_commands.default_permissions(manage_guild=True)
@discord.app_commands.rename(person_id="user")
@discord.app_commands.describe(person_id="who", amount="how many (negatives to remove)", cat_type="what")
@discord.app_commands.autocomplete(cat_type=cat_type_autocomplete)
async def givecat(message: discord.Interaction, person_id: discord.User, cat_type: str, amount: Optional[int]):
    if amount is None:
        amount = 1
    if cat_type not in cattypes:
        await message.response.send_message("bro what", ephemeral=True)
        return

    user = await Profile.get_or_create(guild_id=message.guild.id, user_id=person_id.id)
    user[f"cat_{cat_type}"] += amount
    await user.save()
    await message.response.send_message(f"gave {person_id.mention} {amount:,} {cat_type} cats", allowed_mentions=discord.AllowedMentions(users=True))


@bot.tree.command(name="setup", description="(ADMIN) Setup cat in current channel")
@discord.app_commands.default_permissions(manage_guild=True)
async def setup_channel(message: discord.Interaction):
    try:
        guild = await bot.fetch_guild(message.guild.id)
        if isinstance(message.channel, discord.Thread):
            channel = await guild.fetch_channel(message.channel.parent_id)
        else:
            channel = await guild.fetch_channel(message.channel.id)
        channel_permissions = channel.permissions_for(message.guild.me)
        needed_perms = {
            "View Channel": channel_permissions.view_channel,
            "Send Messages": channel_permissions.send_messages,
            "Attach Files": channel_permissions.attach_files,
        }
        if isinstance(message.channel, discord.Thread):
            needed_perms["Send Messages in Threads"] = channel_permissions.send_messages_in_threads

        for name, value in needed_perms.copy().items():
            if value:
                needed_perms.pop(name)

        missing_perms = list(needed_perms.keys())
        if len(missing_perms) != 0:
            needed_perms = "\n- ".join(missing_perms)
            await message.response.send_message(
                f":x: Missing Permissions! Please give me the following:\n- {needed_perms}\nHint: try setting channel permissions if server ones don't work."
            )
            return

        if await Channel.get_or_none(channel_id=message.channel.id):
            await message.response.send_message(
                "bruh you already setup cat here are you dumb\n\nthere might already be a cat sitting in chat. type `cat` to catch it."
            )
            return

        await Channel.create(channel_id=message.channel.id)
    except Exception:
        await message.response.send_message("error. check if i have permissions to access this channel")
        return

    await spawn_cat(str(message.channel.id))
    await message.response.send_message(f"ok, now i will also send cats in <#{message.channel.id}>")


@bot.tree.command(description="(ADMIN) Undo the setup/unsetup")
@discord.app_commands.default_permissions(manage_guild=True)
async def forget(message: discord.Interaction):
    if channel := await Channel.get_or_none(channel_id=message.channel.id):
        await channel.delete()
        await message.response.send_message(f"ok, now i wont send cats in <#{message.channel.id}>")
    else:
        await message.response.send_message("your an idiot there is literally no cat setupped in this channel you stupid")


@bot.tree.command(description="LMAO TROLLED SO HARD :JOY:")
async def fake(message: discord.Interaction):
    if message.user.id in fakecooldown and fakecooldown[message.user.id] + 60 > time.time():
        await message.response.send_message("your phone is overheating bro chill", ephemeral=True)
        return
    file = discord.File("images/australian cat.png", filename="australian cat.png")
    icon = get_emoji("egirlcat")
    fakecooldown[message.user.id] = time.time()
    try:
        await message.response.send_message(
            str(icon) + ' eGirl cat hasn\'t appeared! Type "cat" to catch ratio!',
            file=file,
        )
    except Exception:
        await message.response.send_message("i dont have perms lmao here is the ach anyways", ephemeral=True)
        pass
    await achemb(message, "trolled", "ephemeral")


@bot.tree.command(description="(ADMIN) Force cats to appear/spawn")
@discord.app_commands.default_permissions(manage_guild=True)
@discord.app_commands.rename(cat_type="type")
@discord.app_commands.describe(cat_type="select a cat type ok")
@discord.app_commands.autocomplete(cat_type=cat_type_autocomplete)
async def forcespawn(message: discord.Interaction, cat_type: Optional[str]):
    if cat_type and cat_type not in cattypes:
        await message.response.send_message("bro what", ephemeral=True)
        return

    ch = await Channel.get_or_none(channel_id=message.channel.id)
    if ch is None:
        await message.response.send_message("this channel is not /setup-ed", ephemeral=True)
        return
    if ch.cat:
        await message.response.send_message("there is already a cat", ephemeral=True)
        return
    ch.yet_to_spawn = 0
    await ch.save()
    await spawn_cat(str(message.channel.id), cat_type, True)
    await message.response.send_message("done!\n**Note:** you can use `/givecat` to give yourself cats, there is no need to spam this")


@bot.tree.command(description="(ADMIN) Give achievements to people")
@discord.app_commands.default_permissions(manage_guild=True)
@discord.app_commands.rename(person_id="user", ach_id="name")
@discord.app_commands.describe(person_id="who", ach_id="name or id of the achievement")
@discord.app_commands.autocomplete(ach_id=ach_autocomplete)
async def giveachievement(message: discord.Interaction, person_id: discord.User, ach_id: str):
    # check if ach is real
    try:
        valid = ach_id in ach_names
    except KeyError:
        valid = False

    if not valid and ach_id.lower() in ach_titles.keys():
        ach_id = ach_titles[ach_id.lower()]
        valid = True

    person = await Profile.get_or_create(guild_id=message.guild.id, user_id=person_id.id)

    if valid and ach_id == "thanksforplaying":
        await message.response.send_message("HAHAHHAHAH\nno", ephemeral=True)
        return

    if valid:
        # if it is, do the thing
        reverse = person[ach_id]
        person[ach_id] = not reverse
        await person.save()
        color, title, icon = (
            Colors.green,
            "Achievement forced!",
            "https://wsrv.nl/?url=raw.githubusercontent.com/staring-cat/emojis/main/ach.png",
        )
        if reverse:
            color, title, icon = (
                Colors.red,
                "Achievement removed!",
                "https://wsrv.nl/?url=raw.githubusercontent.com/staring-cat/emojis/main/no_ach.png",
            )
        ach_data = ach_list[ach_id]
        embed = (
            discord.Embed(
                title=ach_data["title"],
                description=ach_data["description"],
                color=color,
            )
            .set_author(name=title, icon_url=icon)
            .set_footer(text=f"for {person_id.name}")
        )
        await message.response.send_message(person_id.mention, embed=embed, allowed_mentions=discord.AllowedMentions(users=True))
    else:
        await message.response.send_message("i cant find that achievement! try harder next time.", ephemeral=True)


@bot.tree.command(description="(ADMIN) Reset people")
@discord.app_commands.default_permissions(manage_guild=True)
@discord.app_commands.rename(person_id="user")
@discord.app_commands.describe(person_id="who")
async def reset(message: discord.Interaction, person_id: discord.User):
    async def confirmed(interaction):
        if interaction.user.id == message.user.id:
            await interaction.response.defer()
            try:
                og = await interaction.original_response()
                profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=person_id.id)
                profile.guild_id = og.id
                await profile.save()
                async for p in Prism.filter("guild_id = $1 AND user_id = $2", message.guild.id, person_id.id):
                    p.guild_id = og.id
                    await p.save()
                await interaction.edit_original_response(
                    content=f"Done! rip {person_id.mention}. f's in chat.\njoin our discord to rollback: <https://discord.gg/staring>", view=None
                )
            except Exception:
                await interaction.edit_original_response(
                    content="ummm? this person isnt even registered in cat bot wtf are you wiping?????",
                    view=None,
                )
        else:
            await do_funny(interaction)

    view = View(timeout=VIEW_TIMEOUT)
    button = Button(style=ButtonStyle.red, label="Confirm")
    button.callback = confirmed
    view.add_item(button)
    await message.response.send_message(f"Are you sure you want to reset {person_id.mention}?", view=view, allowed_mentions=discord.AllowedMentions(users=True))


@bot.tree.command(description="(HIGH ADMIN) [VERY DANGEROUS] Reset/wipe all Cat Bot data of this server")
@discord.app_commands.default_permissions(administrator=True)
async def nuke(message: discord.Interaction):
    warning_text = "⚠️ This will completely reset **all** Cat Bot progress of **everyone** in this server. Spawn channels and their settings *will not be affected*.\nPress the button 5 times to continue."
    counter = 5

    async def gen(counter):
        lines = [
            "",
            "I'm absolutely sure! (1)",
            "I understand! (2)",
            "You can't undo this! (3)",
            "This is dangerous! (4)",
            "Reset everything! (5)",
        ]
        view = View(timeout=VIEW_TIMEOUT)
        button = Button(label=lines[max(1, counter)], style=ButtonStyle.red)
        button.callback = count
        view.add_item(button)
        return view

    async def count(interaction: discord.Interaction):
        nonlocal message, counter
        if interaction.user.id == message.user.id:
            await interaction.response.defer()
            counter -= 1
            if counter == 0:
                # ~~Scary!~~ Not anymore!
                # how this works is we basically change the server id to the message id and then add user with id of 0 to mark it as deleted
                # this can be rolled back decently easily by asking user for the id of nuking message

                changed_profiles = []
                changed_prisms = []

                async for i in Profile.filter("guild_id = $1", message.guild.id):
                    i.guild_id = interaction.message.id
                    changed_profiles.append(i)

                async for i in Prism.filter("guild_id = $1", message.guild.id):
                    i.guild_id = interaction.message.id
                    changed_prisms.append(i)

                if changed_profiles:
                    await Profile.bulk_update(changed_profiles, "guild_id")
                if changed_prisms:
                    await Prism.bulk_update(changed_prisms, "guild_id")
                await Profile.create(guild_id=interaction.message.id, user_id=0)

                try:
                    await interaction.edit_original_response(
                        content="Done. If you want to roll this back, please contact us in our discord: <https://discord.gg/staring>.",
                        view=None,
                    )
                except Exception:
                    await interaction.followup.send("Done. If you want to roll this back, please contact us in our discord: <https://discord.gg/staring>.")
            else:
                view = await gen(counter)
                try:
                    await interaction.edit_original_response(content=warning_text, view=view)
                except Exception:
                    pass
        else:
            await do_funny(interaction)

    view = await gen(counter)
    await message.response.send_message(warning_text, view=view)


async def recieve_vote(request):
    signature = request.headers.get("x-topgg-signature", "")
    try:
        signature_parts = {i.split("=")[0]: i.split("=")[1] for i in signature.split(",")}
        raw_body = await request.read()
        body = f"{signature_parts['t']}.{raw_body.decode()}".encode("utf-8")
        key = config.WEBHOOK_VERIFY.encode("utf-8")
        if hmac.new(key, body, hashlib.sha256).hexdigest() != signature_parts["v1"]:
            raise ValueError
    except Exception:
        return web.Response(text="bad", status=403)
    request_data = json.loads(raw_body)["data"]

    user = await User.get_or_create(user_id=int(request_data["user"]["platform_id"]))
    created_at = datetime.datetime.fromisoformat(request_data["created_at"]).timestamp()

    await do_vote(user, created_at)

    return web.Response(text="ok", status=200)


async def do_vote(user: User, created_at: float):
    if user.daily_catch_streak < 10:
        extend_time = 24
    elif user.daily_catch_streak < 20:
        extend_time = 36
    elif user.daily_catch_streak < 50:
        extend_time = 48
    elif user.daily_catch_streak < 100:
        extend_time = 60
    else:
        extend_time = 72

    if created_at - user.vote_time_topgg < 3600:
        return

    user.reminder_vote = 1
    user.total_votes += 1
    freeze_note = ""
    if user.vote_time_topgg + extend_time * 3600 <= created_at:
        # streak end
        if user.streak_freezes < 1:
            if user.max_daily_streak < user.daily_catch_streak:
                user.max_daily_streak = user.daily_catch_streak
            user.daily_catch_streak = 1
        else:
            # i initially wanted streak freezes to not increase up
            # but that could result in unexpected repeated milestone rewards
            user.daily_catch_streak += 1

            user.streak_freezes -= 1
            freeze_note = "\n🧊 Streak Freeze Used!"
    else:
        user.daily_catch_streak += 1

    user.vote_time_topgg = created_at

    channeley = await fetch_dm_channel(user)

    if user.daily_catch_streak == 1:
        streak_progress = "🟦⬛⬛⬛⬛⬛⬛⬛⬛⬛\n⬆️"
    else:
        streak_progress = ""
        if user.daily_catch_streak > 0:
            streak_progress += get_streak_reward(user.daily_catch_streak - 1)["done_emoji"]
        streak_progress += get_streak_reward(user.daily_catch_streak)["done_emoji"]

        for i in range(user.daily_catch_streak + 1, user.daily_catch_streak + 9):
            streak_progress += get_streak_reward(i)["emoji"]

        streak_progress += f"\n{get_emoji('empty')}⬆️"

    special_reward = math.ceil(user.daily_catch_streak / 25) * 25
    if special_reward not in range(user.daily_catch_streak, user.daily_catch_streak + 9):
        streak_progress += f"\nNext Special Reward: {get_streak_reward(special_reward)['emoji']} at {special_reward} streak"

    streak_top_position = await User.count("daily_catch_streak > $1", user.daily_catch_streak) + 1
    top_text = f" (top #{streak_top_position}!)" if streak_top_position < 1000 else ""

    try:
        await channeley.send(
            "\n".join(
                [
                    "Thanks for voting! To claim your rewards, run `/battlepass` in every server you want.",
                    f"You can vote again <t:{int(created_at) + 43200}:R>.",
                    "",
                    f":fire: **Streak:** {user.daily_catch_streak:,}{top_text} expires <t:{int(created_at) + extend_time * 3600}:R>{freeze_note}",
                    f"{streak_progress}",
                ]
            ),
        )

        logging.debug("User voted, streak %d", user.daily_catch_streak)
    except Exception:
        # Ignore errors when DMing the user (e.g. if they have DMs closed)
        pass

    await user.save()


async def check_supporter(request):
    if request.headers.get("authorization", "") != config.WEBHOOK_VERIFY:
        return web.Response(text="bad", status=403)
    request_json = await request.json()

    user = await User.get_or_create(user_id=int(request_json["user"]))
    return web.Response(text="1" if user.premium else "0", status=200)


async def bake_gg_reward(request):
    if request.headers.get("Authorization", "") != os.environ.get("BAKE_GG_WEBHOOK_TOKEN", ""):
        return web.Response(text="Invalid or missing authorization token", status=401)

    try:
        request_json = await request.json()
        user_id = int(request_json["user"])
    except Exception:
        return web.Response(text="Invalid user ID", status=400)
    user = await User.get_or_create(user_id=user_id)

    if user.last_bakegg_get == get_current_week():
        return web.Response(text="User already claimed this week", status=429)

    user.last_bakegg_get = get_current_week()
    user.queued_chef_pack = True
    await user.save()
    try:
        channeley = await fetch_dm_channel(user)
        await channeley.send(f"You have received a {get_emoji('chefpack')} Chef Pack from Bake.gg! You can claim it in a single server by running `/bakery`.")
    except Exception:
        pass
    return web.Response(text="Success", status=200)


# cat bot uses glitchtip (sentry alternative) for errors, here u can instead implement some other logic like dming the owner
async def on_error(*args, **kwargs):
    raise


# this is for stats, useless otherwise
async def on_interaction(ctx):
    if ctx.command:
        logging.debug("Command %s was used", ctx.command.name)
        # Data-driven command-use triggers (engine fires aches with
        # trigger.event == "command" and matching command name).
        if ctx.guild is not None and ctx.user is not None:
            try:
                cmd_profile = await Profile.get_or_create(guild_id=ctx.guild.id, user_id=ctx.user.id)
                await ach_engine.evaluate(
                    "command",
                    cmd_profile,
                    {"command": ctx.command.qualified_name},
                    message=ctx,
                    achemb=achemb,
                    send_type="followup",
                )
            except Exception:
                logging.exception("ach_engine command event failed")


async def setup(bot2):
    global bot, RAIN_ID, PLUSH_ID, vote_server

    for command in bot.tree.walk_commands():
        # copy all the commands
        command.guild_only = True
        bot2.tree.add_command(command)

    context_menu_command = discord.app_commands.ContextMenu(name="catch", callback=catch)
    context_menu_command.guild_only = True
    bot2.tree.add_command(context_menu_command)

    # copy all the events
    bot2.on_ready = on_ready
    bot2.on_guild_join = on_guild_join
    bot2.on_message = on_message
    bot2.on_connect = on_connect
    bot2.on_error = on_error
    bot2.on_interaction = on_interaction

    if config.WEBHOOK_VERIFY:
        routes = [
            web.get("/supporter", check_supporter),
            web.post("/bakegg", bake_gg_reward),
        ]
        if config.VOTING_ENABLED:
            routes.insert(0, web.post("/", recieve_vote))
        app = web.Application()
        app.add_routes(routes)
        vote_server = web.AppRunner(app)
        await vote_server.setup()
        site = web.TCPSite(vote_server, "0.0.0.0", 8069)
        await site.start()

    # finally replace the fake bot with the real one
    bot = bot2

    config.SOFT_RESTART_TIME = time.time()

    # Start (or restart) the background spawn-revival loop. We keep the
    # task handle on `config` so it survives cat!restart; the new setup
    # cancels the old loop before creating a fresh one to avoid duplicates.
    old_task = getattr(config, "spawn_revival_task", None)
    if old_task and not old_task.done():
        old_task.cancel()
    config.spawn_revival_task = bot.loop.create_task(_spawn_revival_loop())

    app_commands = await bot.tree.sync()
    for i in app_commands:
        if i.name == "rain":
            RAIN_ID = i.id
        if i.name == "plush":
            PLUSH_ID = i.id

    if bot.is_ready() and not on_ready_debounce:
        await on_ready()


async def teardown(bot):
    if config.WEBHOOK_VERIFY:
        await vote_server.cleanup()


# Reusable UI components
class Option:
    def __init__(self, label, emoji, description=None, value=None):
        self.label = label
        self.emoji = emoji
        self.value = value if value is not None else label
        self.description = description


class Select(discord.ui.Select):
    on_select = None

    def __init__(
        self,
        id: str,
        placeholder: str,
        opts: list[Option],
        selected: str = None,
        on_select: callable = None,
        disabled: bool = False,
    ):
        options = []
        if on_select is not None:
            self.on_select = on_select

        for opt in opts:
            options.append(discord.SelectOption(label=opt.label, description=opt.description, value=opt.value, emoji=opt.emoji, default=opt.value == selected))

        super().__init__(
            placeholder=placeholder,
            options=options,
            custom_id=id,
            max_values=1,
            min_values=1,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.on_select is not None and callable(self.on_select):
            await self.on_select(interaction, self.values[0] if len(self.values) == 1 else self.values)


class Container(discord.ui.Container):
    def __init__(self, *pre_children, **kwargs):
        if "accent_color" not in kwargs:
            kwargs["accent_color"] = Colors.brown

        children = []
        new_children = []

        for chil in pre_children:
            if isinstance(chil, tuple):
                children.extend(chil)
            else:
                children.append(chil)

        for child in children:
            if isinstance(child, str):
                if child == "===":
                    new_children.append(Separator())
                else:
                    new_children.append(TextDisplay(child))
            elif isinstance(child, Button):
                new_children.append(ActionRow(child))
            else:
                new_children.append(child)

        super().__init__(*new_children, **kwargs)


class Section(discord.ui.Section):
    def __init__(self, *children, **kwargs):
        if "accessory" not in kwargs:
            new_children = []

            for child in children:
                if isinstance(child, Button) or isinstance(child, Thumbnail):
                    kwargs["accessory"] = child
                else:
                    new_children.append(child)

            super().__init__(*new_children, **kwargs)
        else:
            super().__init__(*children, **kwargs)
