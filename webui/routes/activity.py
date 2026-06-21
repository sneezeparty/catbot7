"""Activity overview: a stats-first page with KPIs and time-series charts.

Read-only. Most time-series come from the `metric_snapshot` table (hourly
aggregate counters written by main._metrics_snapshot_loop); per-day deltas
are computed via `LAG()` over those rows. The first ~24h after a fresh
deployment will have sparse charts until the snapshot history fills in.

Top-N server / user tables click through to /activity/server/{id} and
/activity/user/{id} for drilldowns.
"""

import datetime
import time

import aiohttp_jinja2
from aiohttp import web

from webui import names, state

JOB_STATES = ["offered", "committed", "resolved", "expired", "declined"]


async def index(request):
    pool = state.get_pool()
    now = int(time.time())
    today_start = int(
        datetime.datetime.now(datetime.timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )
    week_start = today_start - 6 * 86400
    month_start = today_start - 29 * 86400
    window_start = today_start - 29 * 86400

    bot = state.get_bot()

    # ---- live tiles ----
    live = {
        "guild_count": len(bot.guilds) if bot else 0,
        "setupped_channels": 0,
        "profile_count": 0,
        "user_count": 0,
        "live_spawns": 0,
        "active_rains": 0,
        "pending_jobs_offered": 0,
        "pending_jobs_committed": 0,
    }
    activity_counts = {"today": 0, "week": 0, "month": 0}

    # Event counts over the last ~24h, computed by differencing the latest
    # metric_snapshot row against one ~24h old (falls back to the oldest
    # snapshot if there isn't enough history yet). window_hours reflects the
    # actual span covered so the UI can label it honestly.
    last24h = {
        "catches": 0, "packs": 0, "jobs_completed": 0,
        "jobs_failed": 0, "prisms": 0, "window_hours": 0,
    }

    # ---- time-series ----
    catches_per_day: list = []     # [(day, catches)]
    coins_per_day: list = []       # [(day, coins)]
    jobs_per_day_by_outcome: dict = {}  # {day: {outcome: n}}
    jobs_outcomes_seen: list = []  # ordered, unique
    jobs_by_category: list = []    # [(category, n)]
    jobs_by_tier: list = []        # [(tier_label, n)]
    prisms_per_week: list = []     # [(week, n)]
    orders_per_day_buy: list = []  # [(day, n)]
    orders_per_day_sell: list = [] # [(day, n)]
    recency: list = []             # [(day, n)] last_catch histogram

    # ---- tables ----
    top_servers: list = []         # [{guild_id, catches, coins, profile_count}]
    top_users: list = []           # [{user_id, catches}]
    job_states: list = []          # [(state, n)]
    recent_jobs: list = []
    spawns: list = []
    rains: list = []
    recent_prisms: list = []

    snapshot_rows = 0              # for the "warming up" message
    snapshot_oldest = 0

    bot_id = state.bot_user_id_or_zero()

    if pool is not None:
        async with pool.acquire() as conn:
            # --- live counters ---
            # Every row in `channel` represents a /setup'd channel — /forget
            # deletes the row. The older filter `cat<>0 OR yet_to_spawn<>0`
            # missed channels in the brief window after a catch where both
            # fields are 0, and disagreed with the Announcements broadcaster
            # count.
            live["setupped_channels"] = await conn.fetchval(
                "SELECT COUNT(*) FROM channel"
            ) or 0
            live["profile_count"] = await conn.fetchval(
                "SELECT COUNT(*) FROM profile WHERE user_id <> $1", bot_id
            ) or 0
            live["user_count"] = await conn.fetchval(
                'SELECT COUNT(*) FROM "user" WHERE user_id <> $1', bot_id
            ) or 0
            live["live_spawns"] = await conn.fetchval(
                "SELECT COUNT(*) FROM channel WHERE cat <> 0"
            ) or 0
            live["active_rains"] = await conn.fetchval(
                "SELECT COUNT(*) FROM channel WHERE rain_should_end > $1", now,
            ) or 0
            live["pending_jobs_offered"] = await conn.fetchval(
                "SELECT COUNT(*) FROM jobinstance WHERE state = 'offered' AND user_id <> $1",
                bot_id,
            ) or 0
            live["pending_jobs_committed"] = await conn.fetchval(
                "SELECT COUNT(*) FROM jobinstance WHERE state = 'committed' AND user_id <> $1",
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
            activity_counts = {
                "today": int(row["today"] or 0),
                "week":  int(row["week"]  or 0),
                "month": int(row["month"] or 0),
            }

            # --- snapshot-derived time series (may be empty if table missing) ---
            try:
                meta = await conn.fetchrow(
                    "SELECT COUNT(*) AS n, COALESCE(MIN(bucket_time), 0) AS oldest FROM metric_snapshot"
                )
                snapshot_rows = int(meta["n"] or 0)
                snapshot_oldest = int(meta["oldest"] or 0)
            except Exception:
                snapshot_rows = 0

            if snapshot_rows >= 2:
                try:
                    delta_rows = await conn.fetch(
                        "SELECT bucket_time, total_catches, total_packs, "
                        "jobs_completed_lifetime, jobs_failed_lifetime, total_prisms "
                        "FROM metric_snapshot ORDER BY bucket_time DESC LIMIT 50"
                    )
                    if len(delta_rows) >= 2:
                        latest_row = delta_rows[0]
                        target_bucket = int(latest_row["bucket_time"]) - 86400
                        prev_row = next(
                            (r for r in delta_rows[1:] if int(r["bucket_time"]) <= target_bucket),
                            delta_rows[-1],
                        )
                        span = max(1, int(latest_row["bucket_time"]) - int(prev_row["bucket_time"]))
                        last24h = {
                            "catches": max(0, int(latest_row["total_catches"] or 0) - int(prev_row["total_catches"] or 0)),
                            "packs": max(0, int(latest_row["total_packs"] or 0) - int(prev_row["total_packs"] or 0)),
                            "jobs_completed": max(0, int(latest_row["jobs_completed_lifetime"] or 0) - int(prev_row["jobs_completed_lifetime"] or 0)),
                            "jobs_failed": max(0, int(latest_row["jobs_failed_lifetime"] or 0) - int(prev_row["jobs_failed_lifetime"] or 0)),
                            "prisms": max(0, int(latest_row["total_prisms"] or 0) - int(prev_row["total_prisms"] or 0)),
                            "window_hours": max(1, span // 3600),
                        }
                except Exception:
                    pass

            if snapshot_rows:
                try:
                    rows = await conn.fetch(
                        """
                        WITH hourly AS (
                          SELECT bucket_time,
                                 total_catches,
                                 LAG(total_catches) OVER (ORDER BY bucket_time) AS prev_total
                          FROM metric_snapshot
                          WHERE bucket_time >= $1
                        )
                        SELECT to_char(to_timestamp((bucket_time / 86400) * 86400), 'YYYY-MM-DD') AS day,
                               SUM(GREATEST(total_catches - COALESCE(prev_total, total_catches), 0))::bigint AS catches
                        FROM hourly
                        GROUP BY day
                        ORDER BY day ASC
                        """,
                        window_start,
                    )
                    catches_per_day = [(r["day"], int(r["catches"] or 0)) for r in rows]
                except Exception:
                    catches_per_day = []

                try:
                    rows = await conn.fetch(
                        """
                        SELECT to_char(to_timestamp((bucket_time / 86400) * 86400), 'YYYY-MM-DD') AS day,
                               MAX(coins_in_circulation)::bigint AS coins
                        FROM metric_snapshot
                        WHERE bucket_time >= $1
                        GROUP BY day
                        ORDER BY day ASC
                        """,
                        window_start,
                    )
                    coins_per_day = [(r["day"], int(r["coins"] or 0)) for r in rows]
                except Exception:
                    coins_per_day = []

            # --- jobs by day / category / tier ---
            rows = await conn.fetch(
                """
                SELECT to_char(date_trunc('day', to_timestamp(resolved_at)), 'YYYY-MM-DD') AS day,
                       outcome,
                       COUNT(*) AS n
                FROM jobinstance
                WHERE state = 'resolved' AND resolved_at >= $1 AND user_id <> $2
                GROUP BY day, outcome
                ORDER BY day ASC
                """,
                window_start, bot_id,
            )
            outcomes_set: list[str] = []
            for r in rows:
                day = r["day"]
                oc = r["outcome"] or "—"
                jobs_per_day_by_outcome.setdefault(day, {})[oc] = int(r["n"])
                if oc not in outcomes_set:
                    outcomes_set.append(oc)
            jobs_outcomes_seen = outcomes_set

            rows = await conn.fetch(
                """
                SELECT category, COUNT(*) AS n FROM jobinstance
                WHERE state = 'resolved' AND resolved_at >= $1 AND user_id <> $2
                GROUP BY category ORDER BY n DESC
                """,
                window_start, bot_id,
            )
            jobs_by_category = [(r["category"] or "—", int(r["n"])) for r in rows]

            rows = await conn.fetch(
                """
                SELECT tier, COUNT(*) AS n FROM jobinstance
                WHERE state = 'resolved' AND resolved_at >= $1 AND user_id <> $2
                GROUP BY tier ORDER BY tier ASC
                """,
                window_start, bot_id,
            )
            jobs_by_tier = [(f"T{int(r['tier'])}", int(r["n"])) for r in rows]

            # --- prisms per week ---
            rows = await conn.fetch(
                """
                SELECT to_char(date_trunc('week', to_timestamp("time")), 'YYYY-MM-DD') AS week,
                       COUNT(*) AS n
                FROM prism
                WHERE "time" >= $1 AND user_id <> $2
                GROUP BY week ORDER BY week ASC
                """,
                now - 12 * 7 * 86400, bot_id,
            )
            prisms_per_week = [(r["week"], int(r["n"])) for r in rows]

            # --- orders per day (buy vs sell) ---
            # order.user_id is profile.id (not Discord), so the bot filter is a
            # subselect of the bot's profile rows.
            rows = await conn.fetch(
                """
                SELECT to_char(date_trunc('day', to_timestamp("time")), 'YYYY-MM-DD') AS day,
                       type_buy,
                       COUNT(*) AS n
                FROM "order"
                WHERE "time" >= $1
                  AND user_id NOT IN (SELECT id FROM profile WHERE user_id = $2)
                GROUP BY day, type_buy
                ORDER BY day ASC
                """,
                window_start, bot_id,
            )
            buy_map: dict[str, int] = {}
            sell_map: dict[str, int] = {}
            day_keys: list[str] = []
            for r in rows:
                d = r["day"]
                if d not in day_keys:
                    day_keys.append(d)
                if r["type_buy"]:
                    buy_map[d] = int(r["n"])
                else:
                    sell_map[d] = int(r["n"])
            orders_per_day_buy = [(d, buy_map.get(d, 0)) for d in day_keys]
            orders_per_day_sell = [(d, sell_map.get(d, 0)) for d in day_keys]

            # --- recency histogram ---
            rows = await conn.fetch(
                """
                SELECT to_char(date_trunc('day', to_timestamp(last_catch)), 'YYYY-MM-DD') AS day,
                       COUNT(*) AS n
                FROM profile
                WHERE last_catch >= $1 AND user_id <> $2
                GROUP BY day
                ORDER BY day ASC
                """,
                window_start, bot_id,
            )
            recency = [(r["day"], int(r["n"])) for r in rows]

            # --- top servers / top users ---
            # guild_id=0 is the bot's own legacy pseudo-profile (the user_id
            # is the bot's, left over from the old activity-driven market
            # maker that owned bid/ask orders). The simulated-market engine
            # no longer uses it, but the row persists and would contaminate
            # rollups — exclude both that and the live bot user_id.
            rows = await conn.fetch(
                """
                SELECT guild_id,
                       SUM(total_catches)::bigint AS catches,
                       SUM(coins)::bigint AS coins,
                       COUNT(*) AS profile_count
                FROM profile
                WHERE guild_id <> 0 AND user_id <> $1
                GROUP BY guild_id
                ORDER BY catches DESC NULLS LAST
                LIMIT 10
                """,
                bot_id,
            )
            top_servers = [
                {
                    "guild_id": r["guild_id"],
                    "catches": int(r["catches"] or 0),
                    "coins": int(r["coins"] or 0),
                    "profile_count": int(r["profile_count"] or 0),
                }
                for r in rows
            ]

            rows = await conn.fetch(
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
            top_users = [
                {"user_id": r["user_id"], "catches": int(r["catches"] or 0)}
                for r in rows
            ]

            # --- jobs pipeline + recent jobs (kept) ---
            job_rows = await conn.fetch(
                "SELECT state, COUNT(*) AS n FROM jobinstance WHERE user_id <> $1 GROUP BY state",
                bot_id,
            )
            counts_by_state = {r["state"]: int(r["n"]) for r in job_rows}
            job_states = [(s, counts_by_state.get(s, 0)) for s in JOB_STATES]
            for s, n in counts_by_state.items():
                if s not in JOB_STATES:
                    job_states.append((s, n))

            recent_jobs = await conn.fetch(
                "SELECT user_id, guild_id, category, tier, outcome, complication, resolved_at "
                "FROM jobinstance WHERE state = 'resolved' AND user_id <> $1 "
                "ORDER BY resolved_at DESC LIMIT 15",
                bot_id,
            )

            # --- live ops tables (kept, collapsed) ---
            spawns = await conn.fetch(
                "SELECT channel_id, cattype, yet_to_spawn FROM channel "
                "WHERE cat <> 0 ORDER BY channel_id LIMIT 200"
            )
            rains = await conn.fetch(
                "SELECT channel_id, rain_should_end FROM channel "
                "WHERE rain_should_end > $1 ORDER BY rain_should_end DESC LIMIT 100",
                now,
            )
            recent_prisms = await conn.fetch(
                'SELECT name, user_id, guild_id, "time", catches_boosted '
                'FROM prism WHERE user_id <> $1 ORDER BY "time" DESC NULLS LAST LIMIT 20',
                bot_id,
            )

    # --- pivot jobs/day into rows for stacked bar ---
    jobs_day_keys = sorted(jobs_per_day_by_outcome.keys())
    jobs_per_day_stacked = {
        oc: [int(jobs_per_day_by_outcome.get(d, {}).get(oc, 0)) for d in jobs_day_keys]
        for oc in jobs_outcomes_seen
    }

    # --- name resolution ---
    await names.refresh_guild_name_cache()
    uname_ids: list[int] = []
    uname_ids += [u["user_id"] for u in top_users]
    uname_ids += [j["user_id"] for j in recent_jobs]
    uname_ids += [p["user_id"] for p in recent_prisms]
    unames = await names.resolve_users(bot, uname_ids)

    snapshot_warmup = snapshot_rows < 24

    return aiohttp_jinja2.render_template(
        "activity.html",
        request,
        {
            "title": "Activity",
            "active_section": "activity",
            "now": now,
            "live": live,
            "activity_counts": activity_counts,
            "last24h": last24h,
            "catches_per_day": catches_per_day,
            "coins_per_day": coins_per_day,
            "jobs_day_keys": jobs_day_keys,
            "jobs_per_day_stacked": jobs_per_day_stacked,
            "jobs_outcomes_seen": jobs_outcomes_seen,
            "jobs_by_category": jobs_by_category,
            "jobs_by_tier": jobs_by_tier,
            "prisms_per_week": prisms_per_week,
            "orders_per_day_buy": orders_per_day_buy,
            "orders_per_day_sell": orders_per_day_sell,
            "recency": recency,
            "top_servers": top_servers,
            "top_users": top_users,
            "job_states": job_states,
            "recent_jobs": recent_jobs,
            "spawns": spawns,
            "rains": rains,
            "recent_prisms": recent_prisms,
            "snapshot_rows": snapshot_rows,
            "snapshot_oldest": snapshot_oldest,
            "snapshot_warmup": snapshot_warmup,
            "unames": unames,
        },
    )


def register(app: web.Application) -> None:
    app.router.add_get("/activity", index)
