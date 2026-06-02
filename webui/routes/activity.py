"""Activity: live spawns, rains, the jobs pipeline, recency, recent prisms.

Read-only. The schema logs no per-catch event row, so 'activity over time' is
derived from profile.last_catch (a most-recent-catch recency distribution),
which the template labels honestly.
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
    window_start = today_start - 29 * 86400  # 30 day-buckets including today

    spawns: list = []
    rains: list = []
    recent_prisms: list = []
    job_states: list = []
    recent_jobs: list = []
    recency: list = []  # [(YYYY-MM-DD, count)] of profiles by last_catch day

    if pool is not None:
        async with pool.acquire() as conn:
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
                'FROM prism ORDER BY "time" DESC NULLS LAST LIMIT 20'
            )
            job_rows = await conn.fetch(
                "SELECT state, COUNT(*) AS n FROM jobinstance GROUP BY state"
            )
            counts_by_state = {r["state"]: int(r["n"]) for r in job_rows}
            job_states = [(s, counts_by_state.get(s, 0)) for s in JOB_STATES]
            # any states not in our known list
            for s, n in counts_by_state.items():
                if s not in JOB_STATES:
                    job_states.append((s, n))

            recent_jobs = await conn.fetch(
                "SELECT user_id, guild_id, category, tier, outcome, complication, resolved_at "
                "FROM jobinstance WHERE state = 'resolved' "
                "ORDER BY resolved_at DESC LIMIT 15"
            )

            recency_rows = await conn.fetch(
                """
                SELECT to_char(date_trunc('day', to_timestamp(last_catch)), 'YYYY-MM-DD') AS day,
                       COUNT(*) AS n
                FROM profile
                WHERE last_catch >= $1
                GROUP BY day
                ORDER BY day ASC
                """,
                window_start,
            )
            recency = [(r["day"], int(r["n"])) for r in recency_rows]

    uname_ids = [p["user_id"] for p in recent_prisms] + [j["user_id"] for j in recent_jobs]
    unames = await names.resolve_users(state.get_bot(), uname_ids)

    return aiohttp_jinja2.render_template(
        "activity.html",
        request,
        {
            "title": "Activity",
            "active_section": "activity",
            "now": now,
            "spawns": spawns,
            "rains": rains,
            "recent_prisms": recent_prisms,
            "job_states": job_states,
            "recent_jobs": recent_jobs,
            "recency": recency,
            "unames": unames,
        },
    )


def register(app: web.Application) -> None:
    app.router.add_get("/activity", index)
