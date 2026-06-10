"""Per-user activity drilldown (read-only).

Reached by click-through from the /activity "Top catchers" table, the per-server
top-players list, or any user-name link in a recent-jobs/prisms row. Mirrors
the breakdowns shown on /activity_server but scoped to one user_id and
aggregated across every guild that user has a profile in.
"""

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

WINDOW_DAYS = 30


async def index(request):
    user_id = int(request.match_info["user_id"])
    pool = state.get_pool()
    bot = state.get_bot()
    now = int(time.time())
    window_start = now - WINDOW_DAYS * 86400

    tiles = {
        "total_catches": 0,
        "guild_count": 0,
        "total_coins": 0,
        "max_battlepass": 0,
        "jobs_completed": 0,
        "prism_count": 0,
        "daily_catch_streak": 0,
        "max_daily_streak": 0,
        "total_votes": 0,
    }
    user_flags = {
        "premium": False,
        "blessings_enabled": False,
        "blessings_anonymous": False,
        "claimed_free_rain": False,
        "vote_time_topgg": 0,
    }
    per_guild: list = []
    rarities: list[tuple[str, int]] = []
    packs: list[tuple[str, int]] = []
    jobs_per_day_by_outcome: dict = {}
    jobs_outcomes_seen: list = []
    recent_jobs: list = []
    prisms: list = []
    orders: list = []

    await names.refresh_guild_name_cache()

    if pool is not None:
        async with pool.acquire() as conn:
            # --- profile aggregates (across all guilds for this user) ---
            row = await conn.fetchrow(
                """
                SELECT
                  COALESCE(SUM(total_catches), 0) AS total_catches,
                  COUNT(*) AS guild_count,
                  COALESCE(SUM(GREATEST(coins, 0)), 0) AS total_coins,
                  COALESCE(MAX(battlepass), 0) AS max_battlepass,
                  COALESCE(SUM(jobs_completed), 0) AS jobs_completed
                FROM profile
                WHERE user_id = $1
                """,
                user_id,
            )
            if row is not None:
                tiles["total_catches"] = int(row["total_catches"] or 0)
                tiles["guild_count"] = int(row["guild_count"] or 0)
                tiles["total_coins"] = int(row["total_coins"] or 0)
                tiles["max_battlepass"] = int(row["max_battlepass"] or 0)
                tiles["jobs_completed"] = int(row["jobs_completed"] or 0)

            tiles["prism_count"] = int(
                await conn.fetchval(
                    "SELECT COUNT(*) FROM prism WHERE user_id = $1", user_id
                ) or 0
            )

            # --- user-level info ---
            urow = await conn.fetchrow(
                'SELECT daily_catch_streak, max_daily_streak, total_votes, '
                'premium, blessings_enabled, blessings_anonymous, '
                'claimed_free_rain, vote_time_topgg '
                'FROM "user" WHERE user_id = $1',
                user_id,
            )
            if urow is not None:
                tiles["daily_catch_streak"] = int(urow["daily_catch_streak"] or 0)
                tiles["max_daily_streak"] = int(urow["max_daily_streak"] or 0)
                tiles["total_votes"] = int(urow["total_votes"] or 0)
                user_flags["premium"] = bool(urow["premium"])
                user_flags["blessings_enabled"] = bool(urow["blessings_enabled"])
                user_flags["blessings_anonymous"] = bool(urow["blessings_anonymous"])
                user_flags["claimed_free_rain"] = bool(urow["claimed_free_rain"])
                user_flags["vote_time_topgg"] = int(urow["vote_time_topgg"] or 0)

            # --- per-guild profile breakdown ---
            rows = await conn.fetch(
                """
                SELECT guild_id, total_catches, coins, battlepass, catnip_level,
                       jobs_completed, jobs_failed, last_catch
                FROM profile WHERE user_id = $1
                ORDER BY total_catches DESC NULLS LAST
                """,
                user_id,
            )
            per_guild = [
                {
                    "guild_id": r["guild_id"],
                    "total_catches": int(r["total_catches"] or 0),
                    "coins": int(r["coins"] or 0),
                    "battlepass": int(r["battlepass"] or 0),
                    "catnip_level": int(r["catnip_level"] or 0),
                    "jobs_completed": int(r["jobs_completed"] or 0),
                    "jobs_failed": int(r["jobs_failed"] or 0),
                    "last_catch": int(r["last_catch"] or 0),
                }
                for r in rows
            ]

            # --- rarity / pack sums across all profiles for this user ---
            if tiles["guild_count"]:
                rarity_row = await conn.fetchrow(
                    f"SELECT {_rarity_sum_clauses()} FROM profile WHERE user_id = $1",
                    user_id,
                )
                rarities = [(r, int(rarity_row[r] or 0)) for r in RARITY_COLUMNS]

                pack_row = await conn.fetchrow(
                    f"SELECT {_pack_sum_clauses()} FROM profile WHERE user_id = $1",
                    user_id,
                )
                packs = [(p.title(), int(pack_row[p] or 0)) for p in PACK_COLUMNS]

            # --- jobs by day & outcome ---
            rows = await conn.fetch(
                """
                SELECT to_char(date_trunc('day', to_timestamp(resolved_at)), 'YYYY-MM-DD') AS day,
                       outcome, COUNT(*) AS n
                FROM jobinstance
                WHERE state = 'resolved' AND user_id = $1 AND resolved_at >= $2
                GROUP BY day, outcome
                ORDER BY day ASC
                """,
                user_id, window_start,
            )
            outcomes_set: list[str] = []
            for r in rows:
                day = r["day"]
                oc = r["outcome"] or "—"
                jobs_per_day_by_outcome.setdefault(day, {})[oc] = int(r["n"])
                if oc not in outcomes_set:
                    outcomes_set.append(oc)
            jobs_outcomes_seen = outcomes_set

            # --- recent jobs by this user ---
            recent_jobs = await conn.fetch(
                "SELECT guild_id, category, tier, outcome, complication, resolved_at "
                "FROM jobinstance WHERE state = 'resolved' AND user_id = $1 "
                "ORDER BY resolved_at DESC LIMIT 25",
                user_id,
            )

            # --- prisms ---
            prisms = await conn.fetch(
                'SELECT name, guild_id, "time", catches_boosted '
                "FROM prism WHERE user_id = $1 "
                "ORDER BY catches_boosted DESC NULLS LAST",
                user_id,
            )

            # --- orders ---
            orders = await conn.fetch(
                'SELECT "time", ticker, type_buy, quantity, price FROM "order" '
                'WHERE user_id = $1 ORDER BY "time" DESC LIMIT 20',
                user_id,
            )

    # --- pivot stacked data ---
    jobs_day_keys = sorted(jobs_per_day_by_outcome.keys())
    jobs_per_day_stacked = {
        oc: [int(jobs_per_day_by_outcome.get(d, {}).get(oc, 0)) for d in jobs_day_keys]
        for oc in jobs_outcomes_seen
    }

    # --- name resolution ---
    unames = await names.resolve_users(bot, [user_id])

    # Trim distributions
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
        "activity_user.html",
        request,
        {
            "title": f"User {user_id}",
            "active_section": "activity",
            "user_id": user_id,
            "now": now,
            "tiles": tiles,
            "user_flags": user_flags,
            "per_guild": per_guild,
            "rarities": _topN_with_other(rarities, n=10),
            "packs": _topN_with_other(packs, n=12),
            "jobs_day_keys": jobs_day_keys,
            "jobs_per_day_stacked": jobs_per_day_stacked,
            "jobs_outcomes_seen": jobs_outcomes_seen,
            "recent_jobs": recent_jobs,
            "prisms": prisms,
            "orders": orders,
            "unames": unames,
        },
    )


def register(app: web.Application) -> None:
    app.router.add_get(r"/activity/user/{user_id:\d+}", index)
