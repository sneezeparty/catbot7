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
import stock_news
from catpg import RawSQL, pool, transaction
from database import Channel, JobInstance, NewsEvent, Order, PortfolioHistory, PriceHistory, Prism, Profile, Reminder, Reward, Server, User, _coerce_array

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
    "Shadow": 221,
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
    "Terminator": 5,
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
    "Shadow": 80,
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
    "Terminator": 278,
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
    # normal. `totalvalue` is the /stocks deposit payout and the trade-display
    # value — DO NOT inflate it. `store_price` is the /catstore buy price; it
    # diverges from totalvalue on Silver+ to make high-tier packs aspirational.
    # When `store_price` is missing, pack_buy_price falls back to totalvalue.
    {"name": "Wooden",    "value": 98,   "upgrade": 30, "totalvalue": 113,  "store_price": 113,   "special": False},
    {"name": "Stone",     "value": 135,  "upgrade": 30, "totalvalue": 150,  "store_price": 150,   "special": False},
    {"name": "Bronze",    "value": 150,  "upgrade": 30, "totalvalue": 195,  "store_price": 195,   "special": False},
    {"name": "Silver",    "value": 173,  "upgrade": 30, "totalvalue": 300,  "store_price": 600,   "special": False},
    {"name": "Gold",      "value": 345,  "upgrade": 30, "totalvalue": 600,  "store_price": 1800,  "special": False},
    {"name": "Platinum",  "value": 945,  "upgrade": 30, "totalvalue": 1200, "store_price": 4800,  "special": False},
    {"name": "Diamond",   "value": 1290, "upgrade": 30, "totalvalue": 1800, "store_price": 9000,  "special": False},
    {"name": "Celestial", "value": 3000, "upgrade": 0,  "totalvalue": 3000, "store_price": 21000, "special": False},  # is that a madeline celeste reference????
]

# Indices of the non-special tiers in pack_data, in tier order
# (Wooden ... Celestial). Used by _pack_coin_ratio to interpolate the
# coin-variant ratio from Wooden (PACK_COIN_RATIO_WOODEN, most coin-heavy)
# down to Celestial (PACK_COIN_RATIO_CELESTIAL, least coin-heavy).
_NORMAL_PACK_INDICES = [i for i, p in enumerate(pack_data) if not p["special"]]


def _pack_coin_ratio(level_idx: int) -> float:
    """Linear interp of the coin half of a coin-variant pack open, by tier.
    Wooden returns PACK_COIN_RATIO_WOODEN; Celestial returns
    PACK_COIN_RATIO_CELESTIAL; intermediate tiers are linear between.
    Returns 0 for special tiers (they're never eligible for the variant),
    so a coin_variant flag rolled True will silently no-op if a cascade
    somehow lands on one."""
    try:
        pos = _NORMAL_PACK_INDICES.index(level_idx)
    except ValueError:
        return 0.0
    n = len(_NORMAL_PACK_INDICES)
    if n <= 1:
        return PACK_COIN_RATIO_WOODEN
    t = pos / (n - 1)
    return PACK_COIN_RATIO_WOODEN + (PACK_COIN_RATIO_CELESTIAL - PACK_COIN_RATIO_WOODEN) * t


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
    "Cat Bot is open source! <https://github.com/sneezeparty/catbot7>",
    "View all cats and rarities with /catalogue",
    "/catslots has an eGirl bonus round. yes that's a real sentence",
    "Unlike the normal one, Cat's /8ball isn't rigged",
    "/rate says /rate is 100% correct",
    "/casino is *surely* not rigged",
    "You probably shouldn't use a Discord bot for /remind-ers",
    "catbot7 is a fork. the seventh cat is the friendliest cat",
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
    "/catstore: launder cats into coins, no questions asked",
    "Cat Bot was initially made for only one server",
    "Cat Bot is made in Python with discord.py",
    "Looking at Cat's code won't make you regret your life choices!",
    "Cats aren't shared between servers to make it more fair and fun",
    "Cat Bot can go offline! Don't panic if it does",
    "By default, cats spawn 1-10 minutes apart",
    "View the last catch as well as the next one with /last",
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

with open("config/store.json", "r", encoding="utf-8") as f:
    config.store = json.load(f)

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
# Passive XP granted to the prism OWNER when their prism boosts a catch for
# a different player (self-boosts grant nothing). Sourced from tuning.json
# with a literal default so existing tuning files keep working unchanged.
PRISM_OWNER_XP_PER_BOOST = int(config.tuning.get("prism_owner_xp_per_boost", 20))
# Grace window (seconds) during which a recent /jobs commit protects the
# player's mafia (catnip) level from BOTH decay systems — the catnip bounty
# deadline and respect decay. Do a job within this window and a lapsed catnip
# timer won't drop your level; go longer than this with no job and decay
# resumes. Sourced from tuning.json with a literal default so old tuning files
# keep working. See _job_grace_active.
CATNIP_JOB_GRACE_SECONDS = int(config.tuning.get("catnip_job_grace_hours", 24)) * 3600
COIN_PER_PACK = config.tuning["coin_per_pack"]
MAIN_LOOP_INTERVAL = config.tuning["main_loop_interval_seconds"]
SPAWN_REVIVAL_INTERVAL = config.tuning.get("spawn_revival_interval_seconds", 60)
SEASON_ANNOUNCE_INTERVAL = config.tuning.get("season_announce_interval_seconds", 3600)
ANTI_DOUBLE_CATCH_COOLDOWN = config.tuning["anti_double_catch_cooldown_seconds"]
FAST_CATCHER_THRESHOLD = config.tuning["fast_catcher_threshold_seconds"]
SLOW_CATCHER_THRESHOLD = config.tuning["slow_catcher_threshold_seconds"]
# Bonus cats 🎁 (upstream "june update", solo variant): coefficient for the
# rarity-scaled bonus-cat chance (0.02 ≈ 3.7% Fine → 22% eGirl). Setting it
# to 0 disables bonus cats entirely — this is the kill switch. Sourced from
# tuning.json with literal defaults so existing tuning files keep working.
BONUS_CAT_CHANCE_COEF = float(config.tuning.get("bonus_cat_chance_coef", 0.02))
BONUS_MINIGAME_DEADLINE_SECONDS = int(config.tuning.get("bonus_minigame_deadline_seconds", 30))
# Battlepass overflow ("Extra Rewards") past the last season level: XP cost per
# extra level and its reward. "Mystery" resolves at grant time to a random
# non-special pack weighted by 1/totalvalue (commons much more likely).
# 3000 rather than upstream's 2000 because this fork also grants a bonus pack
# on every level — 2000 would roughly quadruple the post-cap pack faucet.
EXTRA_LEVEL_XP = int(config.tuning.get("extra_level_xp", 3000))
EXTRA_LEVEL_REWARD = str(config.tuning.get("extra_level_reward", "Mystery"))
# Mystery outcome table 🎁: what a "Mystery" battlepass reward resolves to.
# double_chance is a separate pre-roll (so it's EXACTLY that probability and
# nested rolls can simply skip it); weights are relative within one roll;
# sub-tier dicts map "value" -> weight. BALANCE RULE: the max XP tier (doubled)
# must stay below the cheapest Mystery-bearing level cost (2,500 XP at S2+ L31)
# or an XP outcome could chain levels faster than it costs them.
MYSTERY_OUTCOMES = config.tuning.get("mystery_outcomes", {})
MYSTERY_DOUBLE_CHANCE = float(MYSTERY_OUTCOMES.get("double_chance", 0.05))
MYSTERY_WEIGHTS = MYSTERY_OUTCOMES.get("weights", {"pack": 72, "rain": 9, "coins": 7, "xp": 7.5, "voucher": 3, "scratchcard": 1.5})
MYSTERY_RAIN_TIERS = MYSTERY_OUTCOMES.get("rain_seconds", {"15": 60, "30": 30, "60": 10})
MYSTERY_COIN_TIERS = MYSTERY_OUTCOMES.get("coins", {"500": 60, "1000": 24, "2000": 11, "2500": 5})
MYSTERY_XP_TIERS = MYSTERY_OUTCOMES.get("xp", {"250": 60, "500": 30, "1000": 10})
MYSTERY_VOUCHER_TIERS = MYSTERY_OUTCOMES.get("vouchers", {"double_pack": 60, "bounty_skip": 32, "egirl_bonus": 8})
MYSTERY_EGIRL_TIER = int(MYSTERY_OUTCOMES.get("egirl_bonus_tier", 3))
# Weekly quest 🍀 fixed reward: XP + /scratch cards per completion. Fixed
# (never perk-scaled or weekend-doubled) — it's the marquee weekly payout.
WEEKLY_QUEST_XP = int(config.tuning.get("weekly_quest_xp", 2000))
WEEKLY_QUEST_SCRATCHCARDS = int(config.tuning.get("weekly_quest_scratchcards", 1))
PACK_DROP_CHANCE_ON_CATCH = config.tuning["pack_drop_chance_on_catch"]
PACK_TIER_WEIGHTS = config.tuning["pack_tier_weights"]
# Pack "coin crate" variant — about half of opens (per coin flip) pay part
# cats + part coins instead of all cats. Total worth stays equal; the
# coin/cat split scales by tier (Wooden most coin-heavy, Celestial least).
PACK_COIN_VARIANT_CHANCE = config.tuning.get("pack_coin_variant_chance", 0.5)
PACK_COIN_RATIO_WOODEN = config.tuning.get("pack_coin_ratio_wooden", 0.5)
PACK_COIN_RATIO_CELESTIAL = config.tuning.get("pack_coin_ratio_celestial", 0.2)
STOCK_MARKET = config.tuning.get("stock_market", {"enabled": False})
# Stock-market v2 named tunables. Re-read on every reload so cat!restart picks
# up changes to config/tuning.json without a process restart. Defaults are
# conservative — sigma values are per-tick log-return σ, not per-day.
STOCK_SPREAD = float(STOCK_MARKET.get("spread", 0.02))
STOCK_PRICE_FLOOR = int(STOCK_MARKET.get("price_floor", 5))
STOCK_PRICE_CEILING = int(STOCK_MARKET.get("price_ceiling", 500))
STOCK_SIGMA_SECTOR = float(STOCK_MARKET.get("sigma_sector", 0.006))
STOCK_SIGMA_MARKET = float(STOCK_MARKET.get("sigma_market", 0.004))
STOCK_MEAN_REVERSION_LAMBDA = float(STOCK_MARKET.get("mean_reversion_lambda", 0.005))
STOCK_EARNINGS_INTERVAL = int(STOCK_MARKET.get("earnings_interval_seconds", 259200))
STOCK_EARNINGS_JITTER = float(STOCK_MARKET.get("earnings_jitter_pct", 0.25))
STOCK_EARNINGS_ANNOUNCE_LEAD = int(STOCK_MARKET.get("earnings_announce_lead_seconds", 86400))
STOCK_SIGMA_EARNINGS = float(STOCK_MARKET.get("sigma_earnings", 0.08))
STOCK_SURPRISE_CHANCE = float(STOCK_MARKET.get("surprise_chance_per_tick", 0.005))
STOCK_SIGMA_SURPRISE = float(STOCK_MARKET.get("sigma_surprise", 0.04))
STOCK_CRASH_CHANCE = float(STOCK_MARKET.get("crash_chance_per_tick", 0.00025))
STOCK_CRASH_IMPULSE_RANGE = tuple(STOCK_MARKET.get("crash_impulse_range", [-0.30, -0.12]))
STOCK_BOOM_CHANCE = float(STOCK_MARKET.get("boom_chance_per_tick", 0.00025))
STOCK_BOOM_IMPULSE_RANGE = tuple(STOCK_MARKET.get("boom_impulse_range", [0.12, 0.30]))
STOCK_DIVIDEND_EX_DIV_IMPULSE = float(STOCK_MARKET.get("dividend_ex_div_impulse", -0.015))

# Per-rarity season gate: rarities listed here only spawn when the current
# season is >= the value. Used to defer a new rarity's debut to a specific
# season without code changes. Rarities not listed are unrestricted.
RARITY_MIN_SEASON: dict[str, int] = config.tuning.get("rarity_min_season", {})

# Starting coin allowance granted at every season rollover. Replaces the
# old "wipe to 0" behavior so players begin each season with a small
# stipend. Players who had earned more than this in the brief window
# between the wipe and the next interaction keep the higher balance.
SEASON_STARTING_COINS = int(config.tuning.get("season_starting_coins", 100))

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

with open("rickroll.txt") as f:
    rickroll_list = [line for line in f.read().split("\n") if line]

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

# top.gg vote URL for this bot
TOP_GG_VOTE_URL = "https://top.gg/bot/1503024098412855458/vote"

# catbot7 support / hangout discord invite — surfaced as an occasional
# button on catch confirmations.
CAT_DISCORD_INVITE = "https://discord.com/invite/GAv9umz5RB"

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

# per-channel reaction-add cooldown (seconds since last add_reaction in that channel)
# discord enforces a per-channel reaction bucket of ~1 PUT per 250ms; a chatty channel
# triggering several easter-egg reactions back-to-back hits it and discord.py logspams 429
# retries. this skips the add_reaction call (silently) when we've reacted in the same
# channel within the last second. react_count / reactions_ratelimit still increment, so
# the "silly" achievement and per-guild cap are unaffected.
reaction_cooldown = {}
REACTION_COOLDOWN_S = 1.0


def _reaction_cooldown_ok(channel_id):
    now = time.time()
    if now - reaction_cooldown.get(channel_id, 0) < REACTION_COOLDOWN_S:
        return False
    reaction_cooldown[channel_id] = now
    return True

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
catslots_lock = []
# In-memory store of the last bet (lines, per_line) each player placed at
# /catslots, keyed by user_id+guild_id. Powers the "Spin Again" button and
# pre-fills the "Change Bet" modal. Resets on bot restart — that's fine.
catslots_last_bet: dict[int, tuple[int, int]] = {}
# Admin-set "force the next spin to trigger an eGirl bonus" queue. Keyed by
# user_id+guild_id, value is the number of eGirls to force (3/4/5). Single
# use: the entry is popped the moment the spin reads it. Module-scope so it
# survives /catstore-style command teardowns but not bot restarts.
catslots_force_bonus_users: dict[int, int] = {}

# ???
rigged_users = []


# /catslots — 5x3 grid, weighted reels, 20 paylines, multi-line payouts.
# Independent from /slots (different stat columns, different aches).
CATSLOTS_SYMBOLS = ["Fine", "8bit", "Corrupt", "Professor", "Divine", "Real", "Ultimate", "eGirl"]
# Variety retune 2026-05-22: Fine weight dropped 55→38, mid-tier weights
# bumped to make 8bit/Corrupt/Professor wins genuinely visible. Player
# complaint was "every win is Fine" — at the old weights P(c0=Fine) was
# 60%, so ~98% of winning lines were Fine 3OAK/5OAK. New distribution
# has Fine at ~42% and mid-tier wins appear in ~18% of winning spins.
# Total RTP target unchanged at ~97% (verified Monte Carlo). eGirl
# weight stays at 3 so bonus trigger rate is unchanged (~1 in 83).
CATSLOTS_WEIGHTS = [38, 14, 11, 9, 8, 5, 3, 3]
CATSLOTS_ALLOWED_LINES = [1, 5, 9, 20]
# Per-line bet cap. Total bet is capped implicitly at
# max(CATSLOTS_ALLOWED_LINES) * CATSLOTS_MAX_PER_LINE (= 2,000 coins by default).
# Keeps the eGirl 5-of-a-kind jackpot bounded so a single lucky spin can't
# obliterate the economy on a small self-hosted instance.
CATSLOTS_MAX_PER_LINE = 100
CATSLOTS_PAYLINES = [
    # Line 1: middle row (also the rigged-win line)
    [(0, 1), (1, 1), (2, 1), (3, 1), (4, 1)],
    # Line 2: top row
    [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)],
    # Line 3: bottom row
    [(0, 2), (1, 2), (2, 2), (3, 2), (4, 2)],
    # Line 4: V-down
    [(0, 0), (1, 1), (2, 2), (3, 1), (4, 0)],
    # Line 5: V-up
    [(0, 2), (1, 1), (2, 0), (3, 1), (4, 2)],
    # Lines 6-9 (zigzags)
    [(0, 0), (1, 0), (2, 1), (3, 2), (4, 2)],
    [(0, 2), (1, 2), (2, 1), (3, 0), (4, 0)],
    [(0, 1), (1, 0), (2, 1), (3, 2), (4, 1)],
    [(0, 1), (1, 2), (2, 1), (3, 0), (4, 1)],
    # Lines 10-20 (additional Vegas-style patterns)
    [(0, 0), (1, 1), (2, 1), (3, 1), (4, 0)],
    [(0, 2), (1, 1), (2, 1), (3, 1), (4, 2)],
    [(0, 1), (1, 0), (2, 0), (3, 0), (4, 1)],
    [(0, 1), (1, 2), (2, 2), (3, 2), (4, 1)],
    [(0, 0), (1, 0), (2, 1), (3, 0), (4, 0)],
    [(0, 2), (1, 2), (2, 1), (3, 2), (4, 2)],
    [(0, 1), (1, 1), (2, 0), (3, 1), (4, 1)],
    [(0, 1), (1, 1), (2, 2), (3, 1), (4, 1)],
    [(0, 0), (1, 1), (2, 0), (3, 1), (4, 0)],
    [(0, 2), (1, 1), (2, 2), (3, 1), (4, 2)],
    [(0, 0), (1, 2), (2, 0), (3, 2), (4, 0)],
]
CATSLOTS_PAYOUTS = {
    # Variety retune 2026-05-22. The third retune got the RTP math right
    # but left Fine at weight 55, so ~98% of winning spins were Fine-only
    # — visually monotonous despite mathematically correct payouts. This
    # retune drops Fine weight to 38 and bumps mid-tier weights/payouts
    # so 8bit/Corrupt/Professor wins land in ~18% of winning spins. To
    # preserve total RTP at ~97% we scaled mid/high-tier payouts up ~50-
    # 60% (since their per-spin probability went up but their share of
    # total RTP still trails Fine's). Verified by 300k-spin Monte Carlo:
    # base 73%, bonus +25pp = total ~97%. The price is a lower base-game
    # win rate (~54% vs the old 81%) — winning spins are rarer but more
    # interesting when they hit.
    "Fine":      {3: 1,     4: 4,       5: 11},
    "8bit":      {3: 20,    4: 100,     5: 450},
    "Corrupt":   {3: 26,    4: 130,     5: 650},
    "Professor": {3: 50,    4: 250,     5: 1150},
    "Divine":    {3: 100,   4: 500,     5: 1950},
    "Real":      {3: 200,   4: 1000,    5: 4000},
    "Ultimate":  {3: 400,   4: 2000,    5: 8000},
    # eGirl base-game payouts are paid via straight match. Same payout
    # ladder as Real to keep the bonus-tier symbol meaningful in the
    # base game without making it the dominant draw.
    "eGirl":     {3: 200,   4: 1000,    5: 4000},
}

# eGirl Party bonus round. 3+ eGirls anywhere on the 5×3 settled grid
# triggers free spins with a multiplier and frozen sticky eGirls. Total
# RTP target after the third retune: base ~80% + bonus ~14pp = ~94%.
# See docs/design/economy.md for the Monte Carlo verification.
CATSLOTS_BONUS_TRIGGERS = {
    # Third retune 2026-05-22: multipliers are now fractional. The bonus
    # eval no longer does wild substitution (see the loop near the bottom
    # of catslots), so the multiplier knob does what you'd intuitively
    # expect. 6+ eGirls falls through to the 5-entry via min(5, count).
    3: {"spins": 5,  "multiplier": 1.25},
    4: {"spins": 7,  "multiplier": 1.5},
    5: {"spins": 10, "multiplier": 2.0},
}
CATSLOTS_BONUS_RETRIGGER_THRESHOLD = 3   # newly-landed eGirls in a single bonus spin
CATSLOTS_BONUS_RETRIGGER_REWARD = 5      # extra spins added on retrigger
CATSLOTS_BONUS_COLOR_OPENING = 0xFFD700  # gold
CATSLOTS_BONUS_COLOR_PARTY = 0xFF1493    # hot pink

# Bonus payout floor — minimum coins the bonus must pay, expressed as a
# multiple of the triggering spin's total bet. Without this, low-bet
# bonuses round down to near-zero and the bonus animation feels
# punishingly disproportionate to the payout. With the floor every bonus
# trigger feels like a real win. RTP impact pushes total from ~95% to
# ~101% (verified Monte Carlo) — the slot is now effectively break-even
# to slightly player-favorable on average, which is the right call for
# a closed-economy fun-game bot.
CATSLOTS_BONUS_FLOORS = {
    3: 5,   # tier 3 (5 spins × 1.25): floor =  5× bet
    4: 10,  # tier 4 (7 spins × 1.5):  floor = 10× bet
    5: 25,  # tier 5 (10 spins × 2):   floor = 25× bet
}

# Bonus-round opening animation: spells out E-G-I-R-L then B-O-N-U-S one
# letter at a time using a 5×5 emoji bitmap per letter. Total runtime
# ~13s. Tune via the *_DELAY constants below — they're the only knobs.
BONUS_INTRO_SPARKLE_DELAY = 0.4
BONUS_INTRO_LETTER_DELAY = 0.8
BONUS_INTRO_PAUSE_DELAY = 1.0
BONUS_INTRO_REVEAL_DELAY = 2.0
BONUS_INTRO_STARTING_DELAY = 0.8
LETTER_SHAPES = {
    "E": ["#####", "#....", "###..", "#....", "#####"],
    "G": [".####", "#....", "#.###", "#...#", ".###."],
    "I": ["#####", "..#..", "..#..", "..#..", "#####"],
    "R": ["####.", "#...#", "####.", "#..#.", "#...#"],
    "L": ["#....", "#....", "#....", "#....", "#####"],
    "B": ["####.", "#...#", "####.", "#...#", "####."],
    "O": [".###.", "#...#", "#...#", "#...#", ".###."],
    "N": ["#...#", "##..#", "#.#.#", "#..##", "#...#"],
    "U": ["#...#", "#...#", "#...#", "#...#", ".###."],
    "S": [".####", "#....", ".###.", "....#", "####."],
}


def _catslots_render_letter(letter: str) -> str:
    """Render a single bonus-intro letter as a 5-row emoji block."""
    egirl = get_emoji("egirlcat")
    blank = get_emoji("empty")
    rows = LETTER_SHAPES[letter]
    return "\n".join("".join(egirl if c == "#" else blank for c in row) for row in rows)


# WELCOME TO THE TEMP_.._STORAGE HELL

# to prevent double catches
temp_catches_storage = []

# to prevent double spawns
temp_spawns_storage = []

# to prevent double belated battlepass progress and for "faster than 10 seconds" belated bp quest
temp_belated_storage = {}

# (guild_id, user_id) tuples of people currently /fish-ing. Reset every
# background_loop as a leak guard; active sessions re-add themselves.
fish_lock = []

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

# Season-end warning dedup: the season number we last broadcast a "season
# ends tomorrow" warning for. Persisted to disk so a restart on the last day
# of the month doesn't re-spam every channel; re-read on each cat!restart
# (module reimport) so it survives reloads.
try:
    with open("season_warn.txt", "r", encoding="utf-8") as f:
        last_season_warned = int(f.read().strip())
except (FileNotFoundError, ValueError):
    last_season_warned = -1

# Same idea for the season-recap leaderboard broadcast: records the just-ended
# season we last posted a recap for, so the 1st-of-the-month broadcast fires at
# most once per season even across restarts.
try:
    with open("season_recap.txt", "r", encoding="utf-8") as f:
        last_season_recapped = int(f.read().strip())
except (FileNotFoundError, ValueError):
    last_season_recapped = -1

# Season-intro dedup: records the new season we last broadcast a "Season N starts
# now" greeting for. Fires once per season on the 1st (right after the recap).
try:
    with open("season_intro.txt", "r", encoding="utf-8") as f:
        last_season_introed = int(f.read().strip())
except (FileNotFoundError, ValueError):
    last_season_introed = -1

# d.py doesnt cache app emojis so we do it on our own yippe
emojis = {}
# Coalesces concurrent fetch_application_emojis() calls — on_connect and
# on_ready both want this populated, and without a lock they race the global
# being empty and both hit Discord's tight per-route bucket → 429 + 3s retry.
_emojis_lock = asyncio.Lock()

# for mentioning it in catch message, will be auto-fetched in on_ready()
RAIN_ID = 1270470307102195752
PLUSH_ID = 0

# for dev commands, this is fetched in on_ready (auto-set to the Discord
# application's owner/team-owner). The fallback below is only live during the
# few-second window between startup and on_ready firing — and is sourced from
# config.OWNER_ID (env var `owner_id`) so no one else's ID is hardcoded.
OWNER_ID = config.OWNER_ID

# for funny stats, you can probably edit background_loop to restart every X of them
loop_count = 0

# loops in dpy can randomly break, i check if is been over X minutes since last loop to restart it
last_loop_time = 0


def get_emoji(name):
    global emojis
    # Accept Discord-style :shortcode: syntax (so :cat: from the News editor
    # resolves to 🐱). Try unicode-shortcode first; if no match, drop the
    # colons and fall through to the app-emoji / unicode lookups below so
    # ":goldpack:" still resolves to the uploaded `goldpack` app emoji.
    if isinstance(name, str) and len(name) >= 3 and name.startswith(":") and name.endswith(":"):
        unicode_try = emoji.emojize(name, language="alias")
        if unicode_try != name:
            return unicode_try
        name = name[1:-1]
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


def fish_emoji(cattype_name):
    # prefer the uploaded <type>fish app emoji; fall back to the cat emoji so
    # missing fish art (e.g. the fork-only Baby/Shadow/Terminator types, which
    # upstream never drew) doesn't render as 🔳.
    name = cattype_name.lower() + "fish"
    if name in emojis:
        return emojis[name]
    return get_emoji(cattype_name.lower() + "cat")


def get_news():
    """The Cat Bot Times articles, read fresh from config/news.json each call so
    the webui News editor's changes go live without a restart. Order = index =
    news_id (read-state in user.news_state is positional). Returns [] on any
    read/parse error (e.g. a transient mid-write read; the webui writes
    atomically so this is just belt-and-suspenders)."""
    try:
        with open("config/news.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        articles = data.get("articles", [])
        return articles if isinstance(articles, list) else []
    except Exception:
        return []


def render_news_body(text: str) -> str:
    """Substitute [[emoji_name]] tokens in an article body with get_emoji(name).
    Everything else is passed through as Discord markdown."""
    return re.sub(r"\[\[(\w+)\]\]", lambda m: get_emoji(m.group(1)), text or "")


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
async def update_daily_catch_streak(user: User, profile: Profile | None = None) -> bool:
    """Bumps the global daily catch streak. Returns True iff this is the
    first catch of today.

    `profile` is the per-server Profile of the catching player and is used
    ONLY to check the streak_protector job perk — when active, a skipped
    day extends the streak instead of resetting it. Caller may omit it
    (preserves pre-perks behavior)."""
    today = int(time.time() // 86400)
    if user.last_catch_day == today:
        return False
    if user.last_catch_day == today - 1:
        user.daily_catch_streak += 1
    else:
        # Skipped a day. streak_protector absorbs the gap — extend the
        # streak by 1 as if the player had caught yesterday.
        if profile is not None and "streak_protector" in _perks_active_ids(profile):
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


async def get_stock_bid(ticker: str) -> int:
    """Price a market sell fills at. Mid * (1 - spread/2), with a 1-coin
    minimum spread so a market buy/sell round-trip always costs at least 2."""
    mid = await get_stock_price(ticker)
    bid = max(STOCK_PRICE_FLOOR, round(mid * (1 - STOCK_SPREAD / 2)))
    if bid >= mid:
        bid = max(STOCK_PRICE_FLOOR, mid - 1)
    return bid


async def get_stock_ask(ticker: str) -> int:
    """Price a market buy fills at. Mid * (1 + spread/2), with a 1-coin
    minimum spread."""
    mid = await get_stock_price(ticker)
    ask = min(STOCK_PRICE_CEILING, round(mid * (1 + STOCK_SPREAD / 2)))
    if ask <= mid:
        ask = min(STOCK_PRICE_CEILING, mid + 1)
    return ask


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
    [floor, ceiling] from config. Always returns a positive int >= 1.

    In stock v2 this is the long-run anchor that the mean-reversion term in
    `_run_stock_tick` pulls toward — it is NOT the displayed price. Day-to-day
    movement is dominated by drift + ticker/sector/market shocks + events;
    mean reversion (λ ≈ 0.005) is only loud enough to matter over weeks.
    """
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
# Stock price simulation engine (stock market v2)
# ---------------------------------------------------------------------------
# A single `_run_stock_tick()` runs from background_loop every MAIN_LOOP_INTERVAL
# seconds. Each tick produces one log-return per ticker:
#
#   log_return = drift
#              + ticker_shock                    # N(0, sigma_ticker), per-ticker
#              + sector_beta * sector_shock      # N(0, sigma_sector), shared in sector
#              + market_beta * market_shock      # N(0, sigma_market), shared across all
#              + mean_reversion                  # -lambda * log(price / fair_value)
#              + event_impulse                   # 0 unless newsevent fires this tick
#
# Events live in the `newsevent` table. Earnings are pre-scheduled and applied
# once `fires_at <= now`; surprise/crash/boom roll inline and apply this tick;
# dividend ex-div impulses are written by `wait_and_do_stock` and consumed on
# the next tick. After applying impulses we mark the consumed rows applied=true.

def _ticker_cfg(ticker: str) -> dict:
    return STOCK_MARKET.get("tickers", {}).get(ticker, {})


async def _schedule_earnings_if_needed(ticker: str, now: int) -> None:
    """Ensure each ticker has at most one unapplied earnings row in the
    future. If none exists, schedule the next one — written with `time` set
    to the announce moment so the news feed (which filters `time <= now`)
    only surfaces it once we're within the announce window."""
    existing = await NewsEvent.get_or_none(ticker=ticker, event_type="earnings", applied=False)
    if existing:
        return
    jitter = 1.0 + random.uniform(-STOCK_EARNINGS_JITTER, STOCK_EARNINGS_JITTER)
    fires_at = now + int(STOCK_EARNINGS_INTERVAL * jitter)
    announce_at = max(now, fires_at - STOCK_EARNINGS_ANNOUNCE_LEAD)
    # Pre-write the announce headline; we relabel it on fire.
    headline = stock_news.pick_headline(ticker, "earnings_announced", 0.0)
    await NewsEvent.create(
        time=announce_at,
        fires_at=fires_at,
        ticker=ticker,
        event_type="earnings",
        headline=headline,
        impulse_pct=0.0,
        applied=False,
    )


async def _roll_surprise(ticker: str, now: int) -> None:
    """Per-tick, per-ticker surprise roll. Hits write an applied=true row
    immediately — the impulse is consumed in the same tick by the sweep."""
    if random.random() >= STOCK_SURPRISE_CHANCE:
        return
    impulse = random.gauss(0, STOCK_SIGMA_SURPRISE)
    headline = stock_news.pick_headline(ticker, "surprise", impulse)
    await NewsEvent.create(
        time=now,
        fires_at=now,
        ticker=ticker,
        event_type="surprise",
        headline=headline,
        impulse_pct=float(impulse),
        applied=False,
    )


async def _roll_crash_or_boom(now: int) -> None:
    """One roll per tick for each of crash and boom; both can independently
    fire in the same tick (it's a market, things happen). Market-wide rows
    have ticker=NULL."""
    if random.random() < STOCK_CRASH_CHANCE:
        lo, hi = STOCK_CRASH_IMPULSE_RANGE
        impulse = random.uniform(lo, hi)
        await NewsEvent.create(
            time=now,
            fires_at=now,
            ticker=None,
            event_type="crash",
            headline=stock_news.pick_headline(None, "crash", impulse),
            impulse_pct=float(impulse),
            applied=False,
        )
    if random.random() < STOCK_BOOM_CHANCE:
        lo, hi = STOCK_BOOM_IMPULSE_RANGE
        impulse = random.uniform(lo, hi)
        await NewsEvent.create(
            time=now,
            fires_at=now,
            ticker=None,
            event_type="boom",
            headline=stock_news.pick_headline(None, "boom", impulse),
            impulse_pct=float(impulse),
            applied=False,
        )


async def _consume_due_events(tickers: list[str], now: int) -> dict[str, float]:
    """Aggregate every unapplied event row with `fires_at <= now` into a
    per-ticker impulse, marking each row applied=true (and finalising earnings
    impulse_pct / headline on fire). Market-wide rows (ticker IS NULL) apply
    to all tickers — they are marked applied=true ONCE, not per-ticker.

    Returns {ticker: sum_of_log_return_impulses}.
    """
    impulses: dict[str, float] = {t: 0.0 for t in tickers}

    rows = await pool.fetch(
        "SELECT id, ticker, event_type, impulse_pct FROM newsevent "
        "WHERE applied = false AND fires_at <= $1",
        now,
    )

    for row in rows:
        evt_id = row["id"]
        evt_ticker = row["ticker"]
        evt_type = row["event_type"]
        impulse = float(row["impulse_pct"] or 0.0)

        if evt_type == "earnings" and impulse == 0.0:
            # Fire-time draw for earnings: pick the magnitude now and update
            # the row's headline to the realised-direction template.
            impulse = random.gauss(0, STOCK_SIGMA_EARNINGS)
            new_headline = stock_news.pick_headline(evt_ticker, "earnings", impulse)
            await pool.execute(
                "UPDATE newsevent SET applied = true, impulse_pct = $1, "
                "headline = $2, time = $3 WHERE id = $4",
                float(impulse), new_headline, now, evt_id,
            )
        else:
            await pool.execute(
                "UPDATE newsevent SET applied = true WHERE id = $1",
                evt_id,
            )

        if evt_ticker is None:
            for t in tickers:
                impulses[t] = impulses.get(t, 0.0) + impulse
        elif evt_ticker in impulses:
            impulses[evt_ticker] = impulses.get(evt_ticker, 0.0) + impulse

    return impulses


async def _run_stock_tick() -> None:
    """One simulation step. Idempotent-ish — re-running it just produces more
    pricehistory rows; the background_loop guard keeps it on its own cadence."""
    if not STOCK_MARKET.get("enabled"):
        return

    tickers = [s["ticker"] for s in stock_data]
    if not tickers:
        return

    now = int(time.time())

    # 1) ensure each ticker has a future scheduled earnings event
    for ticker in tickers:
        try:
            await _schedule_earnings_if_needed(ticker, now)
        except Exception:
            logging.exception("earnings scheduling failed for %s", ticker)

    # 2) roll per-ticker surprise and one market-wide crash/boom pair
    for ticker in tickers:
        try:
            await _roll_surprise(ticker, now)
        except Exception:
            logging.exception("surprise roll failed for %s", ticker)
    try:
        await _roll_crash_or_boom(now)
    except Exception:
        logging.exception("crash/boom roll failed")

    # 3) consume every due event into per-ticker impulses
    try:
        event_impulses = await _consume_due_events(tickers, now)
    except Exception:
        logging.exception("event consumption failed")
        event_impulses = {t: 0.0 for t in tickers}

    # 4) draw the per-tick market shock and one shock per sector
    market_shock = random.gauss(0, STOCK_SIGMA_MARKET)
    sectors_seen: set[str] = set()
    for ticker in tickers:
        sec = _ticker_cfg(ticker).get("sector")
        if sec:
            sectors_seen.add(sec)
    sector_shocks = {s: random.gauss(0, STOCK_SIGMA_SECTOR) for s in sectors_seen}

    # 5) per-ticker formula → new price, append to pricehistory
    for ticker in tickers:
        try:
            cfg = _ticker_cfg(ticker)
            drift = float(cfg.get("drift", 0.0))
            sigma_t = float(cfg.get("sigma_ticker", 0.01))
            sector = cfg.get("sector")
            sector_beta = float(cfg.get("sector_beta", 1.0))
            market_beta = float(cfg.get("market_beta", 1.0))

            current_price = await get_stock_price(ticker)
            fair = await _compute_fair_price(ticker)

            ticker_shock = random.gauss(0, sigma_t)
            sec_component = sector_beta * sector_shocks.get(sector, 0.0) if sector else 0.0
            mkt_component = market_beta * market_shock

            try:
                reversion = -STOCK_MEAN_REVERSION_LAMBDA * math.log(current_price / max(1, fair))
            except (ValueError, ZeroDivisionError):
                reversion = 0.0

            event_imp = event_impulses.get(ticker, 0.0)

            log_return = drift + ticker_shock + sec_component + mkt_component + reversion + event_imp

            try:
                raw = current_price * math.exp(log_return)
            except OverflowError:
                raw = STOCK_PRICE_CEILING

            new_price = max(STOCK_PRICE_FLOOR, min(STOCK_PRICE_CEILING, int(round(raw))))
            # Guarantee at least 1 — defense against any tuning that floors to 0.
            new_price = max(1, new_price)

            await PriceHistory.create(ticker=ticker, price=new_price, time=now)
            temp_stock_prices[ticker] = new_price
            logging.debug(
                "stock tick %s: %d → %d (log_r=%.4f event=%.4f)",
                ticker, current_price, new_price, log_return, event_imp,
            )

            # Now the bid/ask have moved with the price. Any resting limit
            # order that the new spread has crossed gets filled against the
            # house. Wrapped per-ticker so one bad row can't poison sibling
            # tickers' sweeps.
            try:
                await _sweep_crossed_limits(ticker)
            except Exception:
                logging.exception("limit sweep failed for %s", ticker)
        except Exception:
            logging.exception("stock tick failed for %s", ticker)


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


def _battlepass_level_info(profile):
    """Current battlepass level label, XP progress, and XP cap for a profile.

    Mirrors the /battlepass + /inventory logic, including the "Extra Rewards"
    fallback (EXTRA_LEVEL_XP per level, no season cap) once a player passes the
    last defined level of their season. Returns (label, progress, cap, season_max);
    season_max is None in Extra Rewards mode or if the season data is missing."""
    try:
        season_levels = config.battle["seasons"][str(profile.season)]
        if profile.battlepass >= len(season_levels):
            return ("Extra Rewards", profile.progress, EXTRA_LEVEL_XP, None)
        season_max = len(season_levels)
        cap = season_levels[profile.battlepass]["xp"]
        return (f"Level {profile.battlepass + 1}/{season_max}", profile.progress, cap, season_max)
    except Exception:
        return (f"Level {profile.battlepass + 1}", profile.progress, EXTRA_LEVEL_XP, None)


# Catstore-scoped multiplier on top of cat_value(). Doubles every price the
# store quotes (buy AND sell). Trades, gifts, and job reward valuations keep
# the underlying cat_value scale — the multiplier only applies via this
# helper, which is the only thing /catstore code uses for face-value math.
CATSTORE_PRICE_MULTIPLIER = 2


def _catstore_tier_mult(cat_type: str) -> float:
    """Per-rarity multiplier on top of the base catstore face value. Rare
    tiers (Divine and above) cost meaningfully more so a single eGirl isn't
    a sub-day purchase for a maxed mafia player. Lives in tuning.json so it
    can be tuned without code edits. Defaults to 1.0 for rarities that
    aren't in the table."""
    table = config.tuning.get("catstore_tier_mult", {}) if hasattr(config, "tuning") else {}
    return float(table.get(cat_type, 1.0) or 1.0)


def catstore_face_value(cat_type: str) -> int:
    """Catstore's notion of face value: cat_value * CATSTORE_PRICE_MULTIPLIER
    * per-rarity tuning multiplier. All store-side pricing (buy, sell,
    discount/cut displays) routes through this — change the multiplier
    here to rescale the whole storefront. Sell prices automatically follow
    because store_sell_price is a percentage of this."""
    base = cat_value(cat_type) * CATSTORE_PRICE_MULTIPLIER
    return int(base * _catstore_tier_mult(cat_type))


def store_discount_pct(catnip_level: int, perk_bonus: int = 0) -> int:
    """Cat Mafia store discount for the given catnip level. Negative numbers
    are a tax (Newbie/Lurker get charged extra), positive numbers are a real
    discount (Boss+ saves on every purchase). Defaults to 0 if a level entry
    is missing the key (e.g. someone retiring a level without updating the
    store_discount config — better to charge face value than crash).

    `perk_bonus` adds the catstore_discount_stack (job-perk) bonus on top of
    the catnip-level discount. Callers in /catstore look it up via
    _perks_catstore_buy_bonus(profile); other callers may pass 0."""
    try:
        level_data = catnip_list["levels"][catnip_level]
    except (IndexError, KeyError):
        return int(perk_bonus)
    return int(level_data.get("store_discount", 0)) + int(perk_bonus)


def store_buy_price(cat_type: str, catnip_level: int, perk_buy_bonus: int = 0) -> int:
    """Coins to buy one cat. Discount applies multiplicatively then ceils so a
    1-coin floor: ceil(value * (1 - discount/100)). Sell price is intentionally
    NOT routed through here — sell is always at face value (see store_sell_price).

    `perk_buy_bonus` stacks the catstore_discount_stack perk additively onto
    the catnip-level discount before pricing."""
    value = catstore_face_value(cat_type)
    discount = store_discount_pct(catnip_level, perk_buy_bonus)
    price = math.ceil(value * (1 - discount / 100))
    return max(1, int(price))


def store_sell_pct(catnip_level: int, perk_sell_bonus: int = 0) -> int:
    """What fraction of face value the mafia pays out on a sell, as a percent.
    The "natural" curve is 50% at Newbie + 5% per level (so El Patrón would
    sell at 100% face) — but the natural curve crosses the buy curve at Lv7
    and beyond, which would create a buy<face<sell arbitrage loop. We cap
    sell at `buy_pct - 5` so the round-trip stays at least 5 percentage
    points negative at every level. Practical effect: sell tops out around
    65% face at El Patrón rather than the named 100%.

    `perk_sell_bonus` adds the catstore_sell_premium perk pp on top of the
    natural curve — the buy_pct-5 cap still applies so round-trips remain
    negative even with the perk active (this is the spec's anti-arbitrage
    guarantee)."""
    natural = 50 + max(0, catnip_level) * 5 + int(perk_sell_bonus)
    # Cap is computed against the player's catnip-only buy discount, NOT
    # against any buy-side perk bonus. Otherwise catstore_discount_stack and
    # catstore_sell_premium would compound and flip the spread positive.
    buy_pct = 100 - store_discount_pct(catnip_level)
    return min(natural, buy_pct - 5)


def store_sell_price(cat_type: str, catnip_level: int, perk_sell_bonus: int = 0) -> int:
    """Coins received per cat sold. Scales with mafia level: a Newbie only
    gets 50% of face value back, El Patrón gets the full 100%. The asymmetry
    with the buy discount is intentional — sell ceiling is 100% face while
    buy floor is 70% face at max mafia, so round-trips always net negative."""
    value = catstore_face_value(cat_type)
    pct = store_sell_pct(catnip_level, perk_sell_bonus)
    return max(1, value * pct // 100)


def _ordinal(n: int) -> str:
    """1 → '1st', 2 → '2nd', 11 → '11th', etc. Small helper, used for prism
    craft messages and probably anywhere else humans want to count."""
    n = int(n)
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# Prism craft coin tax. Each player's Nth prism craft costs base * growth^N
# (N = how many they've crafted before, on this server). Caps at `cap` so the
# late game still has a ceiling. Per-PROFILE (not per-server, not per-user
# globally) so the user's progress on each server stays independent and a
# returning player doesn't get punished by other server members' activity.
def prism_craft_coin_cost(prisms_crafted: int) -> int:
    cfg = config.tuning.get("prism_craft_coin_cost", {}) if hasattr(config, "tuning") else {}
    base = int(cfg.get("base", 5000) or 5000)
    growth = float(cfg.get("growth", 2) or 2)
    cap = int(cfg.get("cap", 320000) or 320000)
    first = int(cfg.get("first", 1000) or 1000)
    n = max(0, int(prisms_crafted or 0))
    if n == 0:
        # First-ever prism on this server is heavily discounted to lower the
        # entry barrier; the escalating base×growth^n schedule kicks in from
        # the second craft onward.
        return first
    cost = base * (growth ** n)
    return min(cap, int(cost))


# Rain purchase (catstore Extras → Rain). The 2026-05-23 retune dropped
# the price ~75% (12,000 → 3,000 per minute) and switched from "fires
# immediately in this channel" to "adds 1 minute to your rain inventory,
# trigger with /rain when you're ready". Exponential daily scaling
# preserved so a single player can't flood a channel by mass-buying.
RAIN_BASE_PRICE = 3_000         # coins, before mafia adjustment
RAIN_SCALE = 1.5                # multiplier per minute bought today
RAIN_BLOCK_MINUTES = 1          # rain_minutes added to user inventory per buy


def rain_block_price(blocks_bought_today: int, mafia_discount_pct: int) -> int:
    """Cost in coins of the NEXT rain block — block (N+1) where
    N = blocks_bought_today. Applies the same mafia discount/tax convention
    as `store_buy_price()`: positive discount lowers price, negative raises."""
    raw = RAIN_BASE_PRICE * (RAIN_SCALE ** blocks_bought_today)
    adjusted = raw * (1 - mafia_discount_pct / 100)
    return max(1, int(round(adjusted)))


def _rain_blocks_today(profile: Profile) -> int:
    """Lazy UTC-daily reset. Returns the number of blocks the player has
    bought so far today, treating any stored counter from a previous UTC
    date as 0. Pure read — does NOT mutate the profile."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    last = getattr(profile, "rain_blocks_last_date", None) or ""
    if last != today:
        return 0
    return int(getattr(profile, "rain_blocks_bought_today", 0) or 0)


# Pack purchase (catstore Extras → Packs). Stone through Celestial are sold
# at their `store_price` (with mafia discount/tax). Wooden is intentionally
# excluded — /stocks already provides a coins↔Wooden exchange and selling
# Wooden here would duplicate that path with no economic benefit. The
# `special` tag on Halloween/Christmas/etc. packs keeps them out of the
# regular catalog.
# Round-trip note: pre-rebalance, store_price == totalvalue so buy-then-
# /stocks-deposit was a net-zero round-trip. After the high-tier rebalance,
# store_price > totalvalue for Silver+, which means a Celestial bought from
# the store and immediately deposited via /stocks LOSES coins
# (21,000 paid - 3,000 returned). This asymmetry is intentional — store-
# bought packs are meant to be opened, not flipped, and the rebalance is
# what makes top-tier packs aspirational.
CATSTORE_PACK_TIERS = ("Stone", "Bronze", "Silver", "Gold", "Platinum", "Diamond", "Celestial")


def pack_buy_price(pack_name: str, mafia_discount_pct: int) -> int:
    """Coins to buy one pack of the given tier from /catstore. Wooden is
    rejected — it's handled by /stocks and intentionally not sold here.
    Special-event packs (Christmas/Halloween/etc.) are also rejected.

    Uses pack["store_price"] if present, else falls back to pack["totalvalue"].
    The fallback keeps old pack_data entries (and any future tiers) working
    without an explicit store_price field."""
    if pack_name == "Wooden":
        raise ValueError("Wooden packs are sold via /stocks, not /catstore")
    pack = next((p for p in pack_data if p["name"] == pack_name), None)
    if not pack or pack.get("special"):
        raise ValueError(f"Unknown or non-purchasable pack: {pack_name}")
    raw = int(pack.get("store_price", pack["totalvalue"]))
    adjusted = raw * (1 - mafia_discount_pct / 100)
    # ceil to keep a 1-coin floor even with extreme discounts.
    return max(1, math.ceil(adjusted))


async def mark_pack_tier_purchased(profile: Profile, pack_name: str) -> None:
    """Record that this player has bought at least one pack of this tier from
    /catstore. Backs the `catstore_pack_collector` achievement. Idempotent.
    Wooden never enters this set (it isn't sold in /catstore)."""
    if pack_name == "Wooden" or pack_name not in CATSTORE_PACK_TIERS:
        return
    tiers = _coerce_array(profile.store_purchased_pack_tiers)
    if pack_name in tiers:
        return
    profile.store_purchased_pack_tiers = tiers + [pack_name]
    await profile.save()


# ---------------------------------------------------------------------------
# Jobs / Mafia Killings — Phase 1 helpers (offer-board generation, read-only).
# Commit/resolve paths land in Phase 2; rep + heat math wires up in Phases 3-4.
# ---------------------------------------------------------------------------

JOBS_OFFER_REFRESH = JOBS_TUNING["offer_refresh_window_seconds"]
JOBS_MAX_SLOTS = JOBS_TUNING["max_concurrent_offers"]
# Pagination size for /jobs board display. JOBS_MAX_SLOTS controls how many
# offers the bot generates per window; this controls how many are shown per
# page in the board view. Smaller-than-max → board paginates with Prev/Next.
JOBS_BOARD_PAGE_SIZE = 3
# Paid board reroll (coin-cost sibling of the reroll_board perk). Price =
# max(min, catnip_level * per_level), escalating x N within a 12h window.
JOBS_REROLL_PRICE_PER_LEVEL = JOBS_TUNING.get("reroll_price_per_level", 500)
JOBS_REROLL_PRICE_MIN = JOBS_TUNING.get("reroll_price_min", 1000)


def _jobs_window_index(now: int) -> int:
    """Hard global window. All players share the same boundary every 12h
    (offer_refresh_window_seconds in jobs.json tuning) —
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
    if heat > JOBS_HEAT_SCRUTINY_FLOOR:
        return 1.25
    if heat > JOBS_HEAT_WATCHING_FLOOR:
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


def _jobs_template_id(window_idx: int, slot_idx: int, npc: str, tier: int, extra_salt: str = "") -> str:
    base = f"w{window_idx}:s{slot_idx}:{npc}:t{tier}"
    return f"{base}:r{extra_salt}" if extra_salt else base


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


def _jobs_generate_offers(profile: Profile, window_idx: int, user_season: int = 0, extra_salt: str = "") -> list[dict]:
    """Returns a list of 0/1/3 offer dicts ready for DB insert. Deterministic
    in (user_id, guild_id, window_idx, extra_salt) — passing an extra_salt
    (used by reroll_board) yields a different set with salt-tagged template_ids.

    Each offer now carries a pre-rolled `perk_drop` (perk_id string, or "" if
    no perk rolled). Rolled with an independent RNG stream keyed
    `perk:{slot_idx}:{extra_salt}` so future tuning of the perk pool does NOT
    ripple into difficulty/reward determinism."""
    level = int(getattr(profile, "catnip_level", 0) or 0)
    rep = _jobs_faction_rep(profile)
    current_heat = int(getattr(profile, "heat", 0) or 0)
    user_id = int(profile.user_id)
    guild_id = int(profile.guild_id)

    def _perk_rng_for(slot_idx_int: int) -> random.Random:
        perk_salt = f"perk:{slot_idx_int}:{extra_salt}" if extra_salt else f"perk:{slot_idx_int}"
        return _jobs_seed_rng(user_id, guild_id, window_idx, salt=perk_salt)

    if level < 2:
        return []

    if level < 4:
        salt = f"tutorial:{extra_salt}" if extra_salt else "tutorial"
        rng = _jobs_seed_rng(user_id, guild_id, window_idx, salt=salt)
        offer = _jobs_build_tutorial_offer(rng, current_heat)
        offer["_slot_idx"] = 0
        offer["_template_id"] = _jobs_template_id(window_idx, 0, offer["offered_by"], offer["tier"], extra_salt)
        offer["perk_drop"] = _perks_roll_drop(offer["offered_by"], int(offer["tier"]), _perk_rng_for(0), mafia_level=level) or ""
        return [offer]

    out = []
    big_score_eligible = _jobs_big_score_available(profile, user_season)
    for slot_idx in range(JOBS_MAX_SLOTS):
        salt = f"slot:{slot_idx}:{extra_salt}" if extra_salt else f"slot:{slot_idx}"
        rng = _jobs_seed_rng(user_id, guild_id, window_idx, salt=salt)
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
        picked["_template_id"] = _jobs_template_id(window_idx, slot_idx, picked["offered_by"], picked["tier"], extra_salt)
        picked["perk_drop"] = _perks_roll_drop(picked["offered_by"], int(picked["tier"]), _perk_rng_for(slot_idx), mafia_level=level) or ""
        out.append(picked)
    return out


async def _jobs_refresh_offers_if_needed(profile: Profile, now: int) -> list:
    """SELECT-then-INSERT idempotent refresh. Returns the JobInstance rows for
    the current window, sorted by slot_idx encoded in template_id.

    If the player has already rerolled this window (existing rows fill the
    slot count), we skip regeneration — otherwise the baseline templates
    would be re-added on top of the rerolled ones."""
    window_idx = _jobs_window_index(now)
    win_start, win_end = _jobs_window_bounds(window_idx)
    existing = await JobInstance.collect(
        "user_id = $1 AND guild_id = $2 AND state = 'offered' AND offered_at >= $3 AND offered_at < $4",
        int(profile.user_id),
        int(profile.guild_id),
        win_start,
        win_end,
    )
    if len(existing) >= JOBS_MAX_SLOTS:
        # All slots already filled (often via a reroll_board fire). Don't
        # try to top up with baseline templates — that would overfill.
        all_rows = list(existing)
        def _slot_key2(row):
            try:
                return int(row.template_id.split(":")[1][1:])
            except Exception:
                return 0
        all_rows.sort(key=_slot_key2)
        return all_rows
    # Dedup against ALL templates already used in this window (any state),
    # not just currently-offered. Otherwise a resolved/declined/expired job
    # would let _jobs_generate_offers re-emit the same template and pop a
    # duplicate offer back onto the board within the same window. Slots
    # empty out as the player works through them; window rollover refills.
    used_rows = await JobInstance.collect(
        "user_id = $1 AND guild_id = $2 AND offered_at >= $3 AND offered_at < $4",
        int(profile.user_id),
        int(profile.guild_id),
        win_start,
        win_end,
    )
    existing_templates = {row.template_id for row in used_rows}

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
            perk_drop=offer.get("perk_drop", ""),
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


async def _jobs_do_reroll(profile: Profile, now: int) -> bool:
    """Delete this window's `offered` rows and regenerate a fresh set with a
    time-based salt (so the new board diverges from the deterministic baseline).
    Shared by the free reroll_board perk and the paid /jobs + /catstore rerolls.

    Does NOT charge anything or save the profile — the caller deducts coins /
    consumes the perk charge and saves. Returns True on success. The
    `len(existing) >= JOBS_MAX_SLOTS` guard in _jobs_refresh_offers_if_needed
    makes the regenerated board "stick" for the rest of the window."""
    window_idx = _jobs_window_index(now)
    win_start, win_end = _jobs_window_bounds(window_idx)
    try:
        async with transaction() as conn:
            # Offered rows are pre-acceptance; anything mid-send has state != 'offered'.
            await conn.execute(
                "DELETE FROM jobinstance WHERE user_id = $1 AND guild_id = $2 "
                "AND state = 'offered' AND offered_at >= $3 AND offered_at < $4",
                int(profile.user_id), int(profile.guild_id), win_start, win_end,
            )
            try:
                season = int(profile.season or 0)
            except KeyError:
                season = 0
            desired = _jobs_generate_offers(profile, window_idx, user_season=season, extra_salt=f"{now}")
            for offer in desired:
                await JobInstance.create(
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
                    perk_drop=offer.get("perk_drop", ""),
                )
        return True
    except Exception:
        logging.exception("jobs reroll transaction failed")
        return False


def _jobs_reroll_count(profile: Profile, now: int) -> int:
    """Paid rerolls already done in the current 12h window. Lazily resets to 0
    once the window rolls over. Returns 0 (no escalation) if the counter columns
    don't exist yet (migration 023 unrun)."""
    widx = _jobs_window_index(now)
    try:
        stored_idx = int(profile.job_rerolls_window_idx or 0)
        cnt = int(profile.job_rerolls_window or 0)
    except (KeyError, AttributeError):
        return 0
    return cnt if stored_idx == widx else 0


def _jobs_reroll_price(profile: Profile, now: int) -> int:
    """Coin price of the player's NEXT paid board reroll: base * (rerolls_this_window + 1),
    base = max(reroll_price_min, catnip_level * reroll_price_per_level)."""
    base = max(JOBS_REROLL_PRICE_MIN, int(getattr(profile, "catnip_level", 0) or 0) * JOBS_REROLL_PRICE_PER_LEVEL)
    return base * (_jobs_reroll_count(profile, now) + 1)


def _jobs_reroll_charge(profile: Profile, now: int, price: int) -> None:
    """Deduct the paid-reroll price and bump the per-window escalation counter.
    Counter write is migration-guarded; coins always exist. Caller saves."""
    profile.coins = int(profile.coins or 0) - price
    widx = _jobs_window_index(now)
    cnt = _jobs_reroll_count(profile, now)
    try:
        profile.job_rerolls_window = cnt + 1
        profile.job_rerolls_window_idx = widx
    except (KeyError, AttributeError):
        pass


def _jobs_reward_summary(reward: dict) -> str:
    coins = int(reward.get("coins", 0))
    cats = reward.get("cats", {}) or {}
    pack = reward.get("pack")
    parts = []
    if coins:
        parts.append(f"🪙 {coins:,}")
    for t, c in cats.items():
        # Cat emojis are uploaded as "<lowercase_type>cat" (e.g. goodcat,
        # nicecat, egirlcat) — matches the convention used everywhere else
        # in the codebase. Without the "cat" suffix get_emoji falls through
        # to the 🔳 placeholder, which is what produced the empty squares.
        emoji = get_emoji(t.lower() + "cat") if t else ""
        parts.append(f"{c}× {emoji} {t}".strip())
    if pack:
        pack_emoji = get_emoji(f"{pack}pack") or "📦"
        parts.append(f"{pack_emoji} 1× {pack.title()} Pack")
    return "  ·  ".join(parts) if parts else "—"


# ---------------------------------------------------------------------------
# Jobs / Mafia Killings — Phase 2: send/commit/resolve.
# ---------------------------------------------------------------------------

JOBS_DIMINISHING_ALPHA = float(JOBS_TUNING.get("diminishing_returns_alpha", 0.75))
JOBS_COMMITS_PER_WINDOW = int(JOBS_TUNING.get("max_commits_per_window", JOBS_TUNING.get("max_commits_per_day", 3)))


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

# Jobs perks — third reward axis alongside coins/cats/packs. See _perks_*
# helpers near _jobs_perks_suspended for the runtime; pools/catalog stay
# empty in Phase 1 so live players see no behavior change.
JOBS_PERKS = config.jobs.get("perks", {})
PERKS_DROP_POOLS = JOBS_PERKS.get("drop_pools", {})
PERKS_CATALOG = JOBS_PERKS.get("catalog", {})
PERKS_DROP_CHANCE_BY_TIER = JOBS_PERKS.get("drop_chance_by_tier", {})
PERKS_MAX_ACTIVE = int(JOBS_PERKS.get("max_active", 5))

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
    if heat > JOBS_HEAT_SCRUTINY_FLOOR:
        return "scrutiny"
    if heat > JOBS_HEAT_WATCHING_FLOOR:
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
    swallows errors so an embed-post failure can't block the send screen.

    Includes the previewed perk (pre-rolled at offer-gen time) so the channel
    sees what the player is gambling for — Big Score and the rare T4 perks
    are public flexes."""
    if channel is None:
        return
    try:
        rng = random.Random(int(job.id or 0) ^ hash(player_mention))
        tier_info = JOBS_TIERS.get(str(job.tier), {})
        tier_name = tier_info.get("name", f"Tier {job.tier}")
        line = _jobs_format_accept_line(job, player_mention, rng)
        # Perk preview — appended to the description block so it reads as
        # part of the contract terms, not a separate field. Tolerant of
        # catalog drift (renders blank if the perk_id was removed).
        perk_id = (_jobs_col(job, "perk_drop", "") or "").strip()
        if perk_id:
            perk_cat = PERKS_CATALOG.get(perk_id)
            if perk_cat:
                perk_name = perk_cat.get("name", perk_id)
                strength = _perks_format_strength(perk_id, int(job.tier or 0))
                strength_suffix = f" {strength}" if strength else ""
                line = f"{line}\n🎁 Bonus on success: **{perk_name}**{strength_suffix}"
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
            embed.set_footer(text=f"🚓 Heat hit {JOBS_PINCH_THRESHOLD}. The Cat Police picked them up.")

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
    # crew_insurance (job perk, charge): convert a near_miss into a success
    # BEFORE the rest of the pipeline runs. Outer commit-site code re-credits
    # the entire send when outcome=="success", so flipping the outcome and
    # clearing cats_destroyed is sufficient. Stamp a marker for show_result.
    # Fires `lucky_strike` ach on the first successful conversion.
    crew_insurance_fired = False
    if outcome_dict["outcome"] == "near_miss":
        if "crew_insurance" in _perks_active_ids(profile) and _perks_consume_charge(profile, "crew_insurance"):
            outcome_dict["outcome"] = "success"
            outcome_dict["cats_destroyed"] = {}
            crew_insurance_fired = True
            profile.unlock_ach("lucky_strike")

    outcome = outcome_dict["outcome"]
    job.outcome = outcome
    job.roll = outcome_dict["roll"]
    job.success_chance = outcome_dict["success_chance"]
    job.cats_destroyed = outcome_dict["cats_destroyed"]

    # Heat — applies + may trigger the Pinch (Cat Police Station) at >=100.
    # heat_shield (job perk, charge): halves heat cost on this commit.
    prior_heat = int(getattr(profile, "heat", 0) or 0)
    prior_suspended = int(getattr(profile, "perks_suspended_until", 0) or 0)
    prior_big_score_season = int(getattr(profile, "big_score_season", -1) or -1)
    prior_big_score_wins = int(getattr(profile, "big_score_wins", 0) or 0)
    prior_big_score_perk = bool(getattr(profile, "big_score_perk_unlocked", False))
    heat_cost_eff = int(job.heat_cost or 0)
    heat_shield_fired = False
    if "heat_shield" in _perks_active_ids(profile) and _perks_consume_charge(profile, "heat_shield"):
        heat_cost_eff = heat_cost_eff // 2
        heat_shield_fired = True
    pinched = _jobs_apply_commit_heat(profile, heat_cost_eff, int(time.time()))

    # Rep — per-tier swing. Big Score uses fixed swings from JOBS_BIG_SCORE.
    rep = _jobs_faction_rep(profile)
    is_big_score = int(job.tier or 0) == 5
    rep_windfall_fired = False  # set inside the per-tier branch if it fires
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
        # rep_windfall (job perk, charge): doubles offerer rep gain on a success.
        if outcome == "success" and offerer_gain > 0:
            if "rep_windfall" in _perks_active_ids(profile) and _perks_consume_charge(profile, "rep_windfall"):
                offerer_gain *= 2
                rep_windfall_fired = True
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
        # Job-perk fire markers (shown on the result screen in Phase 3).
        "crew_insurance_fired": crew_insurance_fired,
        "heat_shield_fired": heat_shield_fired,
        "rep_windfall_fired": rep_windfall_fired,
    }

    # Big Score: regardless of outcome the season is consumed.
    reward = _jobs_coerce_dict(job.reward_snapshot)
    if is_big_score:
        season = int(reward.get("_season", 0) or 0)
        profile.big_score_season = season

    # Job-grace: any committed job (win, near-miss, or loss — "doing /jobs" is
    # engagement, not winning) shields the mafia level from catnip-deadline and
    # respect decay for CATNIP_JOB_GRACE_SECONDS. See _job_grace_active. No-ops
    # cleanly if migration 027 hasn't been applied.
    if _profile_has_last_job_time(profile):
        profile.last_job_time = int(time.time())

    # Lifetime counters + reward grant
    if outcome == "success":
        profile.jobs_completed = int(getattr(profile, "jobs_completed", 0) or 0) + 1
        coin_reward = int(reward.get("coins", 0) or 0)
        if coin_reward:
            profile.coins = int(getattr(profile, "coins", 0) or 0) + coin_reward
            _bump(profile, "coins_earned", coin_reward)
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
        # Respect: settle prior decay, then grant the tier-keyed bonus. Stamp
        # the result on job.rep_changes so the result screen can render
        # "+N Respect (now M/100)" without recomputing. No-ops cleanly when
        # migration 018 hasn't been applied yet (helper returns 0).
        respect_levels_lost = _respect_settle(profile, int(time.time()))
        if _profile_has_respect_columns(profile):
            respect_before = int(profile.respect or 0)
            respect_gain = _respect_grant_for_tier(int(job.tier or 0))
            if respect_gain > 0:
                cap = max(1, int(_respect_cfg().get("max", 100)))
                profile.respect = min(cap, respect_before + respect_gain)
                profile.respect_last_tick = int(time.time())
            respect_now = int(profile.respect or 0)
        else:
            respect_gain = 0
            respect_now = 0
        # Re-assign rep_changes so catpg sees the dirty change (mutating the
        # JSONB dict in-place would not trigger __setattr__ tracking).
        _rc = dict(job.rep_changes) if isinstance(job.rep_changes, dict) else {}
        _rc["respect_gain"] = int(respect_gain)
        _rc["respect_now"] = respect_now
        _rc["respect_levels_lost"] = int(respect_levels_lost)
        job.rep_changes = _rc
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
                _bump(profile, "coins_earned", consolation)
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

    # --- Extended job achievements (per-NPC firsts, crew flexes, etc.) ---

    # Per-NPC first success — one-time per (player, NPC). Six aches total.
    NPC_FIRST_ACHES = {
        "whiskers":  "whiskers_first",
        "lucian_jr": "lucian_jr_first",
        "jinx":      "jinx_first",
        "jeremy":    "jeremy_first",
        "lucian_sr": "lucian_sr_first",
        "sofia":     "sofia_first",
    }
    if outcome == "success" and job.offered_by in NPC_FIRST_ACHES:
        profile.unlock_ach(NPC_FIRST_ACHES[job.offered_by])

    # First total wipe (the spiritual partner of first_job, which fires on
    # first SUCCESS). Triggers when jobs_failed has just been bumped to 1.
    if outcome == "total_failure" and int(getattr(profile, "jobs_failed", 0) or 0) == 1:
        profile.unlock_ach("first_failure")

    # Milestone: 10 successful jobs.
    if outcome == "success" and int(getattr(profile, "jobs_completed", 0) or 0) >= 10:
        profile.unlock_ach("wise_guy")

    # Crew composition (outcome-independent — these fire on any commit). The
    # send_snapshot was set right before this function was called.
    send_snap = _jobs_coerce_dict(job.send_snapshot)
    total_sent = sum(int(c or 0) for c in send_snap.values())
    distinct_rarities = sum(1 for c in send_snap.values() if int(c or 0) > 0)

    if total_sent >= 100:
        profile.unlock_ach("heavy_crew")
    if distinct_rarities >= 5:
        profile.unlock_ach("diverse_crew")
    if any(t in LEGENDARY_PLUS for t, c in send_snap.items() if int(c or 0) > 0):
        profile.unlock_ach("bringing_the_big_guns")
    if int(send_snap.get("eGirl", 0) or 0) > 0:
        profile.unlock_ach("top_shelf")

    # Lone wolf — succeed on a 1-cat send.
    if outcome == "success" and total_sent == 1:
        profile.unlock_ach("lone_wolf")

    # Stone cold — committed at exactly 0 heat (pre-application; prior_heat
    # was captured at the top of this function before _jobs_apply_commit_heat).
    if prior_heat == 0:
        profile.unlock_ach("stone_cold")

    # Complication-driven aches.
    comp_id = (_jobs_col(job, "complication", "") or "").strip()
    if comp_id:
        profile.unlock_ach("first_complication")
        if comp_id == "easy_mark":
            profile.unlock_ach("easy_money")

    # Perk drop (third reward axis). The roll happened at offer-generation
    # time and is persisted on job.perk_drop — read it here, don't reroll.
    # Successes only; near-miss/wipe surface the "bonus walks" line on the
    # result screen (see show_result). Try/except so a perk grant failure
    # cannot roll back the job resolution above.
    pre_rolled = (_jobs_col(job, "perk_drop", "") or "").strip()
    if outcome == "success" and pre_rolled:
        try:
            if pre_rolled in RESOLVING_PERKS:
                fired = await _perks_resolve_immediate(profile, pre_rolled, npc=job.offered_by, tier=int(job.tier or 0))
            else:
                fired = bool(_perks_grant(profile, pre_rolled, npc=job.offered_by, tier=int(job.tier or 0)))
            if fired:
                rc = _jobs_coerce_dict(job.rep_changes)
                rc["perk_drop"] = pre_rolled
                job.rep_changes = rc
        except Exception:
            logging.exception("jobs: perk grant failed; continuing without")


async def _jobs_commits_this_window(user_id: int, guild_id: int, now: int) -> int:
    """Count commits inside the current offer-refresh window (per-server).
    The window also gates the commit cap, so this is the same boundary used
    by both the offer pool and the cap counter — one timer, one source of
    truth. Cancelled commits zero out `committed_at`, so they aren't counted
    (the cap doesn't punish misclicks)."""
    win_start, _ = _jobs_window_bounds(_jobs_window_index(now))
    return int(await JobInstance.count(
        "user_id = $1 AND guild_id = $2 AND committed_at >= $3 AND state IN ('resolved', 'committed')",
        int(user_id), int(guild_id), win_start,
    ) or 0)


# ---------------------------------------------------------------------------
# Phase 4: heat decay + Cat Police Station pinch.
# ---------------------------------------------------------------------------

JOBS_PINCH_THRESHOLD = JOBS_TUNING.get("pinch_threshold", 100)
JOBS_PINCH_LOCKOUT = JOBS_TUNING.get("pinch_lockout_seconds", 43200)
JOBS_PINCH_RESET = JOBS_TUNING.get("pinch_reset_heat", 30)
JOBS_HEAT_DECAY_PER_HOUR = JOBS_TUNING.get("heat_decay_per_hour", 2)
# Heat band cutoffs derived from the pinch threshold so the heat bar, color
# bands, "scrutiny" cost ramp, and complication tiers all scale together when
# the threshold is retuned. At the default 100 these are 30/70 — the original
# hardcoded values — so behavior is unchanged except by the threshold itself.
JOBS_HEAT_WATCHING_FLOOR = int(JOBS_PINCH_THRESHOLD * 0.3)
JOBS_HEAT_SCRUTINY_FLOOR = int(JOBS_PINCH_THRESHOLD * 0.7)


def _jobs_catnip_active(profile: Profile, now: int | None = None) -> bool:
    now = now if now is not None else int(time.time())
    return int(getattr(profile, "catnip_active", 0) or 0) > now


def _jobs_apply_heat_decay(profile: Profile, now: int | None = None) -> int:
    """Lazy decay. -2 heat per hour since last decay, paused while catnip is
    active. Returns the (possibly updated) heat value. Caller still has to
    save profile if anything else changed.

    cooling_off (job perk, timed): doubles the per-hour decay rate AND
    bypasses the catnip-pauses-decay rule. NB: this function is called
    cross-context (jobs board, /catnip, etc.); we read perks here directly."""
    now = now if now is not None else int(time.time())
    last = int(getattr(profile, "heat_last_decay", 0) or 0)
    current = int(getattr(profile, "heat", 0) or 0)
    cooling_off_active = "cooling_off" in _perks_active_ids(profile, now)
    if last <= 0 or current <= 0:
        profile.heat_last_decay = now
        return current
    # Catnip pause-the-decay rule is bypassed by cooling_off.
    if _jobs_catnip_active(profile, now) and not cooling_off_active:
        profile.heat_last_decay = now
        return current
    hours = max(0.0, (now - last) / 3600.0)
    if hours <= 0:
        return current
    rate = JOBS_HEAT_DECAY_PER_HOUR
    if cooling_off_active:
        rate *= float(_perks_strength(profile, "cooling_off", "multiplier", 2.0) or 2.0)
    decay = int(rate * hours)
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


# ---------------------------------------------------------------------------
# Respect — mafia-decay meter that pressures players to keep doing jobs.
#
# State (per profile):
#   - profile.respect            (int 0..max, default 50)
#   - profile.respect_last_tick  (unix seconds; 0 = "never ticked")
#
# Rules:
#   - Passive: -decay_per_hour each hour since last_tick.
#   - Job completion grants tier-keyed +N respect, capped at max.
#   - At respect == 0, accumulating hours_at_zero_per_level_loss zero-hours
#     drops catnip_level by 1 (down to level_loss_floor) and resets respect
#     to level_loss_grace_respect ("the family gives you a chance").
#   - Store discount continues to track current catnip_level — no separate
#     discount-decay column, lost levels lose their discount too.
#
# Lazy-compute pattern: read/modify-time settle, no background task. last_tick
# is banked forward by the consumed hours so partial hours roll into the next
# call. Iterations are capped at 60 days; anyone gone longer effectively gets
# multiple settles spread across sessions, eventually reaching the floor.
# ---------------------------------------------------------------------------


def _respect_cfg() -> dict:
    return config.tuning.get("respect", {}) if hasattr(config, "tuning") else {}


def _respect_grant_for_tier(tier: int) -> int:
    """Respect gained for completing a job of this tier. Caller still has to
    clamp against max and save the profile."""
    cfg = _respect_cfg()
    table = cfg.get("job_reward", {}) or {}
    return int(table.get(str(int(tier)), 0) or 0)


def _profile_has_respect_columns(profile: Profile) -> bool:
    """True iff the profile row has the respect-meter columns populated.
    catpg's __getattr__ raises KeyError (not AttributeError) for missing
    columns, so `getattr(profile, "respect", default)` does NOT fall back —
    every read of a possibly-missing column has to be guarded. Used by
    every respect helper to no-op cleanly when the migration hasn't been
    applied yet, keeping /jobs and /catnip working in the meantime."""
    try:
        _ = profile.respect_last_tick
        _ = profile.respect
        return True
    except (KeyError, AttributeError):
        return False


def _safe_prisms_crafted(profile: Profile) -> int:
    """Return profile.prisms_crafted as an int, or 0 if the column doesn't
    exist (migration 018 unrun). Same KeyError-vs-AttributeError dance as
    _profile_has_respect_columns."""
    try:
        return int(profile.prisms_crafted or 0)
    except (KeyError, AttributeError):
        return 0


def _profile_has_last_job_time(profile: Profile) -> bool:
    """True iff the profile row has the last_job_time column (migration 027).
    Same KeyError-vs-AttributeError guard as the respect helpers — lets the
    job-grace feature no-op cleanly before the migration is applied."""
    try:
        _ = profile.last_job_time
        return True
    except (KeyError, AttributeError):
        return False


def _safe_last_job_time(profile: Profile) -> int:
    """profile.last_job_time as an int, or 0 if the column doesn't exist
    (migration 027 unrun)."""
    try:
        return int(profile.last_job_time or 0)
    except (KeyError, AttributeError):
        return 0


def _job_grace_active(profile: Profile, now: int | None = None) -> bool:
    """True while a recent /jobs commit shields the mafia (catnip) level from
    decay. Stamped on every committed job (any outcome) via last_job_time; the
    window is CATNIP_JOB_GRACE_SECONDS (24h by default). Returns False before
    migration 027 (no column → _safe_last_job_time gives 0)."""
    if CATNIP_JOB_GRACE_SECONDS <= 0:
        return False
    last = _safe_last_job_time(profile)
    if last <= 0:
        return False
    now = now if now is not None else int(time.time())
    return (now - last) < CATNIP_JOB_GRACE_SECONDS


def _prism_tax_enabled(profile: Profile) -> bool:
    """True iff the prisms_crafted column exists on this profile row. Used
    to gate the coin tax behavior so prism crafting still works pre-migration
    (skip the charge and the counter bump entirely)."""
    try:
        _ = profile.prisms_crafted
        return True
    except (KeyError, AttributeError):
        return False


def _respect_settle(profile: Profile, now: int | None = None) -> int:
    """Apply passive respect decay (and any resulting catnip_level losses)
    since the last tick. Returns the number of catnip levels lost during
    this call so the caller can surface a warning. Caller still has to
    save the profile.

    Safe to call from any read site; idempotent within the same hour. Skips
    the decay pass when respect_last_tick == 0 (newly migrated profiles or
    profiles that have never engaged with jobs) — we stamp the timestamp
    and bail, so the first interaction sets the baseline rather than
    retroactively punishing dormancy. Also no-ops cleanly if migration 018
    hasn't been applied yet (the columns don't exist on the row)."""
    cfg = _respect_cfg()
    if not cfg:
        return 0
    if not _profile_has_respect_columns(profile):
        return 0
    now = now if now is not None else int(time.time())
    last = int(profile.respect_last_tick or 0)
    if last <= 0:
        profile.respect_last_tick = now
        return 0
    elapsed = (now - last) // 3600
    if elapsed <= 0:
        return 0
    # 60-day per-call cap. Anyone gone longer will settle more on subsequent
    # calls; this keeps any single call O(1440) iterations max.
    elapsed = min(int(elapsed), 24 * 60)

    decay = max(0, int(cfg.get("decay_per_hour", 1)))
    hz_per_loss = max(1, int(cfg.get("hours_at_zero_per_level_loss", 6)))
    floor = max(0, int(cfg.get("level_loss_floor", 4)))
    grace = max(0, int(cfg.get("level_loss_grace_respect", 25)))
    cap = max(1, int(cfg.get("max", 100)))

    current = max(0, min(cap, int(profile.respect or 0)))
    cat_level = int(getattr(profile, "catnip_level", 0) or 0)
    levels_lost = 0
    hours_at_zero = 0

    # A recent /jobs commit shields the level from respect-driven loss too, so
    # a daily jobber is provably safe under both decay systems (without this, a
    # tiny tier-1-only daily job protects the catnip deadline but still lets
    # respect drift to the floor). The respect METER still decays normally
    # below — only the level strip is suppressed while grace is active.
    grace_active = _job_grace_active(profile, now)

    for _ in range(elapsed):
        if current > 0:
            current = max(0, current - decay)
            hours_at_zero = 0
        else:
            hours_at_zero += 1
            if not grace_active and hours_at_zero >= hz_per_loss and cat_level > floor:
                cat_level -= 1
                levels_lost += 1
                current = grace
                hours_at_zero = 0
            # else: protected by a recent job, at floor, or still accumulating

    profile.respect = current
    profile.catnip_level = cat_level
    profile.respect_last_tick = last + elapsed * 3600
    return levels_lost


def _respect_apply_job_grant(profile: Profile, tier: int, now: int | None = None) -> int:
    """Settle passive decay first, then add the tier-keyed bonus. Returns the
    new respect value. Caller still has to save the profile and surface any
    level loss returned by the settle pass. No-ops cleanly on profiles
    without the respect columns (migration 018 unrun)."""
    cfg = _respect_cfg()
    cap = max(1, int(cfg.get("max", 100)))
    _respect_settle(profile, now)
    if not _profile_has_respect_columns(profile):
        return 0
    bonus = _respect_grant_for_tier(int(tier))
    if bonus <= 0:
        return int(profile.respect or 0)
    new_val = min(cap, int(profile.respect or 0) + bonus)
    profile.respect = new_val
    profile.respect_last_tick = now if now is not None else int(time.time())
    return new_val


# ---------------------------------------------------------------------------
# Jobs perks — buffs/consumables dropped by NPCs on successful jobs.
#
# Storage shape (profile.job_perks, JSONB list):
#   [{"id": str, "granted_at": int, "expires_at": int,
#     "npc": str, "tier": int, "charges": int}, ...]
# - expires_at == 0 means non-timed (charge-based only).
# - charges    == 0 means non-charge-based (timed only).
# - A perk with both fields populated expires when EITHER hits zero.
#
# IMPORTANT: these perks are NOT suspended by perks_suspended_until — that
# flag only gates catnip perks. Mafia-reward perks were earned, so they keep
# firing through the Pinch. This asymmetry is intentional; see the design
# doc and the catnip side at line ~4341.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Cat Bot Store helpers — Discord native monetization (SKUs + Entitlements).
# user.entitlements is a JSONB list of SKU id strings the user currently holds
# an active entitlement for. user.premium is derived: true iff any held SKU is
# `kind == "supporter"` in config/store.json. All helpers no-op gracefully when
# STORE_ENABLED is off so we don't trash existing data.
# ---------------------------------------------------------------------------


def _user_entitlements_load(user: User) -> list[str]:
    """Returns a fresh list of currently-held SKU id strings. Falls back to []
    on any malformed value so callers never see a non-list type."""
    raw = _coerce_array(getattr(user, "entitlements", None) or [])
    return [str(sku) for sku in raw if sku is not None]


def _user_has_sku(user: User, sku_id: str) -> bool:
    return str(sku_id) in _user_entitlements_load(user)


def _supporter_sku_ids() -> set[str]:
    """SKU ids in config/store.json with kind == 'supporter'. Empty set is the
    common case before any SKUs are configured."""
    try:
        skus = config.store.get("skus") or []
    except Exception:
        return set()
    return {str(s["id"]) for s in skus if s.get("kind") == "supporter" and s.get("id")}


def _store_sku_by_id(sku_id: str) -> dict | None:
    try:
        skus = config.store.get("skus") or []
    except Exception:
        return None
    for s in skus:
        if str(s.get("id")) == str(sku_id):
            return s
    return None


def _recompute_premium(user: User) -> bool:
    """Recompute user.premium from current entitlements. Sets the value on the
    User object (caller is responsible for save()). Returns the new value."""
    supporter_ids = _supporter_sku_ids()
    held = set(_user_entitlements_load(user))
    new_value = bool(held & supporter_ids)
    user.premium = new_value
    return new_value


async def _apply_entitlement_create(entitlement) -> None:
    """Record that `entitlement.user_id` now holds `entitlement.sku_id` and
    refresh premium accordingly. Idempotent — running twice on the same SKU
    is a no-op. Fires the store_first_purchase + store_supporter aches on the
    relevant transitions."""
    if not config.STORE_ENABLED:
        return
    user_id = int(getattr(entitlement, "user_id", 0) or 0)
    sku_id = str(getattr(entitlement, "sku_id", "") or "")
    if not user_id or not sku_id:
        logging.warning("entitlement_create missing user_id or sku_id: %r", entitlement)
        return

    sku_meta = _store_sku_by_id(sku_id)
    sku_type = (sku_meta or {}).get("type", "")

    # Consumable SKUs: log + TODO for cosmetic-grant wiring. Don't store them
    # as entitlements since they expire on consume.
    if sku_type == "consumable":
        logging.info("consumable SKU received user=%s sku=%s — TODO: wire cosmetic grant", user_id, sku_id)
        try:
            await entitlement.consume()
        except Exception:
            logging.exception("entitlement.consume() failed for user=%s sku=%s", user_id, sku_id)
        return

    user = await User.get_or_create(user_id=user_id)
    held = _user_entitlements_load(user)
    if sku_id in held:
        return  # idempotent — already recorded

    user.entitlements = held + [sku_id]
    was_premium = bool(getattr(user, "premium", False))
    is_premium = _recompute_premium(user)
    await user.save()

    # Aches fire after the DB write so a read of has_ach() reflects truth.
    # entitlement events arrive without an Interaction or Message handle, so
    # we use achemb with "send" via a synthetic shim — instead, just unlock
    # silently and skip the celebratory embed (there's no channel context).
    try:
        if not user.unlock_ach("store_first_purchase"):
            pass  # already unlocked
        await user.save()
        if is_premium and not was_premium:
            user.unlock_ach("store_supporter")
            await user.save()
    except Exception:
        logging.exception("store achievement unlock failed for user=%s sku=%s", user_id, sku_id)


async def _apply_entitlement_delete(entitlement) -> None:
    """Remove `entitlement.sku_id` from the user's held set and recompute
    premium. Idempotent — running on a SKU the user no longer holds is a
    no-op."""
    if not config.STORE_ENABLED:
        return
    user_id = int(getattr(entitlement, "user_id", 0) or 0)
    sku_id = str(getattr(entitlement, "sku_id", "") or "")
    if not user_id or not sku_id:
        return

    user = await User.get_or_none(user_id=user_id)
    if user is None:
        return
    held = _user_entitlements_load(user)
    if sku_id not in held:
        return  # idempotent
    user.entitlements = [s for s in held if s != sku_id]
    _recompute_premium(user)
    await user.save()


def _perks_load(profile: Profile) -> list[dict]:
    """Safe read. Returns a fresh list (caller may mutate freely). Falls back
    to [] if the column doesn't exist yet (pre-migration-010)."""
    raw = _jobs_col(profile, "job_perks", [])
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) or []
        except (ValueError, TypeError):
            raw = []
    if not isinstance(raw, list):
        return []
    return [dict(e) for e in raw if isinstance(e, dict)]


def _perks_save(profile: Profile, perks: list[dict]) -> None:
    """Assigns profile.job_perks. Caller is responsible for save()."""
    profile.job_perks = list(perks)


# ---------------------------------------------------------------------------
# Vouchers 🎟️ — one-shot effects granted by battlepass Mystery rewards.
# Stored on profile.vouchers (JSONB list, migration 035) as
# {"id": "double_pack" | "egirl_bonus" | "bounty_skip", "granted_at": int}.
# No expiry, no charges: consuming = removing the first matching entry.
# Stacking is allowed (each grant appends, each trigger consumes one).
# Wiped at season rollover (they're pack-adjacent value; packs wipe too).

VOUCHER_LABELS = {
    "double_pack": ("🎟️", "Double Pack", "your next pack opens with doubled contents"),
    "egirl_bonus": ("🎰", "eGirl Bonus", "your next /catslots spin triggers the eGirl bonus round"),
    "bounty_skip": ("🐾", "Bounty Skip", "your next catch autocompletes a catnip bounty"),
}


def _vouchers_load(profile: Profile) -> list[dict]:
    """Safe read. Returns a fresh list. [] if the column doesn't exist yet
    (pre-migration-035)."""
    raw = _jobs_col(profile, "vouchers", [])
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) or []
        except (ValueError, TypeError):
            raw = []
    if not isinstance(raw, list):
        return []
    return [dict(e) for e in raw if isinstance(e, dict)]


def _vouchers_save(profile: Profile, vouchers: list[dict]) -> None:
    """Assigns profile.vouchers. Caller is responsible for save()."""
    profile.vouchers = list(vouchers)


def _vouchers_has(profile: Profile, voucher_id: str) -> bool:
    return any(e.get("id") == voucher_id for e in _vouchers_load(profile))


def _vouchers_grant(profile: Profile, voucher_id: str) -> bool:
    """Append one voucher. Returns False (no-op) pre-migration-035 so a
    Mystery outcome can fall back to something grantable."""
    try:
        _ = profile.vouchers
    except (KeyError, AttributeError):
        return False
    vouchers = _vouchers_load(profile)
    vouchers.append({"id": voucher_id, "granted_at": int(time.time())})
    _vouchers_save(profile, vouchers)
    return True


def _vouchers_consume(profile: Profile, voucher_id: str) -> bool:
    """Remove the first matching voucher. Returns True if one was spent.
    Caller is responsible for save()."""
    vouchers = _vouchers_load(profile)
    for i, e in enumerate(vouchers):
        if e.get("id") == voucher_id:
            vouchers.pop(i)
            _vouchers_save(profile, vouchers)
            return True
    return False


def _perks_is_active(entry: dict, now: int) -> bool:
    """Internal: True iff this perk row should remain in the active list."""
    exp = int(entry.get("expires_at", 0) or 0)
    ch  = int(entry.get("charges", 0) or 0)
    has_time   = exp > 0
    has_charge = ch  > 0
    if not has_time and not has_charge:
        return False
    if has_time and exp <= now:
        return False
    if has_charge and ch <= 0:
        return False
    return True


def _perks_prune(profile: Profile, now: int | None = None) -> list[dict]:
    """Drop expired-timed and zero-charge entries. Writes back. Idempotent —
    must be called at the top of every hook that reads perks."""
    now = now if now is not None else int(time.time())
    perks = _perks_load(profile)
    kept = [e for e in perks if _perks_is_active(e, now)]
    if len(kept) != len(perks):
        _perks_save(profile, kept)
    return kept


def _perks_active_ids(profile: Profile, now: int | None = None) -> set[str]:
    """Set of currently-active perk IDs after pruning."""
    return {e["id"] for e in _perks_prune(profile, now) if e.get("id")}


def _perks_get(profile: Profile, perk_id: str) -> dict | None:
    """Single active perk lookup. Returns the live dict from job_perks
    (mutations persist via _perks_save)."""
    for e in _perks_prune(profile):
        if e.get("id") == perk_id:
            return e
    return None


def _perks_tier_entry(perk_id: str, tier: int) -> dict:
    """Read perks.catalog[perk_id].tier_table[tier] with fallback to tier 2.
    Returns {} if the catalog entry is missing entirely."""
    cat = PERKS_CATALOG.get(perk_id) or {}
    table = cat.get("tier_table") or {}
    if not isinstance(table, dict):
        return {}
    entry = table.get(str(tier))
    if entry is None:
        entry = table.get(str(2))  # baseline fallback per spec
    if not isinstance(entry, dict):
        return {}
    return entry


def _perks_record_received(profile: Profile, perk_id: str) -> bool:
    """Append perk_id to profile.perks_received (dedup'd lifetime list).
    Returns True iff this is the first time the player has received this perk.
    Caller saves; tolerates pre-migration (no column) by returning False."""
    if not perk_id:
        return False
    try:
        raw = profile.perks_received
    except KeyError:
        return False  # pre-migration-011
    received = _coerce_array(raw)
    if perk_id in received:
        return False
    profile.perks_received = received + [perk_id]
    return True


def _perks_fire_grant_aches(profile: Profile, perk_id: str, tier: int, perks_after: list[dict]) -> list[str]:
    """Silent unlock for grant-time aches. Returns the list of ach IDs that
    just unlocked (caller may embed-fire them). Idempotent — repeated grants
    don't re-fire. perks_after is the post-grant list (for hoarder count)."""
    fired = []
    if profile.unlock_ach("first_perk"):
        fired.append("first_perk")
    # perk_collector: distinct received >= catalog size.
    try:
        received = set(_coerce_array(profile.perks_received))
    except KeyError:
        received = set()
    if PERKS_CATALOG and len(received) >= len(PERKS_CATALOG):
        if profile.unlock_ach("perk_collector"):
            fired.append("perk_collector")
    if len(perks_after) >= 5 and profile.unlock_ach("perk_hoarder"):
        fired.append("perk_hoarder")
    if int(tier) >= 5 and profile.unlock_ach("made_man_mafia"):
        fired.append("made_man_mafia")
    return fired


def _perks_grant(profile: Profile, perk_id: str, *, npc: str, tier: int,
                 now: int | None = None) -> dict | None:
    """Apply grant logic for a single perk.

    - Reads duration_seconds / charges from PERKS_CATALOG[perk_id].tier_table.
    - Refresh-or-extend: if the perk is already active, reset expires_at to
      now+duration and refill charges (not additive).
    - 5-perk cap (PERKS_MAX_ACTIVE): if a 6th distinct perk lands, the oldest
      TIMED active perk is evicted. Charge-based perks are sticky.
    - Returns the granted/refreshed entry dict, or None if the catalog has no
      entry for this perk at all (defensive — caller should already have
      validated via _perks_roll_drop).
    """
    cat = PERKS_CATALOG.get(perk_id)
    if not cat:
        return None
    tdata = _perks_tier_entry(perk_id, tier)
    if not tdata:
        return None

    now = now if now is not None else int(time.time())
    duration = int(tdata.get("duration_seconds", 0) or 0)
    charges  = int(tdata.get("charges", 0) or 0)
    if duration <= 0 and charges <= 0:
        # Catalog entry contributes nothing — skip rather than store a dead row.
        return None

    perks = _perks_prune(profile, now)
    expires_at = now + duration if duration > 0 else 0

    # Refresh-or-extend rule.
    for e in perks:
        if e.get("id") == perk_id:
            e["granted_at"] = now
            if duration > 0:
                e["expires_at"] = expires_at
            if charges > 0:
                e["charges"] = charges
            e["npc"]  = npc
            e["tier"] = int(tier)
            _perks_save(profile, perks)
            # Record + ach fire even on refresh (covers manual repeat-grants
            # in testing; first_perk is one-shot via unlock_ach idempotence).
            _perks_record_received(profile, perk_id)
            _perks_fire_grant_aches(profile, perk_id, tier, perks)
            return e

    new_entry = {
        "id": perk_id,
        "granted_at": now,
        "expires_at": expires_at,
        "npc": npc,
        "tier": int(tier),
        "charges": charges,
    }
    perks.append(new_entry)

    # Cap enforcement — evict oldest TIMED perk if over.
    if len(perks) > PERKS_MAX_ACTIVE:
        timed = [e for e in perks if int(e.get("expires_at", 0) or 0) > 0]
        if timed:
            oldest = min(timed, key=lambda e: int(e.get("granted_at", 0) or 0))
            perks.remove(oldest)
        # If everything is charge-based, no eviction — the cap softens rather
        # than dropping a one-shot the player hasn't used yet.

    _perks_save(profile, perks)
    # Lifetime tracking + grant-time aches (silent unlocks; caller embeds
    # them via the result-screen drop block).
    _perks_record_received(profile, perk_id)
    _perks_fire_grant_aches(profile, perk_id, tier, perks)
    return new_entry


def _perks_consume_charge(profile: Profile, perk_id: str) -> bool:
    """Decrement charges on a perk. Returns True iff a charge was consumed.
    The perk is pruned automatically on the next read once charges hit 0.

    For combo perks (both timed AND charge-based — combo_shield, daily_cap_
    extension, etc.) the spec says "expires after N hours or 1 use, whichever
    first." When the last charge is spent we force-expire `expires_at` so the
    time-side guard in _perks_is_active picks it up on the next prune."""
    perks = _perks_prune(profile)
    for e in perks:
        if e.get("id") == perk_id and int(e.get("charges", 0) or 0) > 0:
            e["charges"] = int(e["charges"]) - 1
            if int(e["charges"]) == 0 and int(e.get("expires_at", 0) or 0) > 0:
                e["expires_at"] = 1  # any value <= now triggers time-expiry
            _perks_save(profile, perks)
            return True
    return False


_PERKS_REQUIRE_FULL_BOARD = frozenset({"daily_cap_extension", "reroll_board"})


def _perks_roll_drop(npc: str, tier: int, rng: random.Random, mafia_level: int | None = None) -> str | None:
    """Roll the perk-drop die for (npc, tier). Returns a perk_id from the
    pool, or None if the die misses, the chance is 0, or the pool is empty.

    `mafia_level`, when provided, drops perks whose effect requires the
    full multi-slot board (`daily_cap_extension`, `reroll_board`) from the
    pool for sub-Lv4 players — at Lv2-3 the player only ever sees the
    single tutorial errand, so those perks would silently expire unused.
    """
    chance = float(PERKS_DROP_CHANCE_BY_TIER.get(str(tier), 0.0) or 0.0)
    if chance <= 0 or rng.random() >= chance:
        return None
    pool = (PERKS_DROP_POOLS.get(npc) or {}).get(str(tier)) or []
    if mafia_level is not None and mafia_level < 4:
        pool = [e for e in pool if e.get("id") not in _PERKS_REQUIRE_FULL_BOARD]
    if not pool:
        return None
    weights = [max(0, int(e.get("weight", 0))) for e in pool]
    if sum(weights) <= 0:
        return None
    picked = rng.choices(pool, weights=weights, k=1)[0]
    pid = picked.get("id")
    return pid if isinstance(pid, str) and pid else None


def _perks_strength(profile: Profile, perk_id: str, key: str, default=0.0):
    """Look up a strength key from an active perk's catalog tier_table.
    Returns `default` if the perk is not active OR the key isn't in the
    tier_table entry. Use this at every hook site that needs a magnitude."""
    e = _perks_get(profile, perk_id)
    if not e:
        return default
    tdata = _perks_tier_entry(perk_id, int(e.get("tier", 2) or 2))
    return tdata.get(key, default)


def _perks_catstore_buy_bonus(profile: Profile) -> int:
    """Active /catstore purchase discount bonus from job perks (pp). Peek-only."""
    if "catstore_discount_stack" not in _perks_active_ids(profile):
        return 0
    return int(_perks_strength(profile, "catstore_discount_stack", "discount_pp", 0) or 0)


def _perks_catstore_sell_bonus(profile: Profile) -> int:
    """Active /catstore sell-rate bonus from job perks (pp). Peek-only.
    Effect is still capped by buy_pct-5 inside store_sell_pct so round-trips
    can never go positive — that anti-arbitrage cap is the spec contract."""
    if "catstore_sell_premium" not in _perks_active_ids(profile):
        return 0
    return int(_perks_strength(profile, "catstore_sell_premium", "sell_pp", 0) or 0)


def _perks_effective_daily_cap(profile: Profile, base: int) -> int:
    """Peek-only effective jobs daily commit cap (does NOT consume the
    daily_cap_extension charge). Display sites should use this; the actual
    consume happens inside _perks_check_and_consume_daily_cap at commit."""
    if "daily_cap_extension" in _perks_active_ids(profile):
        return base + 1
    return base


def _perks_check_and_consume_daily_cap(profile: Profile, today_count: int, base: int) -> tuple[bool, bool]:
    """Commit-time cap check. Returns (allowed, perk_fired).

    - allowed=True iff this commit may proceed.
    - perk_fired=True iff daily_cap_extension was the reason. Consumes the
      charge on fire — DO NOT call this from peek paths.
    """
    if today_count < base:
        return True, False
    if today_count < base + 1 and "daily_cap_extension" in _perks_active_ids(profile):
        if _perks_consume_charge(profile, "daily_cap_extension"):
            return True, True
    return False, False


async def _perks_resolve_immediate(profile: Profile, perk_id: str, *, npc: str, tier: int) -> bool:
    """Self-resolving perks fire their effect at grant time and don't get
    stored in profile.job_perks. Returns True iff the effect was applied.

    Records the perk_id in profile.perks_received and fires grant-time aches
    on success — self-resolvers count toward lifetime distinct receives even
    though no row is stored. Caller saves profile."""
    did = await _perks_resolve_immediate_inner(profile, perk_id, npc=npc, tier=tier)
    if did:
        _perks_record_received(profile, perk_id)
        _perks_fire_grant_aches(profile, perk_id, tier, _perks_load(profile))
    return did


async def _perks_resolve_immediate_inner(profile: Profile, perk_id: str, *, npc: str, tier: int) -> bool:
    """Pure effect dispatch — caller handles lifetime tracking + aches.
    Returns True iff the effect was applied."""
    now = int(time.time())
    tdata = _perks_tier_entry(perk_id, tier)

    if perk_id == "free_pack":
        ptier = (tdata.get("pack_tier") or "wooden").lower()
        col = f"pack_{ptier}"
        try:
            profile[col] = int(profile[col] or 0) + 1
        except KeyError:
            logging.warning("perks.free_pack: unknown pack tier %r", ptier)
            return False
        return True

    if perk_id == "free_catnip":
        # Start a catnip session of duration_seconds. If already active,
        # extend it rather than truncate. No bounties/perk-pick required —
        # the buff just bridges to the player's existing perk loadout, so
        # if they have no perks selected the catnip is a quiet timer.
        dur = int(tdata.get("duration_seconds", 3600) or 3600)
        cur = int(getattr(profile, "catnip_active", 0) or 0)
        new_active = max(now, cur) + dur
        profile.catnip_active = new_active
        # Mirror /catnip activation's pack_attempts bookkeeping so the
        # per-minute pack roll budget tracks the new window.
        try:
            profile.pack_attempts = int(getattr(profile, "pack_attempts", 0) or 0) + dur // 60
        except KeyError:
            pass
        return True

    if perk_id == "catnip_extension":
        ext = int(tdata.get("extension_seconds", 1800) or 1800)
        cur = int(getattr(profile, "catnip_active", 0) or 0)
        profile.catnip_active = max(now, cur) + ext
        try:
            profile.pack_attempts = int(getattr(profile, "pack_attempts", 0) or 0) + ext // 60
        except KeyError:
            pass
        return True

    if perk_id == "bounty_refresh":
        # set_bounties needs catnip_level >= 1; bail if no bounties to refresh.
        lvl = int(getattr(profile, "catnip_level", 0) or 0)
        if lvl < 1:
            return False
        try:
            await set_bounties(lvl, profile)
        except Exception:
            logging.exception("perks.bounty_refresh: set_bounties failed")
            return False
        return True

    if perk_id == "discovery_shortcut":
        # Add one random rarity that isn't already discovered.
        discovered = set(_coerce_array(profile.discovered_cats))
        candidates = [t for t in cattypes if t not in discovered]
        if not candidates:
            return False
        chosen = random.choice(candidates)
        await mark_discovered(profile, chosen)
        return True

    if perk_id == "heat_reset":
        # Spec gates this to T4+ — a T2 drop would trivialize heat management.
        if int(tier) < 4:
            return False
        prior = int(getattr(profile, "heat", 0) or 0)
        if prior <= 0:
            return False
        profile.heat = 0
        profile.heat_last_decay = now
        return True

    if perk_id == "free_define":
        # If misc_quest is currently "define" and not on cooldown, bump
        # progress directly (no progress() call to avoid transaction
        # nesting). If quest reaches its target, mark complete by setting
        # cooldown — the next progress() call picks up the XP.
        if str(getattr(profile, "misc_quest", "") or "") != "define":
            return False
        if int(getattr(profile, "misc_cooldown", 0) or 0) != 0:
            return False
        quest_data = config.battle["quests"]["misc"].get("define")
        if not quest_data:
            return False
        cur_prog = int(getattr(profile, "misc_progress", 0) or 0)
        cur_prog += 1
        if cur_prog >= int(quest_data.get("progress", 1)):
            profile.misc_cooldown = now
            profile.misc_progress = 0
            profile.progress = int(getattr(profile, "progress", 0) or 0) + int(getattr(profile, "misc_reward", 0) or 0)
        else:
            profile.misc_progress = cur_prog
        return True

    return False


# Set of perk IDs that resolve immediately at grant time and are never stored
# in profile.job_perks. Drop hook dispatches to _perks_resolve_immediate for
# these instead of _perks_grant.
RESOLVING_PERKS = {
    "free_pack",
    "free_catnip",
    "catnip_extension",
    "bounty_refresh",
    "discovery_shortcut",
    "heat_reset",
    "free_define",
}


def _perks_format_strength(perk_id: str, tier: int) -> str:
    """Bracket-formatted strength preview for a (perk_id, tier) pair, e.g.
    '(6h)', '(1 use)', '(6h or 1 use)', '(+5pp)'. Returns '' if the catalog
    has nothing meaningful to render. Used on offer cards / send screen to
    give players concrete numbers without dumping the whole tier_table."""
    tdata = _perks_tier_entry(perk_id, tier)
    if not tdata:
        return ""
    parts: list[str] = []
    dur = int(tdata.get("duration_seconds", 0) or 0)
    ext = int(tdata.get("extension_seconds", 0) or 0)
    chg = int(tdata.get("charges", 0) or 0)
    if dur > 0:
        if dur % 3600 == 0:
            parts.append(f"{dur // 3600}h")
        elif dur >= 3600:
            parts.append(f"{dur / 3600:.1f}h")
        else:
            parts.append(f"{dur // 60}m")
    elif ext > 0:
        if ext % 3600 == 0:
            parts.append(f"+{ext // 3600}h")
        else:
            parts.append(f"+{ext // 60}m")
    if chg > 0:
        parts.append(f"{chg} use{'s' if chg != 1 else ''}")
    # Strength readouts that aren't time/charges.
    if "multiplier" in tdata:
        try:
            parts.append(f"×{float(tdata['multiplier']):g}")
        except (ValueError, TypeError):
            pass
    if "chance" in tdata:
        try:
            parts.append(f"{float(tdata['chance']) * 100:.0f}%")
        except (ValueError, TypeError):
            pass
    if "discount_pp" in tdata:
        parts.append(f"+{tdata['discount_pp']}pp")
    if "sell_pp" in tdata:
        parts.append(f"+{tdata['sell_pp']}pp sell")
    if "refund_pct" in tdata:
        try:
            parts.append(f"refund {float(tdata['refund_pct']) * 100:.0f}%")
        except (ValueError, TypeError):
            pass
    if "amount_bonus_pct" in tdata:
        try:
            parts.append(f"+{float(tdata['amount_bonus_pct']) * 100:.0f}%")
        except (ValueError, TypeError):
            pass
    if "reduction_pp" in tdata:
        try:
            parts.append(f"-{float(tdata['reduction_pp']) * 100:.0f}pp")
        except (ValueError, TypeError):
            pass
    if "coins_per_catch" in tdata:
        parts.append(f"{tdata['coins_per_catch']}c/catch")
    if "pack_tier" in tdata:
        parts.append(str(tdata["pack_tier"]).capitalize())
    if "cap_tier" in tdata:
        parts.append(f"→ {str(tdata['cap_tier']).capitalize()}")
    if "max_bet" in tdata:
        try:
            parts.append(f"up to {int(tdata['max_bet']):,}c")
        except (ValueError, TypeError):
            pass
    return f"({', '.join(parts)})" if parts else ""


def _perks_format_offer_preview(perk_id: str, tier: int) -> str:
    """One-line offer-card preview for a previewed perk. Tolerant of catalog
    drift — if the perk_id was removed from the catalog after offer-gen,
    renders 'Unknown perk' gracefully so the board can't crash."""
    if not perk_id:
        return ""
    cat = PERKS_CATALOG.get(perk_id)
    if not cat:
        return "🎁 Bonus on success: *Unknown perk*"
    name = cat.get("name") or perk_id.replace("_", " ").title()
    desc = cat.get("desc") or ""
    strength = _perks_format_strength(perk_id, tier)
    head = f"🎁 Bonus on success: **{name}**"
    if strength:
        head += f" {strength}"
    if desc:
        head += f" — {desc}"
    return head


def _perks_format_status(entry: dict, now: int | None = None) -> str:
    """Short human label for a single active perk row.
    Returns 'expires <t:N:R>' for timed, 'N charge(s) left' for charge,
    and combines both for combo perks."""
    now = now if now is not None else int(time.time())
    parts: list[str] = []
    exp = int(entry.get("expires_at", 0) or 0)
    ch  = int(entry.get("charges", 0) or 0)
    if exp > now:
        parts.append(f"expires <t:{exp}:R>")
    if ch > 0:
        parts.append(f"{ch} charge{'s' if ch != 1 else ''} left")
    return " · ".join(parts) if parts else "—"


def _perks_active_for_display(profile: Profile, now: int | None = None) -> list[dict]:
    """Returns active perk rows enriched with catalog name/desc for UI render.
    Each item: {id, name, desc, status, npc, tier, granted_at, expires_at, charges}."""
    out = []
    for e in _perks_prune(profile, now):
        cat = PERKS_CATALOG.get(e.get("id"), {})
        out.append({
            **e,
            "name": cat.get("name", e.get("id", "?")),
            "desc": cat.get("desc", ""),
            "status": _perks_format_status(e, now),
        })
    return out


def _perks_apply_catch_modifiers(profile: Profile, cat_type: str, ctx: dict | None = None) -> dict:
    """Bundle every catch-loop perk into a single modifier dict so the catch
    handler can apply each effect at its hook site without N separate lookups.

    Pure read — does NOT consume charges. The handler decides when to call
    _perks_consume_charge for charge-based ones (eagle_eye, combo_shield).
    Returns sensible no-op defaults; safe to call regardless of perk state.

    Keys returned:
      double_cat        bool — multiply silly_amount by 2 after catnip math
      rarity_bump_pct   float [0,1] — chance to upgrade cat one tier (cap Mythic)
      catch_xp_mult     float >= 1 — applied to catch-quest XP in progress()
      pack_drop_mult    float >= 1 — multiplies PACK_DROP_CHANCE_ON_CATCH
      combo_shield      bool — absorb the next Snowballer idle reset
      streak_protector  bool — absorb one skipped day in update_daily_catch_streak
      eagle_eye         bool — reveal the rarity on this catch's embed
      lightning_hands_mult float — widens the under-3 threshold
    """
    active = _perks_active_ids(profile)
    return {
        "double_cat":           "double_cat" in active,
        "rarity_bump_pct":      float(_perks_strength(profile, "rarity_bump", "chance", 0.0)) if "rarity_bump" in active else 0.0,
        "catch_xp_mult":        float(_perks_strength(profile, "catch_xp_boost", "multiplier", 1.0)) if "catch_xp_boost" in active else 1.0,
        "pack_drop_mult":       float(_perks_strength(profile, "pack_drop_boost", "multiplier", 1.0)) if "pack_drop_boost" in active else 1.0,
        "combo_shield":         "combo_shield" in active,
        "streak_protector":     "streak_protector" in active,
        "eagle_eye":            "eagle_eye" in active,
        "lightning_hands_mult": float(_perks_strength(profile, "lightning_hands", "multiplier", 1.0)) if "lightning_hands" in active else 1.0,
    }


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
# News ("The Cat Bot Times") is data-driven — articles live in config/news.json
# and are read fresh via get_news() (defined near get_emoji). Managed from the
# webui News editor.

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


def resolve_mystery(user: Profile, *, _depth: int = 0) -> tuple[list[str], int]:
    """Resolve one battlepass "Mystery" grant into a concrete outcome:
    usually a pack, sometimes rain time / coins / a scratchcard / XP / a
    voucher, and a 5% pre-roll for a Double Mystery (two outcomes).

    Applies all non-XP effects to `user` in place (does NOT save — callers
    save). Returns (desc_lines, bonus_xp). bonus_xp MUST be folded into the
    caller's LOCAL xp_progress accumulator — NEVER call grant_achievement_xp
    or progress from here: both level loops mutate this same profile object
    with no isolation, so re-entering them double-counts levels.

    Shared by both level-up paths (progress + grant_achievement_xp) so the
    odds can't drift. Weights live in config/tuning.json -> mystery_outcomes.
    """
    mystery = get_emoji("mysterypack")

    # Double Mystery pre-roll: exactly MYSTERY_DOUBLE_CHANCE, top level only.
    if _depth == 0 and random.random() < MYSTERY_DOUBLE_CHANCE:
        lines = [f"You got a {mystery} -> 🎁🎁 **Double Mystery!**"]
        total_xp = 0
        for _ in range(2):
            inner_lines, inner_xp = resolve_mystery(user, _depth=1)
            lines.extend("› " + line for line in inner_lines)
            total_xp += inner_xp
        return lines, total_xp

    def _roll_tier(tiers: dict) -> str | None:
        # None (-> pack fallback) if an operator emptied/zeroed a tier dict
        # in tuning.json — a config foot-gun must not crash a level-up
        try:
            return random.choices(list(tiers.keys()), weights=list(tiers.values()), k=1)[0]
        except (IndexError, ValueError, TypeError):
            return None

    try:
        family = random.choices(list(MYSTERY_WEIGHTS.keys()), weights=list(MYSTERY_WEIGHTS.values()), k=1)[0]
    except (IndexError, ValueError, TypeError):
        family = "pack"

    if family == "rain":
        # banked in seconds; every full 60s rolls into a real (per-server
        # bonus) rain minute — /rain itself only ever deals in whole minutes
        tier = _roll_tier(MYSTERY_RAIN_TIERS)
        try:
            if tier is not None:
                seconds = int(tier)
                bank = int(user.rain_seconds or 0) + seconds
                rolled_over = bank // 60
                user.rain_seconds = bank % 60
                suffix = ""
                if rolled_over:
                    user.rain_minutes += rolled_over
                    suffix = f" (+{rolled_over} rain minute{'s' if rolled_over != 1 else ''}!)"
                return [f"You got a {mystery} -> ☔ +{seconds}s of rain time!{suffix}"], 0
        except (KeyError, AttributeError):
            pass  # pre-migration-035: fall back to a pack
        family = "pack"

    if family == "coins":
        tier = _roll_tier(MYSTERY_COIN_TIERS)
        if tier is not None:
            amount = int(tier)
            user.coins = int(user.coins or 0) + amount
            _bump(user, "coins_earned", amount)
            return [f"You got a {mystery} -> 🪙 {amount:,} coins!"], 0
        family = "pack"

    if family == "xp":
        tier = _roll_tier(MYSTERY_XP_TIERS)
        if tier is not None:
            return [f"You got a {mystery} -> ⬆️ +{int(tier)} XP!"], int(tier)
        family = "pack"

    if family == "scratchcard":
        try:
            user.scratchcards += 1
            return [f"You got a {mystery} -> 🍀 a /scratch card!"], 0
        except (KeyError, AttributeError):
            family = "pack"  # pre-migration-034: fall back to a pack

    if family == "voucher":
        voucher_id = _roll_tier(MYSTERY_VOUCHER_TIERS)
        if voucher_id and _vouchers_grant(user, voucher_id):
            emoji, name, blurb = VOUCHER_LABELS.get(voucher_id, ("🎟️", voucher_id, "???"))
            return [f"You got a {mystery} -> {emoji} a **{name}** voucher — {blurb}!"], 0
        family = "pack"  # pre-migration-035 (or emptied tier dict): fall back to a pack

    # default / fallback: the classic pack pull, weighted toward cheap tiers
    pack_options = [pack["name"] for pack in pack_data if not pack["special"]]
    pack_weights = [1 / pack["totalvalue"] for pack in pack_data if not pack["special"]]
    pack_chosen = random.choices(pack_options, weights=pack_weights, k=1)[0]
    user[f"pack_{pack_chosen.lower()}"] += 1
    return [f"You got a {mystery} -> {get_emoji(pack_chosen.lower() + 'pack')} {pack_chosen} pack! Do /packs to open it!"], 0


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
        level_data = {"xp": EXTRA_LEVEL_XP, "reward": EXTRA_LEVEL_REWARD, "amount": 1}
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
        mystery_lines = None
        if active_level_data["reward"] in cattypes:
            user[f"cat_{active_level_data['reward']}"] += active_level_data["amount"]
        elif active_level_data["reward"] == "Rain":
            user.rain_minutes += active_level_data["amount"]
        elif active_level_data["reward"] == "Mystery":
            mystery_lines, mystery_xp = resolve_mystery(user)
            if mystery_xp:
                # fold into the LOCAL accumulator (never re-enter the level
                # machinery — see resolve_mystery's docstring); this can
                # legitimately chain the next level via the while re-check
                xp_progress += mystery_xp
                user.progress = xp_progress
        else:
            user[f"pack_{active_level_data['reward'].lower()}"] += 1
        # Optional "extra_reward" stack — a level can grant a second reward on
        # top of the primary. Currently used by S3 L40 (Celestial pack + 1m rain).
        if active_level_data.get("extra_reward"):
            extra = active_level_data["extra_reward"]
            extra_amt = active_level_data.get("extra_amount", 1)
            if extra in cattypes:
                user[f"cat_{extra}"] += extra_amt
            elif extra == "Rain":
                user.rain_minutes += extra_amt
            else:
                user[f"pack_{extra.lower()}"] += 1
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
        elif mystery_lines:
            description = "\n".join(mystery_lines)
        else:
            description = (
                f"You got a {get_emoji(active_level_data['reward'].lower() + 'pack')} {active_level_data['reward']} pack! Do /packs to open it!"
            )
        if active_level_data.get("extra_reward"):
            extra = active_level_data["extra_reward"]
            extra_amt = active_level_data.get("extra_amount", 1)
            if extra == "Rain":
                description += f"\nPlus ☔ {extra_amt} rain minute{'s' if extra_amt != 1 else ''}!"
            elif extra in cattypes:
                description += f"\nPlus {get_emoji(extra.lower() + 'cat')} {extra_amt} {extra}!"
            else:
                description += f"\nPlus a {get_emoji(extra.lower() + 'pack')} {extra} pack!"
        embeds.append(
            discord.Embed(
                title=f"Level {user.battlepass} Complete!",
                description=description,
                color=Colors.yellow,
            )
        )
        embeds.append(build_levelup_pack_embed(user, bonus_pack_name))

        if user.battlepass >= len(season_levels):
            active_level_data = {"xp": EXTRA_LEVEL_XP, "reward": EXTRA_LEVEL_REWARD, "amount": 1}
        else:
            active_level_data = season_levels[user.battlepass]

    return embeds


# Casino quest bitmask. The "casino" extra-slot quest requires playing 3
# different casino games out of the four. We track which games have been
# played via casino_progress_temp; each bit set = that game contributed once.
CASINO_GAME_BITS = {"slots": 1, "roulette": 2, "pig": 4, "cookieclicker": 8, "catslots": 16}


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
            # `reply` only exists on discord.Message; Interactions don't have it.
            # Fall back to followup so callers that mistakenly pass an Interaction
            # don't lose the ach embed (just logged loudly the first time).
            if hasattr(message, "reply"):
                result = await message.reply(embed=embed)
            else:
                logging.warning("achemb: 'reply' send_type used on a non-Message object for %s — falling back to followup", ach_id)
                result = await message.followup.send(embed=embed, ephemeral=not do)
        if send_type == "send" and do:
            result = await message.channel.send(embed=embed)
        if send_type == "followup":
            # `followup` only exists on discord.Interaction. Callers that
            # receive a discord.Message (e.g. on_message → bounty()) and
            # accidentally pass it through can land here; degrade gracefully
            # to channel.send instead of crashing the achievement grant.
            if hasattr(message, "followup"):
                result = await message.followup.send(embed=embed, ephemeral=not do)
            elif do:
                logging.warning("achemb: 'followup' send_type used on a non-Interaction object for %s — falling back to channel.send", ach_id)
                result = await message.channel.send(embed=embed)
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


# Pre-migration-028 guards: profile.vote_quest may not yet exist on the row
# (catpg raises KeyError on attribute access for missing columns). These
# helpers let the vote-substitute-slot codepaths degrade gracefully — read
# returns '' (= "treat as real vote quest"), write is skipped — so the bot
# keeps running until the operator applies migration 028. Remove the guards
# once the migration is universally applied.
def _vote_quest_safe(user) -> str:
    try:
        return user.vote_quest or ""
    except (KeyError, AttributeError):
        return ""


def _set_vote_quest_safe(user, value: str) -> bool:
    try:
        _ = user.vote_quest
    except (KeyError, AttributeError):
        return False
    user.vote_quest = value
    return True


def _weekly_quest_safe(user) -> str:
    # '' both when no weekly quest is active (the sentinel) and when the
    # column doesn't exist yet (migration 034 unrun).
    try:
        return user.weekly_quest
    except (KeyError, AttributeError):
        return ""


async def _append_weekly_catch_quests(user, cattype, quests):
    # weekly quests 🍀: every catch counts for "catch"; "brave+" by rarity
    # index (computed, not hardcoded — the fork's cattype list grew); the
    # "different" dedup mutates weekly_cattypes and must save BEFORE
    # multi_progress refetches, or the append gets clobbered. Shared by the
    # main and belated catch paths.
    quests.append("catch")
    if cattype not in cattypes:
        return
    idx = cattypes.index(cattype)
    if idx > cattypes.index("Brave"):
        quests.append("brave+")
    if _weekly_quest_safe(user) == "different":
        current = user.weekly_cattypes.copy()
        if idx not in current:
            current.append(idx)
            user.weekly_cattypes = current
            quests.append("different")
            await user.save()


async def generate_quest(user: Profile, quest_type: str):
    while True:
        quest = random.choice(list(config.battle["quests"][quest_type].keys()))
        if quest == "define" and not config.WORDNIK_API_KEY:
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
        # ~1/2 of refresh cycles the slot is the real Vote on Top.gg quest
        # ("every other level"); the other half we sub in a random
        # single-action misc quest so the vote prompt doesn't dominate.
        # Substitute reuses vote_reward (XP) and vote_cooldown (claim
        # timestamp). vote_quest stores the misc quest id; empty string
        # means "real vote quest." Pre-migration 028 the vote_quest column
        # may be absent — _set_vote_quest_safe returns False in that case
        # and we fall back to the real vote quest path.
        roll_substitute = random.randint(1, 2) == 1
        sub_assigned = False
        if roll_substitute:
            sub_pool = [
                k for k, q in config.battle["quests"]["misc"].items()
                if q.get("progress", 1) == 1
                and k != user.misc_quest
                and not (k == "define" and not config.WORDNIK_API_KEY)
            ]
            if sub_pool:
                sub = random.choice(sub_pool)
                if _set_vote_quest_safe(user, sub):
                    sub_data = config.battle["quests"]["misc"][sub]
                    user.vote_reward = random.randint(sub_data["xp_min"] // 10, sub_data["xp_max"] // 10) * 10
                    user.vote_cooldown = 0
                    sub_assigned = True
        if not sub_assigned:
            _set_vote_quest_safe(user, "")
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


# ---------------------------------------------------------------------------
# Season rollover wipes. These run once per season per profile (per server)
# from inside refresh_quests when user.season != full_months_passed.
#
# The wipe scope is "active mafia/economy state, not lifetime stats". Cats,
# prisms, stocks, discovered-cats, achievements, streaks, and the per-server
# rain inventory are explicitly preserved (they represent player history, not
# the active economy being reset). Packs are wiped per design intent — the
# user wants seasons to start with empty pack inventories so battlepass
# rewards re-introduce the climb. See docs/design/battlepass.md → Seasons.
# ---------------------------------------------------------------------------


def _wipe_catnip_state(user):
    """Reset catnip session, perks, bounties, and mafia rank to a Season 1
    Day 1 baseline. Lifetime counters (catnip_activations, catnip_bought,
    highest_catnip_level, bounties_complete) are preserved as stats."""
    user.catnip_level = 0
    user.catnip_active = 0
    user.catnip_total_cats = 0
    user.catnip_amount = 0
    user.catnip_price = "Fine"
    # bounty state — four slots (one/two/three/bonus)
    for slot in ("one", "two", "three", "bonus"):
        user[f"bounty_id_{slot}"] = 0
        user[f"bounty_type_{slot}"] = ""
        user[f"bounty_total_{slot}"] = 0
        user[f"bounty_progress_{slot}"] = 0
    user.bounties = 0
    # stored catnip perks
    user.perks = []
    user.perk_selected = True
    user.perk1 = ""
    user.perk2 = ""
    user.perk3 = ""
    user.reroll = False
    user.reroll_level = 0


def _wipe_jobs_state(user):
    """Reset operational jobs state. Lifetime counters (jobs_completed,
    jobs_failed, jobs_near_missed, cats_lost_to_jobs, job_coins_won,
    biggest_score_value, big_score_wins, big_score_perk_unlocked,
    perks_received, tutorial_errand_complete, jobs_send_screen_seen) are
    preserved. In-flight JobInstance rows are left alone — with
    catnip_level=0 the /jobs board early-returns, so they expire naturally."""
    user.heat = 0
    user.heat_last_decay = 0
    user.respect = 50
    user.respect_last_tick = 0
    user.faction_rep = {}
    user.jobs_pending_difficulty_mult = 1.0
    user.jobs_pending_heat_bonus = 0
    user.job_perks = []
    user.perks_suspended_until = 0
    user.big_score_season = -1
    user.whiskers_favor_active = False
    user.whiskers_favor_season = -1


_NORMAL_PACK_TIERS = ("wooden", "stone", "bronze", "silver", "gold", "platinum", "diamond", "celestial")
_SPECIAL_PACK_TIERS = ("christmas", "valentine", "chef", "birthday")


def _wipe_packs(user):
    """Reset every pack tier (normal + event) to zero. Cat inventory is NOT
    touched — only the unopened pack queue."""
    for tier in _NORMAL_PACK_TIERS + _SPECIAL_PACK_TIERS:
        user[f"pack_{tier}"] = 0


# Lifetime counters that back the season-recap leaderboard (migration 022).
# These accumulate forever; the recap turns them into "this season" totals by
# diffing against season_stat_baseline (captured at each rollover, below).
_SEASON_STAT_COUNTERS = (
    "total_catches",
    "jobs_completed",
    "coins_earned",
    "roulette_coins_won",
    "roulette_coins_bet",
    "catslots_coins_won",
    "catslots_bonus_coins_won",
    "catslots_coins_bet",
    "stock_coins_earned",
    "stock_coins_spent",
)


def _bump(profile, col, delta):
    """Increment a lifetime counter column by `delta`, but only if the column
    exists on this row (migration-safe). catpg raises KeyError for a column
    that isn't on the fetched row / pre-migration DB; in that case this is a
    silent no-op so callers in hot money paths never crash on an un-migrated
    instance. `delta` may be negative (e.g. a cancelled buy-order refund)."""
    try:
        cur = profile[col]
    except (KeyError, AttributeError):
        return
    profile[col] = int(cur or 0) + int(delta)


async def _recap_columns_present() -> bool:
    """True iff migration 022's recap columns exist. Probed once and cached on
    the config module (survives cat!restart). The Python `_bump` paths self-
    guard, but the raw-SQL dividend payout and the bulk_update sell-fill path
    can't probe per-row — they consult this so they only reference the new
    columns once the migration has actually added them."""
    cached = getattr(config, "recap_columns_present", None)
    if cached is not None:
        return cached
    try:
        val = await pool.fetchval(
            "SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name='profile' AND column_name='stock_coins_earned'"
        )
        present = val is not None
    except Exception:
        present = False
    config.recap_columns_present = present
    return present


async def _maybe_show_season_reset_notice(interaction, user):
    """If the player just rolled into a new season (refresh_quests set
    season_reset_pending = True on the prior call), send them a one-shot
    ephemeral embed summarizing what was wiped, then clear the flag.

    Idempotent: subsequent calls in the same season are no-ops. Safe to
    call from any slash-command path where the interaction has already
    responded or been deferred (uses interaction.followup.send, which
    requires the response slot to be filled). No-ops cleanly when the
    season_reset_pending column doesn't exist yet (migration 019 unrun).

    The notice is intentionally ephemeral so other players in the channel
    don't see it — this is private 'your account just reset' info."""
    try:
        pending = bool(user.season_reset_pending)
    except (KeyError, AttributeError):
        return
    if not pending:
        return
    user.season_reset_pending = False
    try:
        await user.save()
    except Exception:
        logging.exception("season_reset_pending clear failed")
    try:
        season_num = int(getattr(user, "season", 0) or 0)
        embed = discord.Embed(
            title=f"🆕 Cattlepass Season {season_num} just started",
            description=(
                f"Your **coins** have been reset to **🪙 {SEASON_STARTING_COINS:,}** "
                "(season starting allowance). Your **catnip level**, **packs**, "
                "and all active **mafia/jobs state** have been wiped — build them "
                "back up this season.\n\n"
                "Untouched: your **cats**, **prisms**, **stocks**, **streaks**, "
                "**discovered cats**, and **achievements** stay with you.\n\n"
                "Welcome to the new month."
            ),
            color=Colors.brown,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception:
        # The user already had the wipe applied; missing the notice once is
        # not a correctness issue. Log and move on.
        logging.exception("season reset notice send failed")


async def refresh_quests(user):
    await user.refresh_from_db()
    # season 1 = May 2026 (when this self-hosted instance went live).
    # Each calendar month is a new season; rollover happens on the 1st.
    start_date = datetime.datetime(2026, 4, 1)
    current_date = discord.utils.utcnow() + datetime.timedelta(hours=4)
    full_months_passed = (current_date.year - start_date.year) * 12 + (current_date.month - start_date.month)
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

        # Weekly quest + scratchcards (migration 034). Unspent cards are
        # pack-lottery tickets and the season wipe empties packs, so they
        # reset too — no pack value leaks across the wipe. Probe-read guard
        # like the flags below so an un-migrated DB no-ops cleanly.
        try:
            _ = user.weekly_quest
            user.weekly_quest = list(config.battle["quests"]["weekly"].keys())[0]
            user.weekly_progress = 0
            user.weekly_cattypes = []
            user.scratchcards = 0
        except (KeyError, AttributeError):
            pass

        # Vouchers 🎟️ (migration 035) wipe with the season too — they're
        # pack-adjacent value. rain_seconds is deliberately PRESERVED, same
        # as rain_minutes.
        try:
            _ = user.vouchers
            user.vouchers = []
        except (KeyError, AttributeError):
            pass

        # 0.6.5 — per-season economy wipe. Coins reset to the season starting
        # allowance (SEASON_STARTING_COINS), catnip and jobs state reset, pack
        # queue empties. Cats / stocks / prisms / discovered / achievements /
        # streaks are preserved (see helpers above). The season_reset_pending
        # flag triggers a one-shot ephemeral notice on the player's next
        # /battlepass, /catnip, /jobs, /catstore, /stats, or /inventory call
        # (see _maybe_show_season_reset_notice).
        user.coins = SEASON_STARTING_COINS
        _wipe_catnip_state(user)
        _wipe_jobs_state(user)
        _wipe_packs(user)
        # Guard against the column not existing yet (migration 019 unrun).
        # Probe-read first; catpg raises KeyError when a column isn't on the
        # row, and setting/saving a missing column would error during save().
        try:
            _ = user.season_reset_pending
            user.season_reset_pending = True
        except (KeyError, AttributeError):
            pass

        # Season-recap baseline (migration 022). Capture the lifetime counters
        # as of this rollover — they become the NEW season's starting line, so
        # that season's "this season" total = current_lifetime - baseline. The
        # lifetime counters themselves are NOT wiped (they live across seasons);
        # only this snapshot of them is stored. Guarded like the flag above so
        # an un-migrated DB no-ops cleanly.
        try:
            _ = user.season_stat_baseline
            user.season_stat_baseline = {
                col: int(getattr(user, col, 0) or 0) for col in _SEASON_STAT_COUNTERS
            }
        except (KeyError, AttributeError):
            pass

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
    # Weekly retired-quest guard ('' is always valid — it's the sentinel entry).
    try:
        if user.weekly_quest and user.weekly_quest not in config.battle["quests"]["weekly"]:
            user.weekly_quest = ""
            user.weekly_progress = 0
            user.weekly_cattypes = []
    except (KeyError, AttributeError):
        pass
    # Substitute vote quest retirement: if the misc quest currently hosted in
    # the vote slot got removed (or define lost its API key), force a re-roll.
    # Uses the safe accessor to no-op pre-migration 028 when the column is
    # missing from the profile row.
    _vq = _vote_quest_safe(user)
    if _vq and _vq not in config.battle["quests"]["misc"]:
        _set_vote_quest_safe(user, "")
        user.vote_cooldown = 1
        user.vote_reward = 0
    elif _vq == "define" and not config.WORDNIK_API_KEY:
        _set_vote_quest_safe(user, "")
        user.vote_cooldown = 1
        user.vote_reward = 0
    if QUEST_COOLDOWN < user.catch_cooldown + QUEST_COOLDOWN < time.time():
        await generate_quest(user, "catch")
    if QUEST_COOLDOWN < user.misc_cooldown + QUEST_COOLDOWN < time.time():
        await generate_quest(user, "misc")
    if QUEST_COOLDOWN < user.extra_cooldown + QUEST_COOLDOWN < time.time():
        await generate_quest(user, "extra")
    # vote slot regenerates LAST so the substitute pool can exclude the
    # freshly-rolled misc_quest (avoids the same quest landing in two slots).
    if QUEST_COOLDOWN < user.vote_cooldown + QUEST_COOLDOWN < time.time():
        await generate_quest(user, "vote")
    # Challenge slot was added after the original schema, so existing profiles
    # have challenge_cooldown=0 (which the inequality above misses) and an
    # empty challenge_quest. Treat empty as "needs first generation" so the
    # /battlepass UI never sees an unset slot.
    if not user.challenge_quest or QUEST_COOLDOWN < user.challenge_cooldown + QUEST_COOLDOWN < time.time():
        await generate_quest(user, "challenge")

    # Weekly quest rotation (upstream cattlepass v2.1). Windows are seconds
    # from the start of the month on the same +4h clock as the season rollover
    # above — weeks 1-4 each host one quest, the "" sentinel covers days
    # 28-EOM (no active quest). No 12h cooldown: one completion per window,
    # gated purely by weekly_progress. Probe-read guard for migration 034.
    try:
        curr_weekly = config.battle["quests"]["weekly"][user.weekly_quest]
    except (KeyError, AttributeError):
        return
    # tzinfo=utc matters: a naive datetime's .timestamp() is interpreted in
    # the HOST timezone, which would shift every weekly window by the host's
    # UTC offset (upstream gets away with it on UTC servers; this box isn't).
    month_start = datetime.datetime(current_date.year, current_date.month, 1, tzinfo=datetime.timezone.utc) - datetime.timedelta(hours=4)
    time_in_month = time.time() - int(month_start.timestamp())
    if curr_weekly["start_time"] < time_in_month < curr_weekly["end_time"]:
        return
    for k, v in config.battle["quests"]["weekly"].items():
        if v["start_time"] < time_in_month < v["end_time"]:
            user.weekly_quest = k
            user.weekly_progress = 0
            user.weekly_cattypes = []
            await user.save()
            return


async def multi_progress(message: discord.Message | discord.Interaction, user: Profile, quests: list[str], is_belated: Optional[bool] = False):
    await refresh_quests(user)
    await user.refresh_from_db()
    for quest in quests:
        return_user = await progress(message, user, quest, is_belated, False)
        if return_user:
            user = return_user


async def progress(
    message: discord.Message | discord.Interaction | None, user: Profile, quest: str, is_belated: Optional[bool] = False, refetch: bool = True
) -> Profile:
    if refetch:
        await refresh_quests(user)
        await user.refresh_from_db()

    # Job-perk quest-XP multipliers — computed once per call so they apply
    # uniformly across every quest branch below.
    #   catch_xp_boost  → catch quests only
    #   quest_xp_boost  → all quest types (catch/misc/extra/challenge; not vote)
    def _qxp_bonus(reward_int: int, include_catch_xp: bool) -> int:
        mult = 1.0
        if "quest_xp_boost" in _perks_active_ids(user):
            mult *= float(_perks_strength(user, "quest_xp_boost", "multiplier", 1.0) or 1.0)
        if include_catch_xp and "catch_xp_boost" in _perks_active_ids(user):
            mult *= float(_perks_strength(user, "catch_xp_boost", "multiplier", 1.0) or 1.0)
        return int(round(reward_int * (mult - 1.0))) if mult > 1.0 else 0

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
            current_xp = user.progress + user.catch_reward + _qxp_bonus(user.catch_reward, include_catch_xp=True)
            user.catch_progress = 0
    elif quest == "vote":
        # Vote slot is a misc-pool substitute this cycle — voting doesn't
        # claim it. The substitute is progressed via its own quest name
        # through the user.vote_quest branch below.
        if _vote_quest_safe(user):
            return user
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
            current_xp = user.progress + user.misc_reward + _qxp_bonus(user.misc_reward, include_catch_xp=False)
            user.misc_progress = 0
    elif user.extra_quest == quest:
        if user.extra_cooldown != 0:
            return user
        quest_data = config.battle["quests"]["extra"][quest]
        user.extra_progress += 1
        if user.extra_progress >= quest_data["progress"]:
            quest_complete = True
            user.extra_cooldown = int(time.time())
            current_xp = user.progress + user.extra_reward + _qxp_bonus(user.extra_reward, include_catch_xp=False)
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
            current_xp = user.progress + user.challenge_reward + _qxp_bonus(user.challenge_reward, include_catch_xp=False)
            user.challenge_progress = 0
            if not user.has_ach("challenge_first"):
                # Fire the first-completion ach BEFORE the level-up flow so it
                # lands inline with the other catch-context embeds.
                await achemb(message, "challenge_first", "send")
    elif quest and _weekly_quest_safe(user) == quest and quest in config.battle["quests"].get("weekly", {}):
        # Weekly quest 🍀 (upstream cattlepass v2.1): no cooldown, one
        # completion per calendar-week window, fixed marquee reward of
        # WEEKLY_QUEST_XP + a /scratch card. Deliberately NOT scaled by
        # _qxp_bonus or weekend doubling — a perk swinging 2000 XP would
        # dwarf every other quest.
        quest_data = config.battle["quests"]["weekly"][quest]
        if user.weekly_progress >= quest_data["progress"]:
            return user
        user.weekly_progress += 1
        if user.weekly_progress >= quest_data["progress"]:
            user.weekly_progress = quest_data["progress"]
            quest_complete = True
            current_xp = user.progress + WEEKLY_QUEST_XP
            user.scratchcards += WEEKLY_QUEST_SCRATCHCARDS
    elif _vote_quest_safe(user) == quest and quest:
        # Vote slot is hosting this misc quest as a substitute. Single-action
        # completion (substitute pool is filtered to progress=1). Uses misc
        # quest_data for the progress embed; XP routes through vote_reward.
        if user.vote_cooldown != 0:
            return user
        quest_data = config.battle["quests"]["misc"][quest]
        quest_complete = True
        user.vote_cooldown = int(time.time())
        current_xp = user.progress + user.vote_reward + _qxp_bonus(user.vote_reward, include_catch_xp=False)
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
        level_data = {"xp": EXTRA_LEVEL_XP, "reward": EXTRA_LEVEL_REWARD, "amount": 1}
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
            mystery_lines = None
            if active_level_data["reward"] in cattypes:
                user[f"cat_{active_level_data['reward']}"] += active_level_data["amount"]
            elif active_level_data["reward"] == "Rain":
                user.rain_minutes += active_level_data["amount"]
            elif active_level_data["reward"] == "Mystery":
                mystery_lines, mystery_xp = resolve_mystery(user)
                if mystery_xp:
                    # fold into the LOCAL accumulator (never re-enter the
                    # level machinery — see resolve_mystery's docstring)
                    xp_progress += mystery_xp
                    user.progress = xp_progress
            else:
                user[f"pack_{active_level_data['reward'].lower()}"] += 1
            # Optional "extra_reward" stack — same shape as the primary. See
            # grant_achievement_xp for the matching block.
            if active_level_data.get("extra_reward"):
                extra = active_level_data["extra_reward"]
                extra_amt = active_level_data.get("extra_amount", 1)
                if extra in cattypes:
                    user[f"cat_{extra}"] += extra_amt
                elif extra == "Rain":
                    user.rain_minutes += extra_amt
                else:
                    user[f"pack_{extra.lower()}"] += 1
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
                elif mystery_lines:
                    description = "\n".join(mystery_lines)
                else:
                    description = (
                        f"You got a {get_emoji(active_level_data['reward'].lower() + 'pack')} {active_level_data['reward']} pack! Do /packs to open it!"
                    )
                if active_level_data.get("extra_reward"):
                    extra = active_level_data["extra_reward"]
                    extra_amt = active_level_data.get("extra_amount", 1)
                    if extra == "Rain":
                        description += f"\nPlus ☔ {extra_amt} rain minute{'s' if extra_amt != 1 else ''}!"
                    elif extra in cattypes:
                        description += f"\nPlus {get_emoji(extra.lower() + 'cat')} {extra_amt} {extra}!"
                    else:
                        description += f"\nPlus a {get_emoji(extra.lower() + 'pack')} {extra} pack!"
                title = f"Level {user.battlepass} Complete!"
            else:
                description = f"You got {cat_emojis}!"
                title = "Bonus Complete!"
            embed_level_up = discord.Embed(title=title, description=description, color=Colors.yellow)
            level_complete_embeds.append(embed_level_up)
            level_complete_embeds.append(build_levelup_pack_embed(user, bonus_pack_name))

            if user.battlepass >= len(config.battle["seasons"][str(user.season)]):
                active_level_data = {"xp": EXTRA_LEVEL_XP, "reward": EXTRA_LEVEL_REWARD, "amount": 1}
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

    # message=None is the silent-grant path (e.g. vote XP auto-credited from
    # do_vote with no interaction in hand). XP + inventory rewards still land;
    # we just skip the channel ceremony.
    if message is not None:
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
    elif quest_data in config.battle["quests"].get("weekly", {}).values():
        streak_reward = f"\n🍀 **Weekly Quest!** +{WEEKLY_QUEST_SCRATCHCARDS} /scratch card{'s' if WEEKLY_QUEST_SCRATCHCARDS != 1 else ''}!"
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
    # `message` is an Interaction here, not a discord.Message — use "followup"
    # (Interactions don't have .reply()).
    await achemb(message, "curious", "followup")
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
            icon_url="https://wsrv.nl/?url=raw.githubusercontent.com/sneezeparty/catbot7/main/images/cat.png",
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


# Activity dashboard hourly aggregate snapshot. Cadence is faster than the
# bucket (1 hour) so every hour boundary gets a row promptly AND the current
# bucket's row stays fresh — ON CONFLICT DO UPDATE rewrites the in-progress
# hour every tick so the "Last 24h" tile and the today-bar on the catches/
# coins charts reflect data ≤5 min old instead of waiting up to an hour.
METRICS_SNAPSHOT_INTERVAL = 300


async def _metrics_snapshot_tick():
    """Compute and upsert one row into metric_snapshot for the current hour
    bucket. Also opportunistically refreshes server.name for every guild the
    bot is currently in.

    Robust to a partially-migrated DB: missing table/column is logged once and
    skipped. Robust to restarts: PK on bucket_time + ON CONFLICT DO UPDATE
    means the next tick after a restart just refreshes the current row.
    """
    import asyncpg as _asyncpg
    if pool is None or not bot or not bot.is_ready():
        return
    bucket = (int(time.time()) // 3600) * 3600
    now_ts = int(time.time())
    today_start = int(
        datetime.datetime.now(datetime.timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )
    week_start = today_start - 6 * 86400
    month_start = today_start - 29 * 86400

    try:
        async with pool.acquire() as conn:
            # 1) Opportunistic server.name refresh for every guild the bot is in.
            for g in list(bot.guilds):
                if not g or not g.name:
                    continue
                try:
                    await conn.execute(
                        "UPDATE server SET name = $1 WHERE server_id = $2 AND name <> $1",
                        g.name[:100], g.id,
                    )
                except Exception:
                    logging.debug("server.name update skipped (column missing?)")
                    break

            # 2) Compute the snapshot row.
            # Bot's own profile/user/prism/job rows accumulate from
            # gift/sacrifice/etc. but the bot isn't a real player, so exclude
            # its user_id from every aggregate that feeds the dashboard's
            # Load section + counters. bot_user_id is 0 before on_ready so
            # the predicate degrades to a no-op (Discord ids are never 0).
            bot_user_id = int(bot.user.id) if bot.user else 0
            agg = await conn.fetchrow(
                """
                SELECT
                  COALESCE(SUM(total_catches), 0)            AS total_catches,
                  COALESCE(SUM(packs_opened), 0)             AS total_packs,
                  COALESCE(SUM(GREATEST(coins, 0)), 0)       AS coins,
                  COALESCE(SUM(catnip_total_cats), 0)        AS catnip_total,
                  COALESCE(SUM(jobs_completed), 0)           AS jobs_completed,
                  COALESCE(SUM(jobs_failed), 0)              AS jobs_failed,
                  COUNT(DISTINCT CASE WHEN last_catch >= $1
                                      THEN user_id END)      AS a24,
                  COUNT(DISTINCT CASE WHEN last_catch >= $2
                                      THEN user_id END)      AS a7,
                  COUNT(DISTINCT CASE WHEN last_catch >= $3
                                      THEN user_id END)      AS a30,
                  COUNT(*)                                   AS profile_count
                FROM profile
                WHERE user_id <> $4
                """,
                now_ts - 86400, week_start, month_start, bot_user_id,
            )
            user_count = await conn.fetchval(
                'SELECT COUNT(*) FROM "user" WHERE user_id <> $1', bot_user_id
            )
            prism_count = await conn.fetchval(
                "SELECT COUNT(*) FROM prism WHERE user_id <> $1", bot_user_id
            )
            live_spawns = await conn.fetchval(
                "SELECT COUNT(*) FROM channel WHERE cat <> 0"
            )
            active_rains = await conn.fetchval(
                "SELECT COUNT(*) FROM channel WHERE rain_should_end > $1", now_ts,
            )
            pending_jobs = await conn.fetchval(
                "SELECT COUNT(*) FROM jobinstance "
                "WHERE state IN ('offered','committed') AND user_id <> $1",
                bot_user_id,
            )

            # 3) Upsert (no-op on conflict).
            try:
                await conn.execute(
                    """
                    INSERT INTO metric_snapshot (
                        bucket_time, guild_count, profile_count, user_count,
                        active_24h, active_7d, active_30d,
                        total_catches, total_packs, total_prisms,
                        coins_in_circulation, catnip_total,
                        jobs_completed_lifetime, jobs_failed_lifetime,
                        live_spawns, active_rains, pending_jobs
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                        $11, $12, $13, $14, $15, $16, $17
                    )
                    ON CONFLICT (bucket_time) DO UPDATE SET
                        guild_count = EXCLUDED.guild_count,
                        profile_count = EXCLUDED.profile_count,
                        user_count = EXCLUDED.user_count,
                        active_24h = EXCLUDED.active_24h,
                        active_7d = EXCLUDED.active_7d,
                        active_30d = EXCLUDED.active_30d,
                        total_catches = EXCLUDED.total_catches,
                        total_packs = EXCLUDED.total_packs,
                        total_prisms = EXCLUDED.total_prisms,
                        coins_in_circulation = EXCLUDED.coins_in_circulation,
                        catnip_total = EXCLUDED.catnip_total,
                        jobs_completed_lifetime = EXCLUDED.jobs_completed_lifetime,
                        jobs_failed_lifetime = EXCLUDED.jobs_failed_lifetime,
                        live_spawns = EXCLUDED.live_spawns,
                        active_rains = EXCLUDED.active_rains,
                        pending_jobs = EXCLUDED.pending_jobs
                    """,
                    bucket,
                    len(bot.guilds),
                    int(agg["profile_count"] or 0),
                    int(user_count or 0),
                    int(agg["a24"] or 0),
                    int(agg["a7"] or 0),
                    int(agg["a30"] or 0),
                    int(agg["total_catches"] or 0),
                    int(agg["total_packs"] or 0),
                    int(prism_count or 0),
                    int(agg["coins"] or 0),
                    int(agg["catnip_total"] or 0),
                    int(agg["jobs_completed"] or 0),
                    int(agg["jobs_failed"] or 0),
                    int(live_spawns or 0),
                    int(active_rains or 0),
                    int(pending_jobs or 0),
                )
            except _asyncpg.exceptions.UndefinedTableError:
                logging.warning(
                    "metric_snapshot table missing — run migration 029"
                )
    except Exception:
        logging.exception("metrics snapshot tick failed")


async def _metrics_snapshot_loop():
    """Background ticker that periodically snapshots aggregate counters into
    metric_snapshot. Same shape as _spawn_revival_loop / _season_announcement_loop.
    """
    while not bot.is_closed():
        try:
            await asyncio.sleep(METRICS_SNAPSHOT_INTERVAL)
            await _metrics_snapshot_tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("metrics snapshot loop iteration failed")


def _season_announcement_status():
    """Returns (current_season, season_ends_tomorrow) using the same epoch and
    clock as refresh_quests. season_ends_tomorrow is True on the last calendar
    day of the month — the day before the 1st rolls a fresh season."""
    start_date = datetime.datetime(2026, 4, 1)
    now = discord.utils.utcnow() + datetime.timedelta(hours=4)
    current_season = (now.year - start_date.year) * 12 + (now.month - start_date.month)
    tomorrow = now + datetime.timedelta(days=1)
    return current_season, (tomorrow.day == 1)


def _season_announce_enabled(server) -> bool:
    """server.season_announcements, defaulting to True if the column doesn't
    exist yet (migration 021 unrun) — so a partially-migrated DB still warns."""
    try:
        val = server.season_announcements
    except (KeyError, AttributeError):
        return True
    return True if val is None else bool(val)


def _build_season_warning_embed(current_season: int) -> discord.Embed:
    next_season = current_season + 1
    try:
        next_levels = len(config.battle["seasons"][str(next_season)])
    except Exception:
        next_levels = None
    levels_line = f"\n\n🆕 **Season {next_season}** starts with **{next_levels} levels** to climb." if next_levels else ""
    return discord.Embed(
        title="⏳ Cattlepass season ends tomorrow!",
        color=Colors.brown,
        description=(
            "When the new season begins on the 1st, **each player's per-server profile resets:**\n"
            f"• 🪙 **Coins** → reset to **{SEASON_STARTING_COINS:,}** (season starting allowance)\n"
            "• ⬆️ **Cattlepass** level & XP → 0 (quests reset)\n"
            "• 🎩 **Catnip / mafia** level, bounties & perks wiped\n"
            "• 🔫 **Jobs** heat, respect, faction rep & job perks reset\n"
            "• 📦 **All packs** cleared (event packs included)\n\n"
            "**Kept:** your cats, prisms, stocks, discovered cats, achievements, and streaks.\n\n"
            "⚠️ Spend your coins and **open your packs** before the reset!"
            + levels_line
        ),
    )


async def _broadcast_season_warning() -> int:
    """Post the season-ending warning to every setupped channel whose server
    hasn't opted out. The channel table is keyed by channel_id with no
    guild_id, so we resolve each channel's guild from cache to check the
    per-server toggle (cached per guild). Per-channel failures (missing perms,
    deleted/uncached channel) are skipped silently, like cat!news / spawn_cat."""
    current_season, _ = _season_announcement_status()
    embed = _build_season_warning_embed(current_season)
    server_optin: dict[int, bool] = {}  # guild_id -> enabled, one Server fetch per guild
    sent = 0
    try:
        async for ch in Channel.all():
            try:
                ch_obj = bot.get_channel(int(ch.channel_id))
                if ch_obj is None or ch_obj.guild is None:
                    continue
                gid = ch_obj.guild.id
                if gid not in server_optin:
                    server = await Server.get_or_create(server_id=gid)
                    server_optin[gid] = _season_announce_enabled(server)
                if not server_optin[gid]:
                    continue
                await ch_obj.send(embed=embed)
                sent += 1
                await asyncio.sleep(0.1)
            except Exception:
                pass
    except Exception:
        logging.exception("season warning broadcast failed")
    return sent


def _build_season_intro_embed(new_season: int) -> discord.Embed:
    """The 'Season N starts now' greeting, broadcast on the 1st alongside the
    recap. Pairs with _build_season_warning_embed: the warning lists what's
    about to be wiped, this welcomes the player to the fresh season."""
    try:
        next_levels = len(config.battle["seasons"][str(new_season)])
    except Exception:
        next_levels = None
    levels_line = f"• 📜 **{next_levels} levels** of packs, rare cats, and rain minutes to climb.\n" if next_levels else ""
    return discord.Embed(
        title=f"🆕 Season {new_season} starts now!",
        color=Colors.brown,
        description=(
            f"The Cattlepass has reset and **Season {new_season}** is live.\n\n"
            + levels_line
            + f"• 🪙 You start with **{SEASON_STARTING_COINS:,}** coins — go spend them.\n"
            "• ⏱️ Your `/battlepass` quests have rerolled — check the catch, misc, extra, and challenge slots.\n"
            "• 🏆 Your `/catprofile` medals, stocks, prisms, cats, achievements, and streaks all stayed with you.\n\n"
            "Good hunting!"
        ),
    )


async def _broadcast_season_intro() -> int:
    """Post the new-season greeting to every setupped channel whose server
    hasn't opted out. Mirrors _broadcast_season_warning, runs on the 1st right
    after _broadcast_season_recap so players see: who just won → fresh season
    starts now."""
    current_season, _ = _season_announcement_status()
    embed = _build_season_intro_embed(current_season)
    server_optin: dict[int, bool] = {}
    sent = 0
    try:
        async for ch in Channel.all():
            try:
                ch_obj = bot.get_channel(int(ch.channel_id))
                if ch_obj is None or ch_obj.guild is None:
                    continue
                gid = ch_obj.guild.id
                if gid not in server_optin:
                    server = await Server.get_or_create(server_id=gid)
                    server_optin[gid] = _season_announce_enabled(server)
                if not server_optin[gid]:
                    continue
                await ch_obj.send(embed=embed)
                sent += 1
                await asyncio.sleep(0.1)
            except Exception:
                pass
    except Exception:
        logging.exception("season intro broadcast failed")
    return sent


# ---- Season recap leaderboard (posts the just-ended season's winners) ----
# The season wipe is lazy per-player, so a live query on the 1st would miss the
# most-active players (they log in first and get reset). Instead we SNAPSHOT
# per-guild top-5s during the last calendar day (overwriting each tick, so the
# final pre-rollover tick wins) and BROADCAST from that snapshot on the 1st.
_SEASON_RECAP_FILE = "season_recap.json"

# Categories: 7 per-server top-5 boards. Cumulative metrics ("this season"
# totals) are computed as lifetime_counter - season_stat_baseline[key] so they
# reset cleanly each season; for Season 1 the baseline is '{}' (-> 0), so the
# value is the full lifetime — correct, since this instance launched at S1 start.
def _season_diff_sql(col: str) -> str:
    """SQL fragment: a lifetime counter minus its captured season baseline."""
    return f"({col} - COALESCE((season_stat_baseline->>'{col}')::bigint, 0))"


async def _season_recap_for_guild(gid: int, season: int) -> dict | None:
    """Run the 7 top-5 queries for one guild against the ending `season`.
    Returns a dict of category -> [[user_id, *values], ...], or None if the
    guild had no ranked players in any category."""
    earner = _season_diff_sql("coins_earned")
    cats = _season_diff_sql("total_catches")
    heists = _season_diff_sql("jobs_completed")
    gambling = (
        f"{_season_diff_sql('roulette_coins_won')} - {_season_diff_sql('roulette_coins_bet')} "
        f"+ {_season_diff_sql('catslots_coins_won')} + {_season_diff_sql('catslots_bonus_coins_won')} "
        f"- {_season_diff_sql('catslots_coins_bet')}"
    )
    stocks = f"{_season_diff_sql('stock_coins_earned')} - {_season_diff_sql('stock_coins_spent')}"

    bp = await Profile.collect_limit(
        ["user_id", "battlepass", "progress"],
        "guild_id = $1 AND season = $2 AND (battlepass > 0 OR progress > 0) "
        "ORDER BY battlepass DESC, progress DESC LIMIT 5",
        gid, season,
    )
    mafia = await Profile.collect_limit(
        ["user_id", "catnip_level"],
        "guild_id = $1 AND season = $2 AND catnip_level > 0 ORDER BY catnip_level DESC LIMIT 5",
        gid, season,
    )

    async def _computed(expr: str):
        return await Profile.collect_limit(
            ["user_id", RawSQL(f"({expr}) AS season_val")],
            f"guild_id = $1 AND season = $2 AND ({expr}) > 0 ORDER BY season_val DESC LIMIT 5",
            gid, season,
        )

    earner_r = await _computed(earner)
    cats_r = await _computed(cats)
    heists_r = await _computed(heists)
    gambling_r = await _computed(gambling)
    stocks_r = await _computed(stocks)

    data = {
        "battlepass": [[int(r["user_id"]), int(r["battlepass"]), int(r["progress"])] for r in bp],
        "mafia": [[int(r["user_id"]), int(r["catnip_level"])] for r in mafia],
        "earner": [[int(r["user_id"]), int(r["season_val"])] for r in earner_r],
        "cats": [[int(r["user_id"]), int(r["season_val"])] for r in cats_r],
        "heists": [[int(r["user_id"]), int(r["season_val"])] for r in heists_r],
        "gambling": [[int(r["user_id"]), int(r["season_val"])] for r in gambling_r],
        "stocks": [[int(r["user_id"]), int(r["season_val"])] for r in stocks_r],
    }
    if not any(data.values()):
        return None
    return data


async def _capture_season_recap_snapshot() -> None:
    """Compute every setupped guild's top-5 boards for the (still-active) ending
    season and persist them to season_recap.json. Overwrites on each call so the
    last last-day tick before rollover is the one that gets broadcast. Requires
    migration 022 (season_stat_baseline + counter columns); skips otherwise."""
    if not await _recap_columns_present():
        logging.info("season recap snapshot skipped: migration 022 columns absent")
        return
    snap_season, _ = _season_announcement_status()
    guild_ids: set[int] = set()
    try:
        async for ch in Channel.all():
            ch_obj = bot.get_channel(int(ch.channel_id))
            if ch_obj is not None and ch_obj.guild is not None:
                guild_ids.add(ch_obj.guild.id)
    except Exception:
        logging.exception("season recap snapshot: guild enumeration failed")
        return
    guilds_data: dict[str, dict] = {}
    for gid in guild_ids:
        try:
            g = await _season_recap_for_guild(gid, snap_season)
        except Exception:
            logging.exception("season recap snapshot failed for guild %s", gid)
            continue
        if g:
            guilds_data[str(gid)] = g
    try:
        with open(_SEASON_RECAP_FILE, "w", encoding="utf-8") as f:
            json.dump({"season": snap_season, "guilds": guilds_data}, f)
    except Exception:
        logging.exception("failed to persist season recap snapshot")


def _build_season_recap_embed(guild_name: str, season_num: int, gdata: dict) -> discord.Embed | None:
    """Build one guild's recap embed from its snapshot dict. Returns None if
    every category is empty (nothing to show)."""
    medals = {0: "🥇", 1: "🥈", 2: "🥉"}

    def _board(entries, render) -> str:
        lines = []
        for i, e in enumerate(entries[:5]):
            tag = medals.get(i, f"{i + 1}.")
            lines.append(f"{tag} <@{e[0]}> — {render(e)}")
        return "\n".join(lines)

    # (snapshot key, field label, value renderer)
    categories = [
        ("battlepass", "🏆 Cattlepass", lambda e: f"Lv {e[1]:,}"),
        ("mafia", "🎩 Mafia", lambda e: f"Lv {e[1]:,}"),
        ("earner", "🪙 Biggest Earner", lambda e: f"{e[1]:,}"),
        ("cats", "🐱 Cats Caught", lambda e: f"{e[1]:,}"),
        ("heists", "🔫 Heists", lambda e: f"{e[1]:,}"),
        ("gambling", "🎰 Gambling", lambda e: f"{e[1]:+,}"),
        ("stocks", "📈 Stocks", lambda e: f"{e[1]:+,}"),
    ]
    if not any(gdata.get(k) for k, _, _ in categories):
        return None

    bp = gdata.get("battlepass") or []
    champion = bp[0][0] if bp else None
    desc = (
        f"👑 **{guild_name}'s Season {season_num} champion:** <@{champion}>!\n\nHere's how the season shook out:"
        if champion
        else f"Here's how Season {season_num} shook out in **{guild_name}**:"
    )
    embed = discord.Embed(title=f"🏆 Season {season_num} Recap", description=desc, color=Colors.brown)
    for key, label, render in categories:
        entries = gdata.get(key) or []
        if not entries:
            continue
        embed.add_field(name=label, value=_board(entries, render), inline=True)
    if season_num == 1:
        embed.set_footer(text="🆕 Gambling, stock & earnings totals cover the period since this feature launched.")
    return embed


# Season-trophy categories: the 3 recap categories that promote their top-3 to
# permanent trophies on profile.season_trophies. Snapshot keys match
# _season_recap_for_guild() exactly.
TROPHY_CATEGORIES: list[tuple[str, str, str]] = [
    ("earner", "🪙 Most Coins Earned", "Coins"),
    ("cats", "🐱 Most Cats Caught", "Cats"),
    ("heists", "🔫 Most Heists Completed", "Heists"),
]
TROPHY_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _build_trophy_embed(guild_name: str, season_num: int, gdata: dict) -> discord.Embed | None:
    """Build the "Season N Champions" ceremony embed — top 3 per trophy category
    with medal emojis and totals. Returns None if every trophy category is
    empty (no one earned anything that season)."""
    sections: list[tuple[str, str]] = []
    for key, label, _abbr in TROPHY_CATEGORIES:
        entries = (gdata.get(key) or [])[:3]
        if not entries:
            continue
        lines = []
        for i, e in enumerate(entries):
            medal = TROPHY_MEDALS[i + 1]
            lines.append(f"{medal} <@{e[0]}> — {e[1]:,}")
        sections.append((label, "\n".join(lines)))
    if not sections:
        return None
    embed = discord.Embed(
        title=f"🏆 Season {season_num} Champions",
        description=(
            f"Trophies awarded to **{guild_name}**'s top players. "
            f"These show up on `/catprofile` forever — wear them proudly!"
        ),
        color=Colors.brown,
    )
    for label, value in sections:
        embed.add_field(name=label, value=value, inline=False)
    return embed


async def _award_season_trophies(season: int, gid: int, gdata: dict) -> None:
    """Persist top-3 trophies for one guild to the winning profiles. Idempotent:
    skips entries already present in profile.season_trophies (handles re-broadcast
    after a crash mid-loop). No-ops cleanly if migration 024 hasn't run."""
    for category, _label, _abbr in TROPHY_CATEGORIES:
        entries = (gdata.get(category) or [])[:3]
        for i, e in enumerate(entries):
            user_id = int(e[0])
            rank = i + 1
            try:
                profile = await Profile.get_or_create(guild_id=gid, user_id=user_id)
                existing = list(getattr(profile, "season_trophies", None) or [])
                if any(
                    isinstance(t, dict)
                    and int(t.get("season", -1)) == season
                    and t.get("category") == category
                    and int(t.get("rank", -1)) == rank
                    for t in existing
                ):
                    continue
                existing.append({"season": season, "category": category, "rank": rank})
                profile.season_trophies = existing
                await profile.save()
            except Exception:
                logging.exception(
                    "season trophy award failed: gid=%s user=%s cat=%s rank=%s",
                    gid, user_id, category, rank,
                )


def _format_season_trophies(trophies) -> str:
    """Render profile.season_trophies as a compact medal list, newest season
    first then by rank within season. Returns '' if empty. Caps at 12 entries
    with an overflow suffix to stay well under Discord's 1024-char field limit."""
    if not trophies:
        return ""
    cat_labels = {key: abbr for key, _label, abbr in TROPHY_CATEGORIES}
    items: list[tuple[int, int, str]] = []
    for t in trophies:
        try:
            s = int(t["season"])
            r = int(t["rank"])
            c = t["category"]
        except (KeyError, TypeError, ValueError):
            continue
        if r not in TROPHY_MEDALS or c not in cat_labels:
            continue
        items.append((s, r, c))
    if not items:
        return ""
    items.sort(key=lambda x: (-x[0], x[1]))
    rendered = [f"{TROPHY_MEDALS[r]} S{s} {cat_labels[c]}" for s, r, c in items]
    if len(rendered) > 12:
        extra = len(rendered) - 12
        rendered = rendered[:12] + [f"_(+{extra} more)_"]
    return " • ".join(rendered)


async def _broadcast_season_recap() -> int:
    """Post each guild's recap embed (from the persisted snapshot) to its
    setupped channels, honoring the same season_announcements opt-out. Returns
    the channel count posted to."""
    try:
        with open(_SEASON_RECAP_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (FileNotFoundError, ValueError):
        logging.warning("season recap broadcast: no snapshot file to read")
        return 0
    just_ended = _season_announcement_status()[0] - 1
    if int(payload.get("season", -999)) != just_ended:
        logging.warning(
            "season recap broadcast: snapshot season %s != just-ended %s; skipping",
            payload.get("season"), just_ended,
        )
        return 0

    embeds: dict[int, discord.Embed] = {}
    trophy_embeds: dict[int, discord.Embed] = {}
    guild_data: dict[int, dict] = {}
    for gid_str, gdata in (payload.get("guilds") or {}).items():
        gid = int(gid_str)
        guild = bot.get_guild(gid)
        guild_name = guild.name if guild else "the server"
        emb = _build_season_recap_embed(guild_name, just_ended, gdata)
        if emb is not None:
            embeds[gid] = emb
        trophy_emb = _build_trophy_embed(guild_name, just_ended, gdata)
        if trophy_emb is not None:
            trophy_embeds[gid] = trophy_emb
            guild_data[gid] = gdata

    # Award trophies for every guild whose snapshot has trophy-category data,
    # independent of the per-server announcement opt-in. A server can opt out
    # of channel noise; that shouldn't cost its players their permanent
    # /catprofile medals. _award_season_trophies is idempotent (existing-entry
    # check at the rank level) so a restart mid-loop is safe.
    for gid, gdata in guild_data.items():
        try:
            await _award_season_trophies(just_ended, gid, gdata)
        except Exception:
            logging.exception("season trophy award failed for guild %s", gid)

    server_optin: dict[int, bool] = {}
    sent = 0
    try:
        async for ch in Channel.all():
            try:
                ch_obj = bot.get_channel(int(ch.channel_id))
                if ch_obj is None or ch_obj.guild is None:
                    continue
                gid = ch_obj.guild.id
                if gid not in embeds:
                    continue
                if gid not in server_optin:
                    server = await Server.get_or_create(server_id=gid)
                    server_optin[gid] = _season_announce_enabled(server)
                if not server_optin[gid]:
                    continue
                await ch_obj.send(embed=embeds[gid])
                if gid in trophy_embeds:
                    try:
                        await ch_obj.send(embed=trophy_embeds[gid])
                    except Exception:
                        logging.exception("trophy embed send failed for channel %s", ch.channel_id)
                sent += 1
                await asyncio.sleep(0.1)
            except Exception:
                pass
    except Exception:
        logging.exception("season recap broadcast failed")
    return sent


async def _season_announcement_loop():
    """Standalone ticker that broadcasts the "season ends tomorrow" warning
    once on the last calendar day of the month. Fixed cadence (not
    on_message-driven) so it fires even in quiet periods. Reload-safe via
    config.season_announce_task; setup() cancels the prior task first.

    Dedup: last_season_warned (persisted to season_warn.txt) records the
    season we last warned about, so the broadcast happens at most once per
    season even across restarts. We mark it AFTER the broadcast returns — the
    broadcast swallows per-channel errors and won't raise, so a mid-broadcast
    process death is the only double-send risk (rare, and a duplicate warning
    beats no warning)."""
    global last_season_warned, last_season_recapped, last_season_introed
    while not bot.is_closed():
        try:
            await asyncio.sleep(SEASON_ANNOUNCE_INTERVAL)
            current_season, ends_tomorrow = _season_announcement_status()
            if ends_tomorrow and current_season != last_season_warned:
                count = await _broadcast_season_warning()
                last_season_warned = current_season
                try:
                    with open("season_warn.txt", "w", encoding="utf-8") as f:
                        f.write(str(current_season))
                except Exception:
                    logging.exception("failed to persist season_warn marker")
                logging.info("season-end warning sent to %d channels (season %d ending)", count, current_season)

            # Season recap: snapshot standings on every last-day tick (the final
            # tick before midnight wins), then broadcast on the 1st once per
            # just-ended season.
            if ends_tomorrow:
                try:
                    await _capture_season_recap_snapshot()
                except Exception:
                    logging.exception("season recap snapshot failed")
            now_local = discord.utils.utcnow() + datetime.timedelta(hours=4)
            just_ended = current_season - 1
            if now_local.day == 1 and just_ended != last_season_recapped:
                count = await _broadcast_season_recap()
                last_season_recapped = just_ended
                try:
                    with open("season_recap.txt", "w", encoding="utf-8") as f:
                        f.write(str(just_ended))
                except Exception:
                    logging.exception("failed to persist season_recap marker")
                logging.info("season recap broadcast to %d channels (season %d)", count, just_ended)

            # Season intro: independent dedup on current_season so the greeting
            # fires once per fresh season on the 1st. Runs after the recap so
            # players see "S{N-1} champions" → "S{N} starts now" in that order.
            if now_local.day == 1 and current_season != last_season_introed:
                count = await _broadcast_season_intro()
                last_season_introed = current_season
                try:
                    with open("season_intro.txt", "w", encoding="utf-8") as f:
                        f.write(str(current_season))
                except Exception:
                    logging.exception("failed to persist season_intro marker")
                logging.info("season intro broadcast to %d channels (season %d)", count, current_season)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("season announcement loop iteration failed")


# Snapshot which spawn images exist at module load. A rarity whose image is
# missing is never picked by spawn_cat (avoids FileNotFoundError if a rarity is
# added to type_dict before its art is in place). cat!restart re-snapshots.
_SPAWN_IMAGE_PRESENT: set[str] = {
    k for k in type_dict if os.path.exists(f"images/spawn/{k.lower()}_cat.png")
}


def _spawn_eligible_type_dict() -> dict[str, int]:
    """type_dict filtered to rarities eligible to spawn right now. Drops any
    rarity gated by a future season (`rarity_min_season` in tuning.json) AND
    any rarity whose spawn image is missing on disk."""
    if not RARITY_MIN_SEASON and len(_SPAWN_IMAGE_PRESENT) == len(type_dict):
        return type_dict
    current_season = _season_announcement_status()[0]
    return {
        k: v for k, v in type_dict.items()
        if k in _SPAWN_IMAGE_PRESENT
        and RARITY_MIN_SEASON.get(k, 0) <= current_season
    }


def _season_eligible_cattypes() -> list[str]:
    """cattypes filtered by the season gate only (no image check). Used by
    pack-open rarity rolls and perk drops — places where the bot GIVES the
    player a cat, so getting a brand-new rarity is upside, not friction.
    Returns the full list if no rarity_min_season config is set."""
    if not RARITY_MIN_SEASON:
        return cattypes
    current_season = _season_announcement_status()[0]
    return [k for k in cattypes if RARITY_MIN_SEASON.get(k, 0) <= current_season]


def _quest_eligible_cattypes() -> list[str]:
    """cattypes filtered for quest/bounty/price ASSIGNMENT — strict subset of
    _season_eligible_cattypes(). Additionally excludes rarities whose
    `rarity_min_season` equals the current season (i.e., they debuted this
    season). New rarities get a one-season grace period during which they
    can spawn / be earned but can't be REQUIRED as a quest target — gives
    players time to actually catch some before being asked to turn them in.
    Used by set_mafia_offer (catnip price) and get_bounties (catnip
    bounties)."""
    base = _season_eligible_cattypes()
    if not RARITY_MIN_SEASON:
        return base
    current_season = _season_announcement_status()[0]
    return [k for k in base if RARITY_MIN_SEASON.get(k, 0) != current_season]


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
        eligible = _spawn_eligible_type_dict()
        localcat = random.choices(list(eligible.keys()), weights=list(eligible.values()))[0]
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

        # payout. The SET clause also bumps the season-recap counters
        # (coins_earned + stock_coins_earned) when migration 022 is present; on
        # an un-migrated DB it falls back to crediting coins only.
        _div_set = "coins = coins + sh.quantity * $1"
        if await _recap_columns_present():
            _div_set += (
                ", coins_earned = coins_earned + sh.quantity * $1"
                ", stock_coins_earned = stock_coins_earned + sh.quantity * $1"
            )
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
            SET {_div_set}
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
        # stock_dividend_boost (job perk, timed): per-holder bonus on top of
        # the bulk payout. Iterates only profiles with any active job perks
        # (the SQL filter is loose — jsonb_array_length > 0 — so we still
        # have to check membership in Python). Try/except so a perk failure
        # cannot poison the global payout.
        try:
            stock_col_attr = f"stock_{stock.ticker.lower()}"
            async for fp in Profile.filter(
                "jsonb_array_length(job_perks) > 0",
                fields=["id", "user_id", "guild_id", "job_perks", "coins", stock_col_attr],
                refetch=False,
            ):
                if "stock_dividend_boost" not in _perks_active_ids(fp):
                    continue
                pct = float(_perks_strength(fp, "stock_dividend_boost", "amount_bonus_pct", 0.0) or 0.0)
                if pct <= 0:
                    continue
                holdings = int(getattr(fp, stock_col_attr, 0) or 0)
                if holdings <= 0:
                    continue
                bonus = int(round(holdings * int(stock.amount) * pct))
                if bonus == 0:
                    continue
                fp.coins = int(getattr(fp, "coins", 0) or 0) + bonus
                _bump(fp, "coins_earned", bonus)
                _bump(fp, "stock_coins_earned", bonus)
                await fp.save()
        except Exception:
            logging.exception("stock_dividend_boost per-holder bonus failed; bulk payout still applied")

        # Stock v2: write a dividend headline into the news feed AND queue a
        # small ex-div price drop for the next tick — applied=false with
        # fires_at=now means `_consume_due_events` picks it up on the next
        # tick and applies STOCK_DIVIDEND_EX_DIV_IMPULSE to the log-return.
        # Real cashflow leaves the "company"; the price reflects it.
        try:
            now_ts = int(time.time())
            await NewsEvent.create(
                time=now_ts,
                fires_at=now_ts,
                ticker=stock.ticker,
                event_type="dividend",
                headline=stock_news.pick_headline(stock.ticker, "dividend", 0.0),
                impulse_pct=float(STOCK_DIVIDEND_EX_DIV_IMPULSE),
                applied=False,
            )
        except Exception:
            logging.exception("dividend newsevent insert failed; payout still applied")
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
    if reminder_type != "vote":
        # Stale button on an old quest-reminder DM. Quest reminders were
        # retired; only vote postpone is live now.
        await interaction.response.send_message("this reminder type is no longer supported", ephemeral=True)
        return
    user = await User.get_or_create(user_id=interaction.user.id)
    user.reminder_vote = int(time.time()) + 30 * 60
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
    global pointlaugh_ratelimit, reactions_ratelimit, reaction_cooldown, last_loop_time, loop_count, catchcooldown, temp_belated_storage, fakecooldown, last_vote_cursor, fish_lock
    pointlaugh_ratelimit = {}
    reactions_ratelimit = {}
    reaction_cooldown = {}
    catchcooldown = {}
    fakecooldown = {}
    # leak guard: active /fish sessions re-add themselves every 10ms tick
    fish_lock = []
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

                    # NOTE: top.gg's cursor API re-returns the page containing
                    # the most recent vote on every poll, so the_votes is
                    # almost always non-empty even when nothing new happened.
                    # do_vote() dedups stale ones (< 1h since the user's last
                    # counted vote) — mirror that check here so the log only
                    # speaks up when a vote was actually processed.
                    the_votes = data.get("data", [])
                    new_votes = 0
                    for vote_data in the_votes:
                        if not vote_data.get("created_at", 0) or not vote_data.get("platform_id", 0):
                            continue
                        created_at = datetime.datetime.fromisoformat(vote_data["created_at"]).timestamp()
                        vote_user = await User.get_or_create(user_id=int(vote_data["platform_id"]))
                        if created_at - vote_user.vote_time_topgg >= 3600:
                            new_votes += 1
                        await do_vote(vote_user, created_at)

                    last_vote_cursor = data.get("cursor", "")
                    with open("cursor.txt", "w") as f:
                        f.write(last_vote_cursor)
                    if new_votes:
                        logging.info("Vote replay: processed %d new vote%s", new_votes, "s" if new_votes != 1 else "")
                    else:
                        logging.debug("Vote replay: nothing new (%d stale), cursor %s", len(the_votes), last_vote_cursor)

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

    # stock market v2: one simulated tick per background_loop cycle
    # (~MAIN_LOOP_INTERVAL seconds). See _run_stock_tick.
    try:
        await _run_stock_tick()
    except Exception:
        logging.exception("stock tick failed")

    # cancel old orders
    async for order in Order.filter("time > 0 AND time < $1", time.time() - 3600 * 24 * 7):
        profile = await Profile.get_or_none(id=order.user_id)
        if profile:
            if order.type_buy:
                profile.coins += order.quantity * order.price
                _bump(profile, "stock_coins_spent", -(order.quantity * order.price))
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

            view.add_item(Button(
                label="Vote on top.gg",
                style=ButtonStyle.url,
                url=TOP_GG_VOTE_URL,
                emoji=get_emoji("topgg"),
            ))

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


async def _ensure_emojis_loaded():
    """Populate the `emojis` global from the Discord API at most once.
    Both on_connect and on_ready call this; the lock serializes them so the
    second caller sees the populated dict instead of racing a second GET on
    the rate-limited /applications/{id}/emojis route."""
    global emojis
    if emojis:
        return
    async with _emojis_lock:
        if emojis:
            return
        emojis = {emoji.name: str(emoji) for emoji in await bot.fetch_application_emojis()}


# fetch app emojis early
async def on_connect():
    await _ensure_emojis_loaded()


# some code which is run when bot is started
async def on_ready():
    global OWNER_ID, on_ready_debounce, gen_credits
    if on_ready_debounce:
        return
    on_ready_debounce = True
    logging.info("cat is now online")
    await _ensure_emojis_loaded()
    appinfo = bot.application
    if appinfo.team and appinfo.team.owner_id:
        OWNER_ID = appinfo.team.owner_id
    else:
        OWNER_ID = appinfo.owner.id

    gen_credits = "\n".join(
        [
            "Self-hosted Cat Bot instance",
            "Source: <https://github.com/sneezeparty/catbot7>",
        ]
    )

    # Cat Bot Store reconciliation. Discord entitlement events can be missed
    # while the bot is offline or while a shard is resuming. A one-shot pass
    # of bot.entitlements() at startup brings the DB in line with truth.
    # Spawned as a background task so a slow Discord API response doesn't
    # delay the rest of on_ready.
    if config.STORE_ENABLED:
        bot.loop.create_task(_store_reconcile_entitlements())


async def _store_reconcile_entitlements() -> None:
    """Iterate active entitlements once and write any drift into the DB.
    Idempotent — _apply_entitlement_create is a no-op when the SKU is
    already recorded. asyncio.sleep(0) yields between users so the gateway
    heartbeat keeps ticking even if there are thousands of entitlements."""
    if not config.STORE_ENABLED:
        return
    try:
        by_user: dict[int, set[str]] = {}
        async for ent in bot.entitlements(exclude_ended=True):
            user_id = int(getattr(ent, "user_id", 0) or 0)
            sku_id = str(getattr(ent, "sku_id", "") or "")
            if not user_id or not sku_id:
                continue
            by_user.setdefault(user_id, set()).add(sku_id)
            # Apply now so a partial reconciliation still makes progress.
            await _apply_entitlement_create(ent)
            await asyncio.sleep(0)

        # Inverse pass: anyone with stale SKUs in the DB that Discord no
        # longer reports gets them removed. This is the only place we catch
        # entitlements that lapsed while the bot was offline.
        if not by_user:
            return
        supporter_ids = _supporter_sku_ids()
        async for user in User.filter("entitlements IS NOT NULL AND entitlements <> '[]'::jsonb"):
            held = set(_user_entitlements_load(user))
            truth = by_user.get(int(user.user_id), set())
            stale = held - truth
            if not stale:
                continue
            user.entitlements = sorted(held - stale)
            _recompute_premium(user)
            await user.save()
            await asyncio.sleep(0)

        logging.info(
            "store reconcile: %d users with active entitlements, supporter SKUs=%d",
            len(by_user),
            len(supporter_ids),
        )
    except Exception:
        logging.exception("store reconcile failed")


# ============================== bonus cats 🎁 ==============================
# Ported from upstream's "june update" (commit 3398188) as a SOLO variant:
# there is no late catching here, so only the original catcher can play
# (the Go! button in the catch handler gates on the catcher's id). One
# unique minigame per cattype; success = +3 of that cat. Gremlin's minigame
# became Baby's, and Shadow/Terminator got new ones (we have those types,
# upstream doesn't).

sentences = [
    "The quick brown fox jumps over the lazy dog.",
    "Cat Bot is a Discord bot about catching cats.",
    "The birch canoe slid on the smooth planks.",
    "Glue the sheet to the dark blue background.",
    "It's easy to tell the depth of a well.",
    "These days a chicken leg is a rare dish.",
    "Rice is often served in round bowls.",
    "The juice of lemons makes fine punch.",
    "The box was thrown beside the parked truck.",
    "The hogs were fed chopped corn and garbage.",
    "Four hours of steady work faced us.",
    "A large size in stockings is hard to sell.",
    "Stop posting about Among Us, I'm tired of seeing it!",
    "I love Cat Bot, it is great, now there is a new update!",
    "Yo, my name is Jeremy, my parents left when I was three!",
    "There is just a single rule, Jeremy is really cool!",
    "I am cool and I am green, better than at first it may seem!",
    "Cell machine sticky cell is hypothetical cell",
    "im gonna make catbot - Poggers!",
    "be nice or cat will punish you",
    "Cat Bot pinned a message to this channel.",
    "your sins will not be forgotten",
    "Who needs friends, all i need is to have the best cats",
    "Jane Cat Bot here, I would like to say thanks to myself",
    "Never gonna give you up, never gonna let you down!",
    "Now contains 32 random daily cats!",
    "Cat Rains make cats spawn super fast for a limited period.",
    "spice it up a bit, ban a random half of the server",
    "ok brumbler statue building i think i eat sand sometimes",
    "blame freshpenguin for anything bad which happens",
    "how do i use catch, im on ipad how to use catch",
    "Throw your phone out the window or it will explode!",
    "okay chat an excercise, calmly welcome the new member",
    "devlog is now a separeate channel yay",
    "host update: previous host has been seized by authorities",
    "You are the best Minecraft Discord server I've ever been on.",
    "Cat Bot was permanently banned by RiskLM for silly.",
]


def to_roman_numeral(value):
    roman_map = {1: "I", 4: "IV", 5: "V", 9: "IX", 10: "X", 40: "XL", 50: "L", 90: "XC", 100: "C", 400: "CD", 500: "D", 900: "CM", 1000: "M"}
    result = ""
    remainder = value
    for i in sorted(roman_map.keys(), reverse=True):
        times = remainder // i
        remainder %= i
        result += roman_map[i] * times
    return result


def is_prime(n):
    if n < 2:
        return False

    s = [True] * (n + 1)
    s[0] = s[1] = False

    for i in range(2, int(n**0.5) + 1):
        if s[i]:
            for j in range(i * i, n + 1, i):
                s[j] = False
    return s[n]


async def play_minigame(interaction: discord.Interaction, cattype: str):
    start = int(time.time())
    end = start + BONUS_MINIGAME_DEADLINE_SECONDS

    modal = Modal(title="Bonus Cat Minigame")
    if cattype == "Fine":
        random_letter = random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        random_text = random.choice(sentences)
        answer = random_text.lower().count(random_letter.lower())
        modal.add_item(TextDisplay(f"## Count the amount of {random_letter}'s in the sentence below\n\n{random_text}"))
        modal.add_item(TextInput(label="Answer", id=67))
    elif cattype == "Nice":
        random_numbers = [random.randint(-100, 100) for _ in range(4)]
        answer = " ".join(map(str, sorted(random_numbers)))
        modal.add_item(TextDisplay(f"## Sort the numbers in ascending order\n\n{', '.join(map(str, random_numbers))}"))
        modal.add_item(TextInput(label="Answer", id=67))
    elif cattype == "Good":
        random_text = random.choice(sentences)
        answer = 0
        for vowel in "AEIOU":
            answer += random_text.lower().count(vowel.lower())
        modal.add_item(TextDisplay(f"## Count the amount of vowels (excluding Y) in the sentence below\n\n{random_text}"))
        modal.add_item(TextInput(label="Answer", id=67))
    elif cattype == "Rare":
        base = random.randint(200, 900)
        num_range = [base + (i * 10) for i in range(-2, 2)]
        random.shuffle(num_range)
        items = {
            num_range[0]: str(num_range[0]),
            num_range[1]: str(num_range[1] // 2) + " * 2",
            num_range[2]: str(num_range[2] * 3) + "/3",
            num_range[3]: str(num_range[3] - 111) + " + 111",
        }
        items = dict(random.sample(list(items.items()), len(items)))
        options = [discord.RadioGroupOption(label=value, value=key) for key, value in items.items()]
        modal.add_item(discord.ui.Label(text="Choose the biggest number", component=discord.ui.RadioGroup(options=options, id=67)))
        answer = max(items.keys())
    elif cattype == "Wild":
        options = [discord.RadioGroupOption(label="heads", value="heads"), discord.RadioGroupOption(label="tails", value="tails")]
        modal.add_item(discord.ui.Label(text="Pick heads or tails", component=discord.ui.RadioGroup(options=options, id=67)))
        answer = random.choice(["heads", "tails"])
    elif cattype == "Baby":
        # baby's first order of operations (upstream gave this one to Gremlin)
        a, b, c = random.randint(1, 15), random.randint(1, 15), random.randint(2, 10)
        answer = a + b * c
        modal.add_item(discord.ui.Label(text=f"What's the result of {a} + {b} * {c}?", component=TextInput(placeholder="Answer", id=67)))
    elif cattype == "Shadow":
        lookalikes = random.choice([("I", "l", "1"), ("O", "0", "o"), ("S", "5", "s")])
        target = random.choice(lookalikes)
        shadow_string = "".join(random.choice(lookalikes) for _ in range(25))
        answer = shadow_string.count(target)
        modal.add_item(TextDisplay(f"## Count how many `{target}` hide among the lookalikes below\n\n`{shadow_string}`"))
        modal.add_item(TextInput(label="Answer", id=67))
    elif cattype == "Epic":
        random_text = random.choice(sentences)
        answer = random_text.upper()
        modal.add_item(TextDisplay(f"## Retype this text in UPPERCASE\n\n{random_text}"))
        modal.add_item(TextInput(label="Answer", id=67))
    elif cattype == "Sus":
        random_text = random.choice(sentences)
        random_letter = ""
        while not random_letter.isalpha():
            random_letter = random.choice(random_text).upper()
        answer = random_text.replace(random_letter, "").replace(random_letter.lower(), "")
        modal.add_item(TextDisplay(f"## Retype this text without the letter '{random_letter}'\n\n{random_text}"))
        modal.add_item(TextInput(label="Answer", id=67))
    elif cattype == "Brave":
        option_texts = ["ANSWER"]
        for i in range(1, 25):
            option = list("ANSWER")
            while "".join(option) in option_texts:
                random.shuffle(option)
            option_texts.append("".join(option))
        random.shuffle(option_texts)
        options = [discord.SelectOption(label=text, value=text) for text in option_texts]
        modal.add_item(discord.ui.Label(text='Find "ANSWER"', component=discord.ui.Select(options=options, id=67)))
        answer = "ANSWER"
    elif cattype == "Rickroll":
        answer = random.choice(rickroll_list)
        modal.add_item(TextDisplay(f"## Retype this text\n\n{answer}"))
        modal.add_item(TextInput(label="Answer", id=67))
    elif cattype == "Reverse":
        line = random.choice(sentences)
        split_line = line.split()
        split_line.reverse()
        answer = " ".join(split_line)
        modal.add_item(TextDisplay(f"## Reverse the word order of this text\n\n{line}"))
        modal.add_item(TextInput(label="Answer", id=67))
    elif cattype == "Superior":
        number = random.randint(10_000, 99_999)
        answer = sum(int(i) for i in str(number))
        modal.add_item(TextDisplay(f"## What is the sum of the digits of this number\n\n{number}"))
        modal.add_item(TextInput(label="Answer", id=67))
    elif cattype == "Trash":
        inputs = ['TRO', 'JET', 'STR', 'ADJ', 'CRA', 'ISE', 'TIC', 'INT', 'MIN', 'SCA', 'INC', 'VER', 'RED', 'TRA', 'MEN', 'KIL', 'ZAP', 'LUB', 'STA', 'REF', 'LIT', 'IST', 'MIS', 'ANG', 'REV', 'LAT', 'DIS', 'BLA', 'SYR', 'DIG', 'CAT', 'INE', 'LIN', 'RAF', 'PER', 'SAV', 'ROA', 'SCH', 'LOV', 'SOF', 'CON', 'HUN', 'LAG', 'COM', 'ICA', 'INS', 'RIS', 'GAG', 'INO', 'LOW', 'RAT', 'WOR', 'BRE', 'LOG', 'ORI', 'HAN', 'ATT', 'TIN', 'DRA', 'UNP', 'PUR', 'PAL', 'MIL', 'FOR', 'GRA', 'ATE', 'PAT', 'BER', 'BET', 'WEA', 'IOD', 'RES', 'TRI', 'BRO', 'RAN', 'PRO', 'WHI', 'FLA', 'ELL', 'ENT', 'INK', 'ABS', 'CLA', 'CAL', 'OVE', 'IMI', 'ILL', 'COK', 'SHI', 'SAT', 'CRO', 'DEP', 'STI', 'MAT', 'SIN', 'IDE', 'SPL']  # fmt: skip
        answer = random.choice(inputs)
        modal.add_item(
            discord.ui.Label(text=f"Type a 6+ letter word containing {answer}", component=TextInput(placeholder="Answer", id=67, min_length=6))
        )
    elif cattype == "Legendary":
        shift = random.randint(1, 5)
        out = []
        for ch in "CAT":
            out.append(chr((ord(ch) - ord("A") + shift) % 26 + ord("A")))
        answer = "".join(out)
        modal.add_item(TextDisplay(f"## Shift the word CAT forwards alphabetically by {shift} letters"))
        modal.add_item(TextInput(label="Answer", id=67, min_length=3, max_length=3))
    elif cattype == "Mythic":
        answer = random.randint(15, 89)
        modal.add_item(TextDisplay(f"## What's the value of this roman numeral?\n\n{to_roman_numeral(answer)}"))
        modal.add_item(TextInput(label="Answer", id=67))
    elif cattype == "8bit":
        power = random.randint(3, 10)
        answer = 2**power
        modal.add_item(discord.ui.Label(text=f"What's 2 to the power of {power}?", component=TextInput(placeholder="Answer", id=67)))
    elif cattype == "Corrupt":
        bin_string = "".join(random.choice(["0", "1"]) for _ in range(25))
        to_count = random.choice(["0", "1"])
        answer = bin_string.count(to_count)
        modal.add_item(TextDisplay(f"## How many {to_count}s are in this binary number?\n\n{bin_string}"))
        modal.add_item(TextInput(label="Answer", id=67))
    elif cattype == "Professor":
        answer = random.choice(cattypes)
        show = list(answer)
        random.shuffle(show)
        show = "".join(show).upper()
        modal.add_item(TextDisplay(f"## Decode this cat type\n\n{show}"))
        modal.add_item(TextInput(label="Answer", id=67))
    elif cattype == "Divine":
        letter_mappings = {
            "A": "X",
            "C": "R",
            "D": "K",
            "F": "W",
            "G": "Y",
            "H": "B",
            "I": "T",
            "L": "J",
            "M": "N",
            "O": "E",
            "P": "Q",
            "S": "Z",
            "U": "V",
        }
        letter_mappings.update({v: k for k, v in letter_mappings.items()})  # reverse mappings
        sentence = random.choice(sentences).upper()
        pick_index = random.randint(0, len(sentence) - 1)
        while not sentence[pick_index].isalpha():
            pick_index = random.randint(0, len(sentence) - 1)
        changed = sentence[:pick_index] + letter_mappings[sentence[pick_index]] + sentence[pick_index + 1 :]
        answer = sentence[pick_index] + letter_mappings[sentence[pick_index]]
        modal.add_item(TextDisplay(f"## Type a letter which is different in the sentences\n\n{sentence}\n\n{changed}"))
        modal.add_item(TextInput(label="Answer", id=67, max_length=1))
    elif cattype == "Real":
        try:
            # hard 2s timeout: this runs inside the Go! button callback BEFORE
            # send_modal (modals must be the first response, so we can't defer)
            # — a slow API would blow Discord's 3s interaction window.
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
                async with session.get(
                    "https://the-trivia-api.com/v2/questions?limit=1&difficulties=easy",
                    headers={"User-Agent": "CatBot/1.0 https://github.com/milenakos/cat-bot"},
                ) as response:
                    stuff = await response.json()
                    question = stuff[0]
                    question_text = question["question"]["text"]
                    correct_answer = question["correctAnswer"]
                    answers = question["incorrectAnswers"] + [correct_answer]
        except Exception:
            question_text = "Are cats awesome?"
            answers = ["Yes", "No", "Meh", "IDK"]
            correct_answer = "Yes"
        random.shuffle(answers)
        options = []
        answer = correct_answer
        for answer_value in answers:
            options.append(discord.RadioGroupOption(label=answer_value[:100], value=answer_value[:100]))
        modal.add_item(TextDisplay(f"## {question_text}"))
        modal.add_item(discord.ui.Label(text="Answer", component=discord.ui.RadioGroup(options=options, id=67)))
    elif cattype == "Terminator":
        # inverse of Mythic's - you write the roman numeral
        number = random.randint(15, 89)
        answer = to_roman_numeral(number)
        modal.add_item(TextDisplay(f"## Write this number as a roman numeral\n\n{number}"))
        modal.add_item(TextInput(label="Answer", id=67))
    elif cattype == "Ultimate":
        number = random.randint(10, 150)
        answer = "Yes" if is_prime(number) else "No"
        options = [discord.RadioGroupOption(label="Yes", value="Yes"), discord.RadioGroupOption(label="No", value="No")]
        modal.add_item(discord.ui.Label(text=f"Is {number} a prime number?", component=discord.ui.RadioGroup(options=options, id=67)))
    elif cattype == "eGirl":
        answer = "meow"
        modal.add_item(
            discord.ui.Label(
                text="Meow agressively.",
                component=TextInput(placeholder="meow mrrrp miau nyaa~ :3", min_length=69, style=discord.TextStyle.long, id=67),
            )
        )
    modal.add_item(TextDisplay(f"-# Times up <t:{end}:R>"))

    async def check_minigame(interaction: discord.Interaction):
        nonlocal answer
        if time.time() > end:
            await interaction.response.send_message("❌ You weren't fast enough!", ephemeral=True)
            return
        answer_item = modal.find_item(67)
        if isinstance(answer_item, TextInput) or isinstance(answer_item, discord.ui.RadioGroup):
            answer_raw = answer_item.value
        elif isinstance(answer_item, discord.ui.Select):
            answer_raw = answer_item.values[0]
        answer_clean = re.sub(r"[^0-9A-Za-z \-~]+", "", answer_raw)
        answer = re.sub(r"[^0-9A-Za-z \-~]+", "", str(answer))

        if cattype == "Trash" and answer in answer_clean.upper():
            if not config.WORDNIK_API_KEY:
                # no key to validate against - assume it's a word
                correct = True
            else:
                async with aiohttp.ClientSession() as session:
                    try:
                        async with session.get(
                            f"https://api.wordnik.com/v4/word.json/{answer_clean.lower()}/definitions?api_key={config.WORDNIK_API_KEY}&useCanonical=true&includeTags=false&includeRelated=false&limit=1",
                            headers={"User-Agent": "CatBot/1.0 https://github.com/milenakos/cat-bot"},
                        ) as response:
                            response_text = await response.text()
                            correct = "from" in response_text
                    except Exception:
                        # assume word is valid
                        correct = True
        elif cattype == "Trash":
            correct = False
        elif cattype == "Divine":
            # bool() guard: punctuation strips to "" and "" is in every string,
            # which would make any single symbol a guaranteed win (upstream bug)
            correct = bool(answer_clean) and answer_clean.upper() in answer
        elif cattype == "eGirl":
            # need atleast 10 signals
            signals = 0
            answer_clean = answer_clean.lower()
            for word in ["meow", "purr", "nya", "miau", "mrrp", "www", "ppp", "uuu", "333", ":3", "~"]:
                signals += answer_clean.count(word)
            correct = signals >= 10
            answer = "10+ meow signals"
            answer_clean = f"{signals} meow signals"
        elif cattype == "Epic":
            correct = answer_clean == str(answer)
        elif cattype == "Sus":
            # collapse whitespace: removing a standalone word (e.g. "a") leaves
            # a double space the player can't see or retype (upstream bug)
            correct = " ".join(answer_clean.lower().split()) == " ".join(str(answer).lower().split())
        else:
            correct = answer_clean.lower() == str(answer).lower()

        if correct:
            profile = await Profile.get_or_create(user_id=interaction.user.id, guild_id=interaction.guild.id)
            profile.bonus_catches += 1
            profile[f"cat_{cattype}"] += 3
            await profile.save()
            icon = get_emoji(cattype.lower() + "cat")
            await interaction.response.send_message(f"✅ {interaction.user.mention} got +3 {icon} {cattype} bonus cats.")
            await progress(interaction, profile, "bonus")
            if cattype == "Rare":
                await achemb(interaction, "math_jumpscare", "followup")
        else:
            await interaction.response.send_message(f"❌ Better luck next time!\nCorrect answer: `{answer}`\nYour answer: `{answer_clean}`", ephemeral=True)

    modal.on_submit = check_minigame
    await interaction.response.send_modal(modal)


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
            support_line = ""
            if config.SUPPORT_INVITE:
                support_line = f" and a role in [our Discord server](<{config.SUPPORT_INVITE}>)"
            await person.send(
                f"**You have recieved {rain_duration} minutes of Cat Rain!** ☔\n\n"
                f"Thanks for your support!\nYou can start a rain with `/rain`. By buying you also get access to `/editprofile` and `/customcat` commands{support_line}!\n\n"
                f"Enjoy your goods!"
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
                        if _reaction_cooldown_ok(message.channel.id):
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
                    if _reaction_cooldown_ok(message.channel.id):
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
                if _reaction_cooldown_ok(message.channel.id):
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
                    logging.exception(
                        "pointlaugh react failed (channel=%s guild=%s)",
                        message.channel.id,
                        getattr(message.guild, "id", None),
                    )

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
                    # lightning_hands widens the under-3 window by the perk's multiplier.
                    _under3_window = 3.0 * float(_perks_apply_catch_modifiers(user, channel.cattype or "")["lightning_hands_mult"])
                    if belated_time < _under3_window:
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
                    # weekly quests 🍀 count for belated catchers too
                    await _append_weekly_catch_quests(user, channel.cattype, quests)
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

                # Job-perk modifiers for this catch. Bundle once so each hook
                # site reads its key without re-walking the perks list. Pure
                # read; charges are consumed at the firing sites below.
                _perk_mods = _perks_apply_catch_modifiers(user, le_emoji)

                # Random pack drop on every catch, independent of catnip.
                # Tier is weighted (Wooden common, Celestial extremely rare).
                # When it fires we attach a tier-themed embed to the catch
                # confirmation rather than appending an inline line — the
                # drop is rare enough (~2%) to earn its own moment of drama.
                # pack_drop_boost multiplies the per-catch trigger chance.
                bonus_pack_embed = None
                _pack_drop_chance = PACK_DROP_CHANCE_ON_CATCH * _perk_mods["pack_drop_mult"]
                if random.random() < _pack_drop_chance:
                    bonus_pack_name, _ = grant_bonus_pack(user)
                    bonus_pack_embed = build_bonus_pack_embed(user, bonus_pack_name)
                    if _perk_mods["pack_drop_mult"] > 1.0:
                        suffix_string += "\n🎁 Crate Sniffer boosted your pack-drop odds."

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
                bonus_chance_increase = 0

                if user.perks:
                    if user.catnip_active < time.time():
                        if user.catnip_active != 1:
                            user.catnip_active = 1
                            suffix_string += f"\n{get_emoji('catnip_disabled')} Your catnip expired! Run /catnip to get more."
                        perks = []
                    elif _jobs_perks_suspended(user):
                        # Cat Police lockout — catnip's active but perks are
                        # suspended until perks_suspended_until.
                        suffix_string += f"\n🚓 The Cat Police have your perks. They come back <t:{int(user.perks_suspended_until)}:R>."
                        perks = []
                    else:
                        perks = user.perks
                    perks_info = catnip_list["perks"]
                    perks_by_id = {p["id"]: p for p in perks_info}
                    user.pack_attempts -= 1

                    if len(perks) > 0:
                        logging.debug("Catnip active with %d perks", len(perks))

                    # perk_amplifier (job perk, timed): scales every catnip
                    # perk's value contribution by `multiplier`. Multiplicative
                    # amp on the raw `values[rarity]` reads below; the existing
                    # downstream caps (e.g. 100% chance floor) still apply.
                    _catnip_amp = 1.0
                    if "perk_amplifier" in _perks_active_ids(user):
                        _catnip_amp = float(_perks_strength(user, "perk_amplifier", "multiplier", 1.0) or 1.0)
                    def _amp(v):
                        return v * _catnip_amp if _catnip_amp != 1.0 else v
                    if _catnip_amp > 1.0 and len(perks) > 0:
                        suffix_string += f"\n📣 Perk Amplifier ×{_catnip_amp:g} on your catnip perks."

                    for perk in perks:
                        h = perk.split("_")
                        rarity = int(h[0])
                        type = int(h[1])
                        id = perks_info[type - 1]["id"]

                        if id == "double":
                            double_chance += _amp(perks_info[0]["values"][rarity])
                            single_chance -= _amp(perks_info[0]["values"][rarity])
                        elif id == "triple_none":
                            triple_chance += _amp(perks_info[1]["values"][rarity])
                            none_chance += _amp(perks_info[1]["values"][rarity]) / 2
                            single_chance -= _amp(perks_info[1]["values"][rarity]) * (1.5)
                        elif "pack" in id and user.pack_attempts > 0:
                            for num, pack in enumerate(pack_data):
                                if pack["name"].lower() in id:
                                    packs.append((num, _amp(perks_info[type - 1]["values"][rarity])))
                                    break
                        elif id == "double_boost":
                            double_boost_chance += _amp(perks_info[8]["values"][rarity])
                        elif id == "triple_ach":
                            purr_all_triple = True
                        elif id == "rain_boost":
                            rain_chance += _amp(perks_by_id["rain_boost"]["values"][rarity])
                        elif id == "double_first":
                            double_first += _amp(perks_by_id["double_first"]["values"][rarity])
                        elif id == "combo":
                            combo_per_stack += _amp(perks_by_id["combo"]["values"][rarity])
                        elif id == "bp_xp":
                            bp_xp_chance += _amp(perks_by_id["bp_xp"]["values"][rarity])
                        elif id == "respawn":
                            respawn_chance += _amp(perks_by_id["respawn"]["values"][rarity])
                        elif id == "bonus_catcher":
                            bonus_chance_increase += _amp(perks_by_id["bonus_catcher"]["values"][rarity])

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
                    # combo_shield (job perk, charge-based) absorbs one idle reset.
                    if combo_per_stack > 0:
                        if time.time() - user.last_catch > 300:
                            if _perk_mods["combo_shield"] and _perks_consume_charge(user, "combo_shield"):
                                user.combo_stack = min(30, user.combo_stack + 1)
                                suffix_string += "\n🛡️ Combo Shield absorbed your reset."
                            else:
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
                    # Passive XP to the prism owner when their prism boosts a
                    # different user's catch. The XP value lives in
                    # PRISM_OWNER_XP_PER_BOOST so the grant and the
                    # (+N XP) tag appended to the chat suffix below can't
                    # drift. Self-boosts grant nothing.
                    if prism_which_boosted.user_id != message.author.id:
                        async def _grant_prism_owner_xp(guild_id, owner_id):
                            try:
                                owner = await Profile.get_or_none(guild_id=guild_id, user_id=owner_id)
                                if owner is not None:
                                    await grant_achievement_xp(owner, PRISM_OWNER_XP_PER_BOOST)
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

                    # Cross-user boosts grant +PRISM_OWNER_XP_PER_BOOST XP to
                    # the prism owner (the grant fires above via
                    # _grant_prism_owner_xp). Mirror that gate here so the
                    # suffix only annotates the XP when it actually flows —
                    # self-boosts grant nothing and so display nothing.
                    is_cross_user_boost = prism_which_boosted.user_id != message.author.id
                    xp_tag = f" (+{PRISM_OWNER_XP_PER_BOOST} XP)" if is_cross_user_boost else ""
                    if normal_bump:
                        if double_boost:
                            suffix_string += f"\n{get_emoji('prism')}{get_emoji('prism')} {boost_applied_prism} boosted this catch twice from a {get_emoji(le_old_emoji.lower() + 'cat')} {le_old_emoji} cat!{xp_tag}"
                        else:
                            suffix_string += f"\n{get_emoji('prism')} {boost_applied_prism} boosted this catch from a {get_emoji(le_old_emoji.lower() + 'cat')} {le_old_emoji} cat!{xp_tag}"
                    elif not channel.forcespawned:
                        suffix_string += (
                            f"\n{get_emoji('prism')} {boost_applied_prism} tried to boost this catch, but failed! A {rainboost // 60}m rain will start!{xp_tag}"
                        )

                # ---- Job-perk catch-loop effects ----
                # rarity_bump: % chance to upgrade caught cat one tier. Cap at
                # Mythic so it can't fabricate eGirl/8bit/etc.
                if _perk_mods["rarity_bump_pct"] > 0 and random.random() < _perk_mods["rarity_bump_pct"]:
                    try:
                        cur_idx = cattypes.index(le_emoji)
                    except ValueError:
                        cur_idx = -1
                    mythic_idx = cattypes.index("Mythic") if "Mythic" in cattypes else -1
                    if 0 <= cur_idx < mythic_idx:
                        bumped = cattypes[cur_idx + 1]
                        suffix_string += f"\n✨ Rarity Bump: {le_emoji} → **{bumped}**!"
                        le_emoji = bumped
                # double_cat: silly_amount ×2. Don't double 0 (catnip "none" outcome).
                if _perk_mods["double_cat"] and silly_amount > 0:
                    silly_amount *= 2
                    suffix_string += "\n🐈‍⬛ Double Cat: that one counted twice."
                # eagle_eye: consume a charge to reveal the (final) rarity.
                if _perk_mods["eagle_eye"] and _perks_consume_charge(user, "eagle_eye"):
                    suffix_string += f"\n🦅 Eagle Eye: rarity was **{le_emoji}**."

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
                buttons = []

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

                if random.randint(0, 10) == 0 and user.total_catches > 50 and not user.dark_market_active:
                    shadow_button = Button(label="You see a shadow...", style=ButtonStyle.red)
                    shadow_button.callback = dark_market_cutscene
                    buttons.append(shadow_button)

                # Vote button on the catch message: only ~1 in 40 catches
                # (expected somewhere between every 20th and 60th cat) so it
                # doesn't feel spammy. /battlepass still has a direct link.
                if config.VOTING_ENABLED and random.randint(1, 40) == 1:
                    vote_user = await User.get_or_create(user_id=message.author.id)
                    if vote_user.vote_time_topgg + 43200 < time.time():
                        buttons.append(Button(
                            label=random.choice(vote_button_texts),
                            style=ButtonStyle.url,
                            url=TOP_GG_VOTE_URL,
                            emoji=get_emoji("topgg"),
                        ))

                # Occasional catbot7 discord invite. ~1 in 50 so it shows up
                # less often than the vote nag and doesn't crowd it.
                if random.randint(1, 50) == 1:
                    buttons.append(Button(
                        label="join the catbot7 discord server if you want to i guess idk",
                        style=ButtonStyle.url,
                        url=CAT_DISCORD_INVITE,
                    ))

                if buttons:
                    view = View(timeout=VIEW_TIMEOUT)
                    for b in buttons:
                        view.add_item(b)

                user[f"cat_{le_emoji}"] += silly_amount
                new_count = user[f"cat_{le_emoji}"]
                if silly_amount > 0:
                    await mark_discovered(user, le_emoji)

                # Bonus cats 🎁: rarity-scaled roll, once per catch, catcher-only
                # (solo variant of upstream's june update — no late catching).
                # BONUS_CAT_CHANCE_COEF = 0 disables these (tuning.json kill switch).
                bonus_cattype = channel.cattype
                bonus_minigame = False
                bonus_weight = type_dict.get(bonus_cattype)
                if BONUS_CAT_CHANCE_COEF > 0 and bonus_weight:
                    bonus_chance = BONUS_CAT_CHANCE_COEF * math.log2(_TYPE_DICT_VALUE_SUM / bonus_weight - 0.7)
                    if bonus_chance_increase > 0:
                        # Gift Catcher perk caps at a 2x increase, matching upstream
                        bonus_chance *= min(2, bonus_chance_increase * 0.01 + 1)
                    if random.random() < bonus_chance:
                        if cat_rain_end or channel.cat_rains > 0:
                            # rains move fast - flat +1 instead of a minigame
                            user[f"cat_{bonus_cattype}"] += 1
                            suffix_string += f"\n🎁 Bonus {get_emoji(bonus_cattype.lower() + 'cat')} {bonus_cattype} cat! +1 extra cat."
                            if channel.channel_id in config.cat_cought_rain:
                                if bonus_cattype not in config.cat_cought_rain[channel.channel_id]:
                                    config.cat_cought_rain[channel.channel_id][bonus_cattype] = []
                                config.cat_cought_rain[channel.channel_id][bonus_cattype].append(f"<@{user.user_id}>")
                        else:
                            bonus_minigame = True

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
                            delay = 30 if any(getattr(b, "callback", None) for b in buttons) else 10
                            await result.delete(delay=delay)

                        return result

                    except Exception:
                        logging.exception(
                            "catch confirm send failed (channel=%s guild=%s)",
                            message.channel.id,
                            getattr(message.guild, "id", None),
                        )

                gather_results = await asyncio.gather(delete_cat(), send_confirm())
                if bonus_minigame and gather_results[1]:
                    # defined here (not on every catch) — bonus rolls are rare
                    async def send_bonus_prompt(confirm_msg):
                        bonus_icon = get_emoji(bonus_cattype.lower() + "cat")
                        attempted = False

                        async def bonus_go(interaction: discord.Interaction):
                            nonlocal attempted
                            if interaction.user.id != message.author.id:
                                await do_funny(interaction)
                                return
                            if attempted:
                                await interaction.response.send_message("You already had your shot!", ephemeral=True)
                                return
                            attempted = True
                            await play_minigame(interaction, bonus_cattype)

                        go_button = Button(style=ButtonStyle.green, label="Go!")
                        go_button.callback = bonus_go
                        prompt_view = View(timeout=10)
                        prompt_view.add_item(go_button)
                        try:
                            prompt = await confirm_msg.reply(
                                f"🎁 **BONUS {bonus_icon} {bonus_cattype.upper()} CAT!**\nPlay a minigame and potentially **get +3 more!**",
                                view=prompt_view,
                            )
                            await prompt.delete(delay=10)
                        except Exception:
                            logging.exception("bonus cat prompt failed (channel=%s)", message.channel.id)

                    bot.loop.create_task(send_bonus_prompt(gather_results[1]))

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
                    # cat_rain_coin_yield (job perk, timed): per-catch coin
                    # micro-payout while in an active rain. Spec wants "per
                    # minute" but there's no per-player rain tick; per-catch
                    # is the closest available hook and naturally scales with
                    # how active the player is during the rain.
                    if "cat_rain_coin_yield" in _perks_active_ids(user):
                        _rain_yield = int(_perks_strength(user, "cat_rain_coin_yield", "coins_per_catch", 0) or 0)
                        if _rain_yield > 0:
                            user.coins = int(getattr(user, "coins", 0) or 0) + _rain_yield
                            _bump(user, "coins_earned", _rain_yield)
                            suffix_string += f"\n💰 Cloudburst Wages: +🪙 {_rain_yield:,}."

                await user.save()

                global_user_for_streak = await User.get_or_create(user_id=message.author.id)
                # Pass `user` so streak_protector (job perk) can absorb a skipped day.
                first_catch_of_day = await update_daily_catch_streak(global_user_for_streak, profile=user)

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
                # lightning_hands widens the under-3 window by the perk's multiplier.
                _under3_window = 3.0 * _perk_mods["lightning_hands_mult"]
                if time_caught >= 0 and time_caught < _under3_window:
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
                await _append_weekly_catch_quests(user, channel.cattype, quests)

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
        # syntax: cat!rain <user_id> short
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


# Cat Bot Store entitlement events. Discord delivers these over the gateway
# whenever a user buys, renews, refunds, or lets a subscription lapse on a
# SKU registered in the Developer Portal. All three handlers no-op when
# STORE_ENABLED is off so we don't accidentally trash state if monetization
# is toggled at runtime.
async def on_entitlement_create(entitlement):
    if not config.STORE_ENABLED:
        return
    try:
        await _apply_entitlement_create(entitlement)
    except Exception:
        logging.exception("on_entitlement_create failed for %r", entitlement)


async def on_entitlement_update(entitlement):
    if not config.STORE_ENABLED:
        return
    # ends_at is None for active subscriptions / one_time grants. A datetime
    # in the past means the entitlement just expired (Discord still sends an
    # update before the eventual delete). Route to the right helper.
    try:
        ends_at = getattr(entitlement, "ends_at", None)
        now = datetime.datetime.now(datetime.timezone.utc)
        if ends_at is not None and ends_at < now:
            await _apply_entitlement_delete(entitlement)
        else:
            await _apply_entitlement_create(entitlement)
    except Exception:
        logging.exception("on_entitlement_update failed for %r", entitlement)


async def on_entitlement_delete(entitlement):
    if not config.STORE_ENABLED:
        return
    try:
        await _apply_entitlement_delete(entitlement)
    except Exception:
        logging.exception("on_entitlement_delete failed for %r", entitlement)


# the message when cat gets added to a new server
async def on_guild_join(guild):
    # Cache the guild's display name on join so the activity dashboard can
    # resolve it even if the bot later leaves. Skipped silently if the column
    # is missing (pre-migration). The snapshot loop refreshes this hourly too.
    try:
        server = await Server.get_or_create(server_id=guild.id)
        if guild.name:
            server.name = guild.name[:100]
            await server.save()
    except Exception:
        logging.debug("on_guild_join server.name population skipped", exc_info=True)

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
    if not bot.user:
        unofficial_note = ""
    try:
        if ch.permissions_for(guild.me).send_messages:
            support_line = ""
            if config.SUPPORT_INVITE:
                support_line = f"\nJoin the support server here: {config.SUPPORT_INVITE}"
            await ch.send(
                unofficial_note
                + f"Thanks for adding me!\nTo start, use `/setup` and `/help` to learn more!{support_line}\nHave a nice day :)"
            )
    except Exception:
        pass


@bot.tree.command(description="A guide of how to use the bot")
async def help(message):
    thumb_url = "https://wsrv.nl/?url=raw.githubusercontent.com/sneezeparty/catbot7/main/images/cat.png"
    embed1 = discord.Embed(
        title="How to Setup",
        description=(
            "A moderator with **Manage Server** runs `/setup` in any channel. "
            "Cats start spawning there at 1 to 10 minute intervals.\n"
            "Tweak with `/changetimings`, `/changemessage`, or `/forcespawn`. "
            "Stop spawning in a channel with `/forget`. Any number of channels can be setupped at once."
        ),
        color=Colors.brown,
    ).set_thumbnail(url=thumb_url)

    embed2 = (
        discord.Embed(title="How to Play", color=Colors.brown)
        .add_field(
            name="Catch Cats",
            value=(
                "When a cat appears, say `cat` to catch it. Rarities run from Fine (common) up through "
                "eGirl (rare). Your inventory is **per server**, so each server is its own scoreboard. "
                "View yours or someone else's with `/inventory`, see standings with `/leaderboards`, "
                "and move cats around with `/gift` or `/trade`."
            ),
            inline=False,
        )
        .add_field(
            name="Cat Mafia (`/catnip`, `/jobs`, `/catstore`)",
            value=(
                "Feed cats to the mafia at `/catnip` to climb 10 ranks (Newbie up to El Patrón) and unlock "
                "perks plus a store discount. `/jobs` runs PvE contracts for six NPCs that pay coins, cats, "
                "and **job perks**. `/rep` shows where you stand with each NPC. `/catstore` buys and sells "
                "discovered cat rarities, and its **Extras** tab sells rain blocks and higher-tier packs."
            ),
            inline=False,
        )
        .add_field(
            name="Casino & Economy",
            value=(
                "All games share one **coins** wallet. `/slots` is the 3-reel classic, `/catslots` is the "
                "5x3 cat slot machine with paylines and an eGirl bonus round. `/roulette` plays the wheel, "
                "`/stocks` trades a fake market driven by in-game activity, and `/packs` opens whatever "
                "packs you've earned or bought."
            ),
            inline=False,
        )
        .add_field(
            name="Progression",
            value=(
                "`/achievements` tracks unlocks across catching, casino, jobs, and easter eggs. "
                "`/battlepass` runs monthly seasons with five quest slots per cycle. "
                "`/perks` shows your active catnip and job-perk effects. "
                "Passive XP drips on first daily catch, every 10-catch streak, and every catnip level-up."
            ),
            inline=False,
        )
        .set_footer(
            text=f"Cat Bot self-hosted by sneezeparty, {discord.utils.utcnow().year}",
            icon_url=thumb_url,
        )
    )

    await message.response.send_message(embeds=[embed1, embed2])


# Cat Bot Store — Discord native monetization (SKUs + Entitlements). The
# command itself is always registered so that the slash list stays stable
# across env-var toggles, but the body shows a "not available" message when
# STORE_ENABLED is off. SKUs come from config/store.json — see the store
# step 1 commit message for the schema.
@bot.tree.command(description="Support Cat Bot and grab supporter perks")
async def store(message: discord.Interaction):
    if not config.STORE_ENABLED:
        await message.response.send_message(
            "the store is not currently available on this instance.",
            ephemeral=True,
        )
        return

    skus = []
    try:
        skus = config.store.get("skus") or []
    except Exception:
        skus = []
    if not skus:
        await message.response.send_message(
            "the store has no items configured yet. check back later.",
            ephemeral=True,
        )
        return

    user = await User.get_or_create(user_id=message.user.id)
    held = set(_user_entitlements_load(user))

    STORE_HELP_PAGES = [
        {
            "title": "What is the store?",
            "body": (
                "The Cat Bot Store sells **supporter status** and (in the future) "
                "**cosmetic items** through Discord's official monetization system. "
                "Supporter unlocks `/editprofile`, `/customcat`, and the option to "
                "stay anonymous when you bless other players. Everything else stays "
                "exactly the same — supporter does NOT give coins, packs, cats, or "
                "any gameplay advantage."
            ),
        },
        {
            "title": "How purchases work",
            "body": (
                "Clicking a buy button opens Discord's official checkout right inside "
                "the client. The bot never sees your payment info. Once you complete a "
                "purchase, Discord tells the bot about it over the gateway and your "
                "supporter status flips on within seconds.\n\n"
                "If you ever cancel a subscription or get a refund, Discord notifies "
                "the bot the same way and your supporter status comes back off. The "
                "bot reconciles on startup too, so nothing is lost if it was offline "
                "when something changed."
            ),
        },
    ]

    async def show_store_help(interaction: discord.Interaction, start_page: int = 0):
        page_idx = max(0, min(len(STORE_HELP_PAGES) - 1, int(start_page)))

        async def render(target_interaction: discord.Interaction, idx: int, is_initial: bool):
            page = STORE_HELP_PAGES[idx]
            items: list = [
                f"## 💡 Store Help — {page['title']}",
                f"-# Page {idx + 1} / {len(STORE_HELP_PAGES)}",
                Separator(),
                page["body"],
            ]
            prev_btn = Button(label="← Prev", style=ButtonStyle.gray, custom_id="store_help_prev", disabled=idx == 0)
            next_btn = Button(label="Next →", style=ButtonStyle.gray, custom_id="store_help_next", disabled=idx >= len(STORE_HELP_PAGES) - 1)

            async def on_help_prev(intr: discord.Interaction):
                await render(intr, idx - 1, is_initial=False)

            async def on_help_next(intr: discord.Interaction):
                await render(intr, idx + 1, is_initial=False)

            prev_btn.callback = on_help_prev
            next_btn.callback = on_help_next
            items.append(ActionRow(prev_btn, next_btn))

            v = LayoutView(timeout=VIEW_TIMEOUT)
            container = Container(*items)
            try:
                container.accent_color = Colors.brown
            except Exception:
                pass
            v.add_item(container)

            if is_initial:
                await target_interaction.response.send_message(view=v, ephemeral=True)
            elif target_interaction.response.is_done():
                await target_interaction.edit_original_response(view=v)
            else:
                await target_interaction.response.edit_message(view=v)

        await render(interaction, page_idx, is_initial=True)

    async def on_store_help(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        await show_store_help(interaction, start_page=0)

    view = LayoutView(timeout=VIEW_TIMEOUT)
    items: list = [
        "## 🛒 Cat Bot Store",
        (
            "Support Cat Bot through Discord's official monetization. Supporter "
            "unlocks cosmetic commands like `/editprofile` and `/customcat` — no "
            "gameplay perks, just nice-to-haves."
        ),
    ]

    for sku in skus:
        sku_id = str(sku.get("id") or "")
        if not sku_id:
            continue
        name = sku.get("name") or "(unnamed item)"
        emoji = sku.get("emoji") or ""
        desc = sku.get("description") or ""
        kind = sku.get("kind", "")
        owns_this = sku_id in held
        kind_line = "**Supporter tier**" if kind == "supporter" else "Cosmetic item" if kind == "cosmetic" else kind

        body = f"{desc}\n-# {kind_line}  ·  " + ("✓ Owned" if owns_this else "Not owned")

        if owns_this:
            btn = Button(label="Owned ✓", style=ButtonStyle.gray, disabled=True)
        else:
            # Discord's official Premium Button — handles checkout natively.
            btn = Button(style=ButtonStyle.premium, sku_id=int(sku_id))

        header = f"### {emoji} {name}".strip()
        items.append(Section(header, body, btn))

    help_btn = Button(label="💡 Help", style=ButtonStyle.gray, custom_id="store_help")
    help_btn.callback = on_store_help
    items.append(ActionRow(help_btn))

    container = Container(*items)
    try:
        container.accent_color = Colors.brown
    except Exception:
        pass
    view.add_item(container)
    await message.response.send_message(view=view, ephemeral=True)


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
        url="https://wsrv.nl/?url=raw.githubusercontent.com/sneezeparty/catbot7/main/images/cat.png"
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


# /info is intentionally NOT registered as a slash command — the system /
# tech / global-stats payload it returns (OS, Python ver, RAM, guild count,
# DB row counts, etc.) is considered too revealing for end users. The body
# below is kept in-place so re-enabling is a one-line change: restore the
# `@bot.tree.command(...)` decorator and `cat!restart` to re-sync.
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


@bot.tree.command(description="Read The Cat Bot Times™️")
async def news(message: discord.Interaction):
    articles = get_news()
    user = await User.get_or_create(user_id=message.user.id)
    buttons = []
    current_state = user.news_state.strip()

    if not articles:
        empty = LayoutView(timeout=VIEW_TIMEOUT)
        empty.add_item(Container("## 📰 The Cat Bot Times", "No news yet — check back soon!"))
        await message.response.send_message(view=empty)
        await achemb(message, "news", "followup")
        return

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
        if news_id < len(current_state) and current_state[news_id] not in "123456789":
            user.news_state = current_state[:news_id] + "1" + current_state[news_id + 1 :]
            await user.save()

        view = LayoutView(timeout=VIEW_TIMEOUT)
        back_button = Button(emoji="⬅️", label="Back")
        back_button.callback = go_back
        back_row = ActionRow(back_button)

        all_articles = get_news()
        if news_id >= len(all_articles):
            view.add_item(Container("## (this article no longer exists)"))
            view.add_item(back_row)
            await interaction.edit_original_response(view=view)
            return

        article = all_articles[news_id]
        emoji = get_emoji(article["emoji"]) if article.get("emoji") else ""
        heading = "## " + (f"{emoji} " if emoji else "") + (article.get("title") or "")
        parts = [heading, render_news_body(article.get("body", ""))]
        link_buttons = [
            Button(label=b.get("label") or "Link", url=b["url"])
            for b in (article.get("buttons") or [])
            if b.get("url")
        ]
        if link_buttons:
            parts.append(ActionRow(*link_buttons))
        if article.get("date"):
            parts.append(f"-# <t:{int(article['date'])}>")
        view.add_item(Container(*parts))
        view.add_item(back_row)
        await interaction.edit_original_response(view=view)

    async def regen_buttons():
        nonlocal buttons
        await user.refresh_from_db()
        buttons = []
        current_state = user.news_state.strip()
        for num, article in enumerate(get_news()):
            try:
                have_read_this = current_state[num] != "0"
            except Exception:
                have_read_this = False
            button = Button(
                label=article.get("title") or f"Article {num + 1}",
                emoji=get_emoji(article["emoji"]) if article.get("emoji") else None,
                custom_id=str(num),
                style=ButtonStyle.green if not have_read_this else ButtonStyle.gray,
            )
            button.callback = send_news
            buttons.append(button)
        buttons = buttons[::-1]  # reverse the list so the first button is the most recent article

    await regen_buttons()

    if len(articles) > len(current_state):
        user.news_state = current_state + "0" * (len(articles) - len(current_state))
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
        user.news_state = "1" * len(get_news())
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
                headers={"User-Agent": "CatBot/1.0 https://github.com/sneezeparty/catbot7"},
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


# Paginated to stay under Discord's Components V2 40-child cap per LayoutView.
# Each Section contributes 4 (self + 2 text lines + accessory), so 10 Sections
# in one Container hit 40 before the heading/separators are added. Split into
# two pages; the cap math is in the plan file.
SETTINGS_PAGES = [
    {
        "label": "Display & cleanup",
        "settings": [
            (
                "only_setupped_channels",
                "Only in Setupped",
                "If enabled, mutes reactions, responses, achievements and cattlepass progress outside of setupped channels",
            ),
            ("do_reactions", "Reactions", "Controls all Cat Bot reactions"),
            ("do_responses", "Responses", "Controls Cat Bot easter egg responses to specific messages sent"),
            ("mute_achievements", "Mute Achievements", 'If enabled, will hide all Cat Bot "achievement get" messages'),
            (
                "auto_delete_achievements",
                "Auto-Delete Achievements",
                'If enabled, will delete all "achievement get" messages after 10 seconds',
            ),
            (
                "auto_delete_catches",
                "Auto-Delete Catches",
                'If enabled, will delete all "user cought" messages after ~10 seconds',
            ),
        ],
    },
    {
        "label": "Game features & lifecycle",
        "settings": [
            ("do_rain", "Cat Rains", "Controls whether Cat Rains can happen"),
            ("do_catnip", "Catnip", "Controls whether catnip is accessible"),
            (
                "anti_double_catch",
                "Anti-Double Catch",
                "If enabled, users must wait 5 minutes after catching in one channel to catch in another",
            ),
            (
                "season_announcements",
                "Season Announcements",
                "If enabled, Cat Bot warns your setupped channels the day before a Cattlepass season ends and wipes coins/catnip/jobs/packs",
            ),
        ],
    },
]


@bot.tree.command(description="(ADMIN) tune various cat bot things")
@discord.app_commands.default_permissions(manage_guild=True)
async def settings(message: discord.Interaction):
    server = await Server.get_or_create(server_id=message.guild.id)
    current_page = 0

    async def toggle_parameter(interaction: discord.Interaction):
        if interaction.user != message.user:
            await do_funny(interaction)
            return
        await interaction.response.defer()
        parameter = interaction.data["custom_id"]
        server[parameter] = not server[parameter]
        await server.save()
        await interaction.edit_original_response(view=await settings_view())

    async def page_nav(interaction: discord.Interaction):
        if interaction.user != message.user:
            await do_funny(interaction)
            return
        nonlocal current_page
        await interaction.response.defer()
        if interaction.data["custom_id"] == "settings_prev":
            current_page = (current_page - 1) % len(SETTINGS_PAGES)
        else:
            current_page = (current_page + 1) % len(SETTINGS_PAGES)
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
        page = SETTINGS_PAGES[current_page]
        sections = [
            Section(f"### {title}", desc, make_button(param))
            for (param, title, desc) in page["settings"]
        ]
        prev_btn = Button(label="◀ Prev", custom_id="settings_prev")
        prev_btn.callback = page_nav
        next_btn = Button(label="Next ▶", custom_id="settings_next")
        next_btn.callback = page_nav
        view = LayoutView(timeout=VIEW_TIMEOUT)
        view.add_item(
            Container(
                f"## Cat Bot Settings for {message.guild.name}",
                f"-# Page {current_page + 1} of {len(SETTINGS_PAGES)} — {page['label']}",
                *sections,
                ActionRow(prev_btn, next_btn),
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
    try:
        stats.append(["bonus_catches", "🎁", f"Successful bonus catches: {profile.bonus_catches:,}{star}"])
    except KeyError:
        pass  # migration 032 not run yet

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
    # past seasons. season_max is the configured level count for that season
    # (30 for season 1, 40 for seasons 2+). Going past it means the player
    # entered the Extra Rewards fallback, which is what "season complete"
    # means here — and the bonus XP they earned in Extra Rewards mode is
    # EXTRA_LEVEL_XP per overflow level. (Approximate for levels earned
    # before the knob changed; it's a display stat, not a ledger.)
    for season in profile.bp_history.split(";"):
        if not season:
            break
        season_num, season_lvl, season_progress = map(int, season.split(","))
        if season_num == 0:
            continue
        levels_complete += season_lvl
        total_xp += season_progress
        season_max = len(config.battle["seasons"].get(str(season_num), [])) or 30
        if season_lvl > season_max:
            seasons_complete += 1
            total_xp += EXTRA_LEVEL_XP * (season_lvl - (season_max + 1))
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
        season_max_curr = len(config.battle["seasons"].get(str(profile.season), [])) or 30
        if profile.battlepass > season_max_curr:
            seasons_complete += 1
            total_xp += EXTRA_LEVEL_XP * (profile.battlepass - (season_max_curr + 1))
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
    try:
        rarest_fish_text = f"{fish_emoji(profile.rarest_fish.strip())} {profile.rarest_fish.strip()}" if profile.rarest_fish.strip() else "none"
        stats.append(["fish_caught", "🎣", f"Fish caught: {profile.fish_caught:,} (rarest: {rarest_fish_text})"])
    except KeyError:
        pass  # migration 033 not run yet
    stats.append(["pig_high_score", "🎲", f"Pig high score: {profile.best_pig_score:,}"])
    stats.append(["cats_gifted", "🎁", f"Cats gifted: {profile.cats_gifted:,}{star}"])
    stats.append(["cats_received_as_gift", "🎁", f"Cats received as gift: {profile.cat_gifts_recieved:,}{star}"])
    stats.append(["trades_completed", "💱", f"Trades completed: {profile.trades_completed}{star}"])
    stats.append(["cats_traded", "💱", f"Cats traded: {profile.cats_traded:,}{star}"])
    return stats


@bot.tree.command(name="stats", description="View some advanced stats")
@discord.app_commands.rename(person_id="user")
@discord.app_commands.describe(person_id="Person to view the stats of!")
async def stats_command(message: discord.Interaction, person_id: Optional[discord.User]):
    await message.response.defer()
    if not person_id:
        person_id = message.user
    profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=person_id.id)
    # Run the season-rollover wipe + ephemeral notice for the INVOKER's
    # profile, regardless of whose stats are being viewed. If they're
    # viewing their own stats, reuse `profile`; else fetch the invoker's
    # profile separately so the notice fires for them.
    if int(person_id.id) == int(message.user.id):
        invoker_profile = profile
    else:
        invoker_profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    await refresh_quests(invoker_profile)
    await _maybe_show_season_reset_notice(message, invoker_profile)
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
        needed_xp = EXTRA_LEVEL_XP

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
        coins_now = int(person.coins or 0)
        embedVar.description += f"\n{get_emoji('staring_cat')} Cats: {total:,}, Value: {round(valuenum):,}\n🪙 Coins: {coins_now:,}\n{get_emoji('prism')} Prisms: {prism_list} ({prism_boost}%)\n\n{cat_desc}"

    if user.image.startswith("https://cdn.discordapp.com/attachments/"):
        embedVar.set_thumbnail(url=user.image)

    give_achs = []
    if me:
        # give some aches if we are vieweing our own inventory
        if len(get_news()) > len(user.news_state.strip()) or "0" in user.news_state.strip()[-4:]:
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
    # Season-rollover wipe + ephemeral notice for the INVOKER, regardless
    # of whose inventory is being viewed.
    if int(person_id.id) == int(message.user.id):
        invoker_profile = person
    else:
        invoker_profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    await refresh_quests(invoker_profile)
    await _maybe_show_season_reset_notice(message, invoker_profile)
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
            if config.STORE_ENABLED:
                supporter_intro = "Run `/store` to unlock."
            else:
                supporter_intro = "Supporter features are not currently available on this instance."
            description = f"""👑 __Supporter Settings__
{supporter_intro}
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


@bot.tree.command(name="catprofile", description="View an at-a-glance profile card")
@discord.app_commands.rename(person_id="user")
@discord.app_commands.describe(person_id="Person to view the profile of!")
async def catprofile(message: discord.Interaction, person_id: Optional[discord.User]):
    await message.response.defer()
    if not person_id:
        person_id = message.user
    person = await Profile.get_or_create(guild_id=message.guild.id, user_id=person_id.id)
    user = await User.get_or_create(user_id=person_id.id)
    # Season-rollover wipe + ephemeral notice for the INVOKER, regardless of
    # whose profile is being viewed (matches /stats and /inventory).
    if int(person_id.id) == int(message.user.id):
        invoker_profile = person
    else:
        invoker_profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    await refresh_quests(invoker_profile)
    await _maybe_show_season_reset_notice(message, invoker_profile)

    # --- gather data ---
    # mafia (catnip) level + rank name
    mafia_level = person.catnip_level
    try:
        rank = catnip_list["levels"][mafia_level]["name"]
    except (IndexError, KeyError):
        rank = "?"

    # cattlepass level/progress (handles the Extra Rewards fallback)
    bp_label, bp_progress, bp_cap, _bp_season_max = _battlepass_level_info(person)

    # total cats + collection value (same scale as /inventory and /trade)
    total_cats = 0
    total_value = 0
    for i in cattypes:
        cat_num = person[f"cat_{i}"]
        if cat_num:
            total_cats += cat_num
            total_value += cat_value(i) * cat_num

    # prisms owned in this server, plus the boost % they grant (gen_inventory math)
    prism_count = await Prism.count("guild_id = $1 AND user_id = $2", message.guild.id, person_id.id)
    total_count = await Prism.count("guild_id = $1", message.guild.id)
    global_boost = PRISM_BOOST_GLOBAL_COEF * math.log(2 * total_count + 1)
    prism_boost = round((global_boost + PRISM_BOOST_USER_COEF * math.log(2 * prism_count + 1)) * 100, 1)

    # achievements: visible only (Hidden ones are excluded from the total, like /inventory)
    unlocked = 0
    minus_achs_count = 0
    for k in ach_names:
        if ach_list[k]["category"] == "Hidden":
            minus_achs_count += 1
            continue
        if person.has_ach(k):
            unlocked += 1
    total_achs = len(ach_list) - minus_achs_count

    # per-server catch streak: consecutive catches in THIS server
    # (profile.catch_streak, resets when a catch is missed/laughed at)
    streak = person.catch_streak

    # supporter cosmetics, consistent with /inventory
    emoji_prefix = str(user.emoji) + " " if user.emoji else ""
    color = discord.Colour.from_str(user.color) if user.color else Colors.brown

    embedVar = discord.Embed(
        title=f"{emoji_prefix}{person_id.name.replace('_', r'\_')}'s Profile",
        color=color,
    )
    if user.image.startswith("https://cdn.discordapp.com/attachments/"):
        embedVar.set_thumbnail(url=user.image)
    else:
        embedVar.set_thumbnail(url=person_id.display_avatar.url)

    # fresh, zeroed profile = this person has never played in this server
    if person_id.id != message.user.id and person.new_user:
        embedVar.description = f"{person_id.mention} hasn't played in this server yet."

    # Row 1 — progression
    embedVar.add_field(name="🎩 Mafia", value=f"Level {mafia_level}\n{rank}", inline=True)
    embedVar.add_field(name="⬆️ Cattlepass", value=f"{bp_label}\n{bp_progress:,}/{bp_cap:,} XP\nSeason {person.season}", inline=True)
    embedVar.add_field(name=f"{get_emoji('ach')} Achievements", value=f"{unlocked}/{total_achs}", inline=True)
    # Row 2 — wealth
    embedVar.add_field(name=f"{get_emoji('staring_cat')} Cats", value=f"{total_cats:,}\nValue: {round(total_value):,}", inline=True)
    embedVar.add_field(name="🪙 Coins", value=f"{person.coins:,}", inline=True)
    embedVar.add_field(name=f"{get_emoji('prism')} Prisms", value=f"{prism_count}\nBoost: {prism_boost}%", inline=True)
    # Row 3 — fun
    embedVar.add_field(name="🔥 Streak", value=f"{streak:,}", inline=True)
    embedVar.add_field(name="🎲 Pig", value=f"{person.best_pig_score:,}", inline=True)
    embedVar.add_field(name="🍪 Cookies", value=f"{person.cookies:,}", inline=True)

    # Row 4 — season trophies (only shown if the player has any; full-width row
    # so the 3x3 grid above stays clean)
    trophies_text = _format_season_trophies(getattr(person, "season_trophies", None))
    if trophies_text:
        embedVar.add_field(name="🏆 Season Trophies", value=trophies_text, inline=False)

    await message.followup.send(embed=embedVar)


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

    rain_source = "from the battlepass and from in-channel events"
    if config.STORE_ENABLED:
        rain_source += ", or in single 15-second blocks via `/catstore` → Extras → Rain"
    embed = discord.Embed(
        title="☔ Cat Rains",
        description=f"""Cat Rains are power-ups which spawn cats super fast for a limited amount of time in a channel of your choice.

You earn rain minutes {rain_source}.
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
            shortfall_hint = "play the battlepass or wait for in-channel rain events"
            if config.STORE_ENABLED:
                shortfall_hint = "play the battlepass, wait for in-channel rain events, or grab a 15-second block from `/catstore` → Extras → Rain"
            await interaction.response.send_message(
                f"you dont have enough rain! {shortfall_hint}.",
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

    view = View(timeout=VIEW_TIMEOUT)
    view.add_item(button)

    await message.response.send_message(embed=embed, view=view)


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
            if config.STORE_ENABLED:
                unlock_hint = "Use `/store` to unlock supporter features."
            else:
                unlock_hint = "Supporter features are not currently available on this instance."
            await message.response.send_message(
                f"👑 This feature is supporter-only!\n{unlock_hint}",
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
                bbutton_label = "Supporter Required! Use /store" if config.STORE_ENABLED else "Supporter Required!"
                bbutton = Button(label=bbutton_label, emoji="👑", disabled=True, style=ButtonStyle.gray)
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
            if config.STORE_ENABLED:
                unlock_hint = "Use `/store` to unlock supporter features."
            else:
                unlock_hint = "Supporter features are not currently available on this instance."
            await message.response.send_message(
                f"👑 This feature is supporter-only!\n{unlock_hint}"
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


@bot.tree.command(description="bumbum's scratch off game")
async def scratch(message: discord.Interaction):
    user = await Profile.get_or_create(user_id=message.user.id, guild_id=message.guild.id)

    def prize_emoji(opt):
        # fork has no "1rain" app emoji — plain ☔ stands in for rain minutes
        return "☔" if opt == "1m Rain" else get_emoji(f"{opt.lower()}pack")

    async def scratch_callback(interaction: discord.Interaction):
        if interaction.user != message.user:
            await do_funny(interaction)
            return

        await user.refresh_from_db()
        if user.scratchcards == 0:
            await interaction.response.send_message("You have no scratch cards!", ephemeral=True)
            return

        opts = [
            "1m Rain", "1m Rain",
            "Celestial", "Celestial",
            "Diamond", "Diamond",
            "Platinum", "Platinum",
            "Gold", "Gold", "Gold",
            "Silver", "Silver", "Silver",
            "Bronze", "Bronze", "Bronze",
            "Stone", "Stone", "Stone", "Stone",
            "Wooden", "Wooden", "Wooden", "Wooden",
        ]  # fmt: skip

        random.shuffle(opts)

        # the entire minigame is actually a lie whoopsie daisy!!!
        # this is solely so people who fall asleep midgame wont lose on rewards
        picks = opts[:10]
        winnings = ["Winnings:"]
        user.scratchcards -= 1
        for opt in set(opts):
            amount = picks.count(opt) // 2
            if amount == 0:
                continue
            winnings.append(f"{prize_emoji(opt)} x{amount}")
            if opt == "1m Rain":
                # profile.rain_minutes = per-server bonus minutes (/rain spends
                # these first, before the global user pool)
                user.rain_minutes += amount
            else:
                user[f"pack_{opt.lower()}"] += amount
        await user.save()

        # each key has a list of indices where that item appears in picks
        positions = {}
        for i, x in enumerate(picks):
            if x not in positions:
                positions[x] = []
            positions[x].append(i)

        # this is used during minigame to determine when to reveal the pair
        pairs = {}
        for idxs in positions.values():
            for i in range(0, len(idxs) - 1, 2):
                a, b = idxs[i], idxs[i + 1]
                pairs[a] = b
                pairs[b] = a

        move_spaces = []

        async def scratch_spot(interaction: discord.Interaction):
            if interaction.user != message.user:
                await do_funny(interaction)
                return
            spot = int(interaction.data["custom_id"])
            if len(move_spaces) < 10:
                move_spaces.append(spot)
            await refresh_board(interaction)

        async def refresh_board(interaction: discord.Interaction):
            nonlocal move_spaces
            await interaction.response.defer()
            view = LayoutView(timeout=VIEW_TIMEOUT)
            buttons = []
            empty_idx = 10
            if len(move_spaces) > 10:
                move_spaces = move_spaces[:10]
            for i in range(25):
                if i not in move_spaces:
                    if len(move_spaces) != 10:
                        button = Button(emoji=get_emoji("empty"), custom_id=str(i), style=ButtonStyle.gray)
                        button.callback = scratch_spot
                    else:
                        item = opts[empty_idx]
                        empty_idx += 1
                        button = Button(
                            emoji=prize_emoji(item),
                            disabled=True,
                            style=ButtonStyle.gray,
                        )
                    buttons.append(button)
                    continue
                move_number = move_spaces.index(i)
                button = Button(
                    emoji=prize_emoji(picks[move_number]),
                    style=ButtonStyle.green if move_number in pairs and len(move_spaces) > pairs[move_number] else ButtonStyle.blurple,
                    disabled=True,
                )
                buttons.append(button)

            view.add_item(TextDisplay(f"Clicks remaining: {10 - len(move_spaces)}" if len(move_spaces) != 10 else "\n".join(winnings)))
            for i in range(0, 25, 5):
                view.add_item(ActionRow(*buttons[i : i + 5]))

            if len(move_spaces) == 10:
                await user.refresh_from_db()
                button = Button(label=f"Scratch! ({user.scratchcards})", style=ButtonStyle.green, disabled=user.scratchcards == 0)
                button.callback = scratch_callback
                view.add_item(ActionRow(button))
            await interaction.edit_original_response(view=view)

        await refresh_board(interaction)

    view = LayoutView(timeout=VIEW_TIMEOUT)
    button = Button(label=f"Scratch! ({user.scratchcards})", style=ButtonStyle.green, disabled=user.scratchcards == 0)
    button.callback = scratch_callback
    view.add_item(
        Container(
            "## 🍀 Scratch Off",
            f"You will be able to select **10 out of 25 spots**. Finding a __pair__ will give you it's respective prize. (example: finding 2x {get_emoji('diamondpack')} will give you a Diamond pack)",
            "Get scratch cards by completing *Weekly Quests*.",
            "===",
            ActionRow(button),
        )
    )
    await message.response.send_message(view=view)


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

        # ---- Pack-side job perks (mirror the single-open path in open_pack) ----
        # pack_bonus_cat (timed):     +1 random cat on EVERY open while active.
        # pack_tier_upgrade (charge): spend one charge to bump one open a tier.
        # pack_floor (charge):        spend one charge to lift one Fine to Nice.
        # The two charge perks are "your next pack" — they fire on the first
        # eligible open in the batch and then are exhausted, exactly as if the
        # player had opened that one pack by hand.
        active_perks = _perks_active_ids(user)
        bonus_cat_active = "pack_bonus_cat" in active_perks
        polish_pending = "pack_tier_upgrade" in active_perks
        floor_pending = "pack_floor" in active_perks
        # Double Pack voucher 🎟️ (Mystery reward): like the charge perks, it
        # fires on the first open of the batch and is exhausted.
        double_pending = _vouchers_has(user, "double_pack")
        cap_idx = next((i for i, p in enumerate(pack_data) if p["name"].lower() == "silver"), len(pack_data) - 1)
        perk_msgs: list[str] = []
        bonus_cat_total = 0
        coin_total = 0   # aggregated coin-variant payout across the batch

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
                use_level = level
                if polish_pending and _perks_consume_charge(user, "pack_tier_upgrade"):
                    if use_level < cap_idx:
                        use_level += 1
                        perk_msgs.append(f"🪜 Crate Polish bumped one open to **{pack_data[use_level]['name']}**.")
                    else:
                        perk_msgs.append(f"🪜 Crate Polish: already at cap ({pack_data[cap_idx]['name']}).")
                    polish_pending = False

                # 50/50 coin-variant roll per pack (specials excluded — same
                # rule as single-open). The helper inside get_pack_rewards
                # silently no-ops on special final tiers.
                coin_variant = (not pack_data[use_level]["special"]) and random.random() < PACK_COIN_VARIANT_CHANCE
                chosen_type, cat_amount, upgrades, rewards, coin_amount = get_pack_rewards(
                    use_level, is_single=False, coin_variant=coin_variant
                )

                if chosen_type == "Fine" and floor_pending and _perks_consume_charge(user, "pack_floor"):
                    chosen_type = "Nice"
                    perk_msgs.append("🚫 No Fines: floor lifted one open to Nice.")
                    floor_pending = False

                if double_pending and _vouchers_consume(user, "double_pack"):
                    cat_amount *= 2
                    coin_amount *= 2
                    perk_msgs.append("🎟️ Double Pack voucher: first pack's contents doubled!")
                    double_pending = False

                total_upgrades += upgrades
                coin_total += coin_amount
                if not display_cats:
                    results_detail.append(rewards)
                results_percat[chosen_type] += cat_amount

                if bonus_cat_active:
                    results_percat[random.choice(_season_eligible_cattypes())] += 1
                    bonus_cat_total += 1

            user[pack_id] -= opening_this
            opened_so_far += opening_this

        if bonus_cat_total > 0:
            perk_msgs.append(f"➕ Padded Crate: +{bonus_cat_total:,} bonus cats.")

        user.packs_opened += opened_so_far
        user.pack_upgrades += total_upgrades
        for cat_type, cat_amount in results_percat.items():
            user[f"cat_{cat_type}"] += cat_amount
        if coin_total > 0:
            user.coins = int(user.coins or 0) + coin_total
            _bump(user, "coins_earned", coin_total)
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

        coin_footer = f"\n\n💰 **+{coin_total:,}** coins" if coin_total > 0 else ""

        perk_footer = ""
        if perk_msgs:
            perk_footer = "\n\n" + "\n".join(perk_msgs)

        return discord.Embed(title=final_header, description=f"{pack_list}{final_result}{coin_footer}{perk_footer}", color=Colors.brown)

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
        if total_amount >= 2:
            button = Button(label=f"Open all! ({total_amount:,})", style=ButtonStyle.gray)
            button.callback = confirm_open_all
            view.add_item(button)
        return view, has_special

    def get_pack_rewards(level: int, is_single=True, _cascade_depth=0, coin_variant: bool = False):
        # returns cat_type, cat_amount, upgrades, verbal_output, coin_amount
        #
        # _cascade_depth tracks how many fail-cascades have already happened.
        # 0 = original open. 1 = post-cascade (or post-Wooden-re-roll). At
        # depth >= 1, a sub-1 fail goes straight to "3 Fine cats" consolation
        # with no further retry (per "fails more than once → 3 Fine cats").
        #
        # coin_variant=True turns this into a "coin crate" open: the cat side
        # is rolled at goal_value * (1 - coin_ratio) and the caller is
        # returned coin_amount = final tier's totalvalue * coin_ratio, where
        # coin_ratio is tier-scaled (Wooden most, Celestial least; specials
        # silently fall back to 0 / regular open). Cascade re-opens pass the
        # flag through and the cascade's coin amount wins (consolation tier).
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
        coin_amount = 0
        if coin_variant:
            coin_ratio = _pack_coin_ratio(level)
            if coin_ratio > 0:
                cat_ratio = 1.0 - coin_ratio
                goal_value = max(1, int(goal_value * cat_ratio))
                coin_amount = int(final_level["totalvalue"] * coin_ratio)
            # else: variant rolled but final tier is special — silently
            # behave as a regular open (coin_amount stays 0, goal_value full).
        chosen_type = random.choice(_season_eligible_cattypes())
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
                    new_type = random.choice(_season_eligible_cattypes())
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
                    cascade_type, cascade_amount, cascade_upgrades, cascade_text, cascade_coin = get_pack_rewards(
                        cascade_level, is_single, _cascade_depth + 1, coin_variant=coin_variant
                    )
                    chosen_type = cascade_type
                    cat_amount = cascade_amount
                    upgrades += cascade_upgrades
                    coin_amount = cascade_coin  # consolation tier sets the coin reward
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
            return chosen_type, cat_amount, upgrades, reward_texts, coin_amount
        return chosen_type, cat_amount, upgrades, build_string, coin_amount

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

        # ---- Pack-side job perks ----
        # pack_tier_upgrade (charge): bump `level` one tier (cap silver=3).
        # pack_floor (charge):        if get_pack_rewards returns Fine, upgrade to Nice.
        # pack_bonus_cat (timed):     after the open, grant +1 random cat.
        perk_msgs: list[str] = []
        if "pack_tier_upgrade" in _perks_active_ids(user) and _perks_consume_charge(user, "pack_tier_upgrade"):
            cap_idx = next((i for i, p in enumerate(pack_data) if p["name"].lower() == "silver"), len(pack_data) - 1)
            if level < cap_idx:
                level += 1
                perk_msgs.append(f"🪜 Crate Polish bumped to **{pack_data[level]['name']}**.")
            else:
                perk_msgs.append(f"🪜 Crate Polish: already at cap ({pack_data[cap_idx]['name']}).")

        # Coin-variant coin flip — roll AFTER pack_tier_upgrade has settled
        # `level`, so the tier used for the variant matches the one actually
        # opened. Specials never get the variant (they always open as
        # regular cat packs); the helper inside get_pack_rewards also
        # gracefully returns 0 if a cascade somehow lands on a special.
        coin_variant = (not pack_data[level]["special"]) and random.random() < PACK_COIN_VARIANT_CHANCE
        chosen_type, cat_amount, upgrades, reward_texts, coin_amount = get_pack_rewards(level, coin_variant=coin_variant)
        if coin_amount > 0 and reward_texts:
            # Subtle "this one's a coin crate" tag on the open animation.
            reward_texts[0] = "💰 " + reward_texts[0]

        if chosen_type == "Fine" and "pack_floor" in _perks_active_ids(user) and _perks_consume_charge(user, "pack_floor"):
            chosen_type = "Nice"
            perk_msgs.append("🚫 No Fines: floor upgraded to Nice.")

        # Double Pack voucher 🎟️ (Mystery reward): doubles this open's whole
        # contents — cats AND the coin-variant payout. Rolled after the tier
        # bump / floor lift / sub-1 lottery have settled, so it's a true
        # "double what you got", never a change to the odds.
        if _vouchers_consume(user, "double_pack"):
            cat_amount *= 2
            coin_amount *= 2
            perk_msgs.append("🎟️ Double Pack voucher: contents doubled!")

        bonus_type = None
        bonus_amount = 0
        if "pack_bonus_cat" in _perks_active_ids(user):
            bonus_type = random.choice(_season_eligible_cattypes())
            bonus_amount = 1
            perk_msgs.append(f"➕ Padded Crate: +1 {bonus_type} cat.")

        user[f"cat_{chosen_type}"] += cat_amount
        if bonus_type:
            user[f"cat_{bonus_type}"] += bonus_amount
        if coin_amount > 0:
            user.coins = int(user.coins or 0) + coin_amount
            _bump(user, "coins_earned", coin_amount)
        user.pack_upgrades += upgrades
        user.packs_opened += 1
        user[f"pack_{pack.lower()}"] -= 1
        await user.save()
        if cat_amount > 0 and chosen_type in cattypes:
            await mark_discovered(user, chosen_type)
        if bonus_type and bonus_amount > 0:
            await mark_discovered(user, bonus_type)

        logging.debug("Opened pack %s", pack)

        embed = discord.Embed(title=reward_texts[0], color=Colors.brown)
        await interaction.edit_original_response(embed=embed, view=None)
        for reward_text in reward_texts[1:]:
            await asyncio.sleep(1)
            things = reward_text.split("\n", 1)
            embed = discord.Embed(title=things[0], description=things[1], color=Colors.brown)
            await interaction.edit_original_response(embed=embed)
        # Perk toasts: append once after the open animation finishes.
        if perk_msgs:
            await asyncio.sleep(1)
            final_text = reward_texts[-1] + "\n\n" + "\n".join(perk_msgs)
            things = final_text.split("\n", 1)
            embed = discord.Embed(title=things[0], description=things[1], color=Colors.brown)
            await interaction.edit_original_response(embed=embed)
        # Coin reveal: tick-up animation mirroring the catslots bonus payout
        # (5/15/35/60/85/100% over ~2s). Runs after the perk toasts, before
        # the final view restore. Skipped on bulk Open All (handled there
        # with a static aggregated summary).
        if coin_amount > 0:
            final_text = (reward_texts[-1] + "\n\n" + "\n".join(perk_msgs)) if perk_msgs else reward_texts[-1]
            parts = final_text.split("\n", 1)
            anim_title = parts[0]
            anim_body = parts[1] if len(parts) > 1 else ""
            for frac in (0.05, 0.15, 0.35, 0.60, 0.85, 1.0):
                tick = coin_amount if frac == 1.0 else int(coin_amount * frac)
                try:
                    await interaction.edit_original_response(embed=discord.Embed(
                        title=anim_title,
                        description=f"{anim_body}\n\n💰 **{tick:,}** coins!",
                        color=Colors.brown,
                    ))
                except Exception:
                    pass
                await asyncio.sleep(0.3)
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
    description = "Each pack starts at one of eight tiers of increasing value - Wooden, Stone, Bronze, Silver, Gold, Platinum, Diamond, or Celestial - and can repeatedly move up tiers with a 30% chance per upgrade. This means that even a pack starting at Wooden, through successive upgrades, can reach the Celestial tier."
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
        await _maybe_show_season_reset_notice(interaction, user)

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

        # weekly quest 🍀 — hidden entirely during the days-28+ dead zone
        # (weekly_quest == '') and pre-migration 034 (_weekly_quest_safe '').
        _wq_display = _weekly_quest_safe(user)
        if _wq_display:
            weekly_quest = config.battle["quests"].get("weekly", {}).get(_wq_display)
            if weekly_quest:
                # tzinfo=utc — see the weekly rotation block in refresh_quests
                month_start = datetime.datetime(now.year, now.month, 1, tzinfo=datetime.timezone.utc) - datetime.timedelta(hours=4)
                description += f"__Weekly Quest__ (refreshes <t:{weekly_quest['end_time'] + int(month_start.timestamp())}:R>)\n"
                if weekly_quest["progress"] > user.weekly_progress:
                    description += f"{get_emoji(weekly_quest['emoji'])} {weekly_quest['title']} ({user.weekly_progress}/{weekly_quest['progress']})\n"
                    if _wq_display != "different":
                        colored = int(user.weekly_progress / weekly_quest["progress"] * 10)
                        description += get_emoji("staring_square") * colored + "⬛" * (10 - colored)
                    else:
                        for cat_index in user.weekly_cattypes:
                            description += get_emoji(cattypes[cat_index].lower() + "cat")
                        description += "⬛" * (weekly_quest["progress"] - user.weekly_progress)
                    description += f"\n- Reward: {WEEKLY_QUEST_XP} XP + {WEEKLY_QUEST_SCRATCHCARDS} Scratchcard\n\n"
                else:
                    description += f"✅ ~~{weekly_quest['title']}~~\n\n"

        # vote slot — real Top.gg vote ~1/3 of cycles, otherwise a misc-pool
        # substitute hosted in the same slot (vote_quest carries the misc id).
        # Pre-migration 028, _vote_quest_safe returns '' and we fall through
        # to the real vote branch unconditionally.
        _vq_display = _vote_quest_safe(user)
        if _vq_display:
            sub_quest = config.battle["quests"]["misc"].get(_vq_display)
            if sub_quest:
                if user.vote_cooldown != 0:
                    description += f"✅ ~~{sub_quest['title']}~~\n- Refreshes <t:{int(user.vote_cooldown + QUEST_COOLDOWN)}:R>\n"
                else:
                    description += f"{get_emoji(sub_quest['emoji'])} {sub_quest['title']}\n- Reward: {user.vote_reward} XP\n"
        elif config.VOTING_ENABLED:
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

                description += f"{get_emoji('topgg')} [Vote on Top.gg]({TOP_GG_VOTE_URL})\n"

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
            description += f"**Extra Rewards** [{user.progress}/{EXTRA_LEVEL_XP} XP]\n"
            colored = int(user.progress / (EXTRA_LEVEL_XP / 10))
            if EXTRA_LEVEL_REWARD == "Mystery":
                reward_bit = get_emoji("mysterypack") + " Mystery pack"
            else:
                reward_bit = get_emoji(EXTRA_LEVEL_REWARD.lower() + "pack") + f" {EXTRA_LEVEL_REWARD} pack"
            description += get_emoji("staring_square") * colored + "⬛" * (10 - colored) + "\nReward: " + reward_bit + "\n\n"
        else:
            level_data = config.battle["seasons"][str(user.season)][user.battlepass]
            season_max = len(config.battle["seasons"][str(user.season)])
            description += f"**Level {user.battlepass + 1}/{season_max}** [{user.progress}/{level_data['xp']} XP]\n"
            colored = int(user.progress / level_data["xp"] * 10)
            description += f"**{user.battlepass}** " + get_emoji("staring_square") * colored + "⬛" * (10 - colored) + f" **{user.battlepass + 1}**\n"

            if level_data["reward"] == "Rain":
                description += f"Reward: ☔ {level_data['amount']} minutes of rain\n\n"
            elif level_data["reward"] in cattypes:
                description += f"Reward: {get_emoji(level_data['reward'].lower() + 'cat')} {level_data['amount']} {level_data['reward']} cats\n\n"
            elif level_data["reward"] == "Mystery":
                description += f"Reward: {get_emoji('mysterypack')} Mystery — could be anything!\n\n"
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
            if EXTRA_LEVEL_REWARD == "Mystery":
                description += f"*Extra:* {get_emoji('mysterypack')} per {EXTRA_LEVEL_XP} XP"
            else:
                description += f"*Extra:* {get_emoji(EXTRA_LEVEL_REWARD.lower() + 'pack')} per {EXTRA_LEVEL_XP} XP"

        # held vouchers 🎟️ (Mystery rewards) — only shown when non-empty
        held_vouchers = _vouchers_load(user)
        if held_vouchers:
            counts = {}
            for v in held_vouchers:
                counts[v.get("id", "?")] = counts.get(v.get("id", "?"), 0) + 1
            bits = []
            for vid, n in counts.items():
                emoji, name, _ = VOUCHER_LABELS.get(vid, ("🎟️", vid, ""))
                bits.append(f"{emoji} {name}" + (f" ×{n}" if n > 1 else ""))
            description += "\n🎟️ **Vouchers:** " + ", ".join(bits)

        embedVar = discord.Embed(
            title=f"Cattlepass Season {user.season}",
            description=description,
            color=Colors.brown,
        ).set_footer(text=rain_shill)
        view = View(timeout=VIEW_TIMEOUT)

        button = Button(emoji="🔄", label="Refresh", style=ButtonStyle.blurple)
        button.callback = gen_main
        view.add_item(button)

        if len(get_news()) > len(global_user.news_state.strip()) or "0" in global_user.news_state.strip()[-4:]:
            embedVar.set_author(name="You have unread news! /news")

        if first:
            await interaction.followup.send(embed=embedVar, view=view)
        else:
            await interaction.edit_original_response(embed=embedVar, view=view)

    await gen_main(message, True)


@bot.tree.command(description="Vote for Cat Bot on top.gg for battlepass XP")
async def vote(message: discord.Interaction):
    if not config.VOTING_ENABLED:
        await message.response.send_message("Voting isn't enabled on this instance.", ephemeral=True)
        return

    global_user = await User.get_or_create(user_id=message.user.id)
    profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)

    async def render(interaction, first=False):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        if not first and not interaction.response.is_done():
            await interaction.response.defer()
        await global_user.refresh_from_db()
        await profile.refresh_from_db()

        now = int(time.time())
        next_vote = int(global_user.vote_time_topgg) + 43200
        can_vote = next_vote <= now

        if global_user.vote_time_topgg == 0:
            last_line = "**Last vote:** never"
        else:
            last_line = f"**Last vote:** <t:{int(global_user.vote_time_topgg)}:R>"

        if can_vote:
            next_line = f"{get_emoji('topgg')} **You can vote now!**"
        else:
            next_line = f"**Next vote:** <t:{next_vote}:R>"

        streak_line = f"🔥 **Streak:** {global_user.daily_catch_streak:,}"
        if global_user.daily_catch_streak > 0:
            streak_progress = get_streak_reward(global_user.daily_catch_streak)["done_emoji"]
            for i in range(global_user.daily_catch_streak + 1, global_user.daily_catch_streak + 9):
                streak_progress += get_streak_reward(i)["emoji"]
            streak_line += f"\n{streak_progress}"

        is_weekend = (discord.utils.utcnow() + datetime.timedelta(hours=4)).weekday() >= 4
        xp_line = f"**Reward:** {profile.vote_reward} XP"
        if is_weekend:
            xp_line = f"**Reward:** ~~{profile.vote_reward}~~ **{profile.vote_reward * 2}** XP *(Weekend 2x!)*"
        xp_line += "\n-# Run `/battlepass` after voting to claim."

        embedVar = discord.Embed(
            title=f"{get_emoji('topgg')} Vote for Cat Bot",
            description="\n\n".join([last_line, next_line, streak_line, xp_line, f"**Total votes:** {global_user.total_votes:,}"]),
            color=Colors.brown,
        )

        view = View(timeout=VIEW_TIMEOUT)
        view.add_item(Button(label="Vote on top.gg", style=ButtonStyle.url, url=TOP_GG_VOTE_URL, emoji=get_emoji("topgg")))

        if profile.reminders_enabled:
            toggle_btn = Button(emoji="🔕", label="Disable Reminders", style=ButtonStyle.blurple)
        else:
            toggle_btn = Button(emoji="🔔", label="Enable Reminders", style=ButtonStyle.blurple)
        toggle_btn.callback = toggle_reminders
        view.add_item(toggle_btn)

        refresh_btn = Button(emoji="🔄", label="Refresh", style=ButtonStyle.gray)
        refresh_btn.callback = render
        view.add_item(refresh_btn)

        if first:
            await message.response.send_message(embed=embedVar, view=view)
        else:
            await interaction.edit_original_response(embed=embedVar, view=view)

    async def toggle_reminders(interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        await interaction.response.defer()
        await profile.refresh_from_db()
        if not profile.reminders_enabled:
            try:
                dm_channel = await fetch_dm_channel(global_user)
                await dm_channel.send(
                    f"You have enabled reminders in {interaction.guild.name}. You can disable them in /vote or /battlepass in that server, or by saying `disable {interaction.guild.id}` here any time."
                )
            except Exception:
                await interaction.followup.send(
                    "Failed. Ensure you have DMs open by going to Server > Privacy Settings > Allow direct messages from server members.",
                    ephemeral=True,
                )
                return
        profile.reminders_enabled = not profile.reminders_enabled
        await profile.save()
        await interaction.followup.send(
            f"Reminders are now {'enabled' if profile.reminders_enabled else 'disabled'}.",
            ephemeral=True,
        )
        await render(interaction)

    await render(message, first=True)


_STOCK_HELP_PAGE_1 = """**📈 Cat Bot Stock Market — page 1/3: the basics**

There are **5 stocks** representing Cat Bot mechanics — Prisms (PRSM), Catnip (CTNP), Cattlepass (PASS), Achievements (ACHS), Rain (RAIN). Prices are **global** — the whole market sees the same number — but the shares you own are **per-server**.

**Market vs Limit**
- 🟢 **Market Buy / Market Sell** fills instantly against the house. Buys execute at the **ask** price; sells execute at the **bid** price. Most people should use this most of the time.
- ⚪ **Limit Buy / Limit Sell** lets you pick the price. The order rests in the book until either another player matches it, or the simulated price ticks through your limit (then the house fills it). Limits expire after 7 days.

**Bid / Ask spread**
The gap between bid and ask is the only friction on a market trade. Buy at the ask, sell at the bid — round-tripping costs you the spread (a couple of coins per share at current settings).

Coins come in and out via **Deposit** (a 🪙 100 Wooden pack → 🪙 100 coins, no fee) and **Withdraw** (🪙 coins → Wooden packs, 25% fee). Press *Continue* to see how prices move."""

_STOCK_HELP_PAGE_2 = """**📈 Stock market — page 2/3: how prices move**

Prices update every ~5 minutes. Each update is a combination of:

1. **Drift** — a tiny per-tick bias for each ticker.
2. **Random noise** — the bulk of normal day-to-day movement. Some tickers are more volatile (RAIN > CTNP > PRSM > PASS > ACHS).
3. **Sector correlation** — PRSM and RAIN share a *catch_engine* sector; PASS and ACHS share *progression*; CTNP is *consumable*. A bad day for the sector dings everything in it together.
4. **Market correlation** — every ticker gets a small dose of "is the whole market up or down today."
5. **Mean reversion** — a gentle pull toward the **fair value** shown on each ticker's page. Fair value is computed from in-game activity (e.g. how many prisms exist, how many cats are battlepass-active). This is the long-run anchor, *not* the displayed price.
6. **Events** — earnings, surprises, crashes, booms. See page 3.

There is no "right" price — it's a simulation. Read the news feed before you trade."""

_STOCK_HELP_PAGE_3 = """**📈 Stock market — page 3/3: events, dividends, tips**

**📰 News & events**
- **Earnings** are announced 24h in advance ("📰 PRSM earnings tomorrow") and fire as a one-shot ±8% (typical) move. Direction is hidden until the moment it fires.
- **Surprises** are unannounced ±4% moves on a single ticker. About 1–2 per day across the whole market.
- **🚨 Crashes / 🎉 Booms** are rare market-wide events that hit every ticker at once. Roughly once a week each.
- **💸 Dividends** are global cash payouts to every holder of a ticker (the ⭐ pill on the detail page). The chance/amount may be hidden. A dividend payout drops the price slightly (ex-div), like a real stock.

**Tips**
- Use the **News Feed** button to see what just happened and what's about to happen.
- The **Quick Stats** section on a ticker page shows its volatility class and next scheduled event.
- Your **avg cost** on the portfolio page is approximate — it averages over your historical buys without tracking lots properly. Treat unrealized P&L as a vibe, not an audit.
- Limit orders below the current price are your tool to "buy the dip" automatically — they rest until the price comes to you."""


def _stock_help_view(page: int) -> View:
    view = View(timeout=VIEW_TIMEOUT)
    if page > 1:
        back = Button(label="Back", style=ButtonStyle.gray, emoji="⬅️")
        back.callback = _stock_help_page_2 if page == 3 else _stock_help_page_1
        view.add_item(back)
    if page < 3:
        cont = Button(label="Continue", style=ButtonStyle.blurple)
        cont.callback = _stock_help_page_2 if page == 1 else _stock_help_page_3
        view.add_item(cont)
    return view


async def _stock_help_page_1(interaction):
    # First entry to help comes via the "Help" button on a ticker page (fresh
    # interaction, response not yet sent). Subsequent presses come from
    # Back/Continue buttons on a previously-sent ephemeral — edit_message
    # there. Try/except keeps both paths working without splitting the
    # function signature.
    try:
        await interaction.response.send_message(
            _STOCK_HELP_PAGE_1, view=_stock_help_view(1), ephemeral=True
        )
    except discord.InteractionResponded:
        await interaction.response.edit_message(content=_STOCK_HELP_PAGE_1, view=_stock_help_view(1))


async def _stock_help_page_2(interaction):
    try:
        await interaction.response.edit_message(content=_STOCK_HELP_PAGE_2, view=_stock_help_view(2))
    except (discord.InteractionResponded, discord.HTTPException):
        await interaction.response.send_message(_STOCK_HELP_PAGE_2, view=_stock_help_view(2), ephemeral=True)


async def _stock_help_page_3(interaction):
    try:
        await interaction.response.edit_message(content=_STOCK_HELP_PAGE_3, view=_stock_help_view(3))
    except (discord.InteractionResponded, discord.HTTPException):
        await interaction.response.send_message(_STOCK_HELP_PAGE_3, view=_stock_help_view(3), ephemeral=True)


async def stock_help(message):
    await _stock_help_page_1(message)


async def rewards_help(message):
    text = """**💸 Dividends (the ⭐ pill on a ticker)**

Dividends are random global cash payouts to every holder of a ticker. They schedule themselves every couple of days. You'll see a "💸 X% dividend of 🪙 Y/share <relative time>" line on the ticker page when one is coming up — that means there's an **X%** chance that every share of that ticker pays out **Y coins** to its holder when the time hits.

The chance and the per-share amount are *random*, and the outcome is the same for everyone — if the chance fails, it fails for the whole market at once. To spice it up, sometimes either the chance or the amount is **hidden** until the moment it fires.

The amount can also be negative on rare unlucky cycles. You've been warned.

When a dividend pays out, the stock's price drops a little (the ex-dividend drop) — real cash left the company, so the stock is worth slightly less. This is configured and small (~1.5% by default), but you'll see it on the chart right after a payout.

Holding dividends? The **Stock Dividend Boost** job perk adds a per-holder bonus on top of the global payout."""
    try:
        await message.response.send_message(text, ephemeral=True)
    except discord.InteractionResponded:
        await message.followup.send(text, ephemeral=True)


async def portfolio_help(message):
    text = """**📊 Portfolio**

The top of the page shows your **total portfolio value** (coins + shares × current price), today's change (last 24h on your current holdings), and unrealized **P&L on holdings** (current share value vs. weighted avg buy cost, summed across positions you still hold).

The breakdown below shows each ticker you own:
- **Quantity** and current 🪙 value.
- **Avg cost** — weighted average of every share you've ever bought of this ticker. This is approximate (it doesn't subtract cost basis when you sell), so treat it as a guide, not an audit.
- **P&L %** — current price vs your avg cost. 📈 means you're up on paper.

**Open orders** lists every limit order you have resting in the book. They expire 7 days after they were placed and refund automatically. You can cancel orders that are at least 12 hours old via the *Cancel orders...* button.

**Portfolio history** records the last ~13 events on your book — deposits, withdrawals, buy/sell orders, dividend payouts, and cancellations."""
    try:
        await message.response.send_message(text, ephemeral=True)
    except discord.InteractionResponded:
        await message.followup.send(text, ephemeral=True)


async def view_portfolio(interaction, person, refresh=False, hidden=None):
    if not hidden:
        hidden = False
    await interaction.response.defer(ephemeral=hidden)
    profile = await Profile.get_or_create(user_id=person.id, guild_id=interaction.guild.id)
    user = await User.get_or_create(user_id=person.id)

    view = LayoutView(timeout=VIEW_TIMEOUT)

    # v2: per-holding rows with avg cost + unrealized P&L; portfolio_value
    # is sum-of-(shares × current price) + coin balance. portfolio_value_yday
    # uses *current* shares × 24h-ago price (matches the main page's "today's
    # change" semantics — it's the move on the user's current book).
    now_ts = int(time.time())
    portfolio_value = int(profile.coins or 0)
    portfolio_value_yday = int(profile.coins or 0)
    share_lines = [f"🪙 **{int(profile.coins or 0):,}** coins"]
    cost_basis_total = 0.0
    pnl_basis_value = 0

    for stock in stock_data:
        ticker = stock["ticker"]
        emoji = get_emoji(stock["emoji"])
        amount_owned = int(profile[f"stock_{ticker.lower()}"] or 0)
        cur_price = await get_stock_price(ticker)
        past_price = await _stock_price_at(ticker, now_ts - 86400)
        item_value = cur_price * amount_owned
        portfolio_value += item_value
        portfolio_value_yday += (past_price if past_price is not None else cur_price) * amount_owned
        if amount_owned <= 0:
            continue
        avg_cost = await _compute_avg_cost(profile.id, ticker)
        if avg_cost is not None and avg_cost > 0:
            pnl_pct = (cur_price / avg_cost - 1.0) * 100.0
            pnl_emoji = _change_emoji(pnl_pct)
            share_lines.append(
                f"{emoji} **{amount_owned:,}x** {ticker} · 🪙 *{item_value:,}* · "
                f"avg 🪙 {avg_cost:,.1f} → cur 🪙 {cur_price:,} {pnl_emoji} {pnl_pct:+.1f}%"
            )
            cost_basis_total += avg_cost * amount_owned
            pnl_basis_value += item_value
        else:
            share_lines.append(
                f"{emoji} **{amount_owned:,}x** {ticker} · 🪙 *{item_value:,}* · "
                f"cur 🪙 {cur_price:,}"
            )

    if portfolio_value_yday > 0:
        day_pct = (portfolio_value / portfolio_value_yday - 1.0) * 100.0
    else:
        day_pct = None
    day_delta = portfolio_value - portfolio_value_yday

    shares_display = "\n".join(share_lines)

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

    # Unrealized P&L on currently-held shares: sum of (cur_price − avg_cost) ×
    # shares across positions where _compute_avg_cost returned a basis. Realized
    # gains from past sells are dropped (no lot tracking), so this matches the
    # per-row "+X%" pnl already shown above. The previous "lifetime growth"
    # divided wallet+shares by net pack→coin conversions, which produced
    # absurd ratios for users who rarely use the deposit/withdraw flow.
    if cost_basis_total > 0:
        pnl_pct = (pnl_basis_value / cost_basis_total - 1.0) * 100.0
        pnl_delta = pnl_basis_value - int(round(cost_basis_total))
        pnl_line = (
            f"{_change_emoji(pnl_pct)} {_format_pct(pnl_pct)} P&L on holdings "
            f"({pnl_delta:+,})"
        )
    else:
        pnl_line = "➖ no open positions"

    emoji_prefix = (user.emoji + " ") if user.emoji else ""

    today_line = (
        f"{_change_emoji(day_pct)} {_format_pct(day_pct)} today ({day_delta:+,})"
    )
    first_lines = (
        f"## {emoji_prefix}{person}",
        f"### 🪙 {portfolio_value:,}",
        today_line,
        pnl_line,
    )

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
            _bump(profile, "stock_coins_spent", -(order.price * order.quantity))
            await PortfolioHistory.create(user_id=profile.id, type="c", quantity=order.price * order.quantity, time=int(time.time()))
        else:
            profile[f"stock_{order.ticker.lower()}"] += order.quantity
            await PortfolioHistory.create(user_id=profile.id, type="C", quantity=order.quantity, time=int(time.time()), ticker=order.ticker)
        await order.delete()
    await profile.save()
    await interaction.edit_original_response(content="Orders cancelled!", view=None)


# ---------------------------------------------------------------------------
# Stock market v2: read-only stats helpers (day%/7d%/ATH/ATL/avg cost/etc.)
# ---------------------------------------------------------------------------
# These power the new /stocks UI. Each one is a single SQL aggregation so the
# main page and ticker detail can render without pulling history into Python.


async def _stock_price_at(ticker: str, when: int) -> int | None:
    """Latest pricehistory price at or before `when`. None if there's no row.
    Used for the day/7d % change widgets."""
    val = await pool.fetchval(
        "SELECT price FROM pricehistory WHERE ticker = $1 AND time <= $2 "
        "ORDER BY time DESC LIMIT 1",
        ticker, int(when),
    )
    return int(val) if val is not None else None


async def _stock_change_pct(ticker: str, window_seconds: int) -> float | None:
    """Percent change from `now - window_seconds` to the latest tick. Returns
    None if either anchor is missing (e.g., brand-new ticker)."""
    now = int(time.time())
    current = await get_stock_price(ticker)
    past = await _stock_price_at(ticker, now - window_seconds)
    if past is None or past <= 0 or current <= 0:
        return None
    return (current / past - 1.0) * 100.0


async def _stock_extremes(ticker: str) -> tuple[int | None, int | None]:
    """(all_time_high, all_time_low) from pricehistory. Both None if empty."""
    row = await pool.fetchrow(
        "SELECT MAX(price) AS hi, MIN(price) AS lo FROM pricehistory WHERE ticker = $1",
        ticker,
    )
    if not row:
        return None, None
    hi = row["hi"]
    lo = row["lo"]
    return (int(hi) if hi is not None else None, int(lo) if lo is not None else None)


async def _compute_avg_cost(profile_id: int, ticker: str) -> float | None:
    """Weighted-average buy price for a (profile, ticker) pair, from every
    `b`-typed portfoliohistory row. Returns None if the user has never bought
    this ticker.

    TODO: this is an *approximation* — it averages over every historical buy
    without subtracting cost basis on sells. A user who bought 10 at 40 and
    later sold 5 then bought 5 more at 80 will show avg cost 60, not the
    correct lot-tracked value. For a display-only "unrealized P&L" hint this
    is fine; a proper portfolio system would need lot tracking which is out
    of scope here.
    """
    row = await pool.fetchrow(
        "SELECT SUM(quantity)::bigint AS qty, "
        "SUM(quantity::bigint * price::bigint)::bigint AS cost "
        "FROM portfoliohistory WHERE user_id = $1 AND ticker = $2 AND type = 'b'",
        profile_id, ticker,
    )
    if not row or not row["qty"]:
        return None
    qty = int(row["qty"])
    if qty <= 0:
        return None
    cost = int(row["cost"] or 0)
    return cost / qty


def _change_emoji(pct: float | None) -> str:
    """Color-coded daily/period change emoji. Components V2 containers don't
    do colored embed borders, so we emoji-prefix the change line instead."""
    if pct is None:
        return "➖"
    if pct >= 0.05:
        return "📈"
    if pct <= -0.05:
        return "📉"
    return "➖"


def _format_pct(pct: float | None) -> str:
    if pct is None:
        return "—"
    return f"{pct:+.1f}%"


def _volatility_label(sigma_ticker: float) -> str:
    """Bucket the per-tick σ into a low/med/high human label for the Quick
    Stats section. Bands are derived from the default tuning (0.0011–0.0020)."""
    if sigma_ticker < 0.0013:
        return "low"
    if sigma_ticker < 0.0018:
        return "med"
    return "high"


async def _next_scheduled_event(ticker: str) -> dict | None:
    """The earliest unapplied earnings event for this ticker whose `time`
    (announce moment) is in the past — i.e., one the news feed is already
    surfacing. Returns {fires_at, headline} or None.

    Used for the "📰 Earnings in 18h" hint on the main page + ticker detail.
    """
    now = int(time.time())
    row = await pool.fetchrow(
        "SELECT fires_at, headline FROM newsevent "
        "WHERE ticker = $1 AND event_type = 'earnings' AND applied = false "
        "AND time <= $2 ORDER BY fires_at ASC LIMIT 1",
        ticker, now,
    )
    if not row:
        return None
    return {"fires_at": int(row["fires_at"]), "headline": str(row["headline"])}


async def _recent_news_for_ticker(ticker: str, limit: int = 3) -> list[dict]:
    """Last N applied newsevent rows for this ticker (or market-wide). Used
    by the ticker detail page's news pane."""
    rows = await pool.fetch(
        "SELECT time, ticker, event_type, headline, impulse_pct FROM newsevent "
        "WHERE applied = true AND (ticker = $1 OR ticker IS NULL) "
        "ORDER BY time DESC LIMIT $2",
        ticker, int(limit),
    )
    return [dict(r) for r in rows]


async def _recent_news_global(limit: int = 25) -> list[dict]:
    """Reverse-chrono applied newsevent rows for the global feed. Includes
    every ticker and market-wide rows."""
    rows = await pool.fetch(
        "SELECT time, ticker, event_type, headline, impulse_pct FROM newsevent "
        "WHERE applied = true ORDER BY time DESC LIMIT $1",
        int(limit),
    )
    return [dict(r) for r in rows]


async def _upcoming_earnings(within_seconds: int = 48 * 3600) -> list[dict]:
    """Announced-but-not-yet-fired earnings landing within `within_seconds`.
    Note at the top of the News Feed page."""
    now = int(time.time())
    rows = await pool.fetch(
        "SELECT ticker, fires_at, headline FROM newsevent "
        "WHERE event_type = 'earnings' AND applied = false "
        "AND time <= $1 AND fires_at <= $2 ORDER BY fires_at ASC",
        now, now + int(within_seconds),
    )
    return [dict(r) for r in rows]


_EVENT_TYPE_ICON = {
    "earnings": "📰",
    "surprise": "⚡",
    "crash": "🚨",
    "boom": "🎉",
    "dividend": "💸",
    "system": "ℹ️",
}


def _event_icon(event_type: str) -> str:
    return _EVENT_TYPE_ICON.get(event_type, "📌")


# ---------------------------------------------------------------------------
# Stock market v2: trade execution
# ---------------------------------------------------------------------------
# Two paths from the UI:
#   - execute_market_trade — instant fill against the house (bid/ask spread is
#     the friction). Atomic via `transaction()` + FOR UPDATE on the profile row.
#   - place_limit_order   — escrow + insert into `order`, then try user-vs-user
#     match via `resolve_orders` (unchanged). Anything that survives that pass
#     rests in the book and is picked up by `_sweep_crossed_limits` after each
#     price tick crosses through it.
#
# Caller contract: pass a refreshed profile so we have user_id/guild_id, but
# the helpers refetch under FOR UPDATE so any concurrent change is observed.


class TradeError(Exception):
    """Raised for user-facing validation failures (no coins, no shares, etc.).
    The message is shown verbatim in the trade modal toast."""


async def execute_market_trade(profile, ticker: str, side: str, qty: int) -> tuple[int, int, int]:
    """Instant market trade against the house. Returns (filled_qty, fill_price,
    total_coins). Raises TradeError on validation failure.

    `profile` is used for its (user_id, guild_id) — we relock the row inside the
    transaction so rapid double-clicks can't double-spend. The house has
    effectively infinite virtual capacity; the bid/ask spread is the only
    friction (no per-row inventory).
    """
    side = side.lower()
    if side not in ("buy", "sell"):
        raise TradeError("internal: side must be 'buy' or 'sell'")
    qty = int(qty)
    if qty <= 0:
        raise TradeError("quantity must be a positive integer")

    ticker_upper = ticker.upper()
    if ticker_upper not in {s["ticker"] for s in stock_data}:
        raise TradeError(f"unknown ticker {ticker}")

    stock_col = f"stock_{ticker_upper.lower()}"

    # Probed outside the transaction (cached on config), so we know whether to
    # write to the recap counters at all.
    recap_present = await _recap_columns_present()

    async with transaction() as conn:
        p = await Profile.get_or_create(
            connection=conn,
            user_id=int(profile.user_id),
            guild_id=int(profile.guild_id),
        )

        if side == "buy":
            fill_price = await get_stock_ask(ticker_upper)
            total = qty * fill_price
            if int(p.coins or 0) < total:
                raise TradeError(
                    f"not enough coins — need 🪙 {total:,}, have 🪙 {int(p.coins or 0):,}"
                )
            p.coins = int(p.coins or 0) - total
            p[stock_col] = int(p[stock_col] or 0) + qty
            if recap_present:
                _bump(p, "stock_coins_spent", total)
            type_code = "b"
        else:
            fill_price = await get_stock_bid(ticker_upper)
            held = int(p[stock_col] or 0)
            if held < qty:
                raise TradeError(
                    f"not enough shares — need {qty:,}x {ticker_upper}, have {held:,}"
                )
            p[stock_col] = held - qty
            total = qty * fill_price
            p.coins = int(p.coins or 0) + total
            if recap_present:
                _bump(p, "coins_earned", total)
                _bump(p, "stock_coins_earned", total)
            type_code = "s"

        await p.save()

    # PortfolioHistory + ach trigger are outside the transaction — neither is
    # load-bearing for correctness and both can swallow individual failures
    # without re-entering the trade math.
    now_ts = int(time.time())
    try:
        await PortfolioHistory.create(
            user_id=p.id,
            ticker=ticker_upper,
            type=type_code,
            quantity=qty,
            price=fill_price,
            time=now_ts,
        )
    except Exception:
        logging.exception("portfoliohistory write failed for market %s on %s", side, ticker_upper)

    return qty, fill_price, total


async def place_limit_order(profile, ticker: str, side: str, qty: int, price: int) -> tuple[Order, int]:
    """Escrow + create + immediately try user-vs-user match. The new house-side
    sweep runs from `_run_stock_tick` after the price moves — it doesn't fire
    inside this call. Returns (order, remaining_qty). If remaining_qty == 0
    the order was fully filled in `resolve_orders` and the order row is gone.
    """
    side = side.lower()
    if side not in ("buy", "sell"):
        raise TradeError("internal: side must be 'buy' or 'sell'")
    qty = int(qty)
    price = int(price)
    if qty <= 0:
        raise TradeError("quantity must be a positive integer")
    if price <= 0:
        raise TradeError("price must be a positive integer")

    ticker_upper = ticker.upper()
    if ticker_upper not in {s["ticker"] for s in stock_data}:
        raise TradeError(f"unknown ticker {ticker}")

    stock_col = f"stock_{ticker_upper.lower()}"
    recap_present = await _recap_columns_present()

    # Open-orders cap matches the legacy OrderModal limit so spam-clickers
    # can't fill the book.
    if await Order.count("user_id = $1", profile.id) >= 25:
        raise TradeError("too many open orders (max 25). cancel some first.")

    async with transaction() as conn:
        p = await Profile.get_or_create(
            connection=conn,
            user_id=int(profile.user_id),
            guild_id=int(profile.guild_id),
        )

        if side == "buy":
            total = qty * price
            if int(p.coins or 0) < total:
                raise TradeError(
                    f"not enough coins — need 🪙 {total:,}, have 🪙 {int(p.coins or 0):,}"
                )
            p.coins = int(p.coins or 0) - total
            if recap_present:
                _bump(p, "stock_coins_spent", total)
        else:
            held = int(p[stock_col] or 0)
            if held < qty:
                raise TradeError(
                    f"not enough shares — need {qty:,}x {ticker_upper}, have {held:,}"
                )
            p[stock_col] = held - qty

        await p.save()
        profile_id = p.id

    now_ts = int(time.time())
    order = await Order.create(
        user_id=profile_id,
        ticker=ticker_upper,
        type_buy=(side == "buy"),
        quantity=qty,
        price=price,
        time=now_ts,
    )
    # Legacy PortfolioHistory contract: a `b`/`s` row at placement records the
    # *intent* (limit price). The fill against another user / the house happens
    # silently and `view_portfolio` displays the placement row. Matches the
    # behaviour of the pre-v2 OrderModal so the user-visible activity log
    # doesn't regress.
    try:
        await PortfolioHistory.create(
            user_id=profile_id,
            ticker=ticker_upper,
            type="b" if side == "buy" else "s",
            quantity=qty,
            price=price,
            time=now_ts,
        )
    except Exception:
        logging.exception("portfoliohistory write failed for limit %s on %s", side, ticker_upper)

    # First match against any crossing user orders. Whatever survives rests in
    # the book and is picked up by `_sweep_crossed_limits` next price tick.
    remaining = await resolve_orders(order)
    return order, remaining


async def _sweep_crossed_limits(ticker: str) -> int:
    """Run after the price tick: fill every resting limit order on `ticker`
    that the new bid/ask has crossed, against the house.

    Buy orders with price >= ask fill at min(order.price, ask) — if the user
    overpaid, the difference is refunded to their coin balance. Sell orders
    with price <= bid fill at max(order.price, bid).

    The legacy `time = 0` MM rows are gone (migration 030 cleared them), so we
    don't need to skip them here.

    Returns the count of orders fully filled.
    """
    if not STOCK_MARKET.get("enabled"):
        return 0

    bid = await get_stock_bid(ticker)
    ask = await get_stock_ask(ticker)
    recap_present = await _recap_columns_present()
    now_ts = int(time.time())
    fully_filled = 0

    # Match buys (highest-priced first — those crossed the most)
    async for order in Order.filter(
        "ticker = $1 AND type_buy = true AND price >= $2 ORDER BY price DESC, time ASC",
        ticker, ask,
    ):
        fill_price = min(int(order.price), int(ask))
        refund_per_share = int(order.price) - fill_price
        qty = int(order.quantity)

        profile = await Profile.get_or_none(id=order.user_id)
        if profile is None:
            # Owner profile vanished — orphan the order to keep the loop clean.
            await order.delete()
            continue

        profile[f"stock_{ticker.lower()}"] = int(profile[f"stock_{ticker.lower()}"] or 0) + qty
        if refund_per_share > 0:
            refund_total = refund_per_share * qty
            profile.coins = int(profile.coins or 0) + refund_total
            if recap_present:
                # The original escrow was counted as spent; refund the diff.
                _bump(profile, "stock_coins_spent", -refund_total)
        await profile.save()

        await PortfolioHistory.create(
            user_id=profile.id,
            ticker=ticker,
            type="b",
            quantity=qty,
            price=fill_price,
            time=now_ts,
        )

        await order.delete()
        fully_filled += 1

    # Match sells (lowest-priced first — those crossed the most)
    async for order in Order.filter(
        "ticker = $1 AND type_buy = false AND price <= $2 ORDER BY price ASC, time ASC",
        ticker, bid,
    ):
        fill_price = max(int(order.price), int(bid))
        qty = int(order.quantity)

        profile = await Profile.get_or_none(id=order.user_id)
        if profile is None:
            await order.delete()
            continue

        proceeds = fill_price * qty
        profile.coins = int(profile.coins or 0) + proceeds
        if recap_present:
            _bump(profile, "coins_earned", proceeds)
            _bump(profile, "stock_coins_earned", proceeds)
        await profile.save()

        await PortfolioHistory.create(
            user_id=profile.id,
            ticker=ticker,
            type="s",
            quantity=qty,
            price=fill_price,
            time=now_ts,
        )

        await order.delete()
        fully_filled += 1

    if fully_filled:
        # The new fill is now the latest trade — record it on the chart at the
        # mid so the price line moves through this tick's actual transactions
        # rather than just the simulated price.
        try:
            mid = await get_stock_price(ticker)
            await PriceHistory.create(ticker=ticker, price=mid, time=now_ts)
            temp_stock_prices[ticker] = mid
        except Exception:
            logging.exception("post-sweep pricehistory write failed for %s", ticker)

    return fully_filled


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
                _bump(u, "coins_earned", delta)
                _bump(u, "stock_coins_earned", delta)
                updates.append(u)
            _recap_cols = ["coins"]
            if await _recap_columns_present():
                _recap_cols += ["coins_earned", "stock_coins_earned"]
            await Profile.bulk_update(updates, *_recap_cols)

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
        _stock_proceeds = (order.quantity - remaining_quantity) * order.price
        profile.coins += _stock_proceeds
        _bump(profile, "coins_earned", _stock_proceeds)
        _bump(profile, "stock_coins_earned", _stock_proceeds)
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
                _bump(profile, "coins_earned", pack["totalvalue"])
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
        """Limit-order modal: takes quantity + price, escrows, places, then
        runs user-vs-user matching. Anything that survives rests in the book
        and will be auto-filled by `_sweep_crossed_limits` once the price
        ticks through it."""

        def __init__(self, ticker, type, recommended_price, max_shares=None):
            super().__init__(title=f"Limit {type.capitalize()} {ticker}")

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
                default=str(recommended_price),
                min_length=1,
                max_length=6,
                required=True,
                style=discord.TextStyle.short,
            )
            self.add_item(self.price)

        async def on_submit(self, interaction: discord.Interaction):
            await profile.refresh_from_db()
            try:
                price = int(self.price.value)
                if price <= 0:
                    raise ValueError
            except Exception:
                await interaction.response.send_message(
                    "your price looks funny (it must be a positive integer)", ephemeral=True
                )
                return
            try:
                quantity = int(self.quantity.value)
                if quantity <= 0:
                    raise ValueError
            except Exception:
                await interaction.response.send_message(
                    "your quantity looks funny (it must be a positive integer)", ephemeral=True
                )
                return

            try:
                order, remaining = await place_limit_order(
                    profile, self.ticker, self.type, quantity, price
                )
            except TradeError as e:
                await interaction.response.send_message(str(e), ephemeral=True)
                return

            side_emoji = "🟢" if self.type == "buy" else "🔴"
            msg = f"{side_emoji} Limit {self.type} placed: {quantity:,}x **{self.ticker}** @ 🪙 {price:,}"
            if remaining == 0:
                msg += "\n✅ Filled immediately."
            elif remaining < quantity:
                msg += f"\n✅ Partially filled. {remaining:,}/{quantity:,} shares resting in the book."
            else:
                msg += "\n📖 Resting in the book — will auto-fill when the price crosses your limit."
            await interaction.response.send_message(msg, ephemeral=True)
            await achemb(
                interaction, "buy_stock" if self.type == "buy" else "sell_stock", "followup"
            )

    class MarketModal(Modal):
        """Market-order modal: takes quantity only and fills instantly against
        the house at the current bid/ask."""

        def __init__(self, ticker, type, fill_price_hint, max_shares):
            super().__init__(title=f"Market {type.capitalize()} {ticker}")
            self.ticker = ticker
            self.type = type
            placeholder = (
                f"Shares to buy at ~🪙 {fill_price_hint:,} each"
                if type == "buy"
                else f"Shares to sell at ~🪙 {fill_price_hint:,} each"
            )
            self.quantity = TextInput(
                label=f"Quantity (you have {max_shares:,})",
                placeholder=placeholder,
                min_length=1,
                max_length=6,
                required=True,
                style=discord.TextStyle.short,
            )
            self.add_item(self.quantity)

        async def on_submit(self, interaction: discord.Interaction):
            await profile.refresh_from_db()
            try:
                quantity = int(self.quantity.value)
                if quantity <= 0:
                    raise ValueError
            except Exception:
                await interaction.response.send_message(
                    "your quantity looks funny (it must be a positive integer)", ephemeral=True
                )
                return

            try:
                filled, fill_price, total = await execute_market_trade(
                    profile, self.ticker, self.type, quantity
                )
            except TradeError as e:
                await interaction.response.send_message(str(e), ephemeral=True)
                return

            side_label = "Bought" if self.type == "buy" else "Sold"
            await interaction.response.send_message(
                f"✅ {side_label} {filled:,}x **{self.ticker}** at 🪙 {fill_price:,} each (total 🪙 {total:,})",
                ephemeral=True,
            )
            await achemb(
                interaction, "buy_stock" if self.type == "buy" else "sell_stock", "followup"
            )

    async def buy_stock(interaction):
        # Limit Buy — falls back to old recommended-price logic (lowest
        # outstanding sell, or 40). We still surface it so the modal default
        # is a useful starting price.
        profile_inner = await Profile.get_or_create(user_id=interaction.user.id, guild_id=message.guild.id)
        ticker = interaction.data["custom_id"].split("_")[0]
        try:
            recommended_price = await Order.min("price", "ticker = $1 AND type_buy = $2", ticker, False)
            if not recommended_price:
                recommended_price = await get_stock_ask(ticker)
        except Exception:
            recommended_price = 40
        await interaction.response.send_modal(OrderModal(ticker, "buy", recommended_price, profile_inner.coins))

    async def sell_stock(interaction):
        profile_inner = await Profile.get_or_create(user_id=interaction.user.id, guild_id=message.guild.id)
        ticker = interaction.data["custom_id"].split("_")[0]
        try:
            recommended_price = await Order.max("price", "ticker = $1 AND type_buy = $2", ticker, True)
            if not recommended_price:
                recommended_price = await get_stock_bid(ticker)
        except Exception:
            recommended_price = 40
        await interaction.response.send_modal(OrderModal(ticker, "sell", recommended_price, profile_inner[f"stock_{ticker.lower()}"]))

    async def market_buy_stock(interaction):
        ticker = interaction.data["custom_id"].split("_")[0]
        profile_inner = await Profile.get_or_create(user_id=interaction.user.id, guild_id=message.guild.id)
        ask = await get_stock_ask(ticker)
        await interaction.response.send_modal(
            MarketModal(ticker, "buy", ask, int(profile_inner.coins or 0))
        )

    async def market_sell_stock(interaction):
        ticker = interaction.data["custom_id"].split("_")[0]
        profile_inner = await Profile.get_or_create(user_id=interaction.user.id, guild_id=message.guild.id)
        bid = await get_stock_bid(ticker)
        held = int(profile_inner[f"stock_{ticker.lower()}"] or 0)
        await interaction.response.send_modal(
            MarketModal(ticker, "sell", bid, held)
        )

    async def view_stock(interaction):
        await interaction.response.defer()
        view = LayoutView(timeout=VIEW_TIMEOUT)

        stock_ticker = interaction.data["custom_id"]
        stock = next((s for s in stock_data if s["ticker"] == stock_ticker), None)
        if stock is None:
            await interaction.followup.send("Unknown ticker.", ephemeral=True)
            return

        # ~48–72h window for the chart, same as before.
        data = []
        async for i in PriceHistory.filter(
            "ticker = $1 AND time > $2", stock_ticker, int(time.time() - 3600 * 72)
        ):
            data.append((i.time, i.price))
        buffer = await bot.loop.run_in_executor(None, graph.make_graph, data, 10, 3)
        file = discord.File(fp=buffer, filename="output.png")

        # Live prices + movement
        mid_price = await get_stock_price(stock_ticker)
        bid_price = await get_stock_bid(stock_ticker)
        ask_price = await get_stock_ask(stock_ticker)
        day_pct = await _stock_change_pct(stock_ticker, 86400)
        week_pct = await _stock_change_pct(stock_ticker, 7 * 86400)
        ath, atl = await _stock_extremes(stock_ticker)

        # Header lines
        header_line2 = (
            f"# 🪙 {mid_price:,}  ·  bid 🪙 {bid_price:,} / ask 🪙 {ask_price:,}"
        )
        header_line3 = (
            f"{_change_emoji(day_pct)} {_format_pct(day_pct)} today · "
            f"{_change_emoji(week_pct)} {_format_pct(week_pct)} 7d · "
            f"ATH 🪙 {ath:,} · ATL 🪙 {atl:,}"
            if ath is not None and atl is not None
            else f"{_change_emoji(day_pct)} {_format_pct(day_pct)} today · "
                 f"{_change_emoji(week_pct)} {_format_pct(week_pct)} 7d"
        )

        # Active dividend pill (kept from v1, still valid surface area)
        reward = await Reward.get_or_create(ticker=stock["ticker"])
        reward_suffix = ""
        if reward and reward.active:
            reward_suffix = (
                f"\n💸 {reward.chance if not reward.chance_hidden else '???'}% "
                f"dividend of 🪙 {reward.amount if not reward.amount_hidden else '???'}/share "
                f"<t:{reward.end_time}:R>"
            )

        container = Container(
            f"## {get_emoji(stock['emoji'])} {stock['name']} ({stock['ticker']}){reward_suffix}",
            header_line2,
            header_line3,
            "===",
            discord.ui.MediaGallery(discord.MediaGalleryItem(file)),
            "===",
        )

        # Quick stats
        cfg = _ticker_cfg(stock_ticker)
        sector = cfg.get("sector", "—")
        sigma_label = _volatility_label(float(cfg.get("sigma_ticker", 0.001)))
        fair = await _compute_fair_price(stock_ticker)
        evt = await _next_scheduled_event(stock_ticker)
        if evt:
            evt_line = f"📰 Earnings <t:{evt['fires_at']}:R>"
        else:
            evt_line = "📰 No earnings scheduled within the announce window"
        container.add_item(TextDisplay(
            "### Quick stats\n"
            f"Sector: **{sector}** · Volatility: **{sigma_label}**\n"
            f"Fair value (long-run anchor): 🪙 {fair:,}\n"
            f"{evt_line}"
        ))

        # Your position
        profile_inner = await Profile.get_or_create(
            user_id=interaction.user.id, guild_id=message.guild.id
        )
        held = int(profile_inner[f"stock_{stock_ticker.lower()}"] or 0)
        avg_cost = await _compute_avg_cost(profile_inner.id, stock_ticker)
        if held <= 0:
            pos_lines = "You don't own any **" + stock_ticker + "** yet."
        else:
            cur_val = mid_price * held
            if avg_cost is not None:
                cost_basis = int(round(avg_cost * held))
                pnl = cur_val - cost_basis
                pnl_pct = (mid_price / avg_cost - 1.0) * 100.0
                pos_lines = (
                    f"Shares: **{held:,}x**\n"
                    f"Avg cost: 🪙 {avg_cost:,.1f} (basis 🪙 {cost_basis:,})\n"
                    f"Current value: 🪙 {cur_val:,}\n"
                    f"{_change_emoji(pnl_pct)} Unrealized P&L: "
                    f"{pnl:+,} ({pnl_pct:+.1f}%)"
                )
            else:
                pos_lines = (
                    f"Shares: **{held:,}x**\n"
                    f"Current value: 🪙 {cur_val:,}\n"
                    "_(avg cost unavailable — earlier holdings predate v2)_"
                )
        container.add_item(TextDisplay("### Your position\n" + pos_lines))

        # News for this ticker
        ticker_news = await _recent_news_for_ticker(stock_ticker, limit=3)
        if ticker_news:
            news_lines = []
            for n in ticker_news:
                head = n["headline"]
                if len(head) > 90:
                    head = head[:87] + "…"
                news_lines.append(
                    f"{_event_icon(n['event_type'])} {head} <t:{int(n['time'])}:R>"
                )
            container.add_item(TextDisplay("### Recent news\n" + "\n".join(news_lines)))
        else:
            container.add_item(TextDisplay("### Recent news\n_No news for this ticker yet._"))

        # Order-book preview (single line + button — full depth on the order
        # book sub-page).
        buys_n = await Order.count(
            "ticker = $1 AND type_buy = $2", stock_ticker, True
        )
        sells_n = await Order.count(
            "ticker = $1 AND type_buy = $2", stock_ticker, False
        )
        book_btn = Button(
            label="View Order Book",
            style=ButtonStyle.gray,
            emoji="📖",
            custom_id=stock_ticker + "_book",
        )
        book_btn.callback = view_order_book
        container.add_item(
            Section(
                "### Order book",
                f"**{buys_n:,}** resting buy orders · **{sells_n:,}** resting sell orders",
                book_btn,
            )
        )

        view.add_item(container)

        # Trade buttons — market is loud, limit is gray and de-emphasised.
        market_buy_btn = Button(
            label="Market Buy", style=ButtonStyle.green, emoji="🟢",
            custom_id=stock_ticker + "_marketbuy",
        )
        market_buy_btn.callback = market_buy_stock
        market_sell_btn = Button(
            label="Market Sell", style=ButtonStyle.red, emoji="🔴",
            custom_id=stock_ticker + "_marketsell",
        )
        market_sell_btn.callback = market_sell_stock
        limit_buy_btn = Button(
            label="Limit Buy", style=ButtonStyle.gray,
            custom_id=stock_ticker + "_limitbuy",
        )
        limit_buy_btn.callback = buy_stock
        limit_sell_btn = Button(
            label="Limit Sell", style=ButtonStyle.gray,
            custom_id=stock_ticker + "_limitsell",
        )
        limit_sell_btn.callback = sell_stock

        back_button = Button(style=ButtonStyle.gray, emoji="⬅️")
        back_button.callback = go_back
        refresh_button = Button(
            label="Refresh", style=ButtonStyle.gray, emoji="🔄",
            custom_id=stock_ticker,
        )
        refresh_button.callback = view_stock
        help_button = Button(label="Help", style=ButtonStyle.gray, emoji="💡")
        help_button.callback = stock_help

        container.add_item(Separator())
        container.add_item(ActionRow(market_buy_btn, market_sell_btn))
        container.add_item(ActionRow(limit_buy_btn, limit_sell_btn))
        container.add_item(ActionRow(back_button, refresh_button, help_button))

        await interaction.edit_original_response(view=view, attachments=[file])

    async def main_page():
        await profile.refresh_from_db()

        view = LayoutView(timeout=VIEW_TIMEOUT)

        # Current portfolio value + value 24h ago using *current* shares — the
        # daily delta describes how the user's actual book moved today, which
        # is what "Today's change" means in a real brokerage UI.
        now_ts = int(time.time())
        portfolio_value = int(profile.coins or 0)
        portfolio_value_yesterday = int(profile.coins or 0)
        for s in stock_data:
            held = int(profile[f"stock_{s['ticker'].lower()}"] or 0)
            if held <= 0:
                continue
            cur = await get_stock_price(s["ticker"])
            past = await _stock_price_at(s["ticker"], now_ts - 86400)
            portfolio_value += cur * held
            portfolio_value_yesterday += (past if past is not None else cur) * held

        if portfolio_value_yesterday > 0:
            day_pct = (portfolio_value / portfolio_value_yesterday - 1.0) * 100.0
        else:
            day_pct = None
        day_delta = portfolio_value - portfolio_value_yesterday

        container = Container(
            "## 📈 Stock Market",
            f"**Portfolio:** 🪙 {portfolio_value:,}  ·  "
            f"{_change_emoji(day_pct)} {_format_pct(day_pct)} today "
            f"({day_delta:+,})",
            "===",
        )

        # Per-ticker rows
        for item in stock_data:
            ticker = item["ticker"]
            price = await get_stock_price(ticker)
            day_pct_t = await _stock_change_pct(ticker, 86400)
            amount_owned = int(profile[f"stock_{ticker.lower()}"] or 0)
            own_line = (
                f"You own: **{amount_owned:,}x** (🪙 {amount_owned * price:,})"
                if amount_owned > 0
                else "You own: —"
            )

            # Upcoming-event line takes priority; fall back to last news.
            evt = await _next_scheduled_event(ticker)
            if evt:
                fires_at = evt["fires_at"]
                hint = f"📰 Earnings <t:{fires_at}:R>"
            else:
                recent = await _recent_news_for_ticker(ticker, limit=1)
                if recent:
                    head = recent[0]["headline"]
                    if len(head) > 90:
                        head = head[:87] + "…"
                    hint = f"{_event_icon(recent[0]['event_type'])} {head}"
                else:
                    hint = "—"

            button = Button(label="View", style=ButtonStyle.blurple, custom_id=ticker)
            button.callback = view_stock
            container.add_item(
                Section(
                    f"### {get_emoji(item['emoji'])} {ticker} — 🪙 {price:,}  "
                    f"{_change_emoji(day_pct_t)} {_format_pct(day_pct_t)}",
                    f"{own_line}\n{hint}",
                    button,
                )
            )

        # Recent News teaser (last 3 headlines) — full feed lives behind the
        # News Feed button below.
        news = await _recent_news_global(limit=3)
        if news:
            news_lines = []
            for n in news:
                tkr_badge = n["ticker"] or "🌐"
                head = n["headline"]
                if len(head) > 80:
                    head = head[:77] + "…"
                news_lines.append(
                    f"{_event_icon(n['event_type'])} `{tkr_badge:<4s}` {head} "
                    f"<t:{int(n['time'])}:R>"
                )
            container.add_item(Separator())
            container.add_item(
                TextDisplay("### 📰 Recent News\n" + "\n".join(news_lines))
            )

        row1 = ActionRow()
        row1.add_item(_btn("Deposit", ButtonStyle.green, deposit))
        row1.add_item(_btn("Withdraw", ButtonStyle.red, withdraw))
        row2 = ActionRow()
        row2.add_item(_btn("Your Portfolio", ButtonStyle.blurple, view_user_portfolio))
        row2.add_item(_btn("News Feed", ButtonStyle.gray, view_news_feed, emoji="📰"))

        container.add_item(Separator())
        container.add_item(row1)
        container.add_item(row2)
        view.add_item(container)
        return view

    def _btn(label, style, callback, emoji=None, custom_id=None):
        b = Button(label=label, style=style, emoji=emoji, custom_id=custom_id)
        b.callback = callback
        return b

    async def view_user_portfolio(interaction):
        await view_portfolio(interaction, interaction.user, refresh=False, hidden=True)

    async def view_news_feed(interaction):
        await interaction.response.defer()
        view2 = LayoutView(timeout=VIEW_TIMEOUT)
        container = Container("## 📰 News Feed", "===")

        upcoming = await _upcoming_earnings(within_seconds=48 * 3600)
        if upcoming:
            up_lines = []
            for u in upcoming:
                up_lines.append(
                    f"📰 **{u['ticker']}** earnings <t:{int(u['fires_at'])}:R>"
                )
            container.add_item(
                TextDisplay("### Upcoming earnings (next 48h)\n" + "\n".join(up_lines))
            )
            container.add_item(Separator())

        rows = await _recent_news_global(limit=25)
        if not rows:
            container.add_item(
                TextDisplay("_No news yet. Check back after the next price tick._")
            )
        else:
            lines = []
            for n in rows:
                tkr_badge = n["ticker"] or "🌐"
                impulse = float(n["impulse_pct"] or 0.0)
                if impulse:
                    impulse_str = f" `{impulse * 100:+.1f}%`"
                else:
                    impulse_str = ""
                lines.append(
                    f"{_event_icon(n['event_type'])} `{tkr_badge:<4s}` {n['headline']}{impulse_str} "
                    f"<t:{int(n['time'])}:R>"
                )
            # Discord text component cap is generous, but split if we approach it.
            chunk = "\n".join(lines)
            container.add_item(TextDisplay(chunk))

        container.add_item(Separator())
        back_btn = Button(style=ButtonStyle.gray, emoji="⬅️")
        back_btn.callback = go_back
        refresh_btn = Button(label="Refresh", style=ButtonStyle.gray, emoji="🔄")
        refresh_btn.callback = view_news_feed
        container.add_item(ActionRow(back_btn, refresh_btn))
        view2.add_item(container)
        await interaction.edit_original_response(view=view2, attachments=[])

    async def view_order_book(interaction):
        """Full depth for one ticker — 10 deep on each side."""
        await interaction.response.defer()
        ticker = interaction.data["custom_id"].split("_")[0]
        view2 = LayoutView(timeout=VIEW_TIMEOUT)
        bid = await get_stock_bid(ticker)
        ask = await get_stock_ask(ticker)
        mid = await get_stock_price(ticker)
        container = Container(
            f"## 📖 {ticker} Order Book",
            f"mid 🪙 {mid:,} · bid 🪙 {bid:,} / ask 🪙 {ask:,}",
            "===",
        )

        buys = await Order.collect_limit(
            ["price", RawSQL("SUM(quantity) as total_quantity")],
            "type_buy = $1 AND ticker = $2 GROUP BY price ORDER BY price DESC LIMIT 10",
            True, ticker, add_primary_key=False,
        )
        sells = await Order.collect_limit(
            ["price", RawSQL("SUM(quantity) as total_quantity")],
            "type_buy = $1 AND ticker = $2 GROUP BY price ORDER BY price ASC LIMIT 10",
            False, ticker, add_primary_key=False,
        )
        buy_lines = (
            "\n".join(f"🟢 🪙 **{i.price:,}** — *{i.total_quantity:,}x*" for i in buys)
            or "_No resting buy orders._"
        )
        sell_lines = (
            "\n".join(f"🔴 🪙 **{i.price:,}** — *{i.total_quantity:,}x*" for i in sells)
            or "_No resting sell orders._"
        )
        container.add_item(TextDisplay("### Buy side\n" + buy_lines))
        container.add_item(TextDisplay("### Sell side\n" + sell_lines))

        container.add_item(Separator())
        ticker_back_btn = Button(style=ButtonStyle.gray, emoji="⬅️", custom_id=ticker)
        ticker_back_btn.callback = view_stock
        container.add_item(ActionRow(ticker_back_btn))
        view2.add_item(container)
        await interaction.edit_original_response(view=view2, attachments=[])

    async def go_back(interaction):
        await interaction.response.defer()
        await interaction.edit_original_response(view=await main_page(), attachments=[])

    await message.response.send_message(view=await main_page(), ephemeral=True)


@bot.tree.command(description="buy and sell cats with the cat mafia")
async def catstore(message: discord.Interaction):
    """Cat Store — the late-game coin sink. Two top-level browses from a
    landing page:
       - Cats (the original storefront — buy/sell discovered rarities)
       - Extras → Rain blocks (puncture the coins↔rain wall at scaling cost)
    Screen state lives in a `mode` dict on the closure, mirroring /jobs."""

    # ----- per-invocation state (closure-scoped) -----
    profile = await Profile.get_or_create(user_id=message.user.id, guild_id=message.guild.id)
    # screen ∈ {"landing", "cats", "cat_detail", "extras"}
    mode: dict = {"screen": "landing", "cat_type": None, "page": 0}
    last_toast: Optional[str] = None  # one-line banner, cleared on navigation
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

    # ----- help: paginated, context-aware via start_page -----
    # Page indices are used by _help_start_for_screen() below — keep in sync:
    #   0=Overview, 1=Cats, 2=Extras Overview, 3=Rain, 4=Packs
    HELP_PAGES = [
        {
            "title": "Cat Store Overview",
            "body": (
                "**The store has two browses.**\n"
                "- 🐈 **Cats** — buy & sell from rarities you've discovered in this server.\n"
                "- ✨ **Extras** — pricier coin sinks: **rain blocks** and **higher-tier packs**.\n\n"
                "Everything shares the same **coins** wallet and respects your **Cat Mafia** rank discount/tax. "
                "Cats are the cheap, repeatable use of the store; Extras is where the coin-rich go to burn surplus."
            ),
        },
        {
            "title": "Cats",
            "body": (
                "- Cats cost coins. Their value comes from how rare they are.\n"
                "- Your Cat Mafia level changes the price. Levels 5–10 give you a discount, levels 0–3 charge a tax, level 4 is even.\n"
                "- You can only buy or sell cats you've personally discovered in this server. Catch one to discover it.\n"
                "- Sell prices also scale with Cat Mafia level — Newbies sell at 50% of face value, mid-ranks peak around 80%, "
                "and the rate stays below the buy price at every level so round-trips always lose money. You can't farm the store."
            ),
        },
        {
            "title": "Extras Overview",
            "body": (
                "**Extras has two sub-shops, both more expensive than Cats:**\n"
                "- ☔ **Rain** — buy a 15-second cat rain in the current channel. Price scales per-block per UTC day.\n"
                "- 📦 **Packs** — buy Stone-and-up packs at face value (with mafia discount). Wooden lives in `/stocks`.\n\n"
                "Cat Mafia discount/tax applies to both. Open Help from each sub-page for the full math."
            ),
        },
        {
            "title": "Rain in the Store",
            "body": (
                "**Each purchase adds 1 minute to *this server's* rain inventory.** It does NOT fire immediately — you start it later with `/rain` in this server.\n\n"
                "**The price scales per minute bought per UTC day:**\n"
                f"- Base price: 🪙 **{RAIN_BASE_PRICE:,}** for the first minute today.\n"
                f"- Every minute bought today multiplies the next price by **×{RAIN_SCALE:g}** (so #2 is "
                f"🪙 {int(RAIN_BASE_PRICE * RAIN_SCALE):,}, #3 is 🪙 {int(RAIN_BASE_PRICE * RAIN_SCALE ** 2):,}, …).\n"
                "- Your Cat Mafia discount/tax applies after the scaling — same `store_discount` as Cats.\n"
                "- Counter resets at UTC midnight.\n\n"
                "**Server-isolated.** Catstore-bought rain stays on the server you bought it on — it's stored in `profile.rain_minutes` (per-server) rather than `user.rain_minutes` (cross-server). Battlepass and supporter rain are still cross-server. Catches during your bought rain count for battlepass quests, streaks, and XP same as battlepass-earned rain.\n\n"
                "**Design intent:** the coins↔rain wall is preserved by *pricing*, not prohibition. Per-server isolation keeps each server's economy independent."
            ),
        },
        {
            "title": "Packs in the Store",
            "body": (
                "**Stone through Celestial** are sold here at their `store_price` (with your Cat Mafia discount/tax applied).\n\n"
                "**Wooden is excluded** — `/stocks` already provides a coins↔Wooden exchange at 100 coins per pack via the deposit/withdraw flow. "
                "Selling Wooden here would duplicate that path with no benefit. Use `/stocks` for Wooden.\n\n"
                "**Pack contents are random when opened.** A pack you bought here behaves identically to a pack from the battlepass — same odds, same achievements, same quest progress. Buy then open with `/packs`.\n\n"
                "**Round-trip economics:** Stone/Bronze are net-zero versus a /stocks deposit (`store_price` == `totalvalue`). Silver and up are net-NEGATIVE — store_price is a multiple of the deposit value, so buying then depositing is the worst possible play. "
                "Buying then **opening** is gacha-negative on expectation, because expected pack contents (`value`) are less than the deposit value (`totalvalue`), and the store_price is higher still. Top-tier packs are meant to be opened, not flipped. "
                "Cat Mafia rank changes that math: at Lv10 the discount makes opening packs much more favorable, but Celestial is still meaningfully expensive."
            ),
        },
    ]

    async def show_help(interaction: discord.Interaction, start_page: int = 0):
        """Paginated help. start_page is chosen by the calling screen so the
        first page shown matches where the player was when they clicked 💡."""
        pages = HELP_PAGES
        page_idx = max(0, min(len(pages) - 1, int(start_page)))

        async def render(target_interaction: discord.Interaction, idx: int, is_initial: bool):
            page = pages[idx]
            items: list = [
                f"## 💡 Cat Store Help — {page['title']}",
                f"-# Page {idx + 1} / {len(pages)}",
                Separator(),
                page["body"],
            ]
            prev_btn = Button(label="← Prev", style=ButtonStyle.gray, custom_id="catstore_help_prev", disabled=idx == 0)
            next_btn = Button(label="Next →", style=ButtonStyle.gray, custom_id="catstore_help_next", disabled=idx >= len(pages) - 1)

            async def on_help_prev(intr: discord.Interaction):
                await render(intr, idx - 1, is_initial=False)

            async def on_help_next(intr: discord.Interaction):
                await render(intr, idx + 1, is_initial=False)

            prev_btn.callback = on_help_prev
            next_btn.callback = on_help_next
            items.append(ActionRow(prev_btn, next_btn))

            v = LayoutView(timeout=VIEW_TIMEOUT)
            container = Container(*items)
            try:
                container.accent_color = Colors.brown
            except Exception:
                pass
            v.add_item(container)

            if is_initial:
                await target_interaction.response.send_message(view=v, ephemeral=True)
            elif target_interaction.response.is_done():
                await target_interaction.edit_original_response(view=v)
            else:
                await target_interaction.response.edit_message(view=v)

        await render(interaction, page_idx, is_initial=True)

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
                # catstore_discount_stack (job perk): additive bonus on top of
                # the catnip-level discount. Looked up here so the modal sees
                # whatever the player has active at submit time.
                _buy_perk_bonus = _perks_catstore_buy_bonus(fresh)
                unit_price = store_buy_price(self.cat_type, fresh.catnip_level, _buy_perk_bonus)
                unit_value = catstore_face_value(self.cat_type)
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

            # Battlepass quest progress — buy + (conditional) spree.
            try:
                await progress(interaction, profile, "store_buy")
                if total_cost >= 2500:
                    await progress(interaction, profile, "store_spree")
            except Exception:
                logging.exception("catstore: BP progress (buy) failed")

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
                # catstore_sell_premium (job perk): additive sell-pp bonus,
                # still capped by buy_pct-5 inside store_sell_pct.
                _sell_perk_bonus = _perks_catstore_sell_bonus(fresh)
                unit_price = store_sell_price(self.cat_type, fresh.catnip_level, _sell_perk_bonus)
                total = unit_price * qty
                fresh[f"cat_{self.cat_type}"] -= qty
                fresh.coins += total
                _bump(fresh, "coins_earned", total)
                await fresh.save()
                nonlocal profile
                profile = fresh

            face_total = catstore_face_value(self.cat_type) * qty
            cut = face_total - total
            if cut > 0:
                last_toast = f"✅ Sold {qty}× {self.cat_type} for 🪙 {total:,} (mafia took 🪙 {cut:,})"
            else:
                last_toast = f"✅ Sold {qty}× {self.cat_type} for 🪙 {total:,} (full value)"
            if not profile.has_ach("catstore_first_sell"):
                await achemb(interaction, "catstore_first_sell", "followup")

            # Battlepass quest progress — sell.
            try:
                await progress(interaction, profile, "store_sell")
            except Exception:
                logging.exception("catstore: BP progress (sell) failed")

            await gen_detail(interaction, self.cat_type, use_followup=False)

    # ----- callbacks wired to buttons -----
    async def on_view(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        nonlocal last_toast
        last_toast = None
        mode["cat_type"] = interaction.data["custom_id"].removeprefix("view_")
        mode["screen"] = "cat_detail"
        await interaction.response.defer()
        await gen_detail(interaction, mode["cat_type"], use_followup=False)

    async def on_back(interaction: discord.Interaction):
        """Context-aware Back. The navigation tree is:
             landing → cats → cat_detail
             landing → extras → rain
             landing → extras → packs
        Each Back click pops exactly one level."""
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        nonlocal last_toast
        last_toast = None
        screen = mode.get("screen", "landing")
        await interaction.response.defer()
        if screen == "cat_detail":
            mode["screen"] = "cats"
            mode["cat_type"] = None
            await gen_cats_list(interaction, use_followup=False)
        elif screen in ("rain", "packs", "jobs_reroll"):
            mode["screen"] = "extras"
            await gen_extras(interaction, use_followup=False)
        else:
            # cats or extras → landing
            mode["screen"] = "landing"
            mode["cat_type"] = None
            await gen_landing(interaction, use_followup=False)

    async def on_browse_cats(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        nonlocal last_toast
        last_toast = None
        mode["screen"] = "cats"
        mode["page"] = 0
        await interaction.response.defer()
        await gen_cats_list(interaction, use_followup=False)

    async def on_browse_extras(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        nonlocal last_toast
        last_toast = None
        mode["screen"] = "extras"
        await interaction.response.defer()
        await gen_extras(interaction, use_followup=False)

    async def on_browse_rain(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        nonlocal last_toast
        last_toast = None
        mode["screen"] = "rain"
        await interaction.response.defer()
        await gen_rain(interaction, use_followup=False)

    async def on_browse_packs(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        nonlocal last_toast
        last_toast = None
        mode["screen"] = "packs"
        await interaction.response.defer()
        await gen_packs(interaction, use_followup=False)

    async def on_prev(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        mode["page"] = max(0, int(mode.get("page", 0)) - 1)
        await interaction.response.defer()
        await gen_cats_list(interaction, use_followup=False)

    async def on_next(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        mode["page"] = int(mode.get("page", 0)) + 1
        await interaction.response.defer()
        await gen_cats_list(interaction, use_followup=False)

    def _help_start_for_screen() -> int:
        # Keep in sync with HELP_PAGES index order:
        # 0=Overview, 1=Cats, 2=Extras Overview, 3=Rain, 4=Packs
        s = mode.get("screen", "landing")
        if s == "packs":
            return 4
        if s == "rain":
            return 3
        if s == "extras":
            return 2
        if s in ("cats", "cat_detail"):
            return 1
        return 0

    async def on_help(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        await show_help(interaction, start_page=_help_start_for_screen())

    async def on_buy(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        cat_type = mode.get("cat_type")
        if not cat_type:
            return
        await profile.refresh_from_db()
        unit_price = store_buy_price(cat_type, profile.catnip_level, _perks_catstore_buy_bonus(profile))
        max_affordable = profile.coins // unit_price if unit_price > 0 else 0
        if max_affordable < 1:
            await interaction.response.send_message(
                f"you need 🪙 {unit_price - profile.coins:,} more coins to buy one {cat_type}",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(BuyModal(cat_type, max_affordable))

    async def on_sell(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        cat_type = mode.get("cat_type")
        if not cat_type:
            return
        await profile.refresh_from_db()
        owned = profile[f"cat_{cat_type}"]
        if owned < 1:
            await interaction.response.send_message(
                f"you don't have any {cat_type} cats to sell", ephemeral=True
            )
            return
        await interaction.response.send_modal(SellModal(cat_type, owned))

    async def on_buy_rain(interaction: discord.Interaction):
        """Buy one minute of rain — added to the buyer's user.rain_minutes
        inventory, NOT fired in the current channel. The buyer triggers it
        later with /rain. Price scales with `rain_blocks_bought_today`
        (lazy UTC reset)."""
        nonlocal last_toast, profile

        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return

        await profile.refresh_from_db()

        blocks_today = _rain_blocks_today(profile)
        discount = store_discount_pct(profile.catnip_level)
        price = rain_block_price(blocks_today, discount)
        if profile.coins < price:
            await interaction.response.send_message(
                f"not enough coins — need 🪙 {price:,}, have 🪙 {profile.coins:,}.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        # ---- debit coins, credit user.rain_minutes, persist counter ----
        async with transaction() as conn:
            fresh = await Profile.get_or_create(conn, user_id=message.user.id, guild_id=message.guild.id)
            fresh_blocks_today = _rain_blocks_today(fresh)
            fresh_price = rain_block_price(fresh_blocks_today, store_discount_pct(fresh.catnip_level))
            if fresh.coins < fresh_price:
                await interaction.followup.send(
                    f"price moved while you clicked — now 🪙 {fresh_price:,}, you have 🪙 {fresh.coins:,}.",
                    ephemeral=True,
                )
                return
            fresh.coins -= fresh_price
            fresh.rain_blocks_bought_today = fresh_blocks_today + 1
            fresh.rain_blocks_last_date = time.strftime("%Y-%m-%d", time.gmtime())
            # Server-isolated rain inventory: credit profile.rain_minutes
            # (the per-server "bonus minutes" column /rain consumes first),
            # NOT user.rain_minutes (which is cross-server). This keeps each
            # server's economy independent.
            fresh.rain_minutes = (fresh.rain_minutes or 0) + RAIN_BLOCK_MINUTES
            await fresh.save()
            profile = fresh
            price = fresh_price

            # user.rain_minutes_bought is the lifetime cumulative tracker
            # that drives the blessings system. It's correctly cross-server
            # — it represents "how much rain has this person ever bought"
            # for blessings-rewards purposes, not consumable inventory.
            user_row = await User.get_or_create(conn, user_id=message.user.id)
            user_row.rain_minutes_bought = (user_row.rain_minutes_bought or 0) + RAIN_BLOCK_MINUTES
            await user_row.save()

        # ---- toast + achievements ----
        last_toast = (
            f"☔ Bought {RAIN_BLOCK_MINUTES} rain minute for 🪙 {price:,}. "
            f"Use `/rain` here to start it. "
            f"(This server's inventory: {profile.rain_minutes} min)"
        )
        try:
            await achemb(interaction, "catstore_rainmaker", "followup")
            if profile.rain_blocks_bought_today >= 5:
                await achemb(interaction, "catstore_monsoon", "followup")
            if price >= 10000 and not profile.has_ach("catstore_whale"):
                await achemb(interaction, "catstore_whale", "followup")
            if store_discount_pct(profile.catnip_level) >= 30 and not profile.has_ach("mafia_discount_max"):
                await achemb(interaction, "mafia_discount_max", "followup")
            if profile.catnip_level == 0 and not profile.has_ach("mafia_tax_payer"):
                await achemb(interaction, "mafia_tax_payer", "followup")
        except Exception:
            logging.exception("catstore: rain ach wiring failed")

        # Battlepass quest progress — buy + (conditional) spree. Mirrors the
        # cat- and pack-buy paths so a rain purchase counts the same toward
        # `store_buy` and the 2500+ `store_spree` quest.
        try:
            await progress(interaction, profile, "store_buy")
            if price >= 2500:
                await progress(interaction, profile, "store_spree")
        except Exception:
            logging.exception("catstore: BP progress (rain buy) failed")

        # Re-render so the new (higher) next-block price shows up.
        await gen_rain(interaction, use_followup=False)

    # ----- pack purchase modal + handler -----
    class PackBuyModal(Modal):
        def __init__(self, pack_name: str, max_affordable: int):
            super().__init__(title=f"Buy {pack_name} packs", timeout=VIEW_TIMEOUT)
            self.pack_name = pack_name
            # max_length=2 caps single submissions at 99, matching the spec.
            self.input = TextInput(
                min_length=1,
                max_length=2,
                label=f"How many {pack_name} packs? (max {min(max_affordable, 99)})",
                style=discord.TextStyle.short,
                required=True,
                placeholder="1",
            )
            self.add_item(self.input)

        async def on_submit(self, interaction: discord.Interaction):
            nonlocal last_toast, profile
            try:
                qty = int(self.input.value)
                if qty <= 0:
                    raise ValueError
            except Exception:
                await interaction.response.send_message("invalid quantity", ephemeral=True)
                return
            if qty > 99:
                await interaction.response.send_message(
                    "max 99 per purchase. transact twice if you want more.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer()
            async with transaction() as conn:
                fresh = await Profile.get_or_create(conn, user_id=message.user.id, guild_id=message.guild.id)
                try:
                    unit_price = pack_buy_price(self.pack_name, store_discount_pct(fresh.catnip_level))
                except ValueError as e:
                    await interaction.followup.send(str(e), ephemeral=True)
                    return
                total_cost = unit_price * qty
                if fresh.coins < total_cost:
                    await interaction.followup.send(
                        f"not enough coins — need 🪙 {total_cost:,}, have 🪙 {fresh.coins:,}",
                        ephemeral=True,
                    )
                    return
                fresh.coins -= total_cost
                col_name = f"pack_{self.pack_name.lower()}"
                fresh[col_name] = int(fresh[col_name] or 0) + qty
                # Mark this tier as ever-purchased (idempotent).
                tiers = _coerce_array(fresh.store_purchased_pack_tiers)
                if self.pack_name not in tiers:
                    fresh.store_purchased_pack_tiers = tiers + [self.pack_name]
                await fresh.save()
                profile = fresh

            last_toast = (
                f"✅ Bought {qty}× {self.pack_name} pack{'s' if qty != 1 else ''} for 🪙 {total_cost:,}."
                f" Open with /packs."
            )

            # Achievements — fire after the transaction commits.
            try:
                if not profile.has_ach("catstore_first_buy"):
                    await achemb(interaction, "catstore_first_buy", "followup")
                if not profile.has_ach("catstore_pack_buyer"):
                    await achemb(interaction, "catstore_pack_buyer", "followup")
                if total_cost >= 10000 and not profile.has_ach("catstore_whale"):
                    await achemb(interaction, "catstore_whale", "followup")
                if store_discount_pct(profile.catnip_level) >= 30 and not profile.has_ach("mafia_discount_max"):
                    await achemb(interaction, "mafia_discount_max", "followup")
                if profile.catnip_level == 0 and not profile.has_ach("mafia_tax_payer"):
                    await achemb(interaction, "mafia_tax_payer", "followup")
                # catstore_pack_collector — one of every Stone-through-Celestial.
                tiers_set = set(_coerce_array(profile.store_purchased_pack_tiers))
                if (
                    all(t in tiers_set for t in CATSTORE_PACK_TIERS)
                    and not profile.has_ach("catstore_pack_collector")
                ):
                    await achemb(interaction, "catstore_pack_collector", "followup")
            except Exception:
                logging.exception("catstore: pack ach wiring failed")

            # Battlepass quest progress — buy + (conditional) spree. Mirrors
            # the cat-buy path so a pack purchase counts the same as a cat
            # purchase toward `store_buy` and the 2500+ `store_spree` quest.
            try:
                await progress(interaction, profile, "store_buy")
                if total_cost >= 2500:
                    await progress(interaction, profile, "store_spree")
            except Exception:
                logging.exception("catstore: BP progress (pack buy) failed")

            await gen_packs(interaction, use_followup=False)

    async def on_buy_pack(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        # custom_id is "catstore_buy_pack_{name}".
        pack_name = interaction.data["custom_id"].removeprefix("catstore_buy_pack_")
        if pack_name not in CATSTORE_PACK_TIERS:
            return
        await profile.refresh_from_db()
        try:
            unit_price = pack_buy_price(pack_name, store_discount_pct(profile.catnip_level))
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        max_affordable = profile.coins // unit_price if unit_price > 0 else 0
        if max_affordable < 1:
            await interaction.response.send_message(
                f"you need 🪙 {unit_price - profile.coins:,} more coins to buy one {pack_name} pack",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(PackBuyModal(pack_name, max_affordable))

    # ----- renderers -----
    async def gen_landing(interaction: discord.Interaction, use_followup: bool):
        await profile.refresh_from_db()
        _buy_bonus = _perks_catstore_buy_bonus(profile)
        discount = store_discount_pct(profile.catnip_level, _buy_bonus)
        rank = _rank_name(profile.catnip_level)

        view = LayoutView(timeout=VIEW_TIMEOUT)
        items: list = [
            "## 🛒 Cat Store",
            "What are you here for?",
        ]
        if last_toast:
            items.append(last_toast)

        cats_btn = Button(label="Browse →", style=ButtonStyle.blurple, custom_id="catstore_browse_cats")
        cats_btn.callback = on_browse_cats
        items.append(
            Section(
                "### 🐈 Cats",
                "Buy and sell from your discovered rarities.",
                cats_btn,
            )
        )

        extras_btn = Button(label="Browse →", style=ButtonStyle.blurple, custom_id="catstore_browse_extras")
        extras_btn.callback = on_browse_extras
        items.append(
            Section(
                "### ✨ Extras",
                "Rain blocks and higher-tier packs.",
                extras_btn,
            )
        )

        perk_note = f"  ·  🎁 +{_buy_bonus}pp perk" if _buy_bonus else ""
        items.append(
            f"-# 🪙 Your balance: {profile.coins:,}  ·  Cat Mafia: Lv{profile.catnip_level} ({rank}) "
            f"{_signed_pct(discount)}{perk_note}"
        )

        help_btn = Button(label="💡 Help", style=ButtonStyle.gray, custom_id="catstore_help_landing")
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

    async def gen_cats_list(interaction: discord.Interaction, use_followup: bool):
        await profile.refresh_from_db()
        # Show the combined catnip-level + job-perk discount in the header.
        _buy_bonus = _perks_catstore_buy_bonus(profile)
        discount = store_discount_pct(profile.catnip_level, _buy_bonus)
        rank = _rank_name(profile.catnip_level)
        discovered = _discovered_list(profile)

        view = LayoutView(timeout=VIEW_TIMEOUT)
        items: list = [
            "## 🛒 Cat Store — Cats",
            f"🪙 {profile.coins:,} · Mafia Lv {profile.catnip_level} ({rank}) · {_signed_pct(discount)}"
            + (f"  ·  🎁 +{_buy_bonus}pp perk" if _buy_bonus else ""),
        ]
        if last_toast:
            items.append(last_toast)

        if not discovered:
            items.append(
                "You haven't discovered any cats here yet! Catch one in this server first, then come back."
            )
            back_btn = Button(label="← Back", style=ButtonStyle.gray, custom_id="catstore_back_landing")
            back_btn.callback = on_back
            help_btn = Button(label="💡 Help", style=ButtonStyle.gray, custom_id="catstore_help")
            help_btn.callback = on_help
            items.append(ActionRow(back_btn, help_btn))
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
        if int(mode.get("page", 0)) >= total_pages:
            mode["page"] = total_pages - 1
        if int(mode.get("page", 0)) < 0:
            mode["page"] = 0
        cur_page = int(mode["page"])
        start = cur_page * PAGE_SIZE
        page_cats = discovered[start : start + PAGE_SIZE]

        for cat_type in page_cats:
            owned = profile[f"cat_{cat_type}"]
            price = store_buy_price(cat_type, profile.catnip_level, _buy_bonus)
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

        # Footer: pagination only when multi-page, plus Back + Help always.
        action_buttons: list = []
        if total_pages > 1:
            prev_btn = Button(label="← Prev", style=ButtonStyle.gray, custom_id="catstore_prev")
            prev_btn.callback = on_prev
            prev_btn.disabled = cur_page == 0
            next_btn = Button(label="Next →", style=ButtonStyle.gray, custom_id="catstore_next")
            next_btn.callback = on_next
            next_btn.disabled = cur_page >= total_pages - 1
            action_buttons.append(prev_btn)
            action_buttons.append(next_btn)
            items.append(f"-# Page {cur_page + 1}/{total_pages}")
        back_btn = Button(label="← Back", style=ButtonStyle.gray, custom_id="catstore_back_landing")
        back_btn.callback = on_back
        action_buttons.append(back_btn)
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

    async def gen_extras(interaction: discord.Interaction, use_followup: bool):
        """Extras sub-landing — a tiny menu pointing at Rain and Packs."""
        await profile.refresh_from_db()
        _buy_bonus = _perks_catstore_buy_bonus(profile)
        discount = store_discount_pct(profile.catnip_level, _buy_bonus)
        rank = _rank_name(profile.catnip_level)

        view = LayoutView(timeout=VIEW_TIMEOUT)
        items: list = [
            "## ✨ Cat Store — Extras",
            "What can I get you?",
        ]
        if last_toast:
            items.append(last_toast)

        rain_btn = Button(label="Browse →", style=ButtonStyle.blurple, custom_id="catstore_browse_rain")
        rain_btn.callback = on_browse_rain
        items.append(
            Section(
                "### ☔ Rain",
                "Add rain to this channel. Pricey and scales daily.",
                rain_btn,
            )
        )

        packs_btn = Button(label="Browse →", style=ButtonStyle.blurple, custom_id="catstore_browse_packs")
        packs_btn.callback = on_browse_packs
        items.append(
            Section(
                "### 📦 Packs",
                "Buy higher-tier packs with coins (Wooden lives in /stocks).",
                packs_btn,
            )
        )

        jobs_btn = Button(label="Browse →", style=ButtonStyle.blurple, custom_id="catstore_browse_jobs")
        jobs_btn.callback = on_browse_jobs_reroll
        items.append(
            Section(
                "### 🔄 Job Board Reroll",
                "Pay to roll a fresh set of `/jobs` offers. Price scales with your mafia rank.",
                jobs_btn,
            )
        )

        items.append(
            f"-# 🪙 Your balance: {profile.coins:,}  ·  Cat Mafia: Lv{profile.catnip_level} ({rank}) "
            f"{_signed_pct(discount)}"
        )

        back_btn = Button(label="← Back", style=ButtonStyle.gray, custom_id="catstore_back_landing")
        back_btn.callback = on_back
        help_btn = Button(label="💡 Help", style=ButtonStyle.gray, custom_id="catstore_help_extras")
        help_btn.callback = on_help
        items.append(ActionRow(back_btn, help_btn))

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

    async def gen_rain(interaction: discord.Interaction, use_followup: bool):
        await profile.refresh_from_db()
        _buy_bonus = _perks_catstore_buy_bonus(profile)
        discount = store_discount_pct(profile.catnip_level, _buy_bonus)
        # Rain uses the catnip-only discount (perks don't apply to rain — they're
        # buy-side perks scoped to cats). Keep it explicit so the displayed price
        # matches the actually-charged price.
        rain_discount = store_discount_pct(profile.catnip_level)
        blocks_today = _rain_blocks_today(profile)
        next_price = rain_block_price(blocks_today, rain_discount)
        next_next_price = rain_block_price(blocks_today + 1, rain_discount)
        # Per-server inventory: profile.rain_minutes, NOT user.rain_minutes.
        # Catstore-bought rain is server-isolated; what you buy here stays
        # here. /rain consumes profile.rain_minutes before user.rain_minutes.
        current_inventory = int(profile.rain_minutes or 0)

        # UTC midnight epoch for the "resets <t:...:R>" hint.
        tomorrow = (datetime.datetime.utcnow() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        midnight_struct = time.strptime(tomorrow, "%Y-%m-%d")
        try:
            import calendar
            reset_epoch = int(calendar.timegm(midnight_struct))
        except Exception:
            reset_epoch = int(time.time()) + 86400

        view = LayoutView(timeout=VIEW_TIMEOUT)
        items: list = [
            "## ☔ Cat Store — Rain",
            "Adds a minute to **this server's** rain inventory. **Start it later with `/rain` here**.",
            "-# Catstore-bought rain is server-isolated. Battlepass rain stays cross-server.",
            Separator(),
            f"### Next minute: 1 rain minute",
            (f"🪙 **{next_price:,}**" + (f"  (catnip-rank {_signed_pct(rain_discount)})" if rain_discount else "")),
            f"This server's inventory: **{current_inventory:,}** rain minute{'s' if current_inventory != 1 else ''}",
            f"Bought today: **{blocks_today}** minute{'s' if blocks_today != 1 else ''} "
            f"(price scales each buy)",
            f"After this: next minute costs 🪙 **{next_next_price:,}**",
        ]
        if last_toast:
            items.append(last_toast)

        # Buy button — disabled with a helpful label when the player can't
        # afford the next block.
        buy_btn = Button(
            label=f"Buy ☔ 1 minute — 🪙 {next_price:,}",
            style=ButtonStyle.green,
            custom_id="catstore_buy_rain",
        )
        if profile.coins < next_price:
            buy_btn.disabled = True
            buy_btn.label = f"Need 🪙 {next_price - profile.coins:,} more"
        buy_btn.callback = on_buy_rain

        back_btn = Button(label="← Back", style=ButtonStyle.gray, custom_id="catstore_back_extras")
        back_btn.callback = on_back

        help_btn = Button(label="💡 Help", style=ButtonStyle.gray, custom_id="catstore_help_rain")
        help_btn.callback = on_help

        items.append(ActionRow(buy_btn, back_btn, help_btn))
        items.append(f"-# Daily counter resets <t:{reset_epoch}:R>")

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

    async def on_browse_jobs_reroll(interaction: discord.Interaction):
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        nonlocal last_toast
        last_toast = None
        mode["screen"] = "jobs_reroll"
        await interaction.response.defer()
        await gen_jobs_reroll(interaction, use_followup=False)

    async def gen_jobs_reroll(interaction: discord.Interaction, use_followup: bool):
        """Catstore screen for the paid /jobs board reroll. Mirrors the jobs-board
        reroll (same _jobs_do_reroll + level-scaled escalating price); here it just
        rerolls the player's board in the DB and points them at /jobs."""
        await profile.refresh_from_db()
        _buy_bonus = _perks_catstore_buy_bonus(profile)
        discount = store_discount_pct(profile.catnip_level, _buy_bonus)
        level = int(profile.catnip_level or 0)
        now = int(time.time())

        view = LayoutView(timeout=VIEW_TIMEOUT)
        items: list = [
            "## 🔄 Cat Store — Job Board Reroll",
            "Replace **all** of your current `/jobs` offers with a fresh set.",
            "-# Doesn't change your 3-jobs-per-window commit cap — it just reshuffles what's on offer.",
        ]

        if level < 2:
            items.append(Separator())
            items.append("🔒 You're not in the family yet — reach **Mafia Lv2** (catch more cats) to run jobs.")
            if last_toast:
                items.append(last_toast)
            back_btn = Button(label="← Back", style=ButtonStyle.gray, custom_id="catstore_back_extras")
            back_btn.callback = on_back
            help_btn = Button(label="💡 Help", style=ButtonStyle.gray, custom_id="catstore_help_jobs")
            help_btn.callback = on_help
            items.append(ActionRow(back_btn, help_btn))
        else:
            widx = _jobs_window_index(now)
            win_start, win_end = _jobs_window_bounds(widx)
            offer_count = await JobInstance.count(
                "user_id = $1 AND guild_id = $2 AND state = 'offered' AND offered_at >= $3 AND offered_at < $4",
                int(profile.user_id), int(profile.guild_id), win_start, win_end,
            )
            rerolls_done = _jobs_reroll_count(profile, now)
            price = _jobs_reroll_price(profile, now)
            items.extend([
                Separator(),
                f"Current offers on your board: **{offer_count}**",
                f"Rerolls this window: **{rerolls_done}**  ·  next refresh <t:{win_end}:R>",
                f"### Next reroll: 🪙 **{price:,}**",
                "-# Price = mafia rank × 500 (min 1,000), and rises with each reroll this window.",
            ])
            if last_toast:
                items.append(last_toast)

            coins = int(profile.coins or 0)
            buy_btn = Button(
                label=f"🔄 Reroll — 🪙 {price:,}",
                style=ButtonStyle.green,
                custom_id="catstore_buy_jobs_reroll",
            )
            if coins < price:
                buy_btn.disabled = True
                buy_btn.label = f"Need 🪙 {price - coins:,} more"
            buy_btn.callback = on_buy_jobs_reroll

            back_btn = Button(label="← Back", style=ButtonStyle.gray, custom_id="catstore_back_extras")
            back_btn.callback = on_back
            help_btn = Button(label="💡 Help", style=ButtonStyle.gray, custom_id="catstore_help_jobs")
            help_btn.callback = on_help
            items.append(ActionRow(buy_btn, back_btn, help_btn))

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

    async def on_buy_jobs_reroll(interaction: discord.Interaction):
        """Pay coins to reroll the player's /jobs board. Same engine as the
        board button; charges the level-scaled, per-window-escalating price."""
        nonlocal last_toast

        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return

        await profile.refresh_from_db()
        if int(profile.catnip_level or 0) < 2:
            await interaction.response.send_message("you need to reach Mafia Lv2 to run jobs.", ephemeral=True)
            return

        now = int(time.time())
        price = _jobs_reroll_price(profile, now)
        if int(profile.coins or 0) < price:
            await interaction.response.send_message(
                f"not enough coins — need 🪙 {price:,}, have 🪙 {int(profile.coins or 0):,}.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        # Re-check after defer (price can move if a window rolled or a prior reroll landed).
        await profile.refresh_from_db()
        now = int(time.time())
        price = _jobs_reroll_price(profile, now)
        if int(profile.coins or 0) < price:
            await interaction.followup.send(
                f"price moved while you clicked — now 🪙 {price:,}, you have 🪙 {int(profile.coins or 0):,}.",
                ephemeral=True,
            )
            return

        if await _jobs_do_reroll(profile, now):
            _jobs_reroll_charge(profile, now, price)
            await profile.save()
            last_toast = f"🔄 Job board rerolled for 🪙 {price:,} — head to `/jobs` to see the new offers."
        else:
            last_toast = "⚠️ Reroll failed — try again. (No coins charged.)"
        await gen_jobs_reroll(interaction, use_followup=False)

    async def gen_packs(interaction: discord.Interaction, use_followup: bool):
        """Pack catalog — Stone through Celestial. Wooden is intentionally
        excluded; the help page points players to /stocks for that tier."""
        await profile.refresh_from_db()
        _buy_bonus = _perks_catstore_buy_bonus(profile)
        # Packs use the catnip-only discount (the catstore_discount_stack
        # perk is scoped to cats, not packs). Keep it explicit so the
        # displayed price matches the charged price.
        discount = store_discount_pct(profile.catnip_level, _buy_bonus)
        pack_discount = store_discount_pct(profile.catnip_level)
        rank = _rank_name(profile.catnip_level)

        view = LayoutView(timeout=VIEW_TIMEOUT)
        items: list = [
            "## 📦 Cat Store — Packs",
            f"🪙 {profile.coins:,} · Mafia Lv {profile.catnip_level} ({rank}) · {_signed_pct(pack_discount)}",
            "Buy packs to open later. Higher tiers = better cats inside.",
            "-# Wooden packs are sold via `/stocks` (deposit/withdraw flow).",
        ]
        if last_toast:
            items.append(last_toast)

        for pack_name in CATSTORE_PACK_TIERS:
            try:
                price = pack_buy_price(pack_name, pack_discount)
            except ValueError:
                continue
            owned = int(profile[f"pack_{pack_name.lower()}"] or 0)
            body = f"Owned: {owned:,}  ·  🪙 {price:,}"
            btn = Button(
                label="Buy",
                style=ButtonStyle.green,
                custom_id=f"catstore_buy_pack_{pack_name}",
            )
            if profile.coins < price:
                btn.disabled = True
                btn.label = f"Need 🪙 {price - profile.coins:,} more"
            btn.callback = on_buy_pack
            items.append(
                Section(
                    f"### {get_emoji(pack_name.lower() + 'pack')} {pack_name}",
                    body,
                    btn,
                )
            )

        back_btn = Button(label="← Back", style=ButtonStyle.gray, custom_id="catstore_back_extras")
        back_btn.callback = on_back
        help_btn = Button(label="💡 Help", style=ButtonStyle.gray, custom_id="catstore_help_packs")
        help_btn.callback = on_help
        items.append(ActionRow(back_btn, help_btn))

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
        _buy_bonus = _perks_catstore_buy_bonus(profile)
        _sell_bonus = _perks_catstore_sell_bonus(profile)
        discount = store_discount_pct(profile.catnip_level, _buy_bonus)
        sell_pct = store_sell_pct(profile.catnip_level, _sell_bonus)
        unit_value = catstore_face_value(cat_type)
        unit_buy = store_buy_price(cat_type, profile.catnip_level, _buy_bonus)
        unit_sell = store_sell_price(cat_type, profile.catnip_level, _sell_bonus)
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
            f"https://wsrv.nl/?url=raw.githubusercontent.com/sneezeparty/catbot7/"
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
    # Trigger pending season rollover + ephemeral reset notice before the
    # landing renders. `profile` is the invoker's per-server profile
    # (fetched at the top of /catstore).
    await refresh_quests(profile)
    await _maybe_show_season_reset_notice(message, profile)
    await gen_landing(message, use_followup=True)


@bot.tree.command(description="take contracts from the cat mafia")
async def jobs(message: discord.Interaction):
    """Cat Mafia jobs board. Multi-screen flow:
       board → send screen → result (with 30s cancel grace).
    Acceptance criterion of Phase 1 (deterministic window seeding) is preserved;
    Phase 2 adds the commit path with atomic escrow + roll + reward grant."""
    profile = await Profile.get_or_create(user_id=message.user.id, guild_id=message.guild.id)

    # ----- closure state -----
    # board_page is the current page index in show_board (0-indexed). Clamped
    # to a valid range each render so deleting offers can't strand the user
    # on a now-empty page.
    mode: dict = {"screen": "board", "job_id": None, "board_page": 0}
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
        # Lazy respect decay too. If a level was lost since the last open, save
        # so the persisted state matches what we're about to render, and queue
        # a toast so the next render surfaces the loss.
        _respect_levels_lost_on_open = _respect_settle(profile, now)
        if _respect_levels_lost_on_open > 0:
            last_toast.append(
                f"-# 💀 You lost {_respect_levels_lost_on_open} catnip level"
                f"{'s' if _respect_levels_lost_on_open > 1 else ''} from neglect — "
                "the family doesn't reward absentees."
            )
        level = int(profile.catnip_level or 0)

        async def _say(text: str):
            """Render a plain text "board empty / locked" message. The board is
            a components-v2 LayoutView — `content` is rejected by Discord on
            edit_message / edit_original_response for those messages ("'content'
            field cannot be used when using MessageFlags.IS_COMPONENTS_V2"). So
            wrap the text in a tiny LayoutView Container instead; the absence
            of buttons does the same job that `view=None` used to."""
            say_view = LayoutView(timeout=VIEW_TIMEOUT)
            say_view.add_item(Container(text))
            if use_followup:
                await interaction.followup.send(view=say_view, ephemeral=True)
            elif interaction.response.is_done():
                await interaction.edit_original_response(view=say_view)
            else:
                await interaction.response.edit_message(view=say_view)

        if level < 2:
            await _say("You're not in the family yet. Catch more cats and climb the catnip ranks — "
                       "you can start running errands at Lv2.")
            return

        offers = await _jobs_refresh_offers_if_needed(profile, now)
        if not offers:
            _, win_end = _jobs_window_bounds(_jobs_window_index(now))
            if _jobs_eligible_npcs(level, _jobs_faction_rep(profile)):
                # Board's empty because the player has worked through (accepted
                # or declined) everything this window generated — NOT a rep
                # problem. Offers only refill at the next window boundary.
                await _say(
                    "the family's got nothing left for you this shift — you've cleared "
                    f"every job on the board. a fresh batch comes in <t:{win_end}:R>."
                )
            else:
                # No NPC will hire: faction reputation sits below the refuse
                # threshold with everyone. This one really is a rep problem,
                # and rep doesn't recover on its own.
                await _say(
                    "word's gotten around — your standing with the family is in the gutter "
                    "and nobody'll deal with you right now. you'll have to win back some "
                    "reputation before the work starts coming again."
                )
            return

        rank_name = catnip_list["levels"][level]["name"] if level < len(catnip_list["levels"]) else "?"
        window_idx = _jobs_window_index(now)
        _, win_end = _jobs_window_bounds(window_idx)
        heat = int(getattr(profile, "heat", 0) or 0)
        heat_band = "🟢" if heat <= JOBS_HEAT_WATCHING_FLOOR else ("🟡" if heat <= JOBS_HEAT_SCRUTINY_FLOOR else "🔴")
        # Respect column may not exist yet if migration 018 hasn't been run.
        has_respect = _profile_has_respect_columns(profile)
        respect = int(profile.respect or 0) if has_respect else None
        respect_max = max(1, int(_respect_cfg().get("max", 100)))
        respect_band = None
        if has_respect:
            respect_band = "🟢" if respect >= 67 else ("🟡" if respect >= 26 else "🔴")
        suspended_until = int(getattr(profile, "perks_suspended_until", 0) or 0)
        pinch_active = suspended_until > now
        window_count = await _jobs_commits_this_window(int(profile.user_id), int(profile.guild_id), now)

        # daily_cap_extension: peek-only; charge consumed at commit time.
        _eff_cap_board = _perks_effective_daily_cap(profile, JOBS_COMMITS_PER_WINDOW)
        view = LayoutView(timeout=VIEW_TIMEOUT)
        status_parts = [f"Mafia Lv {level} ({rank_name})", f"{heat_band} Heat: {heat}/{JOBS_PINCH_THRESHOLD}"]
        if has_respect:
            status_parts.append(f"{respect_band} Respect: {respect}/{respect_max}")
        status_parts.extend([
            f"Jobs this window: {window_count}/{_eff_cap_board}",
            f"Refreshes <t:{win_end}:R>",
        ])
        items: list = [
            "## 📋 Jobs Board",
            "  ·  ".join(status_parts),
        ]
        if window_count >= _eff_cap_board:
            items.append(f"-# 🛑 **Window limit hit.** Comes back at the refresh above.")
        if pinch_active:
            items.append(f"-# 🚓 **Pinched.** Catnip perks come back <t:{suspended_until}:R>.")
        if has_respect and respect == 0:
            floor_v = int(_respect_cfg().get("level_loss_floor", 4))
            if level > floor_v:
                items.append(
                    f"-# ⚠️ **Zero respect.** Catnip level will drop (floor Lv{floor_v}) "
                    "if you don't commit jobs."
                )
        if level < 4:
            items.append("-# *Tutorial errand only. Reach Capo (Lv4) for the full board.*")
        for line in last_toast:
            items.append(line)
        last_toast.clear()

        # Pagination — JOBS_BOARD_PAGE_SIZE offers per page. board_page lives
        # on `mode` so Prev/Next callbacks can advance it. Clamp to the valid
        # range each render (handles offers shrinking on reroll/expire).
        total_pages = max(1, (len(offers) + JOBS_BOARD_PAGE_SIZE - 1) // JOBS_BOARD_PAGE_SIZE)
        cur_page = max(0, min(int(mode.get("board_page", 0) or 0), total_pages - 1))
        mode["board_page"] = cur_page
        page_start = cur_page * JOBS_BOARD_PAGE_SIZE
        page_offers = offers[page_start : page_start + JOBS_BOARD_PAGE_SIZE]
        if total_pages > 1:
            items.append(f"-# Page {cur_page + 1}/{total_pages} · showing {len(page_offers)} of {len(offers)} offers")

        for row in page_offers:
            reward = _jobs_coerce_dict(row.reward_snapshot)
            tier_info = JOBS_TIERS.get(str(row.tier), {})
            tier_name = tier_info.get("name", f"Tier {row.tier}")
            category_label = row.category.title() if row.category else ""
            # Perk preview — only render when a perk was actually rolled at
            # offer-gen. Inserted between reward and heat so it reads as a
            # bonus to the success outcome.
            row_perk = (_jobs_col(row, "perk_drop", "") or "").strip()
            perk_line = _perks_format_offer_preview(row_perk, int(row.tier)) if row_perk else ""
            section_body = (
                f"*{row.narrative}*\n"
                f"🎯 Target: **{_jobs_npc_display(row.target_faction)}**\n"
                f"💪 Difficulty: **{row.difficulty} SP**\n"
                f"💰 Reward: {_jobs_reward_summary(reward)}\n"
                + (f"{perk_line}\n" if perk_line else "")
                + f"🚨 Heat cost: +{row.heat_cost}"
            )

            at_cap = window_count >= _eff_cap_board
            accept_btn = Button(
                label="Window limit hit" if at_cap else "Accept",
                style=ButtonStyle.gray if at_cap else ButtonStyle.green,
                custom_id=f"jobs_accept_{row.id}",
            )
            accept_btn.callback = make_on_accept(int(row.id))
            decline_btn = Button(label="Decline", style=ButtonStyle.gray, custom_id=f"jobs_decline_{row.id}")
            decline_btn.callback = make_on_decline(int(row.id))

            items.append(
                Section(
                    f"### {_jobs_npc_display(row.offered_by)}  ·  Tier {row.tier} ({tier_name})  ·  {category_label}",
                    section_body,
                    Thumbnail(
                        f"https://wsrv.nl/?url=raw.githubusercontent.com/sneezeparty/catbot7/"
                        f"refs/heads/main/images/spawn/fine_cat.png"
                    ),
                ),
            )
            items.append(ActionRow(accept_btn, decline_btn))

        help_btn = Button(label="💡 Help", style=ButtonStyle.gray, custom_id="jobs_help")
        help_btn.callback = on_help
        # reroll_board (job perk, charge): visible button when the perk is
        # active. The callback consumes the charge atomically with the DB write.
        board_row_buttons = [help_btn]
        if total_pages > 1:
            prev_btn = Button(
                label="← Prev",
                style=ButtonStyle.gray,
                custom_id="jobs_board_prev",
            )
            prev_btn.callback = on_board_prev
            prev_btn.disabled = cur_page == 0
            next_btn = Button(
                label="Next →",
                style=ButtonStyle.gray,
                custom_id="jobs_board_next",
            )
            next_btn.callback = on_board_next
            next_btn.disabled = cur_page >= total_pages - 1
            board_row_buttons.extend([prev_btn, next_btn])
        if "reroll_board" in _perks_active_ids(profile):
            reroll_btn = Button(
                label="🔄 Reroll Board (1 use)",
                style=ButtonStyle.blurple,
                custom_id="jobs_reroll_board",
            )
            reroll_btn.callback = on_reroll_board
            board_row_buttons.append(reroll_btn)
        # Paid reroll (coins) — level-scaled, escalates within the window.
        _rr_price = _jobs_reroll_price(profile, now)
        _rr_coins = int(profile.coins or 0)
        paid_reroll_btn = Button(
            label=f"🔄 Reroll (🪙 {_rr_price:,})",
            style=ButtonStyle.gray,
            custom_id="jobs_reroll_paid",
        )
        if _rr_coins < _rr_price:
            paid_reroll_btn.disabled = True
            paid_reroll_btn.label = f"🔄 Reroll: need 🪙 {_rr_price - _rr_coins:,}"
        paid_reroll_btn.callback = on_reroll_paid
        board_row_buttons.append(paid_reroll_btn)
        items.append(ActionRow(*board_row_buttons))

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
            sp_str = f"**{eff_sp} SP**" if eff_sp == raw_sp else f"**{eff_sp} SP** *(raw {raw_sp})*"
            crew_lines.append(f"- {c}× {emoji} {t} — {sp_str}")
        if not crew_lines:
            crew_lines.append("-# (no cats in crew yet)")

        reward = _jobs_coerce_dict(job.reward_snapshot)

        items: list = [
            f"## 🎯 {_jobs_npc_display(job.offered_by)} — Tier {job.tier} ({tier_name})",
            f"*{job.narrative}*",
            f"🎯 Target: **{_jobs_npc_display(job.target_faction)}**",
            Separator(),
            "**👥 Your Crew**",
            "\n".join(crew_lines) + f"\n**Total: {send_total} SP**",
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

        # Pre-rolled perk preview for THIS specific offer (set at offer-gen
        # time). Worded committally — by this screen the player has chosen
        # the offer and is deciding the crew. Catalog-tolerant: a perk_id
        # the operator removed renders "Unknown perk" rather than crashing.
        job_perk = (_jobs_col(job, "perk_drop", "") or "").strip()
        if job_perk:
            perk_cat = PERKS_CATALOG.get(job_perk)
            if perk_cat:
                perk_name = perk_cat.get("name", job_perk)
                strength = _perks_format_strength(job_perk, int(job.tier or 0))
                strength_suffix = f" {strength}" if strength else ""
                items.append(f"🎁 You'll receive on success: **{perk_name}**{strength_suffix}.")
            else:
                items.append("🎁 You'll receive on success: *Unknown perk*.")

        # Active job perks affecting THIS commit — surfaced so the player
        # sees their effective SP / heat / chance is inflated before they
        # commit the cats. Only flag the perks that act on commit.
        _active_now = _perks_active_ids(profile)
        send_perk_lines = []
        if "send_power_boost" in _active_now:
            mult = float(_perks_strength(profile, "send_power_boost", "multiplier", 1.5))
            send_perk_lines.append(f"💪 Iron Grip ready — next commit's Send Power ×{mult:g}")
        if "heat_shield" in _active_now:
            send_perk_lines.append("♨️ Heat Shield ready — next commit's heat is halved")
        if "complication_insurance" in _active_now:
            red = float(_perks_strength(profile, "complication_insurance", "reduction_pp", 0.20))
            send_perk_lines.append(f"🛟 Sweep Pattern ready — complication chance −{red * 100:.0f}pp")
        if "crew_insurance" in _active_now:
            send_perk_lines.append("🛡️ Crew Insurance ready — a near-miss will convert to success")
        if "rep_windfall" in _active_now:
            send_perk_lines.append("🌟 Rep Windfall ready — offerer rep gain ×2 on success")
        if send_perk_lines:
            items.append(Separator())
            items.append("**🎁 Active job perks:**")
            for line in send_perk_lines:
                items.append(f"-# {line}")

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
        # Respect line — only on success, since that's the only outcome that
        # actually grants respect (failures don't deduct, decay handles that).
        rep_changes_for_respect = _jobs_coerce_dict(job.rep_changes)
        respect_gain_disp = int(rep_changes_for_respect.get("respect_gain", 0) or 0)
        respect_now_disp = int(rep_changes_for_respect.get("respect_now", 0) or 0)
        respect_lost_disp = int(rep_changes_for_respect.get("respect_levels_lost", 0) or 0)
        respect_max_disp = max(1, int(_respect_cfg().get("max", 100)))
        if outcome == "success" and respect_gain_disp > 0:
            items.append(f"🤝 Respect: +{respect_gain_disp} (now {respect_now_disp}/{respect_max_disp})")
        if respect_lost_disp > 0:
            items.append(
                f"💀 Lost {respect_lost_disp} catnip level"
                f"{'s' if respect_lost_disp > 1 else ''} to mafia decay before this job."
            )
        # Pinch follow-up — only shown on the commit that crossed the threshold.
        rep_changes = _jobs_coerce_dict(job.rep_changes)
        if rep_changes.get("pinched"):
            suspended_until = int(getattr(profile, "perks_suspended_until", 0) or 0)
            items.append(
                f"\n🚓 **Pinched.** Your heat hit {JOBS_PINCH_THRESHOLD}. The Cat Police caught up with your crew.\n"
                f"Catnip perks come back <t:{suspended_until}:R>. Heat reset to {JOBS_PINCH_RESET}."
            )

        # Perks fired on this commit — surface them above the cat voice so the
        # player connects the effect to the cause. Reads markers stamped by
        # _jobs_apply_outcome and the commit-site (see Phase 2b).
        perk_fired_lines = []
        if rep_changes.get("crew_insurance_fired"):
            perk_fired_lines.append("🛡️ **Crew Insurance fired** — the near-miss was converted to a clean success.")
        if rep_changes.get("heat_shield_fired"):
            perk_fired_lines.append("♨️ **Heat Shield** halved this commit's heat cost.")
        if rep_changes.get("rep_windfall_fired"):
            perk_fired_lines.append("🌟 **Rep Windfall** doubled the offerer rep gain.")
        if rep_changes.get("send_power_boost_fired"):
            perk_fired_lines.append("💪 **Iron Grip** boosted this commit's effective Send Power.")
        if rep_changes.get("complication_insurance_fired"):
            perk_fired_lines.append("🛟 **Sweep Pattern** lowered the complication chance.")
        if rep_changes.get("daily_cap_extension_fired"):
            perk_fired_lines.append("⏰ **Overtime** let you commit an extra job today.")
        if perk_fired_lines:
            items.append(Separator())
            for line in perk_fired_lines:
                items.append(line)

        # One cat from the crew gets the last word. Seeded off the job id + outcome
        # so the line is stable across re-renders of the same result.
        voice_rng = random.Random(int(job.id or 0) ^ hash(outcome) ^ int(job.committed_at or 0))
        send_for_voice = _jobs_coerce_dict(job.send_snapshot)
        voice_line = _jobs_pick_cat_voice(send_for_voice, cats_destroyed, outcome, comp_id, voice_rng)
        if voice_line:
            items.append(Separator())
            items.append(voice_line)

        # Perk preview/outcome — surfaced last (between cat voice and action
        # row). Two distinct cases now that the perk is pre-rolled:
        #   1. success + perk granted → "delivers on the promise" (granted_id
        #      is the source of truth — it could differ from the pre-rolled
        #      id only if grant failed and we logged through).
        #   2. non-success + perk was on offer → "the bonus walks" line so
        #      the missed perk feels material, not invisible.
        granted_id = rep_changes.get("perk_drop")
        offered_id = (_jobs_col(job, "perk_drop", "") or "").strip()
        npc_name = _jobs_npc_display(job.offered_by) or job.offered_by
        if granted_id and outcome == "success":
            perk_cat = PERKS_CATALOG.get(granted_id, {})
            perk_name = perk_cat.get("name", granted_id.replace("_", " ").title())
            perk_desc = perk_cat.get("desc", "")
            items.append(Separator())
            items.append(f"🎁 **{npc_name}** delivers on the promise — **{perk_name}** is now active.")
            if perk_desc:
                items.append(f"-# {perk_desc}")
            items.append("-# Check **/perks** to see what's active.")
        elif outcome != "success" and offered_id:
            perk_cat = PERKS_CATALOG.get(offered_id, {})
            perk_name = perk_cat.get("name", offered_id.replace("_", " ").title())
            items.append(Separator())
            items.append(f"💨 The bonus walks. *{perk_name} won't be granted.*")

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
            # Daily cap check BEFORE anything else — including the public embed.
            # If we're at cap, the player can't actually commit, so we shouldn't
            # let them publicly "accept" a job they can't follow through on.
            # Peek at the daily_cap_extension perk so over-cap players with the
            # perk active CAN accept; the charge is consumed at commit, not here.
            now_check = int(time.time())
            window_count = await _jobs_commits_this_window(
                int(message.user.id), int(message.guild.id), now_check
            )
            _accept_eff_cap = _perks_effective_daily_cap(profile, JOBS_COMMITS_PER_WINDOW)
            if window_count >= _accept_eff_cap:
                _, win_end = _jobs_window_bounds(_jobs_window_index(now_check))
                await interaction.response.send_message(
                    f"You've hit your window limit of **{JOBS_COMMITS_PER_WINDOW}** jobs. "
                    f"Comes back at the next refresh <t:{win_end}:R>.",
                    ephemeral=True,
                )
                return

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

    async def on_board_prev(interaction: discord.Interaction):
        """Board pagination — previous page. Re-render in place via show_board's
        interaction.response.edit_message path (same as Decline); show_board
        handles clamping. Do NOT defer first: deferring routes show_board through
        edit_original_response, which leaves the re-rendered components-v2 view
        unregistered on the ephemeral message — its buttons then go dead and the
        next click reads "interaction failed" with nothing logged."""
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        mode["board_page"] = max(0, int(mode.get("board_page", 0) or 0) - 1)
        await show_board(interaction)

    async def on_board_next(interaction: discord.Interaction):
        """Board pagination — next page. Re-render in place (no defer) for the
        same reason as on_board_prev; clamping happens in show_board."""
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        mode["board_page"] = int(mode.get("board_page", 0) or 0) + 1
        await show_board(interaction)

    async def on_reroll_board(interaction: discord.Interaction):
        """reroll_board (job perk, charge): blow away the current window's
        offers and regenerate. Charge is consumed only once the reroll
        succeeds — otherwise the player loses a charge to a DB hiccup."""
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        await interaction.response.defer()
        await profile.refresh_from_db()
        if "reroll_board" not in _perks_active_ids(profile):
            last_toast.append("⚠️ Reroll Board is not active.")
            await show_board(interaction)
            return
        if await _jobs_do_reroll(profile, int(time.time())):
            _perks_consume_charge(profile, "reroll_board")
            await profile.save()
            last_toast.append("🔄 Board rerolled.")
            mode["board_page"] = 0  # reset pagination so player sees the first new offer
        else:
            last_toast.append("⚠️ Reroll failed — try again.")
        await show_board(interaction)

    async def on_reroll_paid(interaction: discord.Interaction):
        """Paid board reroll (coins). Same delete+regenerate as the perk, but
        charges a level-scaled, per-window-escalating coin price."""
        if interaction.user.id != message.user.id:
            await do_funny(interaction)
            return
        await interaction.response.defer()
        await profile.refresh_from_db()
        now_rr = int(time.time())
        price = _jobs_reroll_price(profile, now_rr)  # recomputed post-refresh (anti-stale)
        if int(profile.coins or 0) < price:
            last_toast.append(f"-# 🪙 Not enough coins to reroll — need {price:,}.")
            await show_board(interaction)
            return
        if await _jobs_do_reroll(profile, now_rr):
            _jobs_reroll_charge(profile, now_rr, price)
            await profile.save()
            last_toast.append(f"🔄 Board rerolled for 🪙 {price:,}.")
            mode["board_page"] = 0
        else:
            last_toast.append("⚠️ Reroll failed — try again. (No coins charged.)")
        await show_board(interaction)

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
        # daily_cap_extension (job perk, charge+timed) grants +1 over base;
        # the charge is consumed inside the lock below, NOT here, so a player
        # who bails out of the modal doesn't burn their perk.
        now_check = int(time.time())
        window_count = await _jobs_commits_this_window(int(message.user.id), int(message.guild.id), now_check)
        # Peek-only effective cap for the early gate. The actual consume
        # happens at commit time inside the transaction.
        _peek_profile = await Profile.get_or_create(user_id=int(message.user.id), guild_id=int(message.guild.id))
        _effective_cap_peek = _perks_effective_daily_cap(_peek_profile, JOBS_COMMITS_PER_WINDOW)
        if window_count >= _effective_cap_peek:
            _, win_end = _jobs_window_bounds(_jobs_window_index(now_check))
            await interaction.response.send_message(
                f"You've hit your window limit of **{JOBS_COMMITS_PER_WINDOW}** jobs. "
                f"Comes back at the next refresh <t:{win_end}:R>.",
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

                # In-transaction window cap re-check + perk consume. Two checks
                # are intentional: the peek above prevents the commit modal
                # from spawning over-cap; this one is authoritative and
                # consumes the daily_cap_extension charge atomically.
                window_count_lock = await _jobs_commits_this_window(int(message.user.id), int(message.guild.id), int(time.time()))
                allowed, dce_fired = _perks_check_and_consume_daily_cap(fresh, window_count_lock, JOBS_COMMITS_PER_WINDOW)
                if not allowed:
                    last_toast.append("⚠️ Window cap reached between accept and commit.")
                    await show_send(interaction)
                    return

                # Escrow: decrement first. Survivors get re-added below per outcome.
                for t, c in send_state.items():
                    if not _jobs_subtract_cat(fresh, t, int(c)):
                        last_toast.append(f"⚠️ Escrow failed on {t}.")
                        await show_send(interaction)
                        return

                # send_power_boost (job perk, charge): scale effective SP for
                # this commit only. Consume here so cancelled commits don't
                # burn the perk (we're past the bail-out points above).
                send_total = _jobs_send_total(send_state)
                send_power_boost_fired = False
                if "send_power_boost" in _perks_active_ids(fresh) and _perks_consume_charge(fresh, "send_power_boost"):
                    spb_mult = float(_perks_strength(fresh, "send_power_boost", "multiplier", 1.5))
                    send_total = int(round(send_total * spb_mult))
                    send_power_boost_fired = True
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
                # complication_insurance (job perk, charge): subtract a flat pp
                # from the rolled chance. Stacks additively with rep insurance.
                complication_insurance_fired = False
                if "complication_insurance" in _perks_active_ids(fresh) and _perks_consume_charge(fresh, "complication_insurance"):
                    ci_reduction = float(_perks_strength(fresh, "complication_insurance", "reduction_pp", 0.20))
                    comp_chance = max(0.0, comp_chance - ci_reduction)
                    complication_insurance_fired = True
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

                # Stamp in-transaction perk firings onto rep_changes so the
                # Phase 3 result screen can render "X perk fired" markers.
                # _jobs_apply_outcome already records crew_insurance/heat_shield/
                # rep_windfall; we add the perks consumed at this layer.
                rc_after = _jobs_coerce_dict(job.rep_changes)
                if send_power_boost_fired:
                    rc_after["send_power_boost_fired"] = True
                if complication_insurance_fired:
                    rc_after["complication_insurance_fired"] = True
                if dce_fired:
                    rc_after["daily_cap_extension_fired"] = True
                job.rep_changes = rc_after

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
                # "Have a job perk active" quest — passive condition, so fire
                # whenever a successful commit leaves the player holding any
                # stored perk (covers a fresh drop AND a pre-existing perk
                # from an earlier job). The /perks command also fires this
                # for the case where the player never does another job.
                if _perks_active_ids(profile):
                    try:
                        await progress(interaction, profile, "perk_user")
                    except Exception:
                        logging.exception("jobs: perk_user progress failed")
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
    # Trigger pending season rollover + ephemeral reset notice before the
    # board renders. `profile` is the invoker's per-server profile (fetched
    # at the top of /jobs).
    await refresh_quests(profile)
    await _maybe_show_season_reset_notice(message, profile)
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


@bot.tree.command(description="view your active mafia favors (job perks)")
async def perks(message: discord.Interaction):
    """Player-facing view of active job perks: timed buffs + charge-based
    consumables dropped from successful /jobs. Ephemeral. Container accent
    is brown to match /catstore. Empty state nudges toward /jobs."""
    profile = await Profile.get_or_create(user_id=message.user.id, guild_id=message.guild.id)
    active = _perks_active_for_display(profile)

    async def on_perks_help(interaction: discord.Interaction):
        # Phase 6 adds a dedicated "Perks" help page; until then fall back to
        # the help index landing page so the button still works.
        await _jobs_send_help(interaction, profile, start_page=_jobs_help_index_by_title(profile, "perks"))

    help_btn = Button(label="💡 Help", style=ButtonStyle.gray, custom_id="perks_help")
    help_btn.callback = on_perks_help

    view = LayoutView(timeout=VIEW_TIMEOUT)
    items: list = ["## 🎁 Mafia Favors"]

    if not active:
        items.append("No mafia favors active. Complete jobs to earn perks.")
        items.append(Separator())
        items.append("-# Perks drop on **successful** /jobs. Different NPCs favor different perks.")
        items.append(ActionRow(help_btn))
    else:
        items.append(f"You have **{len(active)}** active favor{'s' if len(active) != 1 else ''}.")
        items.append(Separator())
        for entry in active:
            npc_disp = _jobs_npc_display(entry.get("npc", "")) or "—"
            tier_disp = f"T{entry.get('tier', '?')}"
            # Plain text — Section needs an `accessory` widget (button/thumbnail)
            # but each perk row is info-only, so just append a title + body
            # block. Same visual shape, no widget required.
            items.append(f"### 🎁 {entry.get('name', entry.get('id', '?'))}")
            items.append(
                f"{entry.get('desc', '') or '*(no description)*'}\n"
                f"-# from **{npc_disp}** ({tier_disp})  ·  {entry.get('status', '')}"
            )
        items.append(ActionRow(help_btn))

    container = Container(*items)
    try:
        container.accent_color = Colors.brown
    except Exception:
        pass
    view.add_item(container)
    await message.response.send_message(view=view, ephemeral=True)
    # BP quest: "have a job perk active". Idempotent — progress() handles
    # the per-period cooldown so subsequent /perks checks don't re-progress.
    if active:
        try:
            await progress(message, profile, "perk_user")
        except Exception:
            logging.exception("perk_user quest progress failed")


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

        # Coin tax — re-check at commit time so the player can't sit on a
        # confirm screen, spend their coins elsewhere, then come back. Skipped
        # entirely when the prisms_crafted column isn't present (migration 018
        # unrun) so prism crafting keeps working.
        tax_on = _prism_tax_enabled(user)
        crafts_so_far = _safe_prisms_crafted(user)
        coin_cost = prism_craft_coin_cost(crafts_so_far) if tax_on else 0
        if coin_cost > 0 and int(getattr(user, "coins", 0) or 0) < coin_cost:
            await interaction.followup.send(
                f"You need 🪙 **{coin_cost:,}** coins to craft your "
                f"{_ordinal(crafts_so_far + 1)} prism on this server. "
                f"You have 🪙 **{int(getattr(user, 'coins', 0) or 0):,}**.",
                ephemeral=True,
            )
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

        # actually take away cats and coins, and bump the crafted counter.
        # The coin-tax half no-ops when the column isn't present yet.
        for i in cattypes:
            user["cat_" + i] -= 1
        if tax_on and coin_cost > 0:
            user.coins = int(getattr(user, "coins", 0) or 0) - coin_cost
            user.prisms_crafted = crafts_so_far + 1
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

        cost_suffix = f" (🪙 {coin_cost:,} coins spent)" if (tax_on and coin_cost > 0) else ""
        await message.followup.send(
            f"{icon} {interaction.user.mention} has created prism {selected_name}!{cost_suffix}"
        )
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

        tax_on = _prism_tax_enabled(user)
        crafts_so_far = _safe_prisms_crafted(user)
        coin_cost = prism_craft_coin_cost(crafts_so_far) if tax_on else 0
        coins_have = int(getattr(user, "coins", 0) or 0)
        cost_line = (
            (
                f"\n**Coin cost (your {_ordinal(crafts_so_far + 1)} prism on this server):** "
                f"🪙 **{coin_cost:,}** (you have 🪙 {coins_have:,})"
            ) if (tax_on and coin_cost > 0) else ""
        )

        if len(missing_cats) == 0:
            view = View(timeout=VIEW_TIMEOUT)
            insufficient_coins = (tax_on and coin_cost > 0 and coins_have < coin_cost)
            confirm_button = Button(
                label="Not enough coins!" if insufficient_coins else "Craft!",
                style=ButtonStyle.red if insufficient_coins else ButtonStyle.blurple,
                emoji=icon,
                disabled=insufficient_coins,
            )
            confirm_button.callback = confirm_craft
            description = (
                "The crafting recipe is __ONE of EVERY cat type__."
                + cost_line
                + "\nContinue crafting?"
            )
        else:
            view = View(timeout=VIEW_TIMEOUT)
            confirm_button = Button(label="Not enough cats!", style=ButtonStyle.red, disabled=True)
            description = (
                "The crafting recipe is __ONE of EVERY cat type__."
                + cost_line
                + "\nYou are missing " + "".join(missing_cats) + unknown_suffix
            )

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
            # self-play - no quest progress, that was free real estate
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
        if winner == -1:
            # ttc quest (upstream rework): only ties against Cat Bot count —
            # the minimax bot always ties under perfect play, so it's an
            # actual minigame instead of self-play farming. Runs AFTER the
            # stat saves above: progress() refetches the row, so any unsaved
            # ttt_played/ttt_draws increments would be silently discarded.
            if players[0] == bot.user:
                await progress(message, users[1], "ttc")
            if players[1] == bot.user:
                await progress(message, users[0], "ttc")

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
        # extra_quest/extra_cooldown/casino_progress_temp are read by
        # progress_casino_quest's guard below before it refetches, so they must
        # be in this partial fetch or attribute access KeyErrors. Same for
        # misc_quest and the cookie-quest guard (upstream fix e16db15).
        user = await Profile.get(
            ["cookies", "extra_quest", "extra_cooldown", "casino_progress_temp", "misc_quest"],
            guild_id=message.guild.id,
            user_id=message.user.id,
        )
        user.cookies += 1
        await user.save()
        view.children[0].label = f"{user.cookies:,}"
        await interaction.edit_original_response(view=view)
        if user.cookies < 5:
            await achemb(interaction, "cookieclicker", "followup")
        if 5100 > user.cookies >= 5000:
            await achemb(interaction, "cookiesclicked", "followup")
        if user.misc_quest.strip() == "cookie":
            await progress(message, user, "cookie")
        # casino quest: clicking the cookie counts as the cookieclicker game
        await progress_casino_quest(interaction, user, "cookieclicker")

    view = View(timeout=VIEW_TIMEOUT)
    button = Button(emoji="🍪", label=f"{user.cookies:,}", style=ButtonStyle.blurple)
    button.callback = bake
    view.add_item(button)
    await message.response.send_message(view=view)


@bot.tree.command(description="absolute CHAOS")
async def chaos(message: discord.Interaction):
    profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)

    async def click(interaction: discord.Interaction, first: Optional[bool] = False):
        # the global counter lives in a sentinel profile row (guild 666, owned
        # by the bot user) reusing the cookies column - one atomic upsert per
        # click, so it survives restarts and needs no schema change.
        cookies = await pool.fetchrow(
            """INSERT INTO profile (guild_id, user_id, cookies)
            VALUES (666, $1, 1)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET cookies = profile.cookies + $2
            RETURNING cookies;""",
            bot.user.id,
            random.randint(0, 1000),
        )
        cookies = cookies["cookies"]

        view = LayoutView(timeout=VIEW_TIMEOUT)
        b = Button(label="Chaos!", style=ButtonStyle.red, emoji="💥")
        b.callback = click
        view.add_item(
            Container(
                f"## {cookies:,}",
                "the number above is global for everyone. click the button to add a random number to it.",
                b,
            )
        )

        if first:
            await interaction.response.send_message(view=view)
        else:
            await interaction.response.defer()
            await interaction.edit_original_response(view=view)

        if profile.misc_quest.strip() == "chaos":
            await progress(message, profile, "chaos")

    await click(message, True)


@bot.tree.command(description="yeah i made this solely so i could name it catfishing")
async def fish(message: discord.Interaction):
    profile = await Profile.get_or_create(user_id=message.user.id, guild_id=message.guild.id)

    async def go_fishing(interaction: discord.Interaction):
        if interaction.user != message.user:
            await do_funny(interaction)
            return

        if (interaction.guild.id, interaction.user.id) in fish_lock:
            await interaction.response.send_message("You're already fishing!", ephemeral=True)
            return

        fish_lock.append((interaction.guild.id, interaction.user.id))

        await interaction.response.defer()
        view = LayoutView(timeout=VIEW_TIMEOUT)
        view.add_item(TextDisplay("Fishing... (wait 10-30 seconds)"))
        await interaction.edit_original_response(view=view)

        for _ in range(random.randint(1000, 3000)):
            if (interaction.guild.id, interaction.user.id) not in fish_lock:
                fish_lock.append((interaction.guild.id, interaction.user.id))
            await asyncio.sleep(0.01)

        fishtype = random.choices(cattypes, weights=type_dict.values())[0]
        fish_caught = False

        async def pull_fish(interaction: discord.Interaction):
            nonlocal fish_caught
            if fish_caught:
                return
            if interaction.user != message.user:
                await do_funny(interaction)
                return
            fish_caught = True

            view = LayoutView(timeout=VIEW_TIMEOUT)
            button = Button(emoji="🎣", label="Cast", style=ButtonStyle.blurple)
            button.callback = go_fishing
            view.add_item(TextDisplay(f"You caught a {fish_emoji(fishtype)} {fishtype} fish!"))
            view.add_item(ActionRow(button))
            await interaction.response.defer()
            await interaction.edit_original_response(view=view)

            await profile.refresh_from_db()
            profile.fish_caught += 1
            if not profile.rarest_fish.strip() or cattypes.index(fishtype) > cattypes.index(profile.rarest_fish.strip()):
                profile.rarest_fish = fishtype
            await profile.save()
            await achemb(interaction, "fisherman", "followup")
            if cattypes.index(fishtype) >= cattypes.index("Legendary"):
                await achemb(interaction, "pro_fisher", "followup")

            try:
                fish_lock.remove((interaction.guild.id, interaction.user.id))
            except ValueError:
                pass

            await progress(message, profile, "fish")

        view = LayoutView(timeout=VIEW_TIMEOUT)
        button = Button(label="Pull!", style=ButtonStyle.blurple)
        button.callback = pull_fish

        view.add_item(TextDisplay(f"A {fish_emoji(fishtype)} {fishtype} is on the line! Pull!"))
        view.add_item(ActionRow(button))

        await interaction.edit_original_response(view=view)

        await asyncio.sleep(5)

        if not fish_caught:
            view = LayoutView(timeout=VIEW_TIMEOUT)
            button = Button(emoji="🎣", label="Cast", style=ButtonStyle.blurple)
            button.callback = go_fishing
            view.add_item(TextDisplay("You weren't fast enough..."))
            view.add_item(ActionRow(button))
            await interaction.edit_original_response(view=view)
            try:
                fish_lock.remove((interaction.guild.id, interaction.user.id))
            except ValueError:
                pass

    view = LayoutView(timeout=VIEW_TIMEOUT)

    button = Button(emoji="🎣", label="Cast", style=ButtonStyle.blurple)
    button.callback = go_fishing

    if profile.rarest_fish.strip():
        rarest_fish = f"{fish_emoji(profile.rarest_fish.strip())} {profile.rarest_fish}"
    else:
        rarest_fish = "none"

    view.add_item(Container("## 🎣 catfishing", f"total fish caught: {profile.fish_caught:,}\nyour rarest fish: {rarest_fish}"))
    view.add_item(ActionRow(button))

    await message.response.send_message(view=view)


CAT_FORTUNES = [
    "You will find a mysterious hairball in your shoe. It brings good luck... probably.",
    "A cat will stare at you from across the room today. It is judging you. You will not pass.",
    "Beware of the red dot. It leads nowhere, yet you will chase it anyway.",
    "Your next nap will be legendary. 14 hours minimum. You've earned it.",
    "A cardboard box will present itself. You must sit in it. This is the way.",
    "You will knock something off a table today. Do not apologize. Maintain eye contact.",
    "The vacuum cleaner approaches. Flee now, ask questions never.",
    "Today's lucky number is 9. You have that many lives left... for now.",
    "A can opener will sound in the distance. Follow it. Destiny awaits.",
    "You will receive chin scratches from an unexpected source. Accept them graciously.",
    "The laser pointer of fate shines upon you. Chase it with reckless abandon.",
    "An ancient prophecy foretells: you will ignore an expensive cat toy and play with the bag it came in.",
    "Mercury is in retrograde. This means nothing to you. You are a cat. Nap on.",
    "You will sit on someone's keyboard today and type something profound. Or 'asdfjkl;'. Same thing.",
    "A bird will appear at your window. You will make that weird chattering sound. It is inevitable.",
    "Your food bowl is half empty. Scream about it at 3 AM. This is reasonable.",
    "The bathroom door will close. You must yell. You MUST be on the other side.",
    "A cucumber will appear behind you. Your reaction will be... disproportionate.",
    "You will find the warmest spot in the house and defend it with your life.",
    "Someone will call your name. Ignore them. They will call again. Ignore harder.",
    "The stars align in the shape of a fish. This is the best possible omen.",
    "You will bring a gift to your human today. They will not appreciate the dead bug. Ungrateful.",
    "A door will be slightly ajar. You will not go through it. You will simply stare.",
    "Your horoscope says: if it fits, you sits. The science is settled.",
    "Tonight, you will perform the 3 AM zoomies. The furniture will not survive.",
    "A mysterious force compels you to drink water from the faucet instead of your bowl.",
    "You will claim a laptop as your bed. The human's 'important work' is irrelevant.",
    "The prophecy is clear: you will catch between 0 and 10,000 cats today. Probably.",
    "A great trade offer approaches. You will decline it. Then accept a worse one. This is the way of the cat.",
    "The ancient cat council has spoken: your next pack opening will be... interesting.",
]

CAT_ACTIVITIES = (
    "napping",
    "knocking things off tables",
    "ignoring humans",
    "zoomies",
    "bird watching",
    "box sitting",
    "keyboard walking",
    "3 AM screaming",
)

CAT_FORTUNE_TITLES = [
    "Madame Meowstradamus Speaks",
    "The Crystal Yarn Ball Reveals",
    "Purrfessor Whiskers' Prophecy",
    "The Oracle of Meow",
    "Fortune Paws Has Spoken",
    "The Catstrologer's Vision",
    "Whisker Wisdom™",
    "The Feline Fates Decree",
]


@bot.tree.command(description="🔮 Consult the ancient cat oracle for a purrsonalized fortune")
async def fortune(interaction: discord.Interaction):
    rng = random.Random(interaction.user.id + discord.utils.utcnow().date().toordinal())

    embed = discord.Embed(
        title=f"🔮 {rng.choice(CAT_FORTUNE_TITLES)}",
        description=(
            f"😺 {rng.choice(CAT_FORTUNES)}\n\n"
            f"**Lucky cat type:** {rng.choice(cattypes)}\n"
            f"**Lucky number:** {rng.randint(1, 9)}\n"
            f"**Lucky activity:** {rng.choice(CAT_ACTIVITIES)}"
        ),
        color=Colors.brown,
    )

    embed.set_footer(text="Fortunes reset daily • Your fate is sealed (until tomorrow)")
    await interaction.response.send_message(embed=embed)


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
    async def brew_coffee(interaction: discord.Interaction):
        nonlocal view
        if interaction.user != message.user:
            await do_funny(interaction)
            return

        await interaction.response.defer()

        try:
            # misc_quest must be in this partial fetch — the quest hook below
            # reads it before progress() refetches (upstream fix e16db15).
            user = await Profile.get(["coffees", "misc_quest"], guild_id=message.guild.id, user_id=message.user.id)
            user.coffees += 1
            await user.save()
        except AttributeError:
            await interaction.edit_original_response(content="...", view=None)
            return

        view.children[0].label = f"{user.coffees:,}"
        await interaction.edit_original_response(content="ugh fine", view=view)

        if user.misc_quest.strip() == "coffee":
            await progress(message, user, "coffee")

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


@bot.tree.command(description="(disabled on this self-hosted instance)")
async def bakery(message: discord.Interaction):
    # The Bake.gg partner API only authorizes the public Cat Bot's
    # BAKE_GG_TOKEN, so orders can never be delivered from a fork —
    # /cookie and /brew (and their quests) still work as plain clickers.
    await message.response.send_message("This command is disabled on this self-hosted instance.", ephemeral=True)


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


@bot.tree.command(description="vegas-style 5x3 cat slots — pick lines, place a bet, spin")
@discord.app_commands.describe(
    lines="how many paylines to bet on. omit to use the menu.",
    bet="coins to bet PER LINE (total = lines × bet). omit to use the menu.",
)
@discord.app_commands.choices(lines=[
    discord.app_commands.Choice(name="1 line",   value=1),
    discord.app_commands.Choice(name="5 lines",  value=5),
    discord.app_commands.Choice(name="9 lines",  value=9),
    discord.app_commands.Choice(name="20 lines", value=20),
])
async def catslots(
    message: discord.Interaction,
    lines: Optional[discord.app_commands.Choice[int]] = None,
    bet: Optional[discord.app_commands.Range[int, 1, CATSLOTS_MAX_PER_LINE]] = None,
):
    if message.user.id + message.guild.id in catslots_lock:
        await message.response.send_message(
            "you're already spinning /catslots — wait for the reels to settle, time-traveler",
            ephemeral=True,
        )
        await achemb(message, "paradoxical_catslots", "followup")
        return

    profile = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)
    lock_key = message.user.id + message.guild.id

    def stats_embed(p: Profile) -> discord.Embed:
        broke_suffix = ""
        if p.coins <= 0:
            broke_suffix = "\n-# debt is allowed — you can still gamble up to **100** coins"
        net = int(p.catslots_coins_won) - int(p.catslots_coins_bet)
        return discord.Embed(
            title="🐈 Cat Slots — 5×3",
            description=(
                f"your balance is **{p.coins:,}** coins{broke_suffix}\n\n"
                f"__Your stats__\n"
                f"{p.catslots_spins:,} spins  ·  {p.catslots_wins:,} wins  ·  {p.catslots_big_wins:,} big wins\n"
                f"bet {p.catslots_coins_bet:,} · won {p.catslots_coins_won:,} · net {net:+,}\n\n"
                f"__Lines:__ 1, 5, 9, or 20.  __Per-line cap:__ {CATSLOTS_MAX_PER_LINE} coins (max total bet {max(CATSLOTS_ALLOWED_LINES) * CATSLOTS_MAX_PER_LINE:,}).\n"
                f"__Payouts__ scale by symbol rarity and run length (3, 4, or 5-of-a-kind from column 1)."
            ),
            color=Colors.maroon,
        )

    def post_spin_view(can_repeat: bool):
        v = View(timeout=VIEW_TIMEOUT)
        spin_again = Button(label="Spin Again", style=ButtonStyle.blurple, disabled=not can_repeat)
        spin_again.callback = on_spin_again
        change_bet = Button(label="Change Bet", style=ButtonStyle.gray)
        change_bet.callback = on_open_modal
        v.add_item(spin_again)
        v.add_item(change_bet)
        return v

    class CatSlotsModal(Modal):
        def __init__(self, prefill: tuple[int, int] | None = None):
            super().__init__(title="Place your bet", timeout=VIEW_TIMEOUT)
            default_lines = str(prefill[0]) if prefill else None
            default_per = str(prefill[1]) if prefill else None
            self.lines = TextInput(
                min_length=1,
                max_length=2,
                label="lines (1, 5, 9, or 20)",
                style=discord.TextStyle.short,
                required=True,
                placeholder="20",
                default=default_lines,
            )
            self.add_item(self.lines)

            self.per_line = TextInput(
                min_length=1,
                label=f"coins per line (max {CATSLOTS_MAX_PER_LINE})",
                style=discord.TextStyle.short,
                required=True,
                placeholder="10",
                default=default_per,
            )
            self.add_item(self.per_line)

        async def on_submit(self, interaction: discord.Interaction):
            # ---- validate lines (in-memory, no I/O — safe before defer) ----
            try:
                lines_n = int(self.lines.value)
            except ValueError:
                await interaction.response.send_message("lines must be a number", ephemeral=True)
                return
            if lines_n not in CATSLOTS_ALLOWED_LINES:
                await interaction.response.send_message(
                    f"lines must be one of {CATSLOTS_ALLOWED_LINES}", ephemeral=True
                )
                return

            # ---- validate per-line (in-memory, no I/O — safe before defer) ----
            try:
                per_line = int(self.per_line.value)
            except ValueError:
                await interaction.response.send_message("coins per line must be a number", ephemeral=True)
                return
            if per_line < 1:
                await interaction.response.send_message("coins per line must be at least 1", ephemeral=True)
                return
            if per_line > CATSLOTS_MAX_PER_LINE:
                await interaction.response.send_message(
                    f"coins per line can't exceed **{CATSLOTS_MAX_PER_LINE}** "
                    f"(table max). drop the per-line and add more lines if you want a bigger total bet.",
                    ephemeral=True,
                )
                return

            # Defer BEFORE the DB query — the 3s response window is too
            # tight to fit a refresh_from_db() round-trip if anything blips.
            try:
                await interaction.response.defer()
            except discord.NotFound:
                return

            await profile.refresh_from_db()

            total_bet = lines_n * per_line
            max_bet = max(profile.coins, 100)
            if total_bet > max_bet:
                await interaction.followup.send(
                    f"your total bet ({total_bet:,}) exceeds your max ({max_bet:,}). "
                    f"debt is allowed up to 100 coins.",
                    ephemeral=True,
                )
                return

            # ---- concurrency re-check ----
            if lock_key in catslots_lock:
                await interaction.followup.send(
                    "you're already spinning /catslots — wait for the reels to settle, time-traveler",
                    ephemeral=True,
                )
                await achemb(interaction, "paradoxical_catslots", "followup")
                return

            await _do_spin(interaction, lines_n, per_line)

    async def _do_spin(interaction: discord.Interaction, lines_n: int, per_line: int):
        """Run the actual spin. Caller has validated inputs, checked the
        concurrency lock, and called interaction.response.defer()."""
        catslots_lock.append(lock_key)
        try:
            total_bet = lines_n * per_line

            # ---- debit, persist before animating ----
            profile.coins -= total_bet
            profile.catslots_spins += 1
            profile.catslots_coins_bet += total_bet
            await profile.save()

            # eGirl Bonus voucher 🎟️: capture eligibility BEFORE the quest
            # progress calls below — a voucher granted by this spin's own
            # quest XP (level-up Mystery) must not be consumed by the spin
            # that earned it.
            egirl_voucher_pending = _vouchers_has(profile, "egirl_bonus")

            try:
                await achemb(interaction, "catslots", "followup")
                await progress(message, profile, "catslots")
                await progress(message, profile, "catslots1", refetch=False)
                await progress(message, profile, "catslots2", refetch=False)
                await progress(message, profile, "catslots3", refetch=False)
                await progress_casino_quest(message, profile, "catslots")
            except Exception:
                pass

            # ---- build 5 reels, each with an independent random length ----
            reel_durations = [
                random.randint(8, 11),
                random.randint(13, 16),
                random.randint(18, 21),
                random.randint(23, 26),
                random.randint(28, 31),
            ]
            random.shuffle(reel_durations)
            cols = [
                random.choices(CATSLOTS_SYMBOLS, weights=CATSLOTS_WEIGHTS, k=d)
                for d in reel_durations
            ]

            # rigged_users: force a 5-of-a-kind eGirl on line 1 (middle row).
            # current_i at the end of the animation == len(col)-2, which is
            # the middle row index per the offset-(-1,0,+1) layout below.
            if message.user.id in rigged_users:
                for col in cols:
                    col[len(col) - 2] = "eGirl"

            # catslots_force_bonus_users: admin-set override that overwrites N
            # random visible cells with eGirl so the next spin triggers the
            # bonus round at the requested tier. Single-use — popped on read.
            # The eGirl Bonus voucher rides the same mechanism (admin wins).
            force_egirls = catslots_force_bonus_users.pop(lock_key, 0)
            voucher_bonus_note = ""
            if force_egirls not in (3, 4, 5) and egirl_voucher_pending and _vouchers_consume(profile, "egirl_bonus"):
                # persist consumption BEFORE the animation so a crash
                # mid-spin can't refund an already-fired voucher
                await profile.save()
                force_egirls = MYSTERY_EGIRL_TIER if MYSTERY_EGIRL_TIER in (3, 4, 5) else 3
                voucher_bonus_note = "🎟️ **eGirl Bonus voucher consumed!**\n"
            if force_egirls in (3, 4, 5):
                visible_cells = [(c, r) for c in range(5) for r in range(3)]
                for c, r in random.sample(visible_cells, force_egirls):
                    cur = len(cols[c]) - 2
                    cols[c][cur + (r - 1)] = "eGirl"

            blank_emoji = get_emoji("empty")

            def render_grid(currents: list[int]) -> str:
                lines_out = []
                for offset in [-1, 0, 1]:
                    cells = []
                    for col, cur in zip(cols, currents):
                        sym = col[cur + offset]
                        cells.append(get_emoji(sym.lower() + "cat") or sym)
                    if offset == 0:
                        lines_out.append("➡️ " + " ".join(cells) + " ⬅️")
                    else:
                        lines_out.append(f"{blank_emoji} " + " ".join(cells) + f" {blank_emoji}")
                return "\n".join(lines_out)

            bet_header = f"bet **{total_bet:,}** coins ({lines_n} line{'s' if lines_n != 1 else ''} × {per_line:,})\n\n"

            # ---- spin animation ----
            for slot_loop_ind in range(1, max(reel_durations) - 1):
                currents = [min(len(c) - 2, slot_loop_ind) for c in cols]
                desc = bet_header + render_grid(currents)
                embed = discord.Embed(title="🐈 Cat Slots — spinning…", description=desc, color=Colors.maroon)
                try:
                    await interaction.edit_original_response(embed=embed, view=None)
                except Exception:
                    pass
                await asyncio.sleep(0.125)

            # ---- settled state ----
            finals = [len(c) - 2 for c in cols]
            # grid[row][col] — row 0=top, 1=middle, 2=bottom
            grid = [[cols[c][finals[c] + (r - 1)] for c in range(5)] for r in range(3)]

            # ---- evaluate active paylines ----
            active_lines = CATSLOTS_PAYLINES[:lines_n]
            wins = []  # (line_idx_1based, symbol, count, line_payout)
            total_payout = 0
            for i, line in enumerate(active_lines, start=1):
                syms = [grid[r][c] for (c, r) in line]
                first = syms[0]
                count = 1
                for s in syms[1:]:
                    if s == first:
                        count += 1
                    else:
                        break
                if count >= 3:
                    mult = CATSLOTS_PAYOUTS.get(first, {}).get(count, 0)
                    line_payout = mult * per_line
                    if line_payout > 0:
                        wins.append((i, first, count, line_payout))
                        total_payout += line_payout

            big_win = total_payout >= 100 * total_bet and total_payout > 0

            # ---- credit + persist ----
            await profile.refresh_from_db()
            profile.coins += total_payout
            profile.catslots_coins_won += total_payout
            _bump(profile, "coins_earned", total_payout)
            if total_payout > 0:
                profile.catslots_wins += 1
            if big_win:
                profile.catslots_big_wins += 1
            await profile.save()

            # Remember the bet so Spin Again / Change Bet can pre-fill.
            catslots_last_bet[lock_key] = (lines_n, per_line)

            # ---- render final embed ----
            final_grid_str = render_grid(finals)
            # Marking winning lines on the grid is too fiddly across rows since
            # some paylines visit multiple rows. Per spec we just list winning
            # lines below with a ✨ prefix on each line entry.
            desc = bet_header + final_grid_str + "\n\n"
            if big_win:
                desc = "🎰 **BIG WIN!**\n\n" + desc
            elif total_payout > 0:
                desc = "✅ **You win!**\n\n" + desc
            else:
                desc = "💨 **You lose!**\n\n" + desc

            if voucher_bonus_note:
                desc += voucher_bonus_note + "\n"

            if wins:
                desc += "__Winning lines:__\n"
                for line_idx, sym, count, lp in wins:
                    emoji = get_emoji(sym.lower() + "cat") or sym
                    desc += f"✨ **Line {line_idx}**: {count}× {emoji} {sym} — **{lp:,}** coins\n"
                desc += "\n"

            net = total_payout - total_bet
            desc += f"**Total: {net:+,} coins** (bet {total_bet:,}, won {total_payout:,})\n"
            broke_suffix = ""
            if profile.coins <= 0:
                broke_suffix = "\n-# debt allowed — you can still gamble up to **100** coins"
            desc += f"new balance: **{profile.coins:,}** coins{broke_suffix}"

            # Spin Again is only enabled if the player can still afford the
            # same bet (factoring the debt rule).
            can_repeat = total_bet <= max(profile.coins, 100)
            view = post_spin_view(can_repeat=can_repeat)

            embed = discord.Embed(
                title="🐈 Cat Slots — " + ("BIG WIN!" if big_win else ("winner" if total_payout > 0 else "womp womp")),
                description=desc,
                color=Colors.maroon,
            )
            try:
                await interaction.edit_original_response(embed=embed, view=view)
            except Exception:
                await interaction.followup.send(embed=embed, view=view)

            # ---- aches + quest progress (post-render) ----
            if total_payout > 0:
                try:
                    await achemb(interaction, "win_catslots", "followup")
                except Exception:
                    pass
                try:
                    await progress(message, profile, "catslots_win")
                except Exception:
                    pass
            if big_win:
                try:
                    await achemb(interaction, "big_win_catslots", "followup")
                except Exception:
                    pass

            # ====================================================
            # eGirl Party Bonus Round
            # ====================================================
            # Count eGirls in the settled grid. 3+ triggers the bonus round
            # AFTER the regular result has already been shown + credited.
            # The bonus is purely additive and uses its own catslots_bonus_*
            # counters, so the base-game leaderboard rankings stay stable.
            trigger_egirls = sum(
                1 for r in range(3) for c in range(5) if grid[r][c] == "eGirl"
            )
            if trigger_egirls >= 3:
                # Snapshot the trigger grid for sticky-mask init.
                trigger_grid = [row[:] for row in grid]
                tier_key = min(5, trigger_egirls)
                cfg = CATSLOTS_BONUS_TRIGGERS[tier_key]
                free_spins_initial = int(cfg["spins"])
                bonus_mult = float(cfg["multiplier"])

                # ---- opening animation: letter-by-letter EGIRL BONUS reveal ----
                async def _bonus_frame(title: str, desc: str, color: int, delay: float) -> None:
                    try:
                        await interaction.edit_original_response(
                            embed=discord.Embed(title=title, description=desc, color=color),
                            view=None,
                        )
                    except Exception:
                        pass
                    await asyncio.sleep(delay)

                # Stage 1 — sparkle anticipation (3 frames)
                sparkle_title = "🎰 The Slot Machine"
                for sparkles in ("✨", "✨    ✨", "✨    ✨    ✨"):
                    await _bonus_frame(
                        sparkle_title, sparkles,
                        CATSLOTS_BONUS_COLOR_OPENING, BONUS_INTRO_SPARKLE_DELAY,
                    )

                # Stage 2 — EGIRL letter by letter
                egirl_letters = "EGIRL"
                for i, letter in enumerate(egirl_letters, start=1):
                    revealed = " ".join(egirl_letters[:i])
                    await _bonus_frame(
                        f"🎉  {revealed}  🎉",
                        _catslots_render_letter(letter),
                        CATSLOTS_BONUS_COLOR_OPENING,
                        BONUS_INTRO_LETTER_DELAY,
                    )

                # Stage 3 — EGIRL pause
                await _bonus_frame(
                    "🎉🎉🎉  E G I R L  🎉🎉🎉",
                    "✨ ✨ ✨ ✨ ✨ ✨ ✨ ✨",
                    CATSLOTS_BONUS_COLOR_OPENING,
                    BONUS_INTRO_PAUSE_DELAY,
                )

                # Stage 4 — BONUS letter by letter
                bonus_letters = "BONUS"
                for i, letter in enumerate(bonus_letters, start=1):
                    revealed = " ".join(bonus_letters[:i])
                    await _bonus_frame(
                        f"🎉  E G I R L  ·  {revealed}  🎉",
                        _catslots_render_letter(letter),
                        CATSLOTS_BONUS_COLOR_PARTY,
                        BONUS_INTRO_LETTER_DELAY,
                    )

                # Stage 5 — stats reveal
                reveal_desc = (
                    "✨✨✨✨✨✨✨✨\n"
                    "\n"
                    f"🎰  **FREE SPINS:**  {free_spins_initial}\n"
                    f"⚡  **MULTIPLIER:**   {bonus_mult:g}×\n"
                    f"🐱  **STICKY EGIRLS:** {trigger_egirls}\n"
                    "\n"
                    "✨✨✨✨✨✨✨✨"
                )
                await _bonus_frame(
                    "🎉🎉🎉  EGIRL BONUS  🎉🎉🎉",
                    reveal_desc,
                    CATSLOTS_BONUS_COLOR_PARTY,
                    BONUS_INTRO_REVEAL_DELAY,
                )

                # Stage 6 — starting
                await _bonus_frame(
                    "🎉  PARTY STARTING  🎉",
                    "🌟 GET READY 🌟",
                    CATSLOTS_BONUS_COLOR_PARTY,
                    BONUS_INTRO_STARTING_DELAY,
                )

                # sticky_mask[col][row]; True = locked to eGirl going forward.
                sticky_mask = [[trigger_grid[r][c] == "eGirl" for r in range(3)] for c in range(5)]

                bonus_total = 0
                biggest_hit = 0
                spins_played = 0
                retriggers = 0
                remaining = free_spins_initial

                def total_announced_spins() -> int:
                    # Includes retriggers earned so far. Title formula —
                    # purely cosmetic for the "SPIN x/y" header.
                    return free_spins_initial + retriggers * CATSLOTS_BONUS_RETRIGGER_REWARD

                def render_bonus_grid(b_cols: list[list[str]], currents: list[int]) -> str:
                    lines_out = []
                    for offset in [-1, 0, 1]:
                        row_idx = offset + 1  # -1→0 top, 0→1 mid, +1→2 bot
                        cells = []
                        for c in range(5):
                            if sticky_mask[c][row_idx]:
                                sym = "eGirl"
                            else:
                                sym = b_cols[c][currents[c] + offset]
                            cells.append(get_emoji(sym.lower() + "cat") or sym)
                        if offset == 0:
                            lines_out.append("➡️ " + " ".join(cells) + " ⬅️")
                        else:
                            lines_out.append(f"{blank_emoji} " + " ".join(cells) + f" {blank_emoji}")
                    return "\n".join(lines_out)

                # ---- run free spins (loop until remaining hits 0) ----
                while remaining > 0:
                    spins_played += 1
                    remaining -= 1
                    pre_sticky = [row[:] for row in sticky_mask]

                    b_durations = [random.randint(6, 10) for _ in range(5)]
                    b_cols = [
                        random.choices(CATSLOTS_SYMBOLS, weights=CATSLOTS_WEIGHTS, k=d)
                        for d in b_durations
                    ]
                    # Lock sticky cells to eGirl at the FINAL position so the
                    # settled grid sees them; the renderer also short-circuits
                    # sticky cells to eGirl during animation frames.
                    for c in range(5):
                        cur = len(b_cols[c]) - 2
                        for r in range(3):
                            if sticky_mask[c][r]:
                                b_cols[c][cur + (r - 1)] = "eGirl"

                    # Animation frames — shorter than base game.
                    for sli in range(1, max(b_durations) - 1):
                        currents = [min(len(c) - 2, sli) for c in b_cols]
                        spin_desc = render_bonus_grid(b_cols, currents)
                        try:
                            await interaction.edit_original_response(
                                embed=discord.Embed(
                                    title=f"🎉 EGIRL PARTY - SPIN {spins_played}/{total_announced_spins()} 🎉",
                                    description=spin_desc,
                                    color=CATSLOTS_BONUS_COLOR_PARTY,
                                ),
                                view=None,
                            )
                        except Exception:
                            pass
                        await asyncio.sleep(0.1)

                    # Settle bonus grid.
                    b_finals = [len(c) - 2 for c in b_cols]
                    b_grid = [
                        [b_cols[c][b_finals[c] + (r - 1)] for c in range(5)]
                        for r in range(3)
                    ]

                    # Wild-substitution evaluation per active line.
                    # Bonus eval (third retune 2026-05-22): straight-match,
                    # same rule as the base game. eGirls no longer substitute
                    # as wilds — the prior wild-sub rule was the main driver
                    # behind 190%+ total RTP. Stickies still freeze in place
                    # so they contribute to eGirl 3/4/5-OAK lines when they
                    # happen to be the leading run of a payline.
                    spin_payout = 0
                    spin_wins = []
                    for i, line in enumerate(CATSLOTS_PAYLINES[:lines_n], start=1):
                        syms = [b_grid[r][c] for (c, r) in line]
                        first = syms[0]
                        count = 1
                        for s in syms[1:]:
                            if s == first:
                                count += 1
                            else:
                                break
                        if count >= 3:
                            mult = CATSLOTS_PAYOUTS.get(first, {}).get(count, 0)
                            line_payout = int(round(mult * per_line * bonus_mult))
                            if line_payout > 0:
                                spin_wins.append((i, first, count, line_payout))
                                spin_payout += line_payout

                    bonus_total += spin_payout
                    if spin_payout > biggest_hit:
                        biggest_hit = spin_payout

                    # FIX 1 (emergency retune 2026-05-22): sticky_mask is
                    # FROZEN at trigger time. Newly-landed eGirls still
                    # substitute as wilds for this spin (already evaluated
                    # above) but do NOT lock for future spins. This is the
                    # primary fix for the runaway-payout regression.
                    # Retrigger detection still uses newly-landed count.
                    new_egirls = 0
                    for c in range(5):
                        for r in range(3):
                            if b_grid[r][c] == "eGirl" and not pre_sticky[c][r]:
                                new_egirls += 1

                    retrigger_fired = new_egirls >= CATSLOTS_BONUS_RETRIGGER_THRESHOLD
                    if retrigger_fired:
                        remaining += CATSLOTS_BONUS_RETRIGGER_REWARD
                        retriggers += 1

                    # Render settled spin with payout summary.
                    sticky_count = sum(1 for c in range(5) for r in range(3) if sticky_mask[c][r])
                    settled_desc = render_bonus_grid(b_cols, b_finals) + "\n"
                    settled_desc += f"✨ Sticky eGirls: {sticky_count}/15\n\n"
                    if spin_wins:
                        settled_desc += "__Line payouts:__\n"
                        for line_idx, base, length, lp in spin_wins:
                            base_emoji = get_emoji(base.lower() + "cat") or base
                            settled_desc += f"✨ **Line {line_idx}**: {length}× {base_emoji} {base} — **{lp:,}** coins\n"
                        settled_desc += "\n"
                    settled_desc += f"**This spin:** +{spin_payout:,}\n"
                    settled_desc += f"**Bonus total:** {bonus_total:,}\n"
                    if retrigger_fired:
                        settled_desc += f"\n🎉 **Retrigger!** +{CATSLOTS_BONUS_RETRIGGER_REWARD} spins!\n"
                    settled_desc += f"\nMultiplier: {bonus_mult:g}× | Remaining: {remaining}"
                    try:
                        await interaction.edit_original_response(
                            embed=discord.Embed(
                                title=f"🎉 EGIRL PARTY - SPIN {spins_played}/{total_announced_spins()} 🎉",
                                description=settled_desc,
                                color=CATSLOTS_BONUS_COLOR_PARTY,
                            ),
                            view=None,
                        )
                    except Exception:
                        pass
                    await asyncio.sleep(1.0)

                # ---- apply bonus floor (minimum guaranteed payout) ----
                bonus_floor = CATSLOTS_BONUS_FLOORS.get(tier_key, 0) * total_bet
                floor_topup = 0
                if bonus_total < bonus_floor:
                    floor_topup = bonus_floor - bonus_total
                    bonus_total = bonus_floor

                # ---- credit bonus + persist counters ----
                await profile.refresh_from_db()
                profile.coins += bonus_total
                _bump(profile, "coins_earned", bonus_total)
                profile.catslots_bonus_triggers += 1
                profile.catslots_bonus_coins_won += bonus_total
                profile.catslots_bonus_spins_total += spins_played
                await profile.save()

                # ---- breakdown animation: counter tick-up ----
                if bonus_total > 0:
                    tick_fractions = [0.05, 0.15, 0.35, 0.60, 0.85, 1.0]
                    for frac in tick_fractions:
                        tick = int(bonus_total * frac)
                        if frac == 1.0:
                            tick = bonus_total  # exact landing
                        try:
                            await interaction.edit_original_response(
                                embed=discord.Embed(
                                    title="🎊🎊 EGIRL PARTY OVER 🎊🎊",
                                    description=f"💰 **{tick:,}** coins...",
                                    color=CATSLOTS_BONUS_COLOR_OPENING,
                                ),
                                view=None,
                            )
                        except Exception:
                            pass
                        await asyncio.sleep(0.3)

                # ---- summary frame ----
                sticky_at_end = sum(1 for c in range(5) for r in range(3) if sticky_mask[c][r])
                summary_desc = (
                    "**WOO WOO!**\n\n"
                    f"🎰 Bonus spins played: {spins_played}\n"
                    f"✨ Best single hit: {biggest_hit:,} coins\n"
                    f"🐱 Sticky eGirls at end: {sticky_at_end}\n"
                    f"🎉 Retriggers: {retriggers}\n"
                )
                if floor_topup > 0:
                    summary_desc += (
                        f"🛡️ Bonus floor: **+{floor_topup:,}** coins "
                        f"(guaranteed {CATSLOTS_BONUS_FLOORS[tier_key]}× bet minimum)\n"
                    )
                summary_desc += f"💰 **TOTAL BONUS WON: {bonus_total:,} coins**"
                try:
                    await interaction.edit_original_response(
                        embed=discord.Embed(
                            title="🎊🎊 EGIRL PARTY OVER 🎊🎊",
                            description=summary_desc,
                            color=CATSLOTS_BONUS_COLOR_OPENING,
                        ),
                        view=None,
                    )
                except Exception:
                    pass
                await asyncio.sleep(3.0)

                # ---- bonus aches ----
                try:
                    await achemb(interaction, "egirl_party", "followup")
                    if trigger_egirls >= 5:
                        await achemb(interaction, "egirl_party_max", "followup")
                except Exception:
                    logging.exception("catslots: bonus ach wiring failed")

                # ---- final composite render: regular result + bonus tail + Spin Again ----
                composite_desc = bet_header + final_grid_str + "\n\n"
                if big_win:
                    composite_desc = "🎰 **BIG WIN!**\n\n" + composite_desc
                elif total_payout > 0:
                    composite_desc = "✅ **You win!**\n\n" + composite_desc
                else:
                    composite_desc = "💨 **You lose!**\n\n" + composite_desc

                if wins:
                    composite_desc += "__Winning lines:__\n"
                    for line_idx, sym, count, lp in wins:
                        ge = get_emoji(sym.lower() + "cat") or sym
                        composite_desc += f"✨ **Line {line_idx}**: {count}× {ge} {sym} — **{lp:,}** coins\n"
                    composite_desc += "\n"

                composite_desc += (
                    f"**Base game:** bet {total_bet:,}, won {total_payout:,}\n"
                    f"🎉 **Bonus won: {bonus_total:,} coins** "
                    f"({spins_played} spins @ {bonus_mult:g}×)\n"
                    f"**Grand total:** +{total_payout + bonus_total - total_bet:+,} coins\n"
                )
                broke_suffix_composite = ""
                if profile.coins <= 0:
                    broke_suffix_composite = "\n-# debt allowed — you can still gamble up to **100** coins"
                composite_desc += f"new balance: **{profile.coins:,}** coins{broke_suffix_composite}"

                can_repeat_composite = total_bet <= max(profile.coins, 100)
                composite_view = post_spin_view(can_repeat=can_repeat_composite)
                try:
                    await interaction.edit_original_response(
                        embed=discord.Embed(
                            title="🐈 Cat Slots — eGirl Party result",
                            description=composite_desc,
                            color=CATSLOTS_BONUS_COLOR_OPENING,
                        ),
                        view=composite_view,
                    )
                except Exception:
                    try:
                        await interaction.followup.send(
                            embed=discord.Embed(
                                title="🐈 Cat Slots — eGirl Party result",
                                description=composite_desc,
                                color=CATSLOTS_BONUS_COLOR_OPENING,
                            ),
                            view=composite_view,
                        )
                    except Exception:
                        logging.exception("catslots: final composite render failed")
        finally:
            try:
                catslots_lock.remove(lock_key)
            except ValueError:
                pass

    async def on_spin_again(interaction: discord.Interaction):
        if interaction.user != message.user:
            await do_funny(interaction)
            return

        # Defer BEFORE the DB query — discord's interaction-response window
        # is only 3 seconds. A slow DB call (or a gateway blip) used to
        # expire the token before defer was even called, raising 404
        # Unknown interaction. If the click is already stale (bot was
        # offline when the user pressed it), bail quietly.
        try:
            await interaction.response.defer()
        except discord.NotFound:
            return

        last = catslots_last_bet.get(lock_key)
        if not last:
            # Defensive — shouldn't happen since the button only appears after
            # a recorded spin, but reload restart clears the dict.
            await interaction.followup.send(
                "no previous bet on record — use Change Bet to set one.", ephemeral=True
            )
            return
        lines_n, per_line = last
        total_bet = lines_n * per_line

        # ---- concurrency check ----
        if lock_key in catslots_lock:
            await interaction.followup.send(
                "you're already spinning /catslots — wait for the reels to settle, time-traveler",
                ephemeral=True,
            )
            await achemb(interaction, "paradoxical_catslots", "followup")
            return

        # ---- affordability re-check (coins may have moved between clicks) ----
        await profile.refresh_from_db()
        max_bet = max(profile.coins, 100)
        if total_bet > max_bet:
            await interaction.followup.send(
                "you can't afford the same bet anymore — try a smaller one",
                ephemeral=True,
            )
            # Refresh the result message so Spin Again is disabled until they
            # adjust the bet.
            try:
                await interaction.message.edit(view=post_spin_view(can_repeat=False))
            except Exception:
                pass
            return

        await _do_spin(interaction, lines_n, per_line)

    async def on_open_modal(interaction: discord.Interaction):
        if interaction.user != message.user:
            await do_funny(interaction)
            return
        prefill = catslots_last_bet.get(lock_key)
        await interaction.response.send_modal(CatSlotsModal(prefill=prefill))

    # Slash-param fast path. Both `lines` and `bet` must be supplied to skip
    # the lobby + modal. Choice and Range have already validated value bounds
    # at the Discord layer, so all that's left is affordability and a final
    # concurrency re-check (the entry-point lock check is too early — coins
    # could have moved between command invocation and now). When only one of
    # the two is supplied we silently fall through to the lobby; the modal
    # presents both fields anyway, so the partial-input case is recoverable
    # without a separate hint message.
    if lines is not None and bet is not None:
        await profile.refresh_from_db()
        lines_n = int(lines.value)
        per_line = int(bet)
        total_bet = lines_n * per_line
        max_bet = max(profile.coins, 100)
        if total_bet > max_bet:
            await message.response.send_message(
                f"your total bet ({total_bet:,}) exceeds your max ({max_bet:,}). "
                f"debt is allowed up to 100 coins.",
                ephemeral=True,
            )
            return
        if lock_key in catslots_lock:
            await message.response.send_message(
                "you're already spinning /catslots — wait for the reels to settle, time-traveler",
                ephemeral=True,
            )
            await achemb(message, "paradoxical_catslots", "followup")
            return
        await message.response.defer()
        await _do_spin(message, lines_n, per_line)
        return

    bet_btn = Button(label="Place Bet", style=ButtonStyle.green)
    bet_btn.callback = on_open_modal
    view = View(timeout=VIEW_TIMEOUT)
    view.add_item(bet_btn)

    await message.response.send_message(embed=stats_embed(profile), view=view)


@bot.tree.command(description="(ADMIN) Force next /catslots spin to trigger the eGirl bonus")
@discord.app_commands.default_permissions(manage_guild=True)
@discord.app_commands.describe(
    egirls="How many eGirls to force on the next spin (3, 4, or 5)",
    user="Whose next /catslots spin to force (default: yourself)",
)
async def catslots_force_bonus(
    message: discord.Interaction,
    egirls: Optional[int] = 3,
    user: Optional[discord.Member] = None,
):
    if egirls not in (3, 4, 5):
        await message.response.send_message(
            "egirls must be 3, 4, or 5.", ephemeral=True
        )
        return
    target = user or message.user
    catslots_force_bonus_users[target.id + message.guild.id] = int(egirls)
    if user is None:
        reply = f"✅ Your next /catslots spin will trigger a {egirls}-eGirl bonus round."
    else:
        reply = (
            f"✅ {user.mention}'s next /catslots spin will trigger a "
            f"{egirls}-eGirl bonus round."
        )
    await message.response.send_message(reply, ephemeral=True)


@bot.tree.command(description="bet on red, black, green, or a number 0–36")
@discord.app_commands.describe(
    color="red / black / green. omit if betting on a number.",
    number="any number 0–36. omit if betting on a color.",
    bet="coins to wager. omit to use the menu.",
)
@discord.app_commands.choices(color=[
    discord.app_commands.Choice(name="🔴 red",   value="red"),
    discord.app_commands.Choice(name="⚫ black", value="black"),
    discord.app_commands.Choice(name="🟢 green", value="green"),
])
async def roulette(
    message: discord.Interaction,
    color: Optional[discord.app_commands.Choice[str]] = None,
    number: Optional[discord.app_commands.Range[int, 0, 36]] = None,
    bet: Optional[discord.app_commands.Range[int, 1, 2147483647]] = None,
):
    user = await Profile.get_or_create(guild_id=message.guild.id, user_id=message.user.id)

    # Shared spin runner. Called by both the modal-submit path (after its
    # input validation passes) and the slash-param fast path. Takes the
    # already-validated bet target (a string: "red"/"black"/"green" or
    # "0".."36") and the bet amount. Defers the interaction first thing —
    # callers must NOT have already responded.
    async def _do_roulette_spin(interaction: discord.Interaction, bet_value: str, bet_amount: int):
        # Defer BEFORE the DB query — discord's interaction-response window
        # is only 3 seconds. A slow DB call (or a gateway blip) used to
        # expire the token before defer was even called, raising 404 Unknown
        # interaction. Lock in the window first, then do everything that
        # touches I/O.
        await interaction.response.defer()
        await user.refresh_from_db()

        # Affordability check needs the fresh balance, so it runs as a
        # followup after defer rather than as the initial response.
        if bet_amount > max(user.coins, 100):
            await interaction.followup.send(
                f"your max bet is {max(user.coins, 100):,}", ephemeral=True
            )
            return

        # mapping of colors to numbers by indexes
        colors = [
            "green",
            "red", "black", "red", "black", "red", "black", "red", "black",
            "red", "black", "black", "red", "black", "red", "black", "red",
            "black", "red", "red", "black", "red", "black", "red", "black",
            "red", "black", "red", "black", "black", "red", "black", "red",
            "black", "red", "black", "red",
        ]

        emoji_map = {
            "red": "🔴",
            "black": "⚫",
            "green": "🟢",
        }

        # ---- Roulette job-perks ----
        # roulette_luck: bias final_choice toward a winning slot.
        # roulette_mercy: refund a fraction of losing bets.
        # free_spin:      one-shot — losing bets fully refunded.
        roulette_perks_msgs: list[str] = []
        luck_pp = float(_perks_strength(user, "roulette_luck", "bonus_pp", 0.0)) \
            if "roulette_luck" in _perks_active_ids(user) else 0.0
        mercy_pct = float(_perks_strength(user, "roulette_mercy", "refund_pct", 0.0)) \
            if "roulette_mercy" in _perks_active_ids(user) else 0.0
        free_spin_fired = False
        if "free_spin" in _perks_active_ids(user):
            _fs_cap = int(_perks_strength(user, "free_spin", "max_bet", 1000) or 0)
            if bet_amount <= _fs_cap and _perks_consume_charge(user, "free_spin"):
                free_spin_fired = True

        def _is_winning_slot(idx: int, bv_inner: str) -> bool:
            bv = bv_inner.lower()
            if bv in [str(i) for i in range(37)]:
                return str(idx) == bv
            if bv == "green":
                return colors[idx] == "green"
            if bv == "red":
                return colors[idx] == "red"
            if bv == "black":
                return colors[idx] == "black"
            return False

        if luck_pp > 0 and random.random() < luck_pp:
            winning_slots = [i for i in range(37) if _is_winning_slot(i, bet_value)]
            if winning_slots:
                final_choice = random.choice(winning_slots)
                roulette_perks_msgs.append("🍀 Loaded Wheel: the croupier owed you a favor.")
            else:
                final_choice = random.randint(0, 36)
        else:
            final_choice = random.randint(0, 36)

        user.coins -= bet_amount
        _bump(user, "roulette_coins_bet", bet_amount)
        user.roulette_spins += 1
        win = False
        funny_win = False
        if str(final_choice) == bet_value or colors[final_choice] == bet_value.lower():
            if bet_value in [str(i) for i in range(37)] or bet_value.lower() == "green":
                user.coins += bet_amount * 36
                _bump(user, "roulette_coins_won", bet_amount * 36)
                _bump(user, "coins_earned", bet_amount * 36)
                funny_win = True
            else:
                user.coins += bet_amount * 2
                _bump(user, "roulette_coins_won", bet_amount * 2)
                _bump(user, "coins_earned", bet_amount * 2)
            user.roulette_wins += 1
            win = True
        # Loss-side perks: refund some/all of the bet.
        if not win:
            if free_spin_fired:
                user.coins += bet_amount
                _bump(user, "roulette_coins_won", bet_amount)
                roulette_perks_msgs.append(f"🎟️ Free Spin: full {bet_amount:,} coin refund.")
            elif mercy_pct > 0:
                refund = int(round(bet_amount * mercy_pct))
                if refund > 0:
                    user.coins += refund
                    _bump(user, "roulette_coins_won", refund)
                    roulette_perks_msgs.append(f"🤝 House Mercy: refunded 🪙 {refund:,}.")
        user.coins = int(round(user.coins))
        await user.save()

        for wait_time in [0.025, 0.05, 0.075, 0.1, 0.125, 0.15, 0.175, 0.2, 0.225, 0.25, 0.275, 0.3, 0.375]:
            choice = random.randint(0, 36)
            color = colors[choice]
            embed = discord.Embed(
                color=Colors.maroon,
                title="woo its spinnin",
                description=f"your bet is {bet_amount:,} coins on {bet_value.capitalize()}\n\n{emoji_map[color]} **{choice}**",
            )
            await interaction.edit_original_response(embed=embed, view=None)
            await asyncio.sleep(wait_time)

        color = colors[final_choice]

        broke_suffix = ""
        if user.coins <= 0:
            broke_suffix = "\ndebt is allowed - you can still gamble up to **100** coins"

        perk_suffix = ("\n\n" + "\n".join(roulette_perks_msgs)) if roulette_perks_msgs else ""
        embed = discord.Embed(
            color=Colors.maroon,
            title="winner!!!" if win else "womp womp",
            description=(
                f"your bet was {bet_amount:,} coins on {bet_value.capitalize()}\n\n"
                f"{emoji_map[color]} **{final_choice}**\n\n"
                f"your new balance is **{user.coins:,}** coins{broke_suffix}{perk_suffix}"
            ),
        )
        view = View(timeout=VIEW_TIMEOUT)
        b = Button(label="spin", style=ButtonStyle.blurple)
        b.callback = modal_select
        view.add_item(b)
        await interaction.edit_original_response(embed=embed, view=view)

        if win:
            await progress(message, user, "roulette")
            await progress(message, user, "roulette3")
            await achemb(interaction, "roulette_winner", "followup")
        # casino quest counts every roulette spin (win or lose)
        await progress_casino_quest(message, user, "roulette")
        if funny_win:
            await achemb(interaction, "roulette_prodigy", "followup")
        if user.coins < 0:
            await achemb(interaction, "failed_gambler", "followup")

    # The lobby button. Reads bettype/betamount via the modal, then delegates
    # the actual spin to _do_roulette_spin.
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
            # Cheap, input-only validation goes BEFORE defer — these can
            # use interaction.response.send_message to bail with an
            # ephemeral error without consuming a defer.
            valids = ["red", "black", "green"] + [str(i) for i in range(37)]
            if self.bettype.value.lower() not in valids:
                await interaction.response.send_message("invalid bet", ephemeral=True)
                return
            try:
                bet_amount = int(self.betamount.value)
            except ValueError:
                await interaction.response.send_message("invalid bet amount", ephemeral=True)
                return
            if bet_amount <= 0:
                await interaction.response.send_message("bet amount must be greater than 0", ephemeral=True)
                return
            await _do_roulette_spin(interaction, self.bettype.value, bet_amount)

    async def modal_select(interaction: discord.Interaction):
        if interaction.user != message.user:
            await do_funny(interaction)
            return

        await interaction.response.send_modal(RouletteModel())

    # Slash-param fast path. The user must supply exactly one of color/number
    # plus a bet amount. Both/neither falls through to the lobby (with an
    # ephemeral error when both are supplied — that's user intent we should
    # correct, not silently coerce). Partial input (target without bet, or
    # bet without target) silently opens the lobby; the modal collects what
    # was missing.
    if color is not None and number is not None:
        await message.response.send_message(
            "Pick **one** of `color` or `number`, not both.", ephemeral=True
        )
        return
    target_given = color is not None or number is not None
    if target_given and bet is not None:
        bet_value = color.value if color is not None else str(int(number))
        await _do_roulette_spin(message, bet_value, int(bet))
        return

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


@roulette.autocomplete("number")
async def roulette_number_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete the `number` param with 0..36. Discord caps suggestions
    at 25 per response, so on empty input we surface 0..24 and rely on the
    user typing a digit to filter into the high teens / twenties / thirties.
    The actual Range[0, 36] validator catches anything out of bounds at the
    server side, so this callback is a UX hint, not a constraint."""
    pool = list(range(37))
    current = (current or "").strip()
    if current:
        matches = [n for n in pool if str(n).startswith(current)]
    else:
        matches = pool
    return [discord.app_commands.Choice(name=str(n), value=n) for n in matches[:25]]


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
        rolled = random.randint(1, sides)
        await message.response.send_message(f"🎲 your {dice} lands on **{rolled}**")
        if sides == 6 and rolled == 6:
            await progress(message, user, "roll6")
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
        if score >= 50:
            await progress(message, profile, "pig50")
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


@bot.tree.command(name="random", description="Get a random cat")
async def random_cat(message: discord.Interaction):
    await message.response.defer()
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                "https://api.thecatapi.com/v1/images/search", headers={"User-Agent": "CatBot/1.0 https://github.com/sneezeparty/catbot7"}
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
                    headers={"User-Agent": "CatBot/1.0 https://github.com/sneezeparty/catbot7"},
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
    # bounty_boost (job perk, timed): probabilistic extra tick on each
    # progress fire. Multiplier 1.5 → 50% chance of +1 extra per tick.
    _bounty_boost_extra_p = 0.0
    if "bounty_boost" in _perks_active_ids(user):
        _bb_mult = float(_perks_strength(user, "bounty_boost", "multiplier", 1.5) or 1.5)
        _bounty_boost_extra_p = max(0.0, _bb_mult - 1.0)
    def _bb_extra() -> int:
        return 1 if (_bounty_boost_extra_p > 0 and random.random() < _bounty_boost_extra_p) else 0
    # Bounty Skip voucher 🎟️ (Mystery reward): autocompletes the first
    # incomplete bounty slot this catch touches, whatever cat was caught.
    # Held ("banked") for free while no catnip run is active — the loop
    # below simply doesn't run then. The bonus bounty is never skipped.
    bounty_skip_pending = _vouchers_has(user, "bounty_skip")
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
        if progress < total and bounty_skip_pending and _vouchers_consume(user, "bounty_skip"):
            bounty_skip_pending = False
            progress = total
            complete += 1
            if id == 1:
                title.append(f"Catch {total} {type} cats 🎟️")
            elif id == 2:
                title.append(f"Catch {total} {type} or rarer cats 🎟️")
            else:
                title.append(f"Catch {total} cats 🎟️")
        if progress < total:
            if id == 0:
                progress = min(total, progress + 1 + _bb_extra())
                if progress == total:
                    complete += 1
                    title.append(f"Catch {total} cats")
            if id == 1:
                if cattype == type:
                    progress = min(total, progress + 1 + _bb_extra())
                    if progress == total:
                        complete += 1
                        title.append(f"Catch {total} {type} cats")
            if id == 2:
                if cattypes.index(cattype) >= cattypes.index(type):
                    progress = min(total, progress + 1 + _bb_extra())
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
                user.bounty_progress_bonus = min(user.bounty_total_bonus, user.bounty_progress_bonus + 1 + _bb_extra())
                bonus_title = f"Catch {user.bounty_total_bonus} cats"
            elif user.bounty_id_bonus == 1:
                if cattype == user.bounty_type_bonus:
                    user.bounty_progress_bonus = min(user.bounty_total_bonus, user.bounty_progress_bonus + 1 + _bb_extra())
                bonus_title = f"Catch {user.bounty_total_bonus} {cattype} cats"
            else:
                if cattypes.index(cattype) >= cattypes.index(user.bounty_type_bonus):
                    user.bounty_progress_bonus = min(user.bounty_total_bonus, user.bounty_progress_bonus + 1 + _bb_extra())
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
            await achemb(message, "bounty_novice", "send")
        if user.bounties_complete >= 19:  # we do a little trolling (???)
            await achemb(message, "bounty_hunter", "send")
        if user.bounties_complete >= 100:
            await achemb(message, "bounty_lord", "send")
        await message.channel.send(f"<@{user.user_id}>", embed=embed)
        await user.save()


async def set_mafia_offer(level, user):
    if user.catnip_level == 0:
        user.catnip_amount = 0
        return
    level_data = catnip_list["levels"][level]
    vt = level_data["cost"]
    cattype = "Fine"
    eligible_cattypes = _quest_eligible_cattypes()
    for _ in range(100):
        cattype = random.choice(eligible_cattypes)
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
        if bounty_type == "rarity":
            margin = 0.2
            # Threshold rarity is constrained to quest-eligible (excludes
            # brand-new-this-season rarities). Satisfaction set uses full
            # cattypes order, so rarer cats — including brand-new ones —
            # still count as "X or above" satisfiers when caught.
            quest_eligible = set(_quest_eligible_cattypes())
            rarity_i = random.randint(2, len(cattypes) - 2)

            while True:
                rarity = cattypes[rarity_i]
                if rarity not in quest_eligible:
                    rarity_i -= 1
                    if rarity_i < 0:
                        break
                    continue
                eligible_types = cattypes[rarity_i:]

                prob = sum(type_dict[t] for t in eligible_types) / sum(type_dict.values())
                base_amount = max(1, round(avg_cats_needed * prob))
                expected_total = base_amount / prob if prob > 0 else float("inf")

                if abs(expected_total - avg_cats_needed) / avg_cats_needed <= margin or rarity_i == 0:
                    break
                rarity_i -= 1

            if rarity_i < 0 or rarity_i in used_rarities:
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
            # pick a specific cat type not already used; constrain to
            # quest-eligible so brand-new-this-season rarities are skipped
            available_types = [cat for cat in _quest_eligible_cattypes() if cat not in used_types]
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

    # Trigger any pending season rollover (and the wipe it applies) before
    # rendering anything, then show the one-shot ephemeral reset notice if
    # this is the first slash command after a rollover.
    await refresh_quests(user)
    await _maybe_show_season_reset_notice(message, user)

    if not server.do_catnip:
        await message.followup.send("catnip is disabled in this server.", ephemeral=True)
        return

    if not user.dark_market_active:
        await message.followup.send("You don't have access to the catnip yet. Catch more cats to unlock it!")
        return

    # Settle respect decay before rendering. A level loss here would have
    # already been shown the next time the player opens /jobs; we still
    # surface a one-shot notice so /catnip-only players see it too.
    _respect_lost_at_catnip = _respect_settle(user, int(time.time()))
    if _respect_lost_at_catnip > 0:
        await user.save()
        await message.followup.send(
            f"💀 You lost {_respect_lost_at_catnip} catnip level"
            f"{'s' if _respect_lost_at_catnip > 1 else ''} to mafia decay. "
            "Commit jobs to rebuild Respect.",
            ephemeral=True,
        )

    if user.catnip_active < time.time() and not user.hibernation and user.catnip_level > 0:
        if _job_grace_active(user):
            # Bounty timer lapsed, but a recent /jobs commit is shielding the
            # level. Don't drop it — surface a one-shot heads-up instead. The
            # lapsed timer/bounties are left as-is; once the grace window passes
            # with no new job, the next /catnip drops the level as usual.
            safe_until = _safe_last_job_time(user) + CATNIP_JOB_GRACE_SECONDS
            await message.followup.send(
                f"{get_emoji('catnip')} Your bounty timer lapsed, but a recent job is "
                f"protecting your mafia level — safe until <t:{safe_until}:R>. "
                "Do another job before then to stay safe.",
                ephemeral=True,
            )
        else:
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
        if int(getattr(user, "perks_suspended_until", 0) or 0) > int(time.time()):
            full_desc = f"🚓 The Cat Police have your perks. They come back <t:{int(user.perks_suspended_until)}:R>.\n\n" + full_desc
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

        # Job-grace status — doing /jobs shields your level from the catnip
        # timer for 24h, so you can engage catnip less often.
        if user.catnip_level > 0:
            if _job_grace_active(user):
                safe_until = _safe_last_job_time(user) + CATNIP_JOB_GRACE_SECONDS
                desc += f"\n{get_emoji('catnip')} Mafia level protected by a recent job — safe until <t:{safe_until}:R>.\n"
            else:
                desc += "\n⚠️ Do a `/jobs` to protect your mafia level from the catnip timer.\n"
            if int(getattr(user, "perks_suspended_until", 0) or 0) > int(time.time()):
                desc += f"\n🚓 The Cat Police have your perks. They come back <t:{int(user.perks_suspended_until)}:R>.\n"

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
        if not level == 0 and not user.hibernation and user.catnip_active > time.time():
            if user.catnip_active - int(time.time()) < 1800:
                desc += f"\n\n**Hurry!** Levels down <t:{user.catnip_active}:R> ({duration}h total)"
            else:
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

        filename = "https://wsrv.nl/?url=raw.githubusercontent.com/sneezeparty/catbot7/refs/heads/main/" + filename

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
        if len(get_news()) > len(global_user.news_state.strip()) or "0" in global_user.news_state.strip()[-4:]:
            newembed.set_author(name="You have unread news! /news")

        for k, v in ach_list.items():
            if v["category"] == category:
                if k == "thanksforplaying":
                    if user.has_ach(k):
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
                if user.has_ach(k):
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
    leaderboard_type: Optional[Literal["Cats", "Value", "Fast", "Slow", "Cattlepass", "Cookies", "Pig", "Coins", "Prisms", "Mafia", "Heists", "Job Coins", "Biggest Score", "Mafia Favors", "Catslots"]],
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
        elif type == "Mafia Favors":
            # Lifetime distinct perk IDs ever granted to this profile.
            # Backed by profile.perks_received (JSONB list, deduplicated by
            # _perks_grant + _perks_resolve_immediate).
            unit = "perks"
            result = await Profile.collect_limit(
                ["user_id", RawSQL("jsonb_array_length(perks_received) AS perks_received_count")],
                "guild_id = $1 AND jsonb_array_length(perks_received) > 0 "
                "ORDER BY jsonb_array_length(perks_received) DESC",
                message.guild.id,
            )
            final_value = "perks_received_count"
        elif type == "Catslots":
            # Lifetime gross coins won at /catslots — mirrors the "Job Coins"
            # category. Net (won - bet) would expose the house edge and put
            # most players in the red, which isn't fun to rank by.
            unit = "coins won"
            result = await Profile.collect_limit(
                ["user_id", "catslots_coins_won"],
                "guild_id = $1 AND catslots_coins_won > 0 ORDER BY catslots_coins_won DESC",
                message.guild.id,
            )
            final_value = "catslots_coins_won"
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
                        lv_xp_req = EXTRA_LEVEL_XP
                    else:
                        lv_xp_req = bp_season[int(position[final_value]) - 1]["xp"]
                    interactor_perc = math.floor((100 / lv_xp_req) * position["progress"])
            if interaction.user != message.user and position["user_id"] == message.user.id:
                messager_placement = index + 1
                messager = position[final_value]
                if type == "Cattlepass":
                    if position[final_value] >= len(bp_season):
                        lv_xp_req = EXTRA_LEVEL_XP
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
                    lv_xp_req = EXTRA_LEVEL_XP
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

        if len(get_news()) > len(global_user.news_state.strip()) or "0" in global_user.news_state.strip()[-4:]:
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
            "Catslots": "🎰",
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
                rollback_hint = ""
                if config.SUPPORT_INVITE:
                    rollback_hint = f"\nfor a rollback, ping the operator at <{config.SUPPORT_INVITE}>"
                await interaction.edit_original_response(
                    content=f"Done! rip {person_id.mention}. f's in chat.{rollback_hint}", view=None
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

                rollback_hint = "."
                if config.SUPPORT_INVITE:
                    rollback_hint = f", contact the operator at <{config.SUPPORT_INVITE}>."
                try:
                    await interaction.edit_original_response(
                        content=f"Done. To roll this back{rollback_hint}",
                        view=None,
                    )
                except Exception:
                    await interaction.followup.send(f"Done. To roll this back{rollback_hint}")
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
                    "Thanks for voting! Your battlepass XP has been credited in every server you have a profile in — run `/battlepass` to see your new progress.",
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

    # Auto-grant vote XP across every profile this user has, so they don't
    # have to chase /battlepass server-by-server. progress("vote") is
    # idempotent via vote_cooldown — profiles already claimed this cycle
    # short-circuit; new profiles (created post-vote, within 12h) still get
    # picked up by the existing gen_main fallback when /battlepass runs.
    try:
        profiles = await Profile.collect("user_id = $1", user.user_id)
    except Exception:
        logging.exception("do_vote: failed to collect profiles for user %s", user.user_id)
        profiles = []
    for profile in profiles:
        try:
            await progress(None, profile, "vote")
        except Exception:
            logging.exception("do_vote: auto-grant failed for guild %s", profile.guild_id)


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
    bot2.on_entitlement_create = on_entitlement_create
    bot2.on_entitlement_update = on_entitlement_update
    bot2.on_entitlement_delete = on_entitlement_delete

    if config.WEBHOOK_VERIFY:
        # Port 8069 is exposed to the public internet (top.gg pushes votes
        # here), so aiohttp.access fills the terminal with 404s from random
        # port scanners. Mute the access channel below WARNING — 5xx still
        # surfaces, and recieve_vote success is observable via /vote and
        # /battlepass updates.
        logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
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

    # Same restart-safe pattern for the season-end warning ticker.
    old_season_task = getattr(config, "season_announce_task", None)
    if old_season_task and not old_season_task.done():
        old_season_task.cancel()
    config.season_announce_task = bot.loop.create_task(_season_announcement_loop())

    # Same restart-safe pattern for the activity-dashboard snapshot ticker.
    # Fire one tick immediately so a freshly-restarted bot doesn't wait 5
    # minutes for its first row; ON CONFLICT DO NOTHING makes it safe.
    old_metrics_task = getattr(config, "metrics_snapshot_task", None)
    if old_metrics_task and not old_metrics_task.done():
        old_metrics_task.cancel()
    config.metrics_snapshot_task = bot.loop.create_task(_metrics_snapshot_loop())
    bot.loop.create_task(_metrics_snapshot_tick())

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
