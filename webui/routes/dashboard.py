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


# Columns to derive rate-of-change ("load") from. metric_snapshot stores hourly
# cumulative counters; differencing consecutive rows gives the load. Each entry:
#   (eyebrow label, column name in metric_snapshot, footer caption)
LOAD_METRICS = [
    ("Catches",        "total_catches",          "messages typing 'cat'"),
    ("Packs opened",   "total_packs",            "/pack opens"),
    ("Jobs completed", "jobs_completed_lifetime", "/jobs resolved"),
    ("Catnip procs",   "catnip_total",           "catnip double-catch trigger"),
]


def _load_rates(rows: list) -> dict:
    """Build the load context from metric_snapshot rows (newest first).

    For each metric we compute two rates:
      - `last`: delta between the two most recent buckets / their time span
      - `avg24h`: delta over up to 24 buckets / total span
    Both are returned as per-minute and per-hour. Time spans use the actual
    bucket_time gap so a missed snapshot (downtime, hook hiccup) doesn't
    silently inflate the rate.

    Returns {} when there aren't enough rows to differentiate (the dashboard
    template hides the section in that case).
    """
    if len(rows) < 2:
        return {}
    newest = rows[0]
    prev = rows[1]
    last_span = max(1, int(newest["bucket_time"]) - int(prev["bucket_time"]))
    oldest = rows[min(len(rows) - 1, 24)]
    avg_span = max(1, int(newest["bucket_time"]) - int(oldest["bucket_time"]))
    out: dict[str, dict] = {}
    for eyebrow, col, foot in LOAD_METRICS:
        last_delta = max(0, int(newest[col] or 0) - int(prev[col] or 0))
        avg_delta = max(0, int(newest[col] or 0) - int(oldest[col] or 0))
        last_per_sec = last_delta / last_span
        avg_per_sec = avg_delta / avg_span
        out[col] = {
            "eyebrow": eyebrow,
            "foot": foot,
            "last_per_min": last_per_sec * 60,
            "last_per_hr": last_per_sec * 3600,
            "avg_per_min": avg_per_sec * 60,
            "avg_per_hr": avg_per_sec * 3600,
            "have_avg": len(rows) > 2,
        }
    return out


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
    load: dict = {}
    load_window_hours = 0

    bot_id = state.bot_user_id_or_zero()

    if pool is not None:
        async with pool.acquire() as conn:
            counts["servers"] = await conn.fetchval("SELECT COUNT(*) FROM server")
            # Every row in `channel` is a /setup'd channel (/forget deletes).
            counts["channels"] = await conn.fetchval(
                "SELECT COUNT(*) FROM channel"
            )
            counts["profiles"] = await conn.fetchval(
                "SELECT COUNT(*) FROM profile WHERE user_id <> $1", bot_id
            )
            counts["users"] = await conn.fetchval(
                'SELECT COUNT(*) FROM "user" WHERE user_id <> $1', bot_id
            )
            counts["prisms"] = await conn.fetchval(
                "SELECT COUNT(*) FROM prism WHERE user_id <> $1", bot_id
            )
            counts["active_rains"] = await conn.fetchval(
                "SELECT COUNT(*) FROM channel WHERE rain_should_end > $1", now
            )
            counts["live_spawns"] = await conn.fetchval(
                "SELECT COUNT(*) FROM channel WHERE cat <> 0"
            )
            counts["pending_jobs"] = await conn.fetchval(
                "SELECT COUNT(*) FROM jobinstance WHERE state = 'offered' AND user_id <> $1",
                bot_id,
            )
            # /chaos global counter: sentinel profile row (guild 666, bot user)
            # reusing the cookies column. Excluded from every aggregate above
            # because its user_id is the bot's.
            counts["chaos_counter"] = await conn.fetchval(
                "SELECT cookies FROM profile WHERE guild_id = 666 AND user_id = $1",
                bot_id,
            ) or 0

            row = await conn.fetchrow(
                """
                SELECT
                  COUNT(DISTINCT CASE WHEN last_catch >= $1 THEN user_id END) AS today,
                  COUNT(DISTINCT CASE WHEN last_catch >= $2 THEN user_id END) AS week,
                  COUNT(DISTINCT CASE WHEN last_catch >= $3 THEN user_id END) AS month
                FROM profile
                WHERE user_id <> $4
                """,
                today_start, week_start, month_start, bot_id,
            )
            activity = {"today": row["today"] or 0, "week": row["week"] or 0, "month": row["month"] or 0}

            row = await conn.fetchrow(
                """
                SELECT
                  COALESCE(SUM(total_catches), 0) AS catches,
                  COALESCE(SUM(packs_opened),  0) AS packs
                FROM profile
                WHERE user_id <> $1
                """,
                bot_id,
            )
            totals["catches"] = int(row["catches"] or 0)
            totals["packs"] = int(row["packs"] or 0)
            totals["prism_boosts"] = int(
                await conn.fetchval(
                    "SELECT COALESCE(SUM(catches_boosted), 0) FROM prism WHERE user_id <> $1",
                    bot_id,
                ) or 0
            )

            rarity_row = await conn.fetchrow(
                f"SELECT {_rarity_sum_clauses()} FROM profile WHERE user_id <> $1",
                bot_id,
            )
            rarities = [(r, int(rarity_row[r] or 0)) for r in RARITY_COLUMNS]

            pack_row = await conn.fetchrow(
                f"SELECT {_pack_sum_clauses()} FROM profile WHERE user_id <> $1",
                bot_id,
            )
            packs = [(p.title(), int(pack_row[p] or 0)) for p in PACK_COLUMNS]

            # Prisms created per month, last 12 months
            history = await conn.fetch(
                """
                SELECT to_char(date_trunc('month', to_timestamp("time")), 'YYYY-MM') AS month,
                       COUNT(*) AS n
                FROM prism
                WHERE "time" > $1 AND user_id <> $2
                GROUP BY month
                ORDER BY month ASC
                """,
                now - 86400 * 365, bot_id,
            )
            prism_history = [(r["month"], int(r["n"])) for r in history]

            # Top catchers, summing across (user, guild) profiles. Skip the
            # guild_id=0 bot pseudo-profile (left over from the old stock
            # market-maker; not a real player) and the live bot user_id.
            top = await conn.fetch(
                """
                SELECT user_id, SUM(total_catches)::bigint AS catches
                FROM profile
                WHERE guild_id <> 0 AND user_id <> $1
                GROUP BY user_id
                ORDER BY catches DESC NULLS LAST
                LIMIT 10
                """,
                bot_id,
            )
            leaderboard = [{"user_id": r["user_id"], "catches": int(r["catches"] or 0)} for r in top]

            # Load: differentiate the last ~25 hourly metric_snapshot rows.
            # Defensive against a missing table (migration 029 not yet applied):
            # an exception leaves `load` empty and the section hides itself.
            try:
                snap_cols = ", ".join(["bucket_time"] + [c for _, c, _ in LOAD_METRICS])
                snap_rows = await conn.fetch(
                    f"SELECT {snap_cols} FROM metric_snapshot ORDER BY bucket_time DESC LIMIT 25"
                )
                load = _load_rates(list(snap_rows))
                if len(snap_rows) >= 2:
                    load_window_hours = max(
                        1,
                        (int(snap_rows[0]["bucket_time"]) - int(snap_rows[-1]["bucket_time"])) // 3600,
                    )
            except Exception:
                load = {}

    bot = state.get_bot()
    await names.refresh_guild_name_cache()
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
            "load": load,
            "load_metrics_order": [col for _, col, _ in LOAD_METRICS],
            "load_window_hours": load_window_hours,
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
