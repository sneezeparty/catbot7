"""Profile table (read-only) — per user-per-guild state.

Massive table (~315 columns). The detail view groups the gameplay-relevant
fields into readable sections and dumps the rest in an "all columns" details
block. Nothing here mutates the row.
"""

import aiohttp_jinja2
from aiohttp import web

from webui import names, state
from webui.pagination import make_pager, parse_page

PER_PAGE = 50

INT_FIELDS = [
    # Progression
    "battlepass",
    "progress",
    "season",
    # Quests
    "catch_progress",
    "catch_cooldown",
    "catch_reward",
    "misc_progress",
    "misc_cooldown",
    "misc_reward",
    "vote_reward",
    "vote_cooldown",
    "extra_progress",
    "extra_cooldown",
    "extra_reward",
    "challenge_progress",
    "challenge_cooldown",
    "challenge_reward",
    "reminder_challenge",
    # Streak / misc counters
    "catch_streak",
    # Rain / inventory
    "rain_minutes",
    "coins",
    "cookies",
    "coffees",
    "pack_attempts",
    # Packs
    "pack_wooden",
    "pack_stone",
    "pack_bronze",
    "pack_silver",
    "pack_gold",
    "pack_platinum",
    "pack_diamond",
    "pack_celestial",
    "pack_christmas",
    "pack_valentine",
    "pack_chef",
    "pack_birthday",
    # Catnip / mafia
    "catnip_active",
    "catnip_level",
    "catnip_total_cats",
    "catnip_amount",
    "combo_stack",
    # Bounties
    "bounty_id_one",
    "bounty_id_two",
    "bounty_id_three",
    "bounty_id_bonus",
    "bounty_progress_one",
    "bounty_progress_two",
    "bounty_progress_three",
    "bounty_progress_bonus",
    "bounty_total_one",
    "bounty_total_two",
    "bounty_total_three",
    "bounty_total_bonus",
    "reroll_level",
    # Jobs / Mafia Killings
    "heat",                   # current heat level; admin may reset to 0
    "last_job_time",          # unix ts of most recent committed /jobs (grace-period shield)
    "perks_suspended_until",  # unix ts; set to 0 to lift a Pinch early
    "big_score_season",       # season number of last Big Score attempt
    "big_score_wins",         # all-time Big Score successes
    "whiskers_favor_season",  # season number when Whiskers Favor was granted
    # Job-board reroll window counters (per-12h escalating price)
    "job_rerolls_window",      # number of paid rerolls used in the current window
    "job_rerolls_window_idx",  # bigint window index (same epoch-bucketing as jobs timer)
    # Jobs stat counters — useful for admin review but not typical edit targets
    "jobs_completed",
    "jobs_failed",
    "jobs_near_missed",
    "cats_lost_to_jobs",
    "job_coins_won",
    "biggest_score_value",
    # Season recap stat counters (bigint accumulators for the Season Recap leaderboard;
    # season_stat_baseline captures a snapshot at rollover so per-season deltas can be
    # computed as lifetime - baseline; admin may inspect but rarely needs to edit these)
    "coins_earned",
    "roulette_coins_won",
    "roulette_coins_bet",
    "stock_coins_earned",
    "stock_coins_spent",
    # Cat counters
    "cat_Fine",
    "cat_Nice",
    "cat_Good",
    "cat_Rare",
    "cat_Wild",
    "cat_Baby",
    "cat_Epic",
    "cat_Sus",
    "cat_Brave",
    "cat_Rickroll",
    "cat_Reverse",
    "cat_Superior",
    "cat_Trash",
    "cat_Legendary",
    "cat_Mythic",
    "cat_8bit",
    "cat_Corrupt",
    "cat_Professor",
    "cat_Divine",
    "cat_Real",
    "cat_Shadow",
    "cat_Terminator",
    "cat_Ultimate",
    "cat_eGirl",
]
STR_FIELDS = [
    "catch_quest",
    "misc_quest",
    "vote_quest",
    "extra_quest",
    "challenge_quest",
    "gift3_recipients",
    "custom",
    "perk1",
    "perk2",
    "perk3",
    "catnip_price",
    "bounty_type_one",
    "bounty_type_two",
    "bounty_type_three",
    "bounty_type_bonus",
]
BOOL_FIELDS = [
    "reminders_enabled",
    "hibernation",
    "new_user",
    "website_user",
    "perk_selected",
    "reroll",
    "cat_rain",
    "bounty_novice",
    "bounty_hunter",
    "bounty_lord",
    "cookiesclicked",
    # Jobs / Mafia Killings
    "big_score_perk_unlocked",   # whether the permanent Big Score spawn perk has fired
    "whiskers_favor_active",     # whether a Whiskers Favor is currently pending use
    "jobs_send_screen_seen",     # UX: has the player seen the send screen intro?
    "tutorial_errand_complete",  # UX: has the tutorial errand been completed?
]

