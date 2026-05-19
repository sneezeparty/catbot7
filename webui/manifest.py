"""Webui section manifest.

Maintained by the `webui-sync` subagent (see .claude/agents/webui-sync.md).
Hand edits are allowed but the agent may overwrite them on next sync.

Each section maps to:
  - source:     the file(s) or DB tables this section reflects
  - schema:     loose shape description
  - routes:     list of HTTP routes provided
  - templates:  list of templates rendered
  - references: cross-section reference rules — e.g. "deleting a quest in
                battlepass.json is blocked if profile.catch_quest still
                points to it". Used by save handlers to refuse breaking edits.
"""

from __future__ import annotations

SECTIONS: dict[str, dict] = {
    "dashboard": {
        "source": ["live"],
        "routes": ["GET /"],
        "templates": ["dashboard.html"],
        "references": [],
    },
    "tuning": {
        "source": ["config/tuning.json"],
        "schema": {
            "<key>": "scalar or dict",
            "pack_tier_weights": "dict[str, float]",
            "pack_drop_chance_on_catch": "float",
            "stock_market": (
                "dict — deeply nested, handled by dedicated routes; "
                "top-level scalars: enabled(bool), spread(float), mm_order_quantity(int), "
                "price_floor(int), price_ceiling(int), metric_eps(float); "
                "tickers: dict[str, {base:int, baseline:float, alpha:float}]"
            ),
        },
        "routes": [
            "GET /tuning",
            "GET /tuning/scalar/{key}/edit",
            "POST /tuning/scalar/{key}",
            "GET /tuning/dict/{section}/{entry}/edit",
            "POST /tuning/dict/{section}/{entry}",
            # stock_market structured sub-routes
            "GET /tuning/stock_market/scalar/{key}/edit",
            "GET /tuning/stock_market/scalar/{key}/cancel",
            "POST /tuning/stock_market/scalar/{key}",
            "GET /tuning/stock_market/tickers/{ticker}/edit",
            "GET /tuning/stock_market/tickers/{ticker}/cancel",
            "POST /tuning/stock_market/tickers/{ticker}",
        ],
        "templates": [
            "tuning.html",
            "tuning_row.html",
            "tuning_dict_row.html",
            "tuning_sm_scalar_row.html",
            "tuning_sm_ticker_row.html",
        ],
        "references": [
            # When type_dict changes, cattypes/cattype_lc_dict/allowedemojis
            # in main.py are derived at import time. Edits require a reload.
            ("type_dict", "main.cattypes/cattype_lc_dict/allowedemojis (regen on reload)"),
            # stock_market.enabled gates _run_stock_market_maker() in background_loop.
            # stock_market.tickers keys must match stock_data ticker list in main.py.
            ("stock_market.enabled", "main.STOCK_MARKET — gates _run_stock_market_maker() in background_loop"),
            ("stock_market.tickers", "main.stock_data ticker list — adding/removing tickers here has no effect unless main.py stock_data is also updated"),
        ],
    },
    "battlepass": {
        "source": ["config/battlepass.json"],
        "schema": {
            "seasons": "dict[str, list[{xp, reward, amount}]]",
            "quests": "dict[vote|catch|misc|extra|challenge, dict[name, {emoji, title, xp_min, xp_max, progress, dynamic_reward?}]]",
        },
        "routes": [
            "GET /battlepass",
            "POST /battlepass/season/{n}/level/{i}",
            "POST /battlepass/quest/{qtype}/{name}",
            "POST /battlepass/quest/{qtype}/{name}/delete",
        ],
        "templates": ["battlepass.html", "battlepass_level_row.html", "battlepass_quest_row.html"],
        "references": [
            ("quests.catch.<name>", "profile.catch_quest (delete-guard via COUNT)"),
            ("quests.misc.<name>",  "profile.misc_quest (delete-guard via COUNT)"),
            ("quests.extra.<name>", "profile.extra_quest (delete-guard via COUNT)"),
            ("quests.challenge.<name>", "profile.challenge_quest (delete-guard via COUNT)"),
            ("seasons.<n>.<i>.reward", "cattypes / pack_data names"),
        ],
    },
    "catnip": {
        "source": ["config/catnip.json"],
        "schema": {
            "perks": "list[{id, name, desc, weight, values, exclusive}]  — 17 entries (index 10 timer_add retired weight=0, MUST NOT be removed)",
            "levels": "list[{level, name, duration, cost, bounty_*, bonus, max_amount, weights, store_discount}]  — store_discount: int [-50,+50]; negative=tax on /catstore buys, positive=discount",
            "quotes": "list[{level, name, quotes:{first, normal, levelup, leveldown}}]",
            "bounties": "list[{id, desc}]",
        },
        "routes": [
            "GET /catnip",
            "GET /catnip/perk/{i}/edit",
            "GET /catnip/perk/{i}/cancel",
            "POST /catnip/perk/{i}",
            "GET /catnip/level/{i}/edit",
            "GET /catnip/level/{i}/cancel",
            "POST /catnip/level/{i}",
        ],
        "templates": ["catnip.html", "catnip_perk_row.html", "catnip_level_row.html"],
        "references": [
            ("levels.<i>.level", "profile.catnip_level"),
            ("levels.<i>.store_discount", "main.store_discount_pct() — applied to /catstore buy price; negative values levy a tax on low-rank players"),
            ("perks.<i>.id",      "profile.perk1/perk2/perk3/perk_selected"),
            # perks[14] combo "Snowballer" reads/writes profile.combo_stack
            ("perks[14].id=combo", "profile.combo_stack (reset-on-idle counter, see INT_FIELDS in profile_table.py)"),
            # perks array is append-only — deleting any entry silently rebinds all later stored user perks
            ("perks.<i> (index)", "profile.perk1/perk2/perk3 store 1-indexed positions; deletions are FORBIDDEN"),
        ],
    },
    "server_table": {
        "source": ["db:server"],
        "routes": ["GET /db/server", "POST /db/server/{id}/toggle/{field}"],
        "templates": ["db_server.html", "db_server_row.html"],
        "references": [],
    },
    "channel_table": {
        "source": ["db:channel"],
        "routes": [
            "GET /db/channel",
            "POST /db/channel/{id}",
        ],
        "templates": ["db_channel.html", "db_channel_row.html"],
        "references": [],
    },
    "profile_table": {
        "source": ["db:profile"],
        "routes": ["GET /db/profile", "GET /db/profile/{user_id}/{guild_id}", "POST /db/profile/{user_id}/{guild_id}/{field}"],
        "templates": ["db_profile_search.html", "db_profile_edit.html"],
        "references": [
            # FOR UPDATE on profile writes — gameplay also writes here.
            ("profile.*", "main.on_message writes profiles on every catch (race-protected via FOR UPDATE)"),
            # extra_quest/progress/cooldown/reward — extra (bonus) battlepass quest track
            # catch_streak — incremented per catch, awards XP at multiples of 10
            # casino_progress_temp — bitmask for casino quest; internal state counter (not in edit whitelist)
            # catnip_xp_awarded — catnip XP cap tracker; internal counter (not in edit whitelist)
            # combo_stack — Snowballer perk consecutive-catch counter (cap 30, resets after 5-min idle); in INT_FIELDS edit whitelist
            # challenge_quest/progress/cooldown/reward — 5th battlepass quest slot (challenge track); in STR_FIELDS/INT_FIELDS
            # reminder_challenge — challenge quest DM reminder flag; in INT_FIELDS
            # gift3_recipients — comma-separated user IDs who received gifts this quest cycle (gift3 challenge quest); in STR_FIELDS
            # --- coins (unified wallet, 2026-05-19) ---
            # profile.coins is the single in-game currency wallet.
            # Migration 006 merged the now-removed profile.roulette_balance
            # (bigint, DEFAULT 100) into profile.coins via SUM; roulette_balance
            # no longer exists in schema or in any webui whitelist.
            # /roulette reads and writes profile.coins. The /leaderboards
            # "Roulette Dollars" category was renamed to "Coins".
            # --- catstore columns (added 2026-05-19) ---
            # discovered_cats — JSONB list of rarity names ever owned in this server; written by mark_discovered()
            #   in main.py (every cat-acquisition path). View-only in webui (JSONB_FIELDS).
            # store_purchased_rarities — JSONB list of rarity names ever bought from /catstore; written by
            #   mark_store_purchased() in main.py. Backs catstore_collector achievement (profile must contain
            #   all rarity names from type_dict). View-only in webui (JSONB_FIELDS).
            ("profile.discovered_cats", "main.mark_discovered() — written on every cat acquisition; used by /catstore to show owned-vs-not UI"),
            ("profile.store_purchased_rarities", "main.mark_store_purchased() + catstore_collector ach — must contain all type_dict keys for achievement to fire"),
            ("profile.store_purchased_rarities", "config/aches.json:catstore_collector — award fires when len(set(store_purchased_rarities)) == len(type_dict)"),
        ],
    },
    "user_table": {
        "source": ["db:user"],
        "routes": ["GET /db/user", "GET /db/user/{id}", "POST /db/user/{id}/{field}"],
        "templates": ["db_user.html", "db_user_detail.html"],
        "references": [],
    },
    "prism_table": {
        "source": ["db:prism"],
        "routes": ["GET /db/prism"],
        "templates": ["db_prism.html"],
        "references": [],
    },
    "order_table": {
        "source": ["db:order"],
        "schema": {
            "id": "int PK",
            "user_id": "bigint — profile.id (NOT the Discord snowflake); bot's profile has guild_id=0",
            "time": "bigint unix ts — 0 means market-maker order (recreated each MM tick)",
            "ticker": "varchar(10)",
            "type_buy": "bool",
            "quantity": "int",
            "price": "int (coins)",
        },
        "routes": ["GET /db/order"],
        "templates": ["db_order.html"],
        "references": [
            # MM orders: user_id = bot's profile id AND time = 0.
            # Identified at runtime by querying profile WHERE guild_id=0.
            # Deleting them is safe — next MM tick recreates them.
            ("order.time=0", "market-maker orders — recreated by _run_stock_market_maker() every ~5 min"),
        ],
    },
}


# Files/dirs the sync agent treats as "bot surface area". Edits here trigger
# a manifest diff. The hook script is the authoritative list; this constant
# is informational only.
TRIGGER_PATHS = [
    "main.py",
    "bot.py",
    "config.py",
    "catpg.py",
    "database.py",
    "schema.sql",
    "config/aches.json",
    "config/battlepass.json",
    "config/catnip.json",
    "config/tuning.json",
]
