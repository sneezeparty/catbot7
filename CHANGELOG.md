# Changelog

All notable user-facing changes to Cat Bot are tracked here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project does not currently version with semver tags; entries are grouped by release date or by "[Unreleased]" for the working branch.

The [`changelog-sync`](.claude/agents/changelog-sync.md) subagent updates the `[Unreleased]` section whenever bot-surface files change. Curated wording lives here; the agent appends drafts and flags entries with `> _draft_` until a human approves and de-drafts them.

## [0.0.5.151919052026]

### Added
- **16 new job achievements.** Six per-NPC "first job" aches (Whiskers's Right Hand, Junior's Crew, Vibes Confirmed, On The Books, The Don's Errand, Sofia's Favorite — fire on first successful job for each NPC). Plus Blood On My Hands (first total wipe), Wise Guy (10 jobs completed), Heavy Crew (send 100+ cats), Bringing The Big Guns (send a Legendary+), Top Shelf (send an eGirl — 600 XP), Lone Wolf (succeed with 1 cat), Five-of-a-Kind (5 distinct rarities), Things Happen (first complication), Easy Money (first easy_mark proc), Stone Cold (commit at heat 0). Crew-flex aches fire on any outcome; outcome aches fire on the relevant outcome only.
- **Three new Cat Store battlepass quests** in the `extra` pool: `store_buy` (buy a cat • 240-300 XP), `store_sell` (sell a cat • 220-280 XP), `store_spree` (spend 5,000+ coins on a single store purchase • 320-400 XP). Wired into the buy/sell modals via `progress(...)` calls.

