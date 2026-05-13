"""Profile table — per user-per-guild state.

Massive table (~315 columns). UI surfaces a curated edit set for the
gameplay-relevant fields and a read-only view of everything else.

Edits acquire FOR UPDATE because gameplay writes to profiles every catch.
"""

import aiohttp_jinja2
from aiohttp import web

from webui import state

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
    "cat_Ultimate",
    "cat_eGirl",
]
STR_FIELDS = [
    "catch_quest",
    "misc_quest",
    "extra_quest",
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
]


async def index(request):
    pool = state.get_pool()
    rows = []
    q_user = request.query.get("user", "").strip()
    q_guild = request.query.get("guild", "").strip()
    if pool is not None and (q_user or q_guild):
        where = []
        args = []
        if q_user:
            args.append(f"%{q_user}%")
            where.append(f"CAST(user_id AS TEXT) LIKE ${len(args)}")
        if q_guild:
            args.append(f"%{q_guild}%")
            where.append(f"CAST(guild_id AS TEXT) LIKE ${len(args)}")
        sql = (
            "SELECT user_id, guild_id, battlepass, progress, season, "
            "catch_quest, misc_quest, total_catches, rain_minutes "
            "FROM profile WHERE " + " AND ".join(where) + " ORDER BY total_catches DESC LIMIT 100"
        )
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
    return aiohttp_jinja2.render_template(
        "db_profile_search.html",
        request,
        {
            "title": "Profiles",
            "active_section": "profile_table",
            "rows": rows,
            "q_user": q_user,
            "q_guild": q_guild,
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
    return aiohttp_jinja2.render_template(
        "db_profile_edit.html",
        request,
        {
            "title": f"Profile {user_id}@{guild_id}",
            "active_section": "profile_table",
            "row": dict(row),
            "int_fields": INT_FIELDS,
            "str_fields": STR_FIELDS,
            "bool_fields": BOOL_FIELDS,
        },
    )


async def update(request):
    user_id = int(request.match_info["user_id"])
    guild_id = int(request.match_info["guild_id"])
    field = request.match_info["field"]
    if field not in INT_FIELDS + STR_FIELDS + BOOL_FIELDS:
        return web.Response(status=400, text="field not editable")
    form = await request.post()
    raw = form.get("value", "")
    pool = state.get_pool()
    if pool is None:
        return web.Response(status=503)
    try:
        if field in INT_FIELDS:
            new_value = int(raw)
        elif field in BOOL_FIELDS:
            new_value = raw.lower() in ("1", "true", "on", "yes")
        else:
            new_value = raw
    except ValueError:
        return web.Response(status=400, text="invalid value")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT 1 FROM profile WHERE user_id = $1 AND guild_id = $2 FOR UPDATE",
                user_id, guild_id,
            )
            await conn.execute(
                f'UPDATE profile SET "{field}" = $1 WHERE user_id = $2 AND guild_id = $3',
                new_value, user_id, guild_id,
            )
    return web.Response(text=f"saved {field}")


def register(app: web.Application) -> None:
    app.router.add_get("/db/profile", index)
    app.router.add_get(r"/db/profile/{user_id:\d+}/{guild_id:\d+}", detail)
    app.router.add_post(r"/db/profile/{user_id:\d+}/{guild_id:\d+}/{field:[A-Za-z0-9_]+}", update)
