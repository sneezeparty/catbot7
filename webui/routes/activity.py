"""Activity overview: a stats-first page with KPIs and time-series charts.

Read-only. Most time-series come from the `metric_snapshot` table (hourly
aggregate counters written by main._metrics_snapshot_loop); per-day deltas
are computed via `LAG()` over those rows. The first ~24h after a fresh
deployment will have sparse charts until the snapshot history fills in.

Top-N server / user tables click through to /activity/server/{id} and
/activity/user/{id} for drilldowns.
"""

import datetime
import logging
import time

import aiohttp_jinja2
from aiohttp import web

from webui import names, state

JOB_STATES = ["offered", "committed", "resolved", "expired", "declined"]

# ---------------------------------------------------------------------------
# "Last 24 hours" feature-usage panel
# ---------------------------------------------------------------------------
# Every tile is a delta between two metric_snapshot rows ~24h apart, so each
# entry names a snapshot column holding a LIFETIME total. Columns come from
# migration 029 (the original five) and 037 (the per-feature counters, whose
# aggregates are defined in main._FEATURE_METRICS).
#
# Tuple shape: (snapshot_column, label, footnote, flags)
#   "signed" — allow a negative delta through instead of clamping at 0. Only
#              for genuinely bidirectional totals: stock_coins_earned takes
#              negative bumps on cancelled-order refunds, so a real 24h window
#              can be net-negative and clamping would hide that.
#   "fresh"  — counter introduced by migration 037 with no backfill possible.
#              While its lifetime total is still 0 the tile renders "—/no data
#              yet" rather than a 0 that reads like "nobody used this".
LAST24H_GROUPS = (
    ("Catching & packs", (
        ("total_catches", "Catches", "messages typing <code>cat</code>", ()),
        ("total_packs", "Packs opened", "<code>/packs</code> opens", ()),
        ("total_prisms", "Prisms created", "new prism rows", ()),
        ("total_prisms_crafted", "Prisms crafted", "<code>/prism</code> crafts", ()),
        ("total_rain_participations", "Rain catches", "catches during a rain", ()),
    )),
    ("Minigames", (
        ("total_bonus_offered", "Bonus offered", "prompts shown after a catch", ("fresh",)),
        ("total_bonus_played", "Bonus played", "minigames actually answered", ("fresh",)),
        ("total_bonus_won", "Bonus won", "correct answers (+3 cats)", ()),
        ("total_scratchcards_earned", "Scratchers given", "cards granted", ("fresh",)),
        ("total_scratchcards_scratched", "Scratchers scratched", "<code>/scratch</code> plays", ("fresh",)),
        ("total_chaos_clicks", "Chaos pushed", "<code>/chaos</code> button presses", ("fresh",)),
        ("total_fish_caught", "Fish caught", "<code>/fish</code> catches", ()),
        ("total_ttt_played", "Tic-tac-toe", "games played", ()),
    )),
    ("Casino", (
        ("total_catslots_spins", "Catslots spins", "<code>/catslots</code> spins", ()),
        ("total_catslots_bet", "Catslots bet", "coins wagered", ()),
        ("total_catslots_won", "Catslots won", "coins paid out", ()),
        ("total_catslots_bonus_triggers", "Catslots bonus", "bonus rounds triggered", ()),
        ("total_slot_spins", "Slots spins", "<code>/slots</code> spins", ()),
        ("total_roulette_spins", "Roulette spins", "<code>/roulette</code> spins", ()),
        ("total_roulette_bet", "Roulette bet", "coins wagered", ()),
        ("total_roulette_won", "Roulette won", "coins paid out", ()),
        ("total_gambles", "Gambles", "<code>/gamble</code> plays", ()),
    )),
    ("Coins & market", (
        ("total_coins_earned", "Coins earned", "all sources combined", ("signed",)),
        ("total_job_coins_won", "Job coins", "from <code>/jobs</code> payouts", ()),
        ("total_stock_coins_earned", "Stock proceeds", "sells + dividends, net of refunds", ("signed",)),
        ("total_stock_coins_spent", "Stock spend", "coins into buy orders", ("signed",)),
    )),
    ("Jobs", (
        ("jobs_completed_lifetime", "Jobs completed", "<code>/jobs</code> resolved", ()),
        ("jobs_failed_lifetime", "Jobs failed", "failed outcomes", ()),
    )),
    ("Progression & social", (
        ("total_quests_completed", "Quests completed", "battlepass quests", ()),
        ("total_catnip_activations", "Catnip activations", "<code>/catnip</code> used", ()),
        ("total_cats_gifted", "Cats gifted", "<code>/gift</code> sends", ()),
        ("total_trades_completed", "Trades", "<code>/trade</code> completions", ()),
    )),
)

# Flat list of every snapshot column the panel wants, in declaration order.
LAST24H_COLUMNS = tuple(
    col for _title, metrics in LAST24H_GROUPS for col, _l, _f, _flags in metrics
)

