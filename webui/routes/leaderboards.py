"""Leaderboards: top users across several axes.

Read-only. Each board aggregates per user_id across all their (user, guild)
profiles, mirroring the SQL shapes used by main.py's /leaderboards command.

Each board paginates independently via `?<key>_page=N` (e.g. `?coins_page=2`),
so flipping through "Coins" doesn't disturb "Catches". Look-ahead style:
we fetch `per_page + 1` rows to know whether a Next link is warranted, which
avoids a separate `COUNT(DISTINCT user_id)` per board (those would be the
slowest queries on the page on a many-million-row `profile` table).
"""

import aiohttp_jinja2
from aiohttp import web

from webui import names, state
from webui.pagination import make_pager, parse_page

PER_PAGE = 15

# (key, title, unit, sql) — sql returns (user_id, value) rows, value DESC.
# `$1 = bot_id (excluded)`, `$2 = LIMIT`, `$3 = OFFSET`.
BOARDS = [
    ("catches", "Catches", "catches",
     "SELECT user_id, SUM(total_catches)::bigint AS value FROM profile "
     "WHERE user_id <> $1 "
     "GROUP BY user_id HAVING SUM(total_catches) > 0 "
     "ORDER BY value DESC NULLS LAST LIMIT $2 OFFSET $3"),
    ("coins", "Coins", "coins",
     "SELECT user_id, SUM(coins)::bigint AS value FROM profile "
     "WHERE user_id <> $1 "
     "GROUP BY user_id HAVING SUM(coins) > 0 "
     "ORDER BY value DESC NULLS LAST LIMIT $2 OFFSET $3"),
    ("prisms", "Prisms crafted", "prisms",
     "SELECT user_id, COUNT(*)::bigint AS value FROM prism "
     "WHERE user_id <> $1 "
     "GROUP BY user_id ORDER BY value DESC NULLS LAST LIMIT $2 OFFSET $3"),
    ("battlepass", "Highest battlepass", "level",
     "SELECT user_id, MAX(battlepass)::bigint AS value FROM profile "
     "WHERE user_id <> $1 "
     "GROUP BY user_id HAVING MAX(battlepass) > 0 "
     "ORDER BY value DESC NULLS LAST LIMIT $2 OFFSET $3"),
    ("jobs", "Jobs completed", "jobs",
     "SELECT user_id, SUM(jobs_completed)::bigint AS value FROM profile "
     "WHERE user_id <> $1 "
     "GROUP BY user_id HAVING SUM(jobs_completed) > 0 "
     "ORDER BY value DESC NULLS LAST LIMIT $2 OFFSET $3"),
    ("catnip", "Highest catnip level", "level",
     "SELECT user_id, MAX(catnip_level)::bigint AS value FROM profile "
     "WHERE user_id <> $1 "
     "GROUP BY user_id HAVING MAX(catnip_level) > 0 "
     "ORDER BY value DESC NULLS LAST LIMIT $2 OFFSET $3"),
    ("bonus", "Bonus catches", "wins",
     "SELECT user_id, SUM(bonus_catches)::bigint AS value FROM profile "
     "WHERE user_id <> $1 "
     "GROUP BY user_id HAVING SUM(bonus_catches) > 0 "
     "ORDER BY value DESC NULLS LAST LIMIT $2 OFFSET $3"),
    ("fish", "Fish caught", "fish",
     "SELECT user_id, SUM(fish_caught)::bigint AS value FROM profile "
     "WHERE user_id <> $1 "
     "GROUP BY user_id HAVING SUM(fish_caught) > 0 "
     "ORDER BY value DESC NULLS LAST LIMIT $2 OFFSET $3"),
]


async def index(request):
    pool = state.get_pool()
    boards: list = []
    # `extra_qs` preserves every other board's page param when we render
    # one board's prev/next links — so paging "Coins" doesn't reset "Catches".
    page_keys = {key: f"{key}_page" for key, _, _, _ in BOARDS}
    page_for = {key: parse_page(request, page_keys[key]) for key, _, _, _ in BOARDS}

    bot_id = state.bot_user_id_or_zero()

    if pool is not None:
        async with pool.acquire() as conn:
            for key, title, unit, sql in BOARDS:
                page = page_for[key]
                offset = (page - 1) * PER_PAGE
                # Fetch one extra to detect a next page without a COUNT.
                rows = await conn.fetch(sql, bot_id, PER_PAGE + 1, offset)
                has_next = len(rows) > PER_PAGE
                rows = rows[:PER_PAGE]
                entries = [
                    {
                        "user_id": r["user_id"],
                        "value": int(r["value"] or 0),
                        "rank": offset + i + 1,
                    }
                    for i, r in enumerate(rows) if (r["value"] or 0) > 0
                ]
                top = entries[0]["value"] if entries else 1
                extra_qs = {
                    page_keys[k]: page_for[k]
                    for k in page_keys
                    if k != key and page_for[k] > 1
                }
                pager = make_pager(
                    request,
                    page=page,
                    per_page=PER_PAGE,
                    has_next=has_next,
                    page_key=page_keys[key],
                    base_path="/leaderboards",
                    params=extra_qs,
                    target=f"#pager-lb-{key}",
                )
                boards.append({
                    "key": key, "title": title, "unit": unit,
                    "entries": entries, "top": top, "pager": pager,
                })
    await names.refresh_guild_name_cache()
    unames = await names.resolve_users(
        state.get_bot(), [e["user_id"] for b in boards for e in b["entries"]]
    )
    return aiohttp_jinja2.render_template(
        "leaderboards.html",
        request,
        {
            "title": "Leaderboards",
            "active_section": "leaderboards",
            "boards": boards,
            "per_page": PER_PAGE,
            "unames": unames,
        },
    )


def register(app: web.Application) -> None:
    app.router.add_get("/leaderboards", index)
