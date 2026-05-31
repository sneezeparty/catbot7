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
            "rarity_min_season": "dict[rarity_name, int] — minimum season number for a rarity to be spawn-eligible; auto-rendered as a dict section",
            "pack_tier_weights": "dict[str, float]",
            "pack_drop_chance_on_catch": "float",
            "pack_coin_variant_chance": "float [0,1] — per-open coin-flip chance for the coin variant",
            "pack_coin_ratio_wooden": "float [0,1] — coin share at Wooden tier",
            "pack_coin_ratio_celestial": "float [0,1] — coin share at Celestial tier",
            "season_starting_coins": "int (coins) — coins granted to each player wallet on season wipe; read as SEASON_STARTING_COINS alias in main.py",
            "prism_craft_coin_cost": "dict{first, base, growth, cap} — first=discount on first craft",
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
            # type_dict now includes Shadow (weight 221) and Terminator (weight 5).
            # Both are gated by rarity_min_season (season >= 2) via RARITY_MIN_SEASON alias in main.py.
            ("type_dict", "main.cattypes/cattype_lc_dict/allowedemojis (regen on reload)"),
            # rarity_min_season gates spawn eligibility per season — new entries here are effective on reload.
            ("rarity_min_season", "main.RARITY_MIN_SEASON (re-read on cat!restart) — spawn_cat skips rarities below their min season"),
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
            "perks": "list[{id, name, desc, weight, values, exclusive}]  — 16 entries (timer_add removed); 1-based position is persisted on profile.perk1/2/3, so NEVER insert/remove/reorder without a remap migration (see migrations/020)",
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
        "schema": {
            "TOGGLES": (
                "only_setupped_channels, do_reactions, do_responses, do_rain, do_catnip, "
                "auto_delete_achievements, auto_delete_catches, mute_achievements, "
                "anti_double_catch, season_announcements"
            ),
        },
        "routes": ["GET /db/server", "POST /db/server/{id}/toggle/{field}"],
        "templates": ["db_server.html", "db_server_row.html"],
        "references": [
            # season_announcements: per-guild opt-out (DEFAULT true) for the
            # "season ends tomorrow" broadcast fired by _season_announcement_loop().
            # Toggle off to suppress the announcement in a guild.
            ("server.season_announcements", "main._season_announcement_loop() — broadcast gated per guild; DEFAULT true"),
        ],
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
            # --- jobs / mafia killings columns (added phase 8) ---
            # heat — current heat level; in INT_FIELDS (admin may reset to 0 to spare a player a Pinch)
            # heat_last_decay — internal unix ts for decay bookkeeping; NOT in edit whitelist
            # faction_rep — JSONB dict[npc_key -> int]; in JSONB_FIELDS (view only)
            # jobs_completed/failed/near_missed/cats_lost_to_jobs/job_coins_won/biggest_score_value — stat counters; in INT_FIELDS for admin visibility
            # big_score_season — last season number a Big Score was attempted; INT_FIELDS
            # big_score_wins — all-time Big Score successes; INT_FIELDS
            # big_score_perk_unlocked — permanent spawn perk granted by Big Score win; BOOL_FIELDS
            # whiskers_favor_active / whiskers_favor_season — Whiskers Favor state; BOOL_FIELDS / INT_FIELDS
            # jobs_send_screen_seen / tutorial_errand_complete — UX flags; BOOL_FIELDS
            # perks_suspended_until — unix ts; set 0 in INT_FIELDS to lift Pinch early
            ("profile.heat", "main._jobs_* helpers — written on every job submission; decays hourly"),
            ("profile.faction_rep", "main._jobs_faction_rep() — per-NPC rep dict; updated on job success/failure"),
            ("profile.perks_suspended_until", "main._jobs_pinch() — catnip perks suspended for 2h after Pinch (pinch_lockout_seconds in jobs.json tuning); set 0 to lift"),
            # job_rerolls_window — count of paid job-board rerolls used in the current 12h window; escalates price via reroll_price_per_level in jobs.json tuning
            # job_rerolls_window_idx — bigint epoch-bucket index matching the 12h window (same bucketing as jobs timer); resets window counter when it advances
            ("profile.job_rerolls_window", "main._jobs_reroll (paid reroll) — incremented each paid reroll; reset when job_rerolls_window_idx advances"),
            # job_perks — JSONB list of active mafia-reward perks (third reward axis); writer is main._perks_grant.
            # Each entry: {id, granted_at, expires_at, npc, tier, charges}. Pruned lazily on read by _perks_prune.
            # NOT suspended by perks_suspended_until — that flag only gates catnip perks.
            ("profile.job_perks", "main._perks_grant() — active job perks; pruned lazily on read; NOT suspended by perks_suspended_until"),
            # season_reset_pending — boolean flag set by season rollover on profile save; cleared after
            # _maybe_show_season_reset_notice fires the ephemeral notice to the player. Transient UX
            # state; NOT in edit whitelist (admin setting it true would just surface the notice on next
            # interaction; setting it false mid-season has no effect). Flagged for review.
            # --- season recap stat counters (added 2026-05-28, migration 022) ---
            # coins_earned / roulette_coins_won / roulette_coins_bet / stock_coins_earned / stock_coins_spent —
            # bigint lifetime accumulators; incremented by _bump() across coin-gain, roulette, and stock-trade
            # paths. In INT_FIELDS for admin visibility. Per-season deltas computed as
            # lifetime - season_stat_baseline[key] by _season_diff_sql() in main.py.
            # season_stat_baseline — JSONB dict capturing a snapshot of the above counters at each season
            # rollover (_capture_season_recap_snapshot). Powers the per-server Season Recap leaderboard
            # broadcast on the 1st of each month. View-only in webui (JSONB_FIELDS).
            # Runtime artefacts: season_recap.txt (last-recapped season cursor, analogous to season_warn.txt)
            # and season_recap.json (per-guild snapshot written by _capture_season_recap_snapshot).
            # Neither is surfaced in the webui — no webui section exists for runtime cursor files.
            ("profile.coins_earned", "main._bump() — lifetime coins gained across all paths (jobs, stock, rain, casino); INT_FIELDS"),
            ("profile.roulette_coins_won", "main._bump() in /roulette — cumulative roulette winnings (including refunds); INT_FIELDS"),
            ("profile.roulette_coins_bet", "main._bump() in /roulette — cumulative amount wagered; INT_FIELDS"),
            ("profile.stock_coins_earned", "main._bump() — cumulative proceeds from stock sells; INT_FIELDS"),
            ("profile.stock_coins_spent", "main._bump() — cumulative spend on stock buys; INT_FIELDS"),
            ("profile.season_stat_baseline", "main._capture_season_recap_snapshot() — JSONB snapshot at season rollover; JSONB_FIELDS (view-only)"),
            # season_trophies — append-only JSONB list of {season:int, category:"earner"|"cats"|"heists", rank:1|2|3}
            # records. Written by _award_season_trophies() at season rollover. Shown view-only in JSONB_FIELDS.
            # Admin should not edit; mismatches between list entries would silently corrupt a player's trophy shelf.
            ("profile.season_trophies", "main._award_season_trophies() — append-only trophy list; JSONB_FIELDS (view-only)"),
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
    "jobs": {
        "source": ["config/jobs.json"],
        "schema": {
            "send_power": "dict[cat_type_str, int] — SP per rarity",
            "probability": "{k:float, floor:float, ceiling:float, near_miss_band:float}",
            "tuning": (
                "{offer_refresh_window_seconds, decline_cooldown_seconds, "
                "max_concurrent_offers, cancel_grace_seconds, heat_decay_per_hour, "
                "pinch_threshold, pinch_lockout_seconds, pinch_reset_heat, "
                "reroll_price_per_level, reroll_price_min}"
            ),
            "tiers": "dict['1'..'5', {name, difficulty_range[lo,hi], reward_coin_range[lo,hi], heat, min_catnip_level}]",
            "npcs": (
                "dict[key, {display_name, min_hire_level, tiers_offered[], hires_against[], "
                "reward_mult, heat_mult, reward_bias(read-only), rep_unlock_at_100?, "
                "ally_protection_threshold?, reward_recipes: dict[tier, list[{weight,coins,cats,pack?}]]}]"
            ),
            "targets_only": "dict[key, {display_name, min_catnip_level, owns_egirl_vault?}]",
            "big_score": (
                "{difficulty, patron_npc, target_npc, reward{eGirl,coins,perk}, "
                "near_miss_consolation_coins, heat_cost, rep_changes{success,failure}, "
                "once_per_season, perk_one_time_only, perk_spawn_extra_bonus}"
            ),
            "rep": (
                "{offerer_bonus_per_point, offerer_bonus_cap, target_difficulty_per_negative_point, "
                "target_difficulty_cap, unlock_threshold, refuse_threshold, hostile_threshold, "
                "tier_rep_gain{}, tier_rep_loss{}, failure_penalty, premium_reward_bonus_at_100, "
                "hostile_target_heat_discount, slot_weight_at_50}"
            ),
            "narrative_pools": "dict[npc_key, list[str]]",
            "narrative_pools_big_score": "list[str]",
            "complications": (
                "{base_chance_by_tier: dict[tier,float], heat_modifier: dict[low|watching|scrutiny,float], "
                "rep_discount_per_point: float, rep_discount_cap: float, "
                "sloppy_target_default_pack_tier_by_tier: dict[tier,pack_name]}"
            ),
            "complication_pools": "dict['1'..'5', list[{id,weight,phase,heat_bonus?,wall_fraction?,difficulty_mult?}]]",
            "cat_voices": "dict[rarity, {success:list[str], near_miss:list[str], total_failure:list[str]}] — 22 rarities",
            "complication_quips": "dict[event_id, dict[rarity, list[str]]] — subset of rarities per event",
            "complication_flavor": "dict[event_id, list[str]] — rarity-agnostic narrative lines",
            "perks": (
                "{max_active:int (default 5), drop_chance_by_tier: dict[tier,float], "
                "drop_pools: dict[npc_key, dict[tier, list[{id,weight}]]], "
                "catalog: dict[perk_id, {name?, desc?, tier_table: dict[tier,{duration_seconds?, charges?, ...}]}]} "
                "— Phase 1 ships empty; pools/catalog populated in Phase 4. Drops only on success outcomes."
            ),
        },
        "routes": [
            "GET /jobs",
            # send_power
            "GET /jobs/sp/{cat_type}/edit",
            "GET /jobs/sp/{cat_type}/cancel",
            "POST /jobs/sp/{cat_type}",
            # probability
            "GET /jobs/probability/edit",
            "GET /jobs/probability/cancel",
            "POST /jobs/probability",
            # tuning
            "GET /jobs/tuning/edit",
            "GET /jobs/tuning/cancel",
            "POST /jobs/tuning",
            # tiers
            "GET /jobs/tier/{tier}/edit",
            "GET /jobs/tier/{tier}/cancel",
            "POST /jobs/tier/{tier}",
            # npcs
            "GET /jobs/npc/{npc}/edit",
            "GET /jobs/npc/{npc}/cancel",
            "POST /jobs/npc/{npc}",
            # big_score
            "GET /jobs/big_score/edit",
            "GET /jobs/big_score/cancel",
            "POST /jobs/big_score",
            # rep
            "GET /jobs/rep/edit",
            "GET /jobs/rep/cancel",
            "POST /jobs/rep",
            # narrative
            "GET /jobs/narrative/{npc}/edit",
            "GET /jobs/narrative/{npc}/cancel",
            "POST /jobs/narrative/{npc}",
            # complications scalars
            "GET /jobs/complications/edit",
            "GET /jobs/complications/cancel",
            "POST /jobs/complications",
            # complication pools
            "GET /jobs/complication_pool/{tier}/edit",
            "GET /jobs/complication_pool/{tier}/cancel",
            "POST /jobs/complication_pool/{tier}",
            # reward_recipes
            "GET /jobs/npc/{npc}/recipe/{tier}/edit",
            "GET /jobs/npc/{npc}/recipe/{tier}/cancel",
            "POST /jobs/npc/{npc}/recipe/{tier}",
            # cat_voices
            "GET /jobs/voice/{rarity}/edit",
            "GET /jobs/voice/{rarity}/cancel",
            "POST /jobs/voice/{rarity}",
            # complication_quips
            "GET /jobs/quip/{event_id}/edit",
            "GET /jobs/quip/{event_id}/cancel",
            "POST /jobs/quip/{event_id}",
            # complication_flavor
            "GET /jobs/flavor/{event_id}/edit",
            "GET /jobs/flavor/{event_id}/cancel",
            "POST /jobs/flavor/{event_id}",
            # perks — drop_chance_by_tier + max_active (single form)
            "GET /jobs/perks/chances/edit",
            "GET /jobs/perks/chances/cancel",
            "POST /jobs/perks/chances",
            # perks — drop_pools[npc][tier] (per-(NPC,tier) JSON list)
            "GET /jobs/perks/pool/{npc}/{tier}/edit",
            "GET /jobs/perks/pool/{npc}/{tier}/cancel",
            "POST /jobs/perks/pool/{npc}/{tier}",
            # perks — catalog[perk_id] (per-perk name + desc + tier_table JSON)
            "GET /jobs/perks/catalog/{perk_id}/edit",
            "GET /jobs/perks/catalog/{perk_id}/cancel",
            "POST /jobs/perks/catalog/{perk_id}",
        ],
        "templates": [
            "jobs.html",
            "jobs_sp_row.html",
            "jobs_probability_form.html",
            "jobs_tuning_form.html",
            "jobs_tier_row.html",
            "jobs_npc_row.html",
            "jobs_big_score_form.html",
            "jobs_rep_form.html",
            "jobs_narrative_row.html",
            "jobs_complications_form.html",
            "jobs_complication_pool_row.html",
            "jobs_recipe_row.html",
            "jobs_voice_row.html",
            "jobs_quip_row.html",
            "jobs_flavor_row.html",
            "jobs_perks_chances_form.html",
            "jobs_perks_pool_row.html",
            "jobs_perks_catalog_row.html",
        ],
        "references": [
            # NPC tiers_offered must reference keys in tiers — checked at save time (hard block)
            ("npcs.<key>.tiers_offered", "tiers keys — save blocked if unknown tier number"),
            # hires_against: NPC/target keys or magic strings — soft warning only
            ("npcs.<key>.hires_against", "npcs/targets_only keys or 'dynamic_higher_rank'/'commoners' — soft warning"),
            # big_score patron/target referential integrity — checked at save time (hard block)
            ("big_score.patron_npc", "npcs keys — save blocked if key missing"),
            ("big_score.target_npc", "npcs or targets_only keys — save blocked if key missing"),
            # rep tier tables must match tier keys — enforced by iterating tier_keys at save time
            ("rep.tier_rep_gain keys", "tiers keys — form fields generated from tier_keys list"),
            ("rep.tier_rep_loss keys", "tiers keys — form fields generated from tier_keys list"),
            # send_power keys are the same as catnip type_dict keys in tuning.json
            ("send_power keys", "tuning.type_dict keys (main.JOBS_SEND_POWER) — adding a new rarity requires both"),
            # profile columns holding job state — read-only in current webui scope
            ("profile.job_heat", "main._jobs_* helpers — written every job submission"),
            ("profile.job_rep_*", "main._jobs_faction_rep() — per-NPC rep stored as JSONB; not in webui edit whitelist"),
            # complication_pools event ids cross-reference complication_flavor — soft warning
            ("complication_pools[*][*].id", "complication_flavor keys — soft warning if event_id has no flavor entry"),
            # complication_quips rarity keys should be known rarities — soft warning
            ("complication_quips[event_id] keys", "send_power keys (cattypes) — soft warning if rarity unknown"),
            # reward_recipes cats keys should be known rarities — soft warning
            ("npcs.<key>.reward_recipes[*].cats keys", "send_power keys (cattypes) — soft warning if rarity unknown"),
            # reward_recipes pack values must be valid pack tier names — validated on save
            ("npcs.<key>.reward_recipes[*].pack", "validators.PACK_TIER_LIST — save blocked if unknown pack tier"),
            # complications.sloppy_target pack tiers must be valid — validated on save
            ("complications.sloppy_target_default_pack_tier_by_tier[*]", "validators.PACK_TIER_LIST — save blocked if unknown"),
            # jobinstance.perk_drop — per-offer pre-rolled perk_id (text, '' = no perk).
            # Rolled at offer-generation in main._jobs_generate_offers with seeded RNG;
            # read at success time in main._jobs_apply_outcome to grant. Surfaced on
            # board / send / accept-embed / result screen. No webui editor — operator
            # tunes pools/catalog and the bot re-rolls future offers from there.
            ("jobinstance.perk_drop", "main._jobs_generate_offers — pre-rolled perk_id; read at outcome time"),
            # perks pool entries reference perks catalog — save blocked if id missing
            ("perks.drop_pools[*][*].id", "perks.catalog keys — save blocked if perk id unknown"),
            # perks pool tier keys should be in jobs.tiers — soft warning
            ("perks.drop_pools[npc][tier]", "jobs.tiers keys — soft warning if tier missing"),
            # perks pool (npc, tier) should be one this NPC actually offers — soft warning (Big Score under whiskers/5 exempted)
            ("perks.drop_pools[npc][tier]", "npcs[npc].tiers_offered — soft warning if mismatch (Big Score whiskers/5 exempted)"),
            # perks catalog tier_table keys should be in jobs.tiers — hard block on save
            ("perks.catalog[id].tier_table keys", "jobs.tiers keys — save blocked if unknown tier"),
            # drop_chance_by_tier values must be in [0,1] — hard block on save
            ("perks.drop_chance_by_tier[tier]", "float in [0,1] — save blocked if out of range"),
            # perks catalog perk used at runtime
            ("perks.catalog", "main.PERKS_CATALOG (re-read on cat!restart). Per-perk tier_table feeds _perks_grant / _perks_strength"),
            ("perks.drop_pools", "main.PERKS_DROP_POOLS — read by _perks_roll_drop at job-success time"),
            ("perks.drop_chance_by_tier", "main.PERKS_DROP_CHANCE_BY_TIER — top-die for whether a perk drops at all"),
            ("perks.max_active", "main.PERKS_MAX_ACTIVE — cap on simultaneous active perks; oldest TIMED perk evicted on overflow"),
        ],
    },
    "jobs_help": {
        "source": ["config/jobs_help.json"],
        "schema": {
            "pages": "list[{title:str, body:str (Discord Markdown), min_level_to_see:int}]",
        },
        "routes": [
            "GET /jobs/help",
            "GET /jobs/help/{i}/edit",
            "GET /jobs/help/{i}/cancel",
            "POST /jobs/help/{i}",
        ],
        "templates": ["jobs_help.html", "jobs_help_page_row.html"],
        "references": [
            # min_level_to_see is compared against profile.catnip_level at render time
            ("pages[i].min_level_to_see", "profile.catnip_level — gates page visibility in /jobs help"),
        ],
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
    "config/jobs.json",
    "config/jobs_help.json",
]
