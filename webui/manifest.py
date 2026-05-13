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
        "schema": {"<key>": "scalar or dict", "pack_tier_weights": "dict[str, float]", "pack_drop_chance_on_catch": "float"},
        "routes": [
            "GET /tuning",
            "GET /tuning/scalar/{key}/edit",
            "POST /tuning/scalar/{key}",
            "GET /tuning/dict/{section}/{entry}/edit",
            "POST /tuning/dict/{section}/{entry}",
        ],
        "templates": ["tuning.html", "tuning_row.html", "tuning_dict_row.html"],
        "references": [
            # When type_dict changes, cattypes/cattype_lc_dict/allowedemojis
            # in main.py are derived at import time. Edits require a reload.
            ("type_dict", "main.cattypes/cattype_lc_dict/allowedemojis (regen on reload)"),
        ],
    },
    "battlepass": {
        "source": ["config/battlepass.json"],
        "schema": {
            "seasons": "dict[str, list[{xp, reward, amount}]]",
            "quests": "dict[vote|catch|misc|extra, dict[name, {emoji, title, xp_min, xp_max, progress, dynamic_reward?}]]",
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
            ("seasons.<n>.<i>.reward", "cattypes / pack_data names"),
        ],
    },
    "catnip": {
        "source": ["config/catnip.json"],
        "schema": {
            "perks": "list[{id, name, desc, weight, values, exclusive}]",
            "levels": "list[{level, name, duration, cost, bounty_*, bonus, max_amount, weights}]",
            "quotes": "list[{level, name, quotes:{first, normal, levelup, leveldown}}]",
            "bounties": "list[{id, desc}]",
        },
        "routes": [
            "GET /catnip",
            "POST /catnip/perk/{i}",
            "POST /catnip/level/{i}",
        ],
        "templates": ["catnip.html", "catnip_perk_row.html", "catnip_level_row.html"],
        "references": [
            ("levels.<i>.level", "profile.catnip_level"),
            ("perks.<i>.id",      "profile.perk1/perk2/perk3/perk_selected"),
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