# JSONB list columns — view-only (no edit route; freeform JSONB editing is
# too risky for a raw admin form). Shown as pill lists in the profile detail.
JSONB_FIELDS = [
    "unlocked_aches",           # JSONB list of achievement IDs ever unlocked
    "discovered_cats",          # JSONB list of rarity names ever owned (catstore/catch)
    "store_purchased_rarities", # JSONB list of rarity names ever bought from /catstore
    "faction_rep",              # JSONB dict[npc_key -> int]; per-NPC reputation (jobs system)
    "job_perks",                # JSONB list of active mafia-reward perks; writer is main._perks_grant
    "season_stat_baseline",     # JSONB dict snapshot of bigint counters at season rollover; used to compute per-season deltas
    "season_trophies",          # JSONB list of {season, category, rank} trophy records; append-only, written by _award_season_trophies at rollover
]


async def index(request):
    pool = state.get_pool()
    rows = []
    total = 0
    q_user = request.query.get("user", "").strip()
    q_guild = request.query.get("guild", "").strip()
    page = parse_page(request)
    pager = None
    if pool is not None and (q_user or q_guild):
        where = []
        args = []
        if q_user:
            args.append(f"%{q_user}%")
            where.append(f"CAST(user_id AS TEXT) LIKE ${len(args)}")
        if q_guild:
            args.append(f"%{q_guild}%")
            where.append(f"CAST(guild_id AS TEXT) LIKE ${len(args)}")
        where_sql = " AND ".join(where)
        async with pool.acquire() as conn:
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM profile WHERE {where_sql}", *args,
            ) or 0
            limit_arg = f"${len(args) + 1}"
            offset_arg = f"${len(args) + 2}"
            sql = (
                "SELECT user_id, guild_id, battlepass, progress, season, "
                "catch_quest, misc_quest, total_catches, rain_minutes "
                f"FROM profile WHERE {where_sql} "
                f"ORDER BY total_catches DESC LIMIT {limit_arg} OFFSET {offset_arg}"
            )
            rows = await conn.fetch(sql, *args, PER_PAGE, (page - 1) * PER_PAGE)
        pager = make_pager(
            request,
            page=page,
            per_page=PER_PAGE,
            total=int(total),
            base_path="/db/profile",
            params={"user": q_user, "guild": q_guild},
            target="#pager-profile",
        )
    await names.refresh_guild_name_cache()
    unames = await names.resolve_users(state.get_bot(), [r["user_id"] for r in rows])
    return aiohttp_jinja2.render_template(
        "db_profile_search.html",
        request,
        {
            "title": "Profiles",
            "active_section": "profile_table",
            "rows": rows,
            "q_user": q_user,
            "q_guild": q_guild,
            "unames": unames,
            "pager": pager,
        },
    )


async def detail(request):
    user_id = int(request.match_info["user_id"])
    guild_id = int(request.match_info["guild_id"])
    pool = state.get_pool()
    if pool is None:
        return web.Response(status=503)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM profile WHERE user_id = $1 AND guild_id = $2",
            user_id, guild_id,
        )
    if row is None:
        return web.Response(status=404)
    # Deserialize JSONB columns (asyncpg may return them as strings or lists)
    import json as _json
    row_dict = dict(row)
    for jf in JSONB_FIELDS:
        raw = row_dict.get(jf)
        if isinstance(raw, str):
            try:
                row_dict[jf] = _json.loads(raw)
            except Exception:
                row_dict[jf] = []
        elif raw is None:
            row_dict[jf] = []
    await names.refresh_guild_name_cache()
    unames = await names.resolve_users(state.get_bot(), [user_id])
    return aiohttp_jinja2.render_template(
        "db_profile_detail.html",
        request,
        {
            "title": f"Profile {user_id}@{guild_id}",
            "active_section": "profile_table",
            "row": row_dict,
            "int_fields": INT_FIELDS,
            "str_fields": STR_FIELDS,
            "bool_fields": BOOL_FIELDS,
            "jsonb_fields": JSONB_FIELDS,
            "unames": unames,
        },
    )


def register(app: web.Application) -> None:
    app.router.add_get("/db/profile", index)
    app.router.add_get(r"/db/profile/{user_id:\d+}/{guild_id:\d+}", detail)