# Selectable windows for the feature-usage panel. metric_snapshot holds hourly
# rows indefinitely (nothing prunes it), so a longer window costs exactly the
# same two indexed lookups as the short one — only the target timestamp moves.
# ?window=<key>; anything unrecognised falls back to the first entry.
USAGE_WINDOWS = (
    ("24h", "24 hours", 86400),
    ("7d", "7 days", 7 * 86400),
    ("30d", "30 days", 30 * 86400),
    ("90d", "90 days", 90 * 86400),
)
USAGE_WINDOW_DEFAULT = USAGE_WINDOWS[0][0]


async def _usage_delta_rows(conn, columns, span_seconds):
    """(latest_row, previous_row) for a window `span_seconds` wide.

    Two PK-indexed lookups rather than the old "fetch the 50 newest and scan"
    — 50 hourly rows only reach back two days, which was fine when 24h was the
    only window and silently wrong for anything longer.

    Falls back to the oldest row when history doesn't span the full window, so
    a 90d view on 55d of data reports 55d rather than nothing. Returns
    (None, None) when there aren't two distinct rows to difference.
    """
    cols = ", ".join(columns)
    latest = await conn.fetchrow(
        f"SELECT bucket_time, {cols} FROM metric_snapshot ORDER BY bucket_time DESC LIMIT 1"
    )
    if latest is None:
        return None, None
    target = int(latest["bucket_time"]) - span_seconds
    prev = await conn.fetchrow(
        f"SELECT bucket_time, {cols} FROM metric_snapshot "
        "WHERE bucket_time <= $1 ORDER BY bucket_time DESC LIMIT 1",
        target,
    )
    if prev is None:
        prev = await conn.fetchrow(
            f"SELECT bucket_time, {cols} FROM metric_snapshot ORDER BY bucket_time ASC LIMIT 1"
        )
    if prev is None or int(prev["bucket_time"]) == int(latest["bucket_time"]):
        return None, None
    return latest, prev


async def _snapshot_columns(conn) -> set:
    """Which of LAST24H_COLUMNS actually exist on metric_snapshot.

    Migration 037 adds most of them; on an un-migrated DB the panel simply
    renders the groups it can fill and drops the rest, rather than 500ing.
    """
    try:
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'metric_snapshot' "
            "AND column_name = ANY($1::text[])",
            list(LAST24H_COLUMNS),
        )
        return {r["column_name"] for r in rows}
    except Exception:
        return set()


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
    last24h = {"window_hours": 0, "window_days": 0, "truncated": False}
    # [(group_title, [tile, ...])] — built below from LAST24H_GROUPS, filtered
    # to the columns this DB actually has.
    last24h_groups: list = []

    # ?window= selects how far back the feature-usage panel differences.
    window_key = request.query.get("window", USAGE_WINDOW_DEFAULT)
    if window_key not in {k for k, _l, _s in USAGE_WINDOWS}:
        window_key = USAGE_WINDOW_DEFAULT
    window_seconds = next(s for k, _l, s in USAGE_WINDOWS if k == window_key)
    window_name = next(lbl for k, lbl, _s in USAGE_WINDOWS if k == window_key)

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
                    have = await _snapshot_columns(conn)
                    wanted = [c for c in LAST24H_COLUMNS if c in have]
                    latest_row, prev_row = (
                        await _usage_delta_rows(conn, wanted, window_seconds)
                        if wanted else (None, None)
                    )
                    if latest_row is not None:
                        span = max(1, int(latest_row["bucket_time"]) - int(prev_row["bucket_time"]))
                        last24h["window_hours"] = max(1, span // 3600)
                        last24h["window_days"] = round(span / 86400.0, 1)
                        # True when history doesn't reach back the full window,
                        # so the page can say "43 of 90 days" instead of
                        # implying it covered the period asked for.
                        last24h["truncated"] = span < window_seconds - 3600

                        for title, metrics in LAST24H_GROUPS:
                            tiles = []
                            for col, label, foot, flags in metrics:
                                if col not in have:
                                    continue
                                latest = int(latest_row[col] or 0)
                                delta = latest - int(prev_row[col] or 0)
                                if "signed" not in flags:
                                    # A counter can legitimately drop: season
                                    # rollover wipes some, prism rows get
                                    # deleted. Clamp so one rollover hour shows
                                    # "no activity" instead of a negative spike.
                                    delta = max(0, delta)
                                tiles.append({
                                    "key": col,
                                    "label": label,
                                    "foot": foot,
                                    "value": delta,
                                    # A brand-new counter that has never been
                                    # written can't be distinguished from a
                                    # genuine zero by its delta alone — the
                                    # lifetime total can.
                                    "pending": "fresh" in flags and latest == 0,
                                })
                            if tiles:
                                last24h_groups.append((title, tiles))
                except Exception:
                    logging.exception("last-24h panel failed")

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
            "last24h_groups": last24h_groups,
            "usage_windows": USAGE_WINDOWS,
            "window_key": window_key,
            "window_name": window_name,
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
