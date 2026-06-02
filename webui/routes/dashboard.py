"""Dashboard: uptime, dirty banner, live counts, activity stats, and charts."""

import time
import datetime

import aiohttp_jinja2
from aiohttp import web

from webui import names, state


# Rarity columns on profile (preserves quoted-identifier capitalization).
RARITY_COLUMNS = [
    "Fine", "Nice", "Good", "Rare", "Wild", "Baby", "Epic", "Sus",
    "Brave", "Rickroll", "Reverse", "Superior", "Trash", "Legendary",
    "Mythic", "8bit", "Corrupt", "Professor", "Divine", "Real",
    "Ultimate", "eGirl",
]

# Pack tier columns (case-sensitive on the column name itself).
PACK_COLUMNS = [
    "wooden", "stone", "bronze", "silver", "gold", "platinum",
    "diamond", "celestial", "christmas", "valentine", "chef", "birthday",
]


def _rarity_sum_clauses() -> str:
    parts = []
    for r in RARITY_COLUMNS:
        # cat_8bit is unquoted (no caps), the rest are quoted.
        col = f'cat_{r}' if r == "8bit" else f'"cat_{r}"'
        parts.append(f'COALESCE(SUM({col}), 0) AS "{r}"')
    return ", ".join(parts)


def _pack_sum_clauses() -> str:
    return ", ".join(f'COALESCE(SUM(pack_{p}), 0) AS "{p}"' for p in PACK_COLUMNS)


def _topN_with_other(items: list[tuple[str, int]], n: int = 8) -> list[tuple[str, int]]:
    """Trim a (label, value) list to top-N by value, bucketing the rest into 'Other'."""
    items = [(k, int(v)) for k, v in items if v]
    items.sort(key=lambda kv: kv[1], reverse=True)
    if len(items) <= n:
        return items
    other = sum(v for _, v in items[n:])
    head = items[:n]
    if other:
        head.append(("Other", other))
    return head


async def index(request):
    pool = state.get_pool()
    now = int(time.time())
    today_start = int(datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    week_start = today_start - 6 * 86400
    month_start = today_start - 29 * 86400

    counts: dict = {}
    activity: dict = {"today": 0, "week": 0, "month": 0}
    totals: dict = {"catches": 0, "packs": 0, "prism_boosts": 0}
    rarities: list[tuple[str, int]] = []
    packs: list[tuple[str, int]] = []
    prism_history: list[tuple[str, int]] = []
    leaderboard: list[dict] = []

    if pool is not None:
        async with pool.acquire() as conn:
            counts["servers"] = await conn.fetchval("SELECT COUNT(*) FROM server")
            counts["channels"] = await conn.fetchval(
                "SELECT COUNT(*) FROM channel WHERE cat <> 0 OR yet_to_spawn <> 0"
            )
            counts["profiles"] = await conn.fetchval("SELECT COUNT(*) FROM profile")
            counts["users"] = await conn.fetchval('SELECT COUNT(*) FROM "user"')
            counts["prisms"] = await conn.fetchval("SELECT COUNT(*) FROM prism")
            counts["active_rains"] = await conn.fetchval(
                "SELECT COUNT(*) FROM channel WHERE rain_should_end > $1", now
            )
            counts["live_spawns"] = await conn.fetchval(
                "SELECT COUNT(*) FROM channel WHERE cat <> 0"
            )
            counts["pending_jobs"] = await conn.fetchval(
                "SELECT COUNT(*) FROM jobinstance WHERE state = 'offered'"
            )

            row = await conn.fetchrow(
                """
                SELECT
                  COUNT(DISTINCT CASE WHEN last_catch >= $1 THEN user_id END) AS today,
                  COUNT(DISTINCT CASE WHEN last_catch >= $2 THEN user_id END) AS week,
                  COUNT(DISTINCT CASE WHEN last_catch >= $3 THEN user_id END) AS month
                FROM profile
                """,
                today_start, week_start, month_start,
            )
            activity = {"today": row["today"] or 0, "week": row["week"] or 0, "month": row["month"] or 0}

            row = await conn.fetchrow(
                """
                SELECT
                  COALESCE(SUM(total_catches), 0) AS catches,
                  COALESCE(SUM(packs_opened),  0) AS packs
                FROM profile
                """
            )
            totals["catches"] = int(row["catches"] or 0)
            totals["packs"] = int(row["packs"] or 0)
            totals["prism_boosts"] = int(
                await conn.fetchval("SELECT COALESCE(SUM(catches_boosted), 0) FROM prism") or 0
            )

            rarity_row = await conn.fetchrow(f"SELECT {_rarity_sum_clauses()} FROM profile")
            rarities = [(r, int(rarity_row[r] or 0)) for r in RARITY_COLUMNS]

            pack_row = await conn.fetchrow(f"SELECT {_pack_sum_clauses()} FROM profile")
            packs = [(p.title(), int(pack_row[p] or 0)) for p in PACK_COLUMNS]

            # Prisms created per month, last 12 months
            history = await conn.fetch(
                """
                SELECT to_char(date_trunc('month', to_timestamp("time")), 'YYYY-MM') AS month,
                       COUNT(*) AS n
                FROM prism
                WHERE "time" > $1
                GROUP BY month
                ORDER BY month ASC
                """,
                now - 86400 * 365,
            )
            prism_history = [(r["month"], int(r["n"])) for r in history]

            # Top catchers, summing across (user, guild) profiles
            top = await conn.fetch(
                """
                SELECT user_id, SUM(total_catches)::bigint AS catches
                FROM profile
                GROUP BY user_id
                ORDER BY catches DESC NULLS LAST
                LIMIT 10
                """
            )
            leaderboard = [{"user_id": r["user_id"], "catches": int(r["catches"] or 0)} for r in top]

    bot = state.get_bot()
    unames = await names.resolve_users(bot, [u["user_id"] for u in leaderboard])

    # Trim distributions for legibility
    rarities_trimmed = _topN_with_other(rarities, n=10)
    packs_trimmed = _topN_with_other(packs, n=12)

    return aiohttp_jinja2.render_template(
        "dashboard.html",
        request,
        {
            "title": "Dashboard",
            "active_section": "dashboard",
            "counts": counts,
            "activity": activity,
            "totals": totals,
            "rarities": rarities_trimmed,
            "packs": packs_trimmed,
            "prism_history": prism_history,
            "leaderboard": leaderboard,
            "unames": unames,
            "guild_count": len(bot.guilds) if bot else 0,
            "shard_count": getattr(bot, "shard_count", None) if bot else None,
            "hard_restart": state.get_hard_restart_time(),
            "soft_restart": state.get_soft_restart_time(),
            "uptime": state.uptime_seconds(),
        },
    )


def register(app: web.Application) -> None:
    app.router.add_get("/", index)
