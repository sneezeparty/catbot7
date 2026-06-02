"""Webui section manifest.

Maintained by the `webui-sync` subagent (see .claude/agents/webui-sync.md).
Hand edits are allowed but the agent may overwrite them on next sync.

The webui is a **read-only activity dashboard** — it never mutates game state
or configs. Each section therefore maps to its *data sources* (the tables,
columns, and helper functions its read queries depend on), so the sync agent
can tell when a schema/model change would break a dashboard query.

Each section maps to:
  - source:       "live" (bot/Discord), or the DB table(s) it reads
  - routes:       list of HTTP routes provided (all GET — read-only)
  - templates:    list of templates rendered
  - data_sources: the tables + columns + helpers the queries depend on. If a
                  column here is renamed/dropped in schema.sql, the query
                  breaks — that's the invariant the agent guards.

Name resolution: snowflake IDs are turned into human names by `webui/names.py`
(`guild_name`/`channel_name` from the bot's cache, registered as Jinja globals
in server.py; `resolve_users` fetches + memoizes usernames). Routes that list
user_ids pre-resolve them and pass a `unames` map to their template.
"""

from __future__ import annotations

SECTIONS: dict[str, dict] = {
    # ----------------------------------------------------------------- Insights
    "dashboard": {
        "source": ["live", "db:profile", "db:channel", "db:prism", "db:server", "db:user", "db:jobinstance"],
        "routes": ["GET /"],
        "templates": ["dashboard.html"],
        "data_sources": [
            "server (COUNT)",
            "channel.cat, channel.yet_to_spawn, channel.rain_should_end",
            "profile.total_catches, profile.packs_opened, profile.last_catch, profile.cat_<rarity>, profile.pack_<tier>",
            'profile (COUNT), "user" (COUNT), prism (COUNT), prism.catches_boosted, prism.time',
            "jobinstance.state ('offered')",
            "dashboard.py: RARITY_COLUMNS, PACK_COLUMNS, _rarity_sum_clauses, _pack_sum_clauses, _topN_with_other",
        ],
    },
    "activity": {
        "source": ["db:channel", "db:prism", "db:jobinstance", "db:profile"],
        "routes": ["GET /activity"],
        "templates": ["activity.html"],
        "data_sources": [
            "channel.cat, channel.cattype, channel.yet_to_spawn, channel.rain_should_end",
            "prism.name, prism.user_id, prism.guild_id, prism.time, prism.catches_boosted",
            "jobinstance.state, jobinstance.outcome, jobinstance.category, jobinstance.tier, jobinstance.complication, jobinstance.resolved_at",
            "profile.last_catch (recency histogram)",
            "activity.py: JOB_STATES — keep in sync with jobinstance.state values used in main.py",
        ],
    },
    "economy": {
        "source": ["db:profile", "db:pricehistory", "db:order"],
        "routes": ["GET /economy"],
        "templates": ["economy.html"],
        "data_sources": [
            "profile.coins, profile.coins_earned, profile.stock_coins_earned, profile.stock_coins_spent",
            "profile.roulette_coins_won, profile.roulette_coins_bet, profile.catslots_coins_won, profile.catslots_coins_bet",
            "pricehistory.ticker, pricehistory.price, pricehistory.time",
            'order.ticker, order.type_buy',
            "economy.py: TICKERS — mirror of main.stock_data tickers (PRSM/CTNP/PASS/ACHS/RAIN)",
        ],
    },
    "leaderboards": {
        "source": ["db:profile", "db:prism"],
        "routes": ["GET /leaderboards"],
        "templates": ["leaderboards.html"],
        "data_sources": [
            "profile.total_catches, profile.coins, profile.battlepass, profile.jobs_completed, profile.catnip_level",
            "prism (COUNT per user_id)",
            "leaderboards.py: BOARDS — one SQL per board, each returns (user_id, value)",
        ],
    },
    "commands": {
        "source": ["live"],
        "routes": ["GET /commands"],
        "templates": ["commands.html"],
        "data_sources": [
            "bot.tree.walk_commands() — reflects every @bot.tree.command registered in main.py",
        ],
    },
    # ---------------------------------------------------- Database (read-only)
    "server_table": {
        "source": ["db:server"],
        "routes": ["GET /db/server"],
        "templates": ["db_server.html"],
        "data_sources": [
            "server.server_id + the bool feature flags in server_table.py:TOGGLES",
        ],
    },
    "channel_table": {
        "source": ["db:channel"],
        "routes": ["GET /db/channel"],
        "templates": ["db_channel.html"],
        "data_sources": [
            "channel.channel_id, channel.cat, channel.cattype, channel.yet_to_spawn, channel.spawn_times_min, channel.spawn_times_max, channel.rain_should_end",
        ],
    },
    "profile_table": {
        "source": ["db:profile"],
        "routes": ["GET /db/profile", "GET /db/profile/{user_id}/{guild_id}"],
        "templates": ["db_profile_search.html", "db_profile_detail.html"],
        "data_sources": [
            "profile.* (full row in detail view)",
            "profile_table.py: INT_FIELDS / STR_FIELDS / BOOL_FIELDS / JSONB_FIELDS — display groupings; "
            "new columns can be added here to surface them, but nothing is written back",
        ],
    },
    "user_table": {
        "source": ["db:user"],
        "routes": ["GET /db/user", "GET /db/user/{id}"],
        "templates": ["db_user.html", "db_user_detail.html"],
        "data_sources": [
            '"user".* (full row in detail view)',
            "user_table.py: INT_FIELDS / STR_FIELDS / BOOL_FIELDS — display groupings",
        ],
    },
    "prism_table": {
        "source": ["db:prism"],
        "routes": ["GET /db/prism"],
        "templates": ["db_prism.html"],
        "data_sources": [
            "prism.id, prism.user_id, prism.guild_id, prism.time, prism.creator, prism.name, prism.catches_boosted",
        ],
    },
    "order_table": {
        "source": ["db:order"],
        "routes": ["GET /db/order"],
        "templates": ["db_order.html"],
        "data_sources": [
            'order.id, order.user_id, order.time, order.ticker, order.type_buy, order.quantity, order.price',
            "profile (guild_id=0 row) — resolves the market-maker user_id for annotation",
        ],
    },
}


# Files the sync agent treats as "bot surface area". Edits here can change a
# dashboard query's assumptions (a renamed/dropped column, a new model, a new
# slash command), so they trigger a manifest diff. Config JSON files are no
# longer listed — the webui stopped editing them when it went read-only. The
# hook script (.claude/hooks/webui-sync-on-edit.sh) is the authoritative list;
# this constant is informational only.
TRIGGER_PATHS = [
    "main.py",
    "bot.py",
    "config.py",
    "catpg.py",
    "database.py",
    "schema.sql",
]