### Changed
- **Reward recipe rebalance — more cats and packs, in-character.** Per-NPC bumps so the "coins + cats/pack" rate goes from ~75% to ~88% overall, and the pack-anywhere rate from ~11% to ~18%. Per-NPC notes: Whiskers T2 pack 5→10% / T3 pack 10→15%; Lucian Jr T1 pack 0→10% + cat rate 30→65% (he grabs an extra crate dad doesn't know about); Jinx T1-3 now has packs at 10-15% and cats at 70-80% (her first pack drops); Jeremy T2 stays mostly coin (60%) but gains a 25% Stone pack and a 15% Good-cat tail (the laundering "gratuity"); Lucian Sr T2-3 gets first packs at 10% (Stone at T2, Bronze at T3 — old crates from his prime); Sofia T3 pack 20→25%. Jeremy still reads as the coin guy; the cat-flavored NPCs still pay cats. See `docs/design/jobs.md` for recipe philosophy.
- **`job_easy` quest progress lowered from 2 → 1.** Title updated to "Complete a job for the mafia." Pairs with the daily 3-job cap so a single job-day still satisfies the quest.
- **Daily-cap gate moved earlier.** Clicking Accept on a job offer now rechecks the daily commit count BEFORE the public Accept embed posts. If you're at 3/3 you see an ephemeral "come back tomorrow" — no public announcement of a job you can't actually run. Accept buttons on the board also render gray with "Daily limit hit" when at cap.

### Removed
- **`/store` slash command removed.** It was a 3-line upstream stub linking to `catbot.shop` (the public bot's monetization endpoint), useless for self-hosted. `/catstore`, the actual in-bot cat marketplace, is unaffected.

### Fixed
- **`achemb` no longer crashes when called with `"reply"` send_type on an Interaction object.** `do_funny` (button-click handler) was passing an Interaction to `achemb(message, "curious", "reply")`, which then tried `message.reply()` — only valid on `discord.Message`. Direct fix: `do_funny` now uses `"followup"`. Defensive guard: `achemb` falls back to followup with a warning log when `reply` is requested on a non-Message object.

## [0.0.5.142819052026]

### Added
- **Jobs — public outcome embeds.** Two new in-channel announcements: a thematic embed when a player **accepts** a contract (NPC-specific flavor, tier, target, difficulty, narrative quote), and one when a job **resolves** (success / near-miss / wipe — with the reward summary, complication block if one fired, and a pinch footer if heat hit 100). Both are public to the channel where `/jobs` was invoked. Fire-and-forget posts; channel-send failures can't block the ephemeral send/result screens. Text lives in `config/jobs.json → accept_announcements`, `accept_announcements_big_score`, `outcome_announcements`.
- **Jobs — cat dialogue.** After every resolve, one cat from the crew gets the last word on the result screen as a quote block. Survivors speak on success/near-miss; casualties speak posthumously on total failure. The picker weights candidates by `count × (1 / spawn_weight)` — rare cats are likelier to speak even from a Fine-stack crew. Every rarity has a one-note voice in `config/jobs.json → cat_voices` (Sus says ඞ things, Rickroll quotes song lyrics, Professor is academic, eGirl is gen-Z, 8bit is glitched ASCII, etc.) — 22 rarities × 3 outcomes × 3-5 lines per cell. A small `complication_quips` block lets specific events pull a themed line in preference to the generic one (Sus cat on `cat_police_raid` → "i told you that guy was a fed").
- **Jobs — reward recipes per (NPC, tier).** Replaced the `reward_bias` enum-knob with explicit weighted recipe tables in `config/jobs.json` per NPC. Each recipe entry is `{weight, coins range, cats dict, pack tier}`. Pack rewards land in `/packs` inventory (existing `pack_{tier}` columns), not auto-opened. NPCs now feel mechanically distinct: Whiskers balanced with a T4 Silver-pack jackpot; Lucian Jr coin-leaning with a T2 Stone tail; Jinx low-heat coin grinder; Jeremy mostly pure coin with a 15%-weight Stone pack; Lucian Sr Superior/Legendary specialist; Sofia cat dealer with occasional packs and a 5%-weight Mythic+Silver T4 jackpot. `reward_mult` continues to apply as a global scalar on coins+cats (not packs). Offer-card and result-screen reward summary now renders packs (e.g. `📦 1× Bronze Pack`).
- **Jobs / Mafia Killings — complications (second die).** Every commit now rolls an independent **complication** die on top of the success roll, closing the 95%-ceiling loophole where SP-saturated crews were effectively immune to total failure. Final chance is `base_by_tier × (1 + heat_factor) × (1 - rep_discount)` with the offerer's rep capable of buying down up to 40%. Ten events seeded across tier pools:
  - **Teeth**: `cat_police_raid` (+30 heat), `rival_crew` (downgrade to near-miss if effective SP < 40% of difficulty), `double_cross` (offerer skims half the cat reward), `boss_arrives` (×1.4 difficulty, success die rerolls against the new wall), `informant` (force near-miss).
  - **Sweeteners**: `easy_mark` (×2 reward), `found_a_stash` (+1 cat one rarity tier above the recipe), `sloppy_target` (replace cat reward with a pack tier above the recipe default).
  - **Aftermath**: `witness` (+20% difficulty on the next commit), `loose_end` (+10 heat on the next commit). Stored on `profile.jobs_pending_difficulty_mult` / `jobs_pending_heat_bonus`, consumed on the next commit.
  
  Tier 1 jobs only roll `easy_mark` (newbies get sweeteners, never punishment). T5 base 35%, with aftermath events zeroed out (Big Score is once-per-season — no next job to apply to). Reward-modifying events are gated to successful outcomes only, so the result screen doesn't show "rewards doubled!" next to "all cats destroyed." Surfaced live on the send screen (`⚠️ Complication chance: X%`) and on the result screen (event header + flavor line + heat bump if applicable). Tunable via `config/jobs.json → complications` and `complication_pools`.
- **Jobs / Mafia Killings** — full PvE contract system. `/jobs` offers 3 contracts every 6 UTC hours, deterministic per (user, server, window). Send cats as a crew, roll against a sigmoid (`ratio = crew_SP / difficulty`), three outcomes (success / 10pp near-miss / total failure). Six NPCs (Whiskers, Lucian Jr, Jinx, Jeremy, Lucian Sr, Sofia) with distinct stat blocks. `/rep` shows per-NPC standing. Big Score is a Tier 5 once-per-season Lv10 capstone — 3 eGirls + 15k coins + permanent +5% spawn-extra perk on first win. 12 new achievements, 2 new battlepass extra quests (job_easy, job_hard), 3 new leaderboards (Heists, Job Coins, Biggest Score). Paginated 9-page `/jobs help` reachable from every UI surface. Operator config editor in the admin webui under `/jobs` and `/jobs_help`.
- **Diminishing returns on crew composition.** Mono-rarity stacking is dampened: `effective_SP = base_SP × count^0.75`. Mixing rarities preserves full efficiency. 100 Fines contribute ~32 SP, not 100 — closes the arbitrage of farming coins by spamming Tier 1 jobs with bought Fines. Tunable via `config/jobs.json → tuning.diminishing_returns_alpha`.
- **Daily job-commit cap** — 3 commits per UTC day, per server. Surfaces on the `/jobs` board header as `Jobs today: X/3`. Tunable via `config/jobs.json → tuning.max_commits_per_day`. Misclicks would have burned a slot, so the cancel-grace mechanic that used to allow a 30s undo has been removed (it also let players undo total wipes — strategic re-rolling, which the spec explicitly disallowed). The roll is now final the moment you click Send Crew.
- **Mafia leaderboard category** — `/leaderboards type:Mafia` ranks players by catnip level (Cat Mafia rank, 0–10). Per-server like the other categories. Sorted by `user.catnip_level` joined into the per-profile rollup.
- **Fifth battlepass quest slot** (`challenge_quest`) for harder catch-condition quests. Wired through `generate_quest`, `refresh_quests` (season rollover + retired-quest cleanup), `progress()`, the /battlepass UI render, and DM reminders (with postpone button). Five challenge quests in the new `quests.challenge` config section:
  - `under3` — Catch a cat in under 3 seconds • 320–370 XP
  - `slow` — Catch after a cat has sat for a full minute • 250–290 XP
  - `legendary+` — Catch a Legendary or rarer cat • 380–400 XP
  - `catnip_catch` — Catch 10 cats while catnip is active • 280–340 XP • progress 10
  - `streak10` — Catch 10 cats in a row without missing • 320–380 XP • progress 10
  - Achievement `challenge_first` — "Challenge Accepted": complete a challenge quest for the first time • 350 XP
- **`define` misc quest** — Use /define once • 250–290 XP. Added to the `quests.misc` pool.
- **`gift3` extra quest** — /Gift 3 different players in one quest window • 320–380 XP • progress 3. Tracks distinct recipients via a new `gift3_recipients` text column on profile (cleared on quest completion and season rollover).
- **Third battlepass quest slot** (`extra_quest`) with four candidate quests:
  - `catnip_session` — activate /catnip (requires catnip access)
  - `casino` — play 3 different games of {slots, roulette, pig, cookieclicker}
  - `social` — complete a /gift to a player or /trade
  - `sacrifice` — gift the cat a cat; XP scales 25–300 by cat rarity, hidden from the user
- **Passive XP drips**: +50 XP for the first catch of the UTC day, +20 XP every 10-catch streak, +100 XP per catnip level-up (capped at 1000/season), +20 XP to prism owners when their prism boosts another user's catch.
- **`docs/design/`** evergreen design docs covering economy, battlepass, catnip, and achievements. Maintained by the `design-docs-sync` subagent.
- **`CHANGELOG.md`** with auto-draft maintenance via the `changelog-sync` subagent.
- **Pack rewards in battlepass level-up track.** One pack (Wooden through Celestial, scaling with tier) is now interspersed as a reward at specific levels across all 17 seasons. Season 1 has one of each tier across its 30 levels.
- **Snowballer** catnip perk: each consecutive catch builds a combo stack (cap 30); per-stack % chance to trigger a double-catch feeds into the existing double pool. Stack resets to 1 after 5 minutes idle. Per-stack % by tier: 0.5 / 0.75 / 1.25 / 2.0 / 3.0. Maximum double-chance contribution at cap: 15 / 22.5 / 37.5 / 60 / 90%. Hidden achievement `snowballer_max` ("Avalanche") unlocked on reaching a 30-stack • 400 XP.
- **Battlepass Booster** catnip perk: each catch has a % chance to grant +5 battlepass XP immediately (via the existing XP path; can trigger a level-up). Chance by tier: 5 / 8 / 12 / 20 / 30%. Hidden achievement `bp_xp_proc` ("Cram Session") unlocked on first proc • 200 XP.
- **Bait & Switch** catnip perk: each catch has a % chance to immediately respawn a cat in the same channel. Does not fire during a rain. Chance by tier: 1 / 1.5 / 2.5 / 5 / 8%. Hidden achievement `bait_switch_proc` ("Bait Master") unlocked on first proc • 200 XP.

### Added
- **/catstore command** — buy and sell cats for coins, per-server. Purchase price is adjusted by your Cat Mafia (catnip) level: levels 0–3 apply a tax (−20% to −5%), level 4 is face value, levels 5–10 give a discount (+5% to +30%). Sell prices scale with mafia level (see Changed section).
- **Discovery gate for /catstore** — a cat rarity is only available in a server's store once you've personally caught at least one of that rarity in that server. Discovery is lifetime: catching a Mythic once unlocks Mythics in that server's store forever. Existing players are backfilled from their current catch counters via migration 005.
- **6 new achievements** added for /catstore activity:
  - `catstore_first_buy` — "First Purrchase": complete your first store purchase • 250 XP
  - `catstore_first_sell` — "Profit Margin": sell a cat to the store for the first time • 250 XP
  - `catstore_whale` — "Whale Watcher": spend 10 000+ coins in a single transaction • 400 XP
  - `catstore_collector` (hidden) — "Compulsive Shopper": buy every rarity at least once • 500 XP
  - `mafia_discount_max` (hidden) — "Made Man": buy at maximum Cat Mafia discount • 350 XP
  - `mafia_tax_payer` (hidden) — "Sucker": buy as a Newbie (maximum tax) • 200 XP
- **`store_discount` field on every catnip level** in `config/catnip.json` (range −20 to +30; hidden level 11 "Most Wanted" matches +30). The catnip editor in the webui surfaces this field automatically alongside existing level keys.

### Changed
- **/catstore sell prices now scale with Cat Mafia (catnip) level.** The natural curve starts at 50% of face value at Newbie and rises +5% per level, but is capped at `(buy_pct − 5)` at every level to prevent buy-low-sell-high arbitrage. Practical curve across Lv0–11: 50, 55, 60, 65, 70, 75, 80, 80, 75, 70, 65, 65%. El Patrón tops out at 65% sell (not 100%). The /catstore detail view now shows the mafia cut inline — e.g. "(mafia takes 🪙 N — N% of face)" — and the sell confirmation toast reflects the cut or says "(full value)" when none applies. Help text updated to mention the scaling and that round-trips always net negative.
- **README rewritten** to document fork-specific gameplay divergence (unified coins wallet, /catstore, activity-driven stocks, reshuffled catnip perks, 5-quest battlepass, passive XP drips, pack-open polish) and to list every migration with what it does and when it's needed. The `voting_enabled` / `webhook_verify` env-var rows now make clear voting is permanently retired scaffolding.
- **/roulette now uses `coins`**, the same wallet as /stocks, /packs, and /catstore. Migration 006 sums each player's existing `roulette_balance` into `coins` — nobody loses earned currency, and gambling debts are preserved as negative coin balances. The debt-recovery mechanic is unchanged: max bet is `max(coins, 100)` so a player in the red can still wager up to 100 coins.
- **"Cat dollars" terminology retired from /roulette UI.** Bet modal label, win/loss embeds, balance description, and the broke-recovery message all now read "coins" with the 🪙 emoji.
- **"Roulette Dollars" leaderboard category renamed to "Coins".** Debtors with non-positive balances are still ranked (gambling debt is meaningful game info). Coins is now a first-class leaderboard category alongside Cats, Cattlepass, Pig, etc.
- **`profile.roulette_balance` column removed; the default 100-coin starting balance for new profiles is gone.** New profiles start at 0 coins and accumulate them by catching packs, depositing into /stocks, or winning roulette. Existing balances were merged into `coins` by migration 006 before the column was dropped.
- **Stock prices now track in-game activity** instead of sitting at the default 40 indefinitely. A background tick (every 5 min, on the existing `MAIN_LOOP_INTERVAL` cadence) computes a fair price per ticker from live game state — PRSM from prisms outstanding, CTNP from active-catnip profiles, PASS from average battlepass level, ACHS from average unlocked achievements, RAIN from total rain minutes bought — then places bot-owned bid/ask orders at fair ± spread. `PriceHistory` receives a sample every tick so charts always have data. Buy/sell flow still moves prices normally via order matching. Legacy 10k-share startup order (price 40, time=0) is cancelled on the first MM tick and shares returned so it no longer absorbs all buy demand at the floor.
- **`user.vote_streak` renamed to `user.daily_catch_streak`**; `user.max_vote_streak` renamed to `user.max_daily_streak`. Data copied 1:1 by migration 004; old columns dropped. The counter increments on the first catch of each UTC day and resets if a day is skipped — semantics unchanged, name now accurate.
- **"Voting Booster" catnip perk renamed to "Loyalty Streak"** (ID: `timer_add_streak` → `loyalty_streak`). Description updated to "Your daily catch streak (N) boosts catnip duration." Mechanic unchanged — still extends /catnip activation duration scaled to streak count.
- **/stats display label** "Current vote streak" → "Current daily catch streak".
- **/battlepass fire-emoji line** "N× catch streak" → "N-day catch streak". Previous label implied a per-catch streak; this aligns with the daily-reset semantics.
- Pack values rebalanced (+50% across all tiers) to suit the self-hosted instance's smaller economy.
- Catch streak (`profile.catch_streak`) now resets when the bot laughs at a missed catch.
- **Pack sub-1 fail behavior overhauled.** Instead of always giving a single Fine cat as consolation:
  - Wooden fails re-roll the cat type and run the lottery once more.
  - Stone+ fails open a pack one tier lower as consolation (with that pack's normal upgrade chain).
  - If the retry also fails, the consolation is 3 Fine cats instead of 1.
- **Random pack drops from catches now show a tier-themed embed** instead of a single inline line, with per-tier color, a quirky random opener, and tier-scaled hype text (chill for Wooden, full caps drama for Celestial).
- **Battlepass level-up bonus packs also get their own tier-themed embed** alongside the level-up reward embed. Same color palette as catch drops but distinct opener/vibe copy so the two are recognizable.
- **All catnip durations unified to 24 hours.** Previously levels 1–4 lasted 2–8 h; now every level runs for 24 h.
> **REVERTED:** **Time Manipulator perk** timer-extend change superseded — perk has since been removed entirely (see Removed section).
- Catnip level names for L5–L11 updated to reflect the uniform duration (e.g. "Second Bounty", "Tougher Bounties", etc.); old names referenced duration increments that no longer apply.
- **Plush promo footer removed** from bot messages; the `/plush` limited-time campaign has ended.
- **Spawn revival now runs on a real background ticker** (every 60s by default), not only when someone chats. Previously, quiet channels could sit with an overdue spawn waiting for the next message to wake `background_loop` — long-running gaps after `cat!restart` or a scheduled-spawn task drop were possible. A standalone `_spawn_revival_loop` task now scans `channel WHERE yet_to_spawn < now() AND cat = 0` on a timer and respawns. The inline scan in `background_loop` remains as defense in depth; `spawn_cat` is self-guarded so the two paths race cleanly. Tunable via `spawn_revival_interval_seconds` in `config/tuning.json`. Task handle stored on `config.spawn_revival_task` so it survives `cat!restart`.

### Fixed
- **/inventory and /achievements no longer crash on newly-added achievement IDs.** Five sites in `main.py` were probing `person[k]` (legacy boolean column lookup), which raises `KeyError` for aches that exist only in the JSONB `profile.unlocked_aches` array. Replaced with `person.has_ach(k)`. Affected newer achievements: `catstore_*`, `mafia_*`, `challenge_first`, `snowballer_max`, `bp_xp_proc`, `bait_switch_proc`.

### Removed
- **Time Manipulator** catnip perk retired. Weight set to 0; no new players will receive it. Existing holders: perk goes inert (no effect on catches). Entry kept in config for index-stability of stored perk references.
- Hidden easter-egg achievements `website_user` (`cat!i_like_cat_website`) and `click_here` (`cat!i_clicked_there`) removed from the trigger list; the phrases no longer unlock anything.
- **Discord-invite buttons removed from catch messages.** The four "Join our Discord" button variants ("Join our Discord!", "John Discord 🤠", "DAVE DISCORD 😀💀⚠️🥺", and "JOHN AND DAVE HAD A SON 💀🤠😀⚠️🥺") no longer appear under catch messages. The top.gg vote button and the dark-market shadow button are unaffected.

### Internal
- `update_catch_streak()` now returns a `bool` indicating whether this was the first catch of the UTC day, used to gate the first-catch passive XP grant. Renamed to `update_daily_catch_streak()` to distinguish it from `profile.catch_streak` (the per-catch counter driving the `streak10` challenge quest).
- New helpers in `main.py`: `progress_casino_quest`, `grant_catnip_levelup_xp`, `grant_first_catch_of_day_xp`, `grant_catch_streak_xp`.
- New profile columns: `extra_quest`, `extra_progress`, `extra_cooldown`, `extra_reward`, `catch_streak`, `casino_progress_temp`, `catnip_xp_awarded`. (Already applied to the live DB; mirrored in `schema.sql`.)
- `CATNIP_TIMER_EXTEND` and other tuning constants now read from `config/tuning.json` at module load; previously some were hardcoded literals.
- New profile columns for the challenge slot: `challenge_quest`, `challenge_progress`, `challenge_cooldown`, `challenge_reward`, `reminder_challenge`, `gift3_recipients`. Added to `schema.sql`; backfilled by `migrations/003_challenge_slot.py` (idempotent ADD COLUMNs). `LEGENDARY_PLUS` frozenset constant added to `main.py` for the `legendary+` quest trigger.
- New `profile.combo_stack` integer column (default 0) tracks Snowballer per-user stack. Added to `schema.sql`; backfilled by `migrations/002_combo_stack.py`.
- `TriggerEngine` (from `ach_engine.py`) imported and constructed at module load; achievement trigger dispatch is now data-driven for aches with a `trigger` block in `config/aches.json`.
- **Migration 004** (`migrations/004_voting_cleanup.py`): idempotent ADD new columns → backfill UPDATE → DROP old columns for the `vote_streak`/`daily_catch_streak` rename. Bot must be stopped to run.
- **webui admin panel** user-table field whitelist (`webui/routes/user_table.py`) updated to expose `daily_catch_streak` and `max_daily_streak` in place of the dropped column names.
- `CLAUDE.md` and `config.py` comments updated: "vote_streak is repurposed" note removed; `VOTING_ENABLED` documented as the dormant on/off switch for `/vote` + top.gg webhook.
- New profile columns `discovered_cats` and `store_purchased_rarities` (both JSONB, default `[]`). Mirrored in `schema.sql`; backfilled by `migrations/005_cat_store.py` (idempotent ADD COLUMN + per-rarity discovery backfill from existing `cat_<Type>` counters, batched 5 000 rows at a time).
- New helpers in `main.py` (placed near `get_stock_price`): `cat_value`, `store_discount_pct`, `store_buy_price`, `store_sell_price`, `mark_discovered` (called from every cat acquisition site: catch, pack opens, battlepass level-up rewards, /gift recipient, /trade settlement), `mark_store_purchased` (backs the `catstore_collector` achievement). Store touches `profile.coins` only; `roulette_balance` is unaffected and no new currency type was added. _(Note: `roulette_balance` has since been merged into `coins` by migration 006 — see Changed section.)_
- New helper `store_sell_pct(catnip_level)` added to `main.py`; `store_sell_price` signature updated to accept `catnip_level`. Both placed near the existing buy-price helpers.
- New `stock_market` block in `config/tuning.json`: operator-tunable enabled flag, spread, MM order quantity, price floor/ceiling, and per-ticker base/baseline/alpha. Hot-reloads on `cat!restart`.
- New helpers in `main.py`: `_fair_price_metric(ticker)`, `_compute_fair_price(ticker)`, `_run_stock_market_maker()` — placed near `get_stock_price` and `_init_stock_orders`. MM orders identified by `user_id=<bot profile> AND time=0`; existing 7-day stale-order sweep already skips them.
- **`failed_gambler` achievement trigger unchanged** by the wallet merge. The condition fires when `profile.coins` goes negative, same logic as before — only the column underneath it was renamed from `roulette_balance`. Migration 006: idempotent `UPDATE profile SET coins = coins + roulette_balance` → `ALTER TABLE profile DROP COLUMN roulette_balance`. Bot must be stopped to run.

## Conventions

- **One bullet = one user-perceivable change.** Internal refactors that don't change behavior go under "Internal" and are optional.
- **Lead with the noun, not the verb.** "Third quest slot added" reads worse than "Third quest slot — adds…". Use "Added/Changed/Fixed/Removed" headers and a bulleted list.
- **Cross-link to design docs** when the entry is a balance change; cross-link to `docs/design/economy.md#xp--battlepass-currency` etc.
- **Numbers are config, not changelog.** "XP bonus per level changed from 50 to 100" belongs here; "level 7 reward is now 3 Rare cats" does not — that's just a config tune.
- **Squash trivia.** A series of "fix typo" / "tweak wording" commits should collapse into one line.
