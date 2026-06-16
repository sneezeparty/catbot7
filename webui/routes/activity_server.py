"""Per-server activity drilldown (read-only).

Reached by click-through from the /activity "Top servers" table. Mirrors the
breakdowns shown on /activity but scoped to one guild_id. Reuses the rarity
and pack column lists from dashboard.py so the doughnut data stays in sync.

The channel table has no guild_id column — to filter active spawns/rains
to this guild we pull all live channels and resolve each back to its guild
via the bot's cache, the same pattern main._broadcast_season_warning uses.
"""

import datetime
import time

import aiohttp_jinja2
from aiohttp import web

from webui import names, state
from webui.routes.dashboard import (
    PACK_COLUMNS,
    RARITY_COLUMNS,
    _pack_sum_clauses,
    _rarity_sum_clauses,
)


async def index(request):
    guild_id = int(request.match_info["guild_id"])
    pool = state.get_pool()
    bot = state.get_bot()
    now = int(time.time())
    today_start = int(
        datetime.datetime.now(datetime.timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )
    week_start = today_start - 6 * 86400
    month_start = today_start - 29 * 86400
    window_start = today_start - 29 * 86400

    tiles = {
        "profile_count": 0,
        "total_catches": 0,
        "total_coins": 0,
        "total_packs": 0,
        "jobs_completed": 0,
        "jobs_failed": 0,
        "prism_count": 0,
        "active_7d": 0,
        "active_30d": 0,
    }
    rarities: list[tuple[str, int]] = []
    packs: list[tuple[str, int]] = []
    catnip_distribution: list[tuple[int, int]] = []  # [(level, count)]
    jobs_per_day_by_outcome: dict = {}
    jobs_outcomes_seen: list = []
    top_users: list = []
    recent_jobs: list = []
    spawns_here: list = []
    rains_here: list = []
    recent_prisms: list = []

    await names.refresh_guild_name_cache()

    bot_id = state.bot_user_id_or_zero()

    if pool is not None:
        async with pool.acquire() as conn:
            # --- tiles ---
            row = await conn.fetchrow(
                """
                SELECT
                  COUNT(*) AS profile_count,
                  COALESCE(SUM(total_catches), 0) AS total_catches,
                  COALESCE(SUM(GREATEST(coins, 0)), 0) AS total_coins,
                  COALESCE(SUM(packs_opened), 0) AS total_packs,
                  COALESCE(SUM(jobs_completed), 0) AS jobs_completed,
                  COALESCE(SUM(jobs_failed), 0) AS jobs_failed,
                  COUNT(DISTINCT CASE WHEN last_catch >= $2 THEN user_id END) AS active_7d,
                  COUNT(DISTINCT CASE WHEN last_catch >= $3 THEN user_id END) AS active_30d
                FROM profile
                WHERE guild_id = $1 AND user_id <> $4
                """,
                guild_id, week_start, month_start, bot_id,
            )
            if row is not None:
                tiles["profile_count"] = int(row["profile_count"] or 0)
                tiles["total_catches"] = int(row["total_catches"] or 0)
                tiles["total_coins"] = int(row["total_coins"] or 0)
                tiles["total_packs"] = int(row["total_packs"] or 0)
                tiles["jobs_completed"] = int(row["jobs_completed"] or 0)
                tiles["jobs_failed"] = int(row["jobs_failed"] or 0)
                tiles["active_7d"] = int(row["active_7d"] or 0)
                tiles["active_30d"] = int(row["active_30d"] or 0)
            tiles["prism_count"] = int(
                await conn.fetchval(
                    "SELECT COUNT(*) FROM prism WHERE guild_id = $1 AND user_id <> $2",
                    guild_id, bot_id,
                ) or 0
            )

            # --- rarity / pack doughnuts ---
            if tiles["profile_count"]:
                rarity_row = await conn.fetchrow(
                    f"SELECT {_rarity_sum_clauses()} FROM profile WHERE guild_id = $1 AND user_id <> $2",
                    guild_id, bot_id,
                )
                rarities = [(r, int(rarity_row[r] or 0)) for r in RARITY_COLUMNS]

                pack_row = await conn.fetchrow(
                    f"SELECT {_pack_sum_clauses()} FROM profile WHERE guild_id = $1 AND user_id <> $2",
                    guild_id, bot_id,
                )
                packs = [(p.title(), int(pack_row[p] or 0)) for p in PACK_COLUMNS]

            # --- catnip level distribution ---
            rows = await conn.fetch(
                """
                SELECT catnip_level, COUNT(*) AS n FROM profile
                WHERE guild_id = $1 AND user_id <> $2
                GROUP BY catnip_level
                ORDER BY catnip_level ASC
                """,
                guild_id, bot_id,
            )
            catnip_distribution = [(int(r["catnip_level"] or 0), int(r["n"])) for r in rows]

            # --- jobs per day by outcome ---
            rows = await conn.fetch(
                """
                SELECT to_char(date_trunc('day', to_timestamp(resolved_at)), 'YYYY-MM-DD') AS day,
                       outcome,
                       COUNT(*) AS n
                FROM jobinstance
                WHERE state = 'resolved' AND guild_id = $1 AND resolved_at >= $2 AND user_id <> $3
                GROUP BY day, outcome
                ORDER BY day ASC
                """,
                guild_id, window_start, bot_id,
            )
            outcomes_set: list[str] = []
            for r in rows:
                day = r["day"]
                oc = r["outcome"] or "—"
                jobs_per_day_by_outcome.setdefault(day, {})[oc] = int(r["n"])
                if oc not in outcomes_set:
                    outcomes_set.append(oc)
            jobs_outcomes_seen = outcomes_set

            # --- top users in this guild ---
            rows = await conn.fetch(
                """
                SELECT p.user_id,
                       p.total_catches,
                       p.coins,
                       p.battlepass,
                       p.catnip_level,
                       p.jobs_completed,
                       (SELECT COUNT(*) FROM prism pr
                          WHERE pr.user_id = p.user_id AND pr.guild_id = p.guild_id) AS prism_count
                FROM profile p
                WHERE p.guild_id = $1 AND p.user_id <> $2
                ORDER BY p.total_catches DESC NULLS LAST
                LIMIT 25
                """,
                guild_id, bot_id,
            )
            top_users = [
                {
                    "user_id": r["user_id"],
                    "total_catches": int(r["total_catches"] or 0),
                    "coins": int(r["coins"] or 0),
                    "battlepass": int(r["battlepass"] or 0),
                    "catnip_level": int(r["catnip_level"] or 0),
                    "jobs_completed": int(r["jobs_completed"] or 0),
                    "prism_count": int(r["prism_count"] or 0),
                }
                for r in rows
            ]

            # --- recent jobs in this guild ---
            recent_jobs = await conn.fetch(
                "SELECT user_id, category, tier, outcome, complication, resolved_at "
                "FROM jobinstance WHERE state = 'resolved' AND guild_id = $1 AND user_id <> $2 "
                "ORDER BY resolved_at DESC LIMIT 25",
                guild_id, bot_id,
            )

            # --- recent prisms in this guild ---
            recent_prisms = await conn.fetch(
                'SELECT name, user_id, "time", catches_boosted '
                'FROM prism WHERE guild_id = $1 AND user_id <> $2 ORDER BY "time" DESC NULLS LAST LIMIT 20',
                guild_id, bot_id,
            )

            # --- live spawns/rains in this guild's channels ---
            live_channels = await conn.fetch(
                "SELECT channel_id, cat, cattype, yet_to_spawn, rain_should_end FROM channel "
                "WHERE cat <> 0 OR rain_should_end > $1",
                now,
            )
            for ch in live_channels:
                ch_obj = bot.get_channel(int(ch["channel_id"])) if bot else None
                if ch_obj is None or ch_obj.guild is None or ch_obj.guild.id != guild_id:
                    continue
                if ch["cat"]:
                    spawns_here.append(ch)
                if ch["rain_should_end"] > now:
                    rains_here.append(ch)

    # --- pivot stacked data ---
    jobs_day_keys = sorted(jobs_per_day_by_outcome.keys())
    jobs_per_day_stacked = {
        oc: [int(jobs_per_day_by_outcome.get(d, {}).get(oc, 0)) for d in jobs_day_keys]
        for oc in jobs_outcomes_seen
    }

    # --- name resolution ---
    uname_ids = [u["user_id"] for u in top_users]
    uname_ids += [j["user_id"] for j in recent_jobs]
    uname_ids += [p["user_id"] for p in recent_prisms]
    unames = await names.resolve_users(bot, uname_ids)

    # Trim distributions for legibility (top-N + other)
    def _topN_with_other(items, n=10):
        items = [(k, int(v)) for k, v in items if v]
        items.sort(key=lambda kv: kv[1], reverse=True)
        if len(items) <= n:
            return items
        other = sum(v for _, v in items[n:])
        head = items[:n]
        if other:
            head.append(("Other", other))
        return head

    return aiohttp_jinja2.render_template(
        "activity_server.html",
        request,
        {
            "title": f"Server {guild_id}",
            "active_section": "activity",
            "guild_id": guild_id,
            "now": now,
            "tiles": tiles,
            "rarities": _topN_with_other(rarities, n=10),
            "packs": _topN_with_other(packs, n=12),
            "catnip_distribution": catnip_distribution,
            "jobs_day_keys": jobs_day_keys,
            "jobs_per_day_stacked": jobs_per_day_stacked,
            "jobs_outcomes_seen": jobs_outcomes_seen,
            "top_users": top_users,
            "recent_jobs": recent_jobs,
            "recent_prisms": recent_prisms,
            "spawns_here": spawns_here,
            "rains_here": rains_here,
            "unames": unames,
        },
    )


def register(app: web.Application) -> None:
    app.router.add_get(r"/activity/server/{guild_id:\d+}", index)
