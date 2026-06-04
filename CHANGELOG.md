# Changelog

All notable user-facing changes to Cat Bot are tracked here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project does not currently version with semver tags; entries are grouped by release date or by "[Unreleased]" for the working branch.

The [`changelog-sync`](.claude/agents/changelog-sync.md) subagent updates the `[Unreleased]` section whenever bot-surface files change. Curated wording lives here; the agent appends drafts and flags entries with `> _draft_` until a human approves and de-drafts them.

## [Unreleased]

### Added
- **Pack "coin crate" variant.** Roughly half of all pack opens (50% chance per open, tunable via `pack_coin_variant_chance`) pay out part cats and part coins instead of all cats. The coin/cat split scales by tier: Wooden is 50/50; Celestial is 80% cats / 20% coins; tiers between interpolate linearly. Total payout value is equal to a regular open. Single opens play a tick-up coin animation matching the one `/catslots` bonuses use. "Open All" skips the per-pack animation and shows a `💰 +X coins` line in the result embed. Special packs (Christmas, Valentine, Chef, Birthday) are always regular opens. Job perks Padded Crate, Crate Polish, and No Fines still work normally on coin-variant opens. New tuning keys: `pack_coin_variant_chance` (0.5), `pack_coin_ratio_wooden` (0.5), `pack_coin_ratio_celestial` (0.2).
  > _draft_
- **First-prism discount.** A player's very first prism craft on a given server now costs **1,000 coins** instead of 5,000. The escalating schedule (5k → 10k → 20k → … capped at 320k) is unchanged from the second prism onward. New tuning key: `prism_craft_coin_cost.first` (1000).
  > _draft_
- **`/catprofile [user]` — compact at-a-glance profile card.** Shows mafia level + rank name, cattlepass level/XP/season, total cats held + collection value, prisms owned + boost %, coins, achievement progress (visible only), current and best catch streak (global, noted in footer), pig high score, and cookies clicked. Supports viewing another user's card. Honors supporter cosmetics (custom color, emoji prefix, image thumbnail).
  > _draft_
- **Season-end broadcast warning.** On the last calendar day of each month, Cat Bot posts an embed to every setupped channel reminding players that the Cattlepass season rolls over tomorrow and listing exactly what resets (coins, cattlepass level/XP, catnip/mafia level, jobs state, all packs) and what is kept (cats, prisms, stocks, discovered cats, achievements, streaks). A new `/settings` toggle **Season Announcements** (default ON) lets server admins opt their server out. Backed by a new hourly background task (`_season_announcement_loop`) with a once-per-season dedup marker (`season_warn.txt`) so the broadcast fires at most once per season even across restarts.
  > _draft_
- **Season Recap leaderboard.** On the 1st of each month, after the season rolls over, Cat Bot posts a per-server embed to every setupped channel crowning the just-ended season's winners. The embed shows **Top 5 players across 7 categories**: 🏆 Cattlepass champion (highest level reached), 🎩 Mafia level (highest catnip level), 🪙 Biggest coin earner, 🐱 Cats caught, 🔫 Heists pulled (jobs completed), 🎰 Gambling winnings (roulette net + catslots net), 📈 Stock profit (sell proceeds minus buy spend). The #1 Cattlepass climber is headlined as the season's champion. Standings are snapshotted on the season's last day (while lazy per-player resets are still outstanding) and broadcast on the 1st. Honors the existing per-server **Season Announcements** opt-out toggle. For the very first recap (Season 1), the gambling, stock, and coins-earned categories only cover the period since this feature launched; the embed footer flags this. They are complete from Season 2 onward.
  > _draft_
- **Season Trophies.** At season rollover, the top 3 players per server in three categories — **🪙 Most Coins Earned**, **🐱 Most Cats Caught**, and **🔫 Most Heists Completed** — are awarded a permanent trophy. Trophies accumulate across seasons and are displayed on `/catprofile` as a compact medal list (e.g. `🥇 S1 Coins • 🥈 S1 Cats …`), ordered newest season first. Immediately after the existing recap embed, a separate **🏆 Season N Champions** embed is posted in-channel naming the top-3 medallists and their totals per category. Backed by migration 024 which adds `profile.season_trophies` (JSONB list of `{season, category, rank}` records). Award is idempotent — safe to re-run after a crash mid-loop. Respects the per-server **Season Announcements** opt-out toggle (opted-out servers neither receive the Champions embed nor have trophies awarded).
  > _draft_
- **Three short-spin battlepass quests for `/catslots`** join the misc pool: `catslots1` "Spin the /catslots machine once" (150–250 XP), `catslots2` "Spin the /catslots machine twice" (200–280 XP), `catslots3` "Spin the /catslots machine 3 times" (250–330 XP). Previously the only `/catslots` spin quest required 10 spins; these give quicker XP for brief sessions.
  > _draft_
- **Paid `/jobs` board reroll.** Players can now pay coins to replace all current job offers with a fresh set. Exposed two ways: a **🔄 Reroll (🪙 X)** button directly on the `/jobs` board, and a **🔄 Job Board Reroll** screen under `/catstore → Extras`. Price formula: `max(1,000, mafia_level × 500)` — Lv2 costs 1,000, Lv12 costs 6,000. Price **escalates within each 12h window**: the 1st reroll costs the base, the 2nd costs ×2, the 3rd ×3, and so on; the counter resets at the window boundary. The existing 3-commits-per-window cap is unchanged — this reshuffles what's on offer (and can refill a board cleared mid-window). The free `reroll_board` perk is separate and does not count toward the escalation. Requires migration `023_job_reroll_counter.py` (adds `profile.job_rerolls_window` and `profile.job_rerolls_window_idx`; bot degrades gracefully at flat base price without it).
  > _draft_
- **`/inventory` now shows coin balance.** A `🪙 Coins: N,NNN` line appears between the Cats and Prisms lines in the inventory embed. Uses `person.coins` (per-server, same scope as the rest of `/inventory`), so it reflects the viewed player's balance whether you're checking your own or someone else's.
  > _draft_
- **Two new cat rarities: Shadow and Terminator.** Shadow (weight 221, ~5.1% spawn rate, 19 coins base) slots between Baby and Epic on the rarity ladder. Terminator (weight 5, ~0.115% spawn rate, 866 coins base) slots between Real and Ultimate. Both can be caught, sacrificed for XP (Shadow 80 XP, Terminator 278 XP), sent on `/jobs` (Shadow send power 10, Terminator 128), and traded. Coin values across all rarities shift slightly upward (~5% catalog-wide) because the total weight pool grew. Both rarities are live from Season 2 onward via a new `rarity_min_season` gate in `config/tuning.json`; a rarity whose spawn image is missing on disk is automatically held back until the image is in place. The gate is enforced consistently across all rarity-selection paths via `_season_eligible_cattypes()`: natural spawns, pack-open main rarity roll, pack-open cascade re-roll, pack-open bonus-cat draw, Padded Crate job-perk bonus cat, and catnip level-up price selection — gated rarities cannot appear in any of these until their season floor is met.
  > _draft_
- **`/jobs` now shields your Cat Mafia (catnip) level from decay for 24 hours.** Committing a job within the last 24h suppresses both timer-based level loss (lapsed bounty) and respect-driven level loss — your mafia rank is safe as long as you do at least one job per day. Go 24h+ without any `/jobs` activity and decay resumes as before. The grace period does **not** keep catnip perks active or substitute for completing bounties; it only protects your current level. `/catnip` now shows your protection status: either "Mafia level protected by a recent job — safe until \<time\>" or "Do a `/jobs` to protect your level." Backed by `profile.last_job_time` (migration 027) and the `catnip_job_grace_hours` tuning key (default 24).
  > _draft_

### Changed
- **Battlepass XP requirements roughly doubled — leveling is now better paced for a 30-day season.** Per-level XP costs are ~2× higher through the front half and steepen further toward the back. Reaching Level 30 now takes ~44,500 XP (was ~22,250); Level 40 takes ~82,500 XP (was ~36,750). The post-Level-40 **Extra Rewards** grind cost rises from 1,500 → 6,000 XP per Stone pack to stay consistent with the steeper curve. Design target: an average daily player finishes around L30 in a 30-day season; a very engaged player reaches ~L40. The change affects Seasons 2–17; Season 1 is unaffected.
  > _draft_
- **Season rollover grants a 🪙 100 coin starting allowance instead of wiping to 0.** At every Cattlepass season reset, each player's coins are set to `SEASON_STARTING_COINS` (default 100, tunable via `season_starting_coins` in `config/tuning.json`) rather than zeroed out. Players who already held more than 100 coins at rollover keep the higher balance — only those below 100 are topped up. The season-end broadcast warning embed and the one-shot ephemeral reset notice both now state "Coins → reset to 100 (season starting allowance)" instead of "Coins → 0".
  > _draft_
- **`/catslots` now counts as its own game for the "Play 3 different casino games" extra-slot quest.** Previously a `/catslots` spin was tracked under the same `slots` bit as `/slots`, so playing both games only registered as one. `/catslots` now has its own `CASINO_GAME_BITS` entry (bit 16). The quest title is updated to list `/catslots` alongside `/slots`, `/roulette`, `/pig`, and `/cookieclicker`. Players who have already completed this quest are unaffected; players still working toward it now get credit for each game independently.
  > _draft_
- **Cat Police Pinch rebalanced: lockout cut to 2h, threshold raised to 150.** The Pinch (catnip-perk suspension triggered when Heat tops out from `/jobs` commits) is now harder to reach and faster to recover from. `pinch_threshold` 100 → 150 — it now takes ~50% more Heat to get Pinched. `pinch_lockout_seconds` 21,600 → 7,200 — suspension lasts 2 hours instead of 6. Heat bar on the `/jobs` board now reads `/150`. Color bands and the "scrutiny" cost ramp scale proportionally (cutoffs are 30%/70% of the threshold = 45/105 instead of 30/70). The Big Score auto-Pinch and `heat_cost` are both bumped to 150 to match. Pinch messages say "hit 150" instead of "hit 100". Heat reset after Pinch (30) and complication heat bonuses are unchanged.
  > _draft_
- **`/jobs` "no offers" message now distinguishes between a cleared board and a reputation block.** Previously the empty-board response always said "the family doesn't want anything to do with you right now — lay low, fix your reputation, and check back later," which blamed reputation even when the player had simply worked through every job in the current 12h window. Two cases are now handled separately: if eligible NPCs exist (the common case), the message says the shift board is clear and shows a Discord relative timestamp for when the next batch of jobs arrives; if no NPC will hire the player because faction reputation is below the refuse threshold with everyone, the message explains their standing is the issue. Players who previously saw the generic blame message after a productive shift will now see the accurate, actionable one.
  > _draft_

### Fixed
- **`/jobs` board Prev/Next pagination buttons no longer mis-route through `defer()`.** Both callbacks were calling `await interaction.response.defer()` before handing off to `show_board`, which caused `show_board` to follow the `edit_original_response` path instead of the correct `edit_message` path. Clicking Prev or Next would silently fail or render into the wrong interaction slot. Removed the `defer()` calls so both buttons go through `interaction.response.edit_message`, matching the Decline button's working path. No visible layout change; the fix is the navigation no longer breaks.
  > _draft_
- **`/jobs` board no longer crashes when re-rendering into an empty or locked state.** The `_say` helper inside `show_board` was passing `content=text, view=None` to `edit_message` / `edit_original_response`, which Discord rejects with a 400 for components-v2 messages (`MessageFlags.IS_COMPONENTS_V2`). Most easily hit by clicking "📋 Back to board" on the post-result screen when no offers remain. `_say` now wraps its text in a `LayoutView(Container(text))` and edits with `view=` only, matching the rest of the board. All three branches (entry followup, deferred edit-original-response, undeferred edit-message) are fixed. No buttons are rendered — the player re-runs `/jobs` to get a fresh board, same end-state as before.
  > _draft_
- **"Open All" packs now applies job perks consistently with single-pack opens.** `process_pack_opening` (the Open All handler in `/packs`) was silently skipping all three pack-side job perks; `open_pack` (the single-open button) already applied them. The three perks now work as expected during bulk opens: **Padded Crate** (`pack_bonus_cat`, timed) grants +1 random bonus cat on every pack opened in the batch; **Crate Polish** (`pack_tier_upgrade`, charge) spends its single charge to bump the first eligible pack up a tier; **No Fines** (`pack_floor`, charge) spends its single charge to lift the first Fine draw to Nice. The Open All result embed now shows a perk-activity footer when any of these fire, matching the existing single-open display.
  > _draft_
- **Cookie button no longer crashes with `KeyError` for users with an active casino quest.** The `/cookie` click handler was doing a partial DB fetch for `cookies` only, but `progress_casino_quest` reads `extra_quest`, `extra_cooldown`, and `casino_progress_temp` before its refetch. Any player with the casino extra-slot quest active would get a silent error instead of their click counting. The three columns are now included in the partial fetch.
  > _draft_

### Removed
- **Time Manipulator catnip perk removed entirely.** The perk had already been inert for some time (no chance to ever roll it). It is now deleted from `config/catnip.json` rather than left frozen in place. No player loses an active perk — it could not be held under the old inert state.

### Internal
- Migration `020_remove_timer_add.py` deletes the `timer_add` perk and remaps stored perk indices: every reference ≥ 12 in `profile.perks`/`perk1`/`perk2`/`perk3` is decremented by one to track the array shift. **Not safe to re-run** (data mutation, guarded only by `020.done`).
- Removed the orphaned `CATNIP_TIMER_EXTEND` constant (`main.py`) and the `catnip_timer_extend_seconds` tuning key (`config/tuning.json`, plus webui validators/labels). Both only ever fed the now-deleted perk.
- Migration `021_season_announcements.py` adds `server.season_announcements BOOLEAN DEFAULT TRUE`. Backs the `/settings` Season Announcements toggle — without it, the column is absent and the broadcast loop treats every server as opted in (the safe default), but admins have no way to opt out. Idempotent per-column gate. No backfill needed.
- Migration `022_season_recap.py` adds six columns to `profile`: `coins_earned`, `roulette_coins_won`, `roulette_coins_bet`, `stock_coins_earned`, `stock_coins_spent` (all `BIGINT DEFAULT 0`) and `season_stat_baseline` (`JSONB DEFAULT '{}'`). No backfill needed — defaults are correct for all existing rows. New `_bump(profile, col, delta)` helper increments these counters at every money-in/out site in roulette, catslots, stocks, and jobs, guarded with try/except so an un-migrated DB is a silent no-op. `_recap_columns_present()` probes the schema once and caches the result on `config` for bulk-update paths that can't probe per-row. Season rollover captures the current lifetime totals into `season_stat_baseline` so the recap can diff "this season" = lifetime − baseline.
  > _draft_
- Migration `023_job_reroll_counter.py` adds `profile.job_rerolls_window INTEGER DEFAULT 0` and `profile.job_rerolls_window_idx BIGINT DEFAULT 0`. Idempotent per-column gate. No backfill needed. Mirrored in `schema.sql`. New `jobs.json` tuning keys: `reroll_price_per_level` (500) and `reroll_price_min` (1000). The `_jobs_do_reroll` helper is extracted from the existing perk handler and shared by the paid reroll paths (`on_reroll_paid` in `/jobs`, `on_buy_jobs_reroll` in `/catstore`).
  > _draft_

## [0.6.6.060025052026]

### Changed
- **Cat Police Pinch lockout cut in half: 12h → 6h.** When your Heat hits 100 from `/jobs` commits, the Pinch fires (catnip perks suspended, Heat reset to 30). The suspension now lasts **6 hours** instead of 12. `pinch_lockout_seconds` in `config/jobs.json` drops from 43,200 → 21,600. Threshold (100) and post-Pinch Heat reset (30) are unchanged.

### Fixed
- **"Suspended until in 12 hours" grammar bug.** Three places combined "until" with a Discord relative timestamp (`<t:N:R>` renders as "in N hours"), producing sentences like "suspended until in 12 hours." Rewritten to put the relative timestamp at the end: "🚓 The Cat Police have your perks. They come back in 6 hours." Same fix in the /jobs board Pinched status line and the /jobs result-screen Pinch follow-up.

## [0.6.5.073224052026]

### Changed
- **Battlepass season rollover now wipes coins, catnip, jobs state, and packs.** At the start of each season, every player's per-server profile resets: **coins → 0**, **catnip level → 0** (along with all bounties, stored catnip perks, the active session, and the mafia offer state), **all operational jobs state** (heat, respect, faction reputation, job perks, in-flight modifiers, the per-season Big Score season marker, Whiskers's Favor state), and **the entire pack inventory** (every tier including event packs). Cats, stocks, prisms, discovered-cats, achievements, daily catch streak, lifetime stats, and the per-server rain inventory are explicitly **preserved** — they represent player history, not active economy. The wipe is lazy per profile: it kicks in on the player's next interaction with the bot on each server. JobInstance rows (offered/accepted contracts) are left alone but become inert because `/jobs` early-returns at the level-2 gate.
- **One-shot ephemeral "season reset" notice.** On the first `/battlepass`, `/catnip`, `/jobs`, `/catstore`, `/stats`, or `/inventory` call after the rollover, the bot sends an ephemeral (private) embed summarizing what was reset and what was preserved. The notice fires once per player per season — controlled by a new `profile.season_reset_pending` flag set during the wipe and cleared when the notice renders. Catch events don't trigger the notice (no interaction to attach an ephemeral to); the player sees it on whatever slash command they run next.

### Internal
- Migration `019_season_reset_notice.py` adds `profile.season_reset_pending BOOLEAN DEFAULT FALSE NOT NULL`. Mirrored in `schema.sql`. No backfill needed; the default of false is correct for every existing row.
- New helpers next to `refresh_quests` in `main.py`: `_wipe_catnip_state`, `_wipe_jobs_state`, `_wipe_packs`, and `_maybe_show_season_reset_notice`. The season-rollover block in `refresh_quests` calls the three wipe helpers + zeroes `coins` + sets the pending flag. The notice helper is invoked from the six slash-command entry points.
- The new wipe code is column-existence-guarded — if migration 019 isn't applied yet, the flag set/notice no-ops silently. The wipes themselves still take effect.

## [0.6.4.071424052026]

### Changed
- **Battlepass seasons 2+ extended from 30 to 40 levels.** Every season from Season 2 onward now has 10 additional levels (31 through 40) after the original 30. Season 1 is unchanged (kept at 30 levels as the legacy onboarding shape). The new levels use a gentle XP ramp — **1100, 1200, 1300, …, 2000 XP** — adding 15,500 XP to season completion (total goes from 23,250 to 38,750). Each season's new levels were designed to mirror its existing 21–30 character: pack-heavy seasons get more packs at higher tiers, the dominant-tier-of-that-season repeats early in 31–34, then ramps up through Silver/Gold/Platinum/Diamond, and **every season's level 40 is a 1× Celestial pack** as a consistent capstone. Past level 40, the existing Extra Rewards fallback (1 Stone pack per 1,500 XP) still kicks in. The `/battlepass` embed and `/stats` "seasons completed" calculation are both per-season-aware now (no more hardcoded `30` checks). Existing players past level 30 keep their current state; their next level-up grants the new level-N reward instead of an Extra Rewards Stone pack.

## [0.6.3.070224052026]

### Changed
- **Prism-owner passive XP is now visible inline in the catch message.** When somebody else's prism boosts your catch, the existing chat suffix already names the owner (`🔮 <@owner>'s prism Alpha boosted this catch from a Fine cat!`). That line now ends with `(+20 XP)` so the owner — already pinged by the @-mention — sees the grant in the same beat instead of having to check `/battlepass` to notice. The +20 grant itself has been there all along (`asyncio.create_task(_grant_prism_owner_xp(...))` in the catch handler); only the silence is fixed. Self-boosts still grant nothing and still show no XP tag. The XP value moves to a new `PRISM_OWNER_XP_PER_BOOST` module constant (tunable via `config/tuning.json → prism_owner_xp_per_boost`, default 20) so the grant call and the chat tag can't drift.

## [0.6.2.062624052026]

### Changed
- **`/jobs` cadence aligned with `/battlepass` (12h windows, single timer).** The `/jobs` board used to have two independent timers — a 6h offer-refresh window and a 24h commit cap anchored at UTC midnight — that drifted apart and confused players. (A player who'd played late in their local night, which is "this morning" in UTC, would open the board at 7am local and see "Refreshes in 5h" alongside "Daily limit hit, resets in 11h" with no obvious connection between the two.) Both timers now collapse into a single 12h cadence, matching the `/battlepass` quest cooldown length. Windows are anchored at 00:00 and 12:00 UTC. The cap is **3 commits per 12h window** (configurable via `config/jobs.json → max_commits_per_window`), which works out to up to 6 commits per UTC day — slightly more generous than before, but you only see one number on the board: "Refreshes <t:N:R>". Internals: `JOBS_MAX_DAILY_COMMITS` → `JOBS_COMMITS_PER_WINDOW`, `_jobs_commits_today` → `_jobs_commits_this_window`, `_jobs_start_of_utc_day` removed (unused after the alignment). `max_commits_per_day` is kept in `jobs.json` as a deprecated alias the loader still reads if `max_commits_per_window` is missing.

## [0.6.1.180323052026]

### Changed
- **`/catslots` and `/roulette` accept native slash-command parameters.** Skip the lobby + modal round-trip for repeat play. `/catslots lines:5 bet:10` goes straight to spin (total wager 50). `/roulette color:red bet:100` or `/roulette number:17 bet:50` likewise. The existing lobby + modal flow is the **fallback** when params are omitted, so the onboarding path is unchanged for first-time players. For `/roulette`, exactly one of `color` or `number` must be supplied — both at once gets an ephemeral "pick one" error. The `number` param uses an autocomplete callback that surfaces matching numbers as you type (Discord caps suggestions at 25 per response, so empty input shows 0–24 and typing a digit filters into the higher numbers). Discord-side `Choice` + `Range[int]` validation handles bound-checking before the bot is even invoked; the existing affordability + concurrency guards still run server-side.

### Fixed
- **`/catnip` and `/jobs` no longer crash with `KeyError: 'respect_last_tick'` when migration 018 hasn't been applied.** catpg's `__getattr__` raises `KeyError` (not `AttributeError`), so `getattr(profile, "respect", default)` was not falling back. Added `_profile_has_respect_columns()` / `_prism_tax_enabled()` / `_safe_prisms_crafted()` guards so the respect meter and prism coin tax cleanly no-op on profiles that don't have the new columns yet. The bot will now boot and serve `/catnip`, `/jobs`, and `/prism` even if migration 018 is still queued.

## [0.6.0.164423052026]

### Added
- **Respect meter — mafia decay.** New `profile.respect` (0..100, default 50) ticks down passively while you ignore the family. Each completed job grants a tier-keyed bonus (`T1 +10, T2 +25, T3 +50, T4 +100, Big Score +200`, capped at 100). Decay rate is **−1/hr**, with a 60-day per-call cap on the lazy settle. When respect hits 0, every `hours_at_zero_per_level_loss` (default 6) hours at zero costs **−1 catnip_level**, floored at Lv4 so Tier‑2 jobs always remain a recovery path. After a level loss, respect resets to **25** (the grace bump) so the player has a runway to recover before the next loss. The store discount continues to track current catnip_level, so losing a level loses the discount that came with it. Wired into `/jobs` board, `/jobs` result screen, and `/catnip` open. `_respect_settle()`, `_respect_grant_for_tier()`, and the tuning block in `config/tuning.json → respect` are the source of truth.
- **Prism craft coin tax.** Each player's Nth prism craft on a given server now costs **`5,000 × 2^N` coins** (capped at 320,000) on top of the cat recipe. Per-PROFILE counter (`profile.prisms_crafted`) keyed off the prism table's `creator` column at migration time, so the cost ramp is independent on each server. The confirm dialog surfaces the cost upfront, the craft button is disabled when you can't afford it, and a re-check at commit time prevents the "stay on confirm, spend coins elsewhere, then craft" race. Tunable via `config/tuning.json → prism_craft_coin_cost`.

### Changed
- **Top-tier pack prices increased** (Silver and up). New catstore prices: Silver **600** (was 300, 2×), Gold **1,800** (was 600, 3×), Platinum **4,800** (was 1,200, 4×), Diamond **9,000** (was 1,800, 5×), Celestial **21,000** (was 3,000, 7×). Wooden/Stone/Bronze unchanged. Implemented by adding a new `store_price` field to `pack_data` entries — `totalvalue` (the `/stocks` deposit payout and trade-display value) is unchanged, so deposits still pay what the cats inside are worth. Round-trip economics are now meaningfully negative for Silver+ (buying a Celestial then depositing it loses 18,000 coins), which is intentional: top-tier packs are meant to be opened, not flipped. Help text updated to explain the new asymmetry. Mafia discount continues to apply on top of `store_price`.
- **Top-tier cat prices increased.** New `catstore_tier_mult` table in tuning.json applies a per-rarity multiplier on top of the base `cat_value × CATSTORE_PRICE_MULTIPLIER`: **Mythic ×1.5, Divine ×4, Real ×5, Ultimate ×6, eGirl ×7**. Rarities below Mythic are unchanged. Effective buy prices at Capo (Lv4, 0% adjustment): Divine ~5,600 (was ~1,400), Real ~10,000, Ultimate ~20,400, eGirl ~28,700 (was ~4,100). Sell prices follow automatically because `store_sell_price` is a percentage of face. `type_dict` itself is unchanged so catch drop rates are untouched. Mafia discount still stacks on top.

### Internal
- Migration `018_respect_and_prism_count.py` adds three columns to `profile`: `respect INT DEFAULT 50`, `respect_last_tick BIGINT DEFAULT 0`, `prisms_crafted INT DEFAULT 0`. Backfills `prisms_crafted` from the existing `prism` table grouped by `(creator, guild_id)` so current prism owners don't get hit with the "1st craft = 5,000" cost on their next craft. Mirrored in `schema.sql`.
- `pack_data` entries gain an optional `store_price` field. `pack_buy_price()` reads it with a fallback to `totalvalue` so old pack definitions (and any future tiers added without a store_price) keep working.
- New helpers in `main.py`: `_respect_cfg`, `_respect_grant_for_tier`, `_respect_settle`, `_respect_apply_job_grant`, `_catstore_tier_mult`, `prism_craft_coin_cost`, `_ordinal`. The respect helper lazy-computes decay on read (no background task) and is called from the `/jobs` board open, the `/jobs` result-screen path (via the success branch in `_jobs_apply_outcome`), and the `/catnip` command entry.
- New tuning blocks in `config/tuning.json`: `respect`, `catstore_tier_mult`, `prism_craft_coin_cost`. All hot-reloadable via `cat!restart`.

## [0.5.6.070123052026]

### Fixed
- **`/catstore` rain purchases are now server-isolated.** The previous patch credited `user.rain_minutes` (cross-server), which let coins earned on one server spawn rain on another. Switched to `profile.rain_minutes` (per-server) — the same "bonus minutes" column `/rain` already consumes before the cross-server inventory. Server economies stay independent. `user.rain_minutes_bought` (lifetime cumulative, drives the blessings system) is still incremented at the user level since that's a global tracker for blessings rewards, not consumable inventory. UI and help text updated to clarify "this server's inventory" vs cross-server.

## [0.5.5.065623052026]

### Changed
- **`/catstore` rain is 75% cheaper and no longer fires immediately.** `RAIN_BASE_PRICE` dropped 12,000 → 3,000 coins. The buy button now adds **1 minute to your `user.rain_minutes` inventory** instead of immediately starting rain in the current channel — you trigger it later with `/rain`, on whichever server you want. Rain inventory is cross-server (`user.rain_minutes` is on the user, not the profile), so coins earned on one server can spawn rain on another. `RAIN_BLOCK_SECONDS` (15s, channel-fired) is gone, replaced by `RAIN_BLOCK_MINUTES = 1`. The buy handler also bumps `user.rain_minutes_bought` so blessings math (which keys off lifetime bought-rain) stays consistent.
- **Removed channel-side gating from the rain buy flow.** No more "channel needs to be setupped" / "rain is disabled in this server" / "there's a cat to catch first" ephemeral errors at purchase time — those concerns belong to `/rain` and already exist there. Buying is now a pure transaction: debit coins, credit rain minutes, scale the next price.
- **Catstore Rain UI rewrite.** The page now shows your current rain-minute inventory and explains that purchases queue rather than fire. The button reads "Buy ☔ 1 minute — 🪙 N", and the per-buy daily price scaling is preserved (`×1.5` per minute bought today, lazy UTC reset). Help text rewritten to match.
- **Achievements unchanged** — `catstore_rainmaker` still fires on first rain purchase, `catstore_monsoon` still fires at 5+ minutes bought in a UTC day, `catstore_whale` still triggers at 10,000+ coins per purchase (which now requires 4-5 buys into the daily ramp instead of being trivially trippable on the first purchase).

## [0.5.4.184822052026]

### Changed
- **`/catslots` symbol variety retune.** Player complaint: "every win is about the Fines." At the prior weights, P(c0=Fine) was ~60%, so 98% of winning spins were Fine-only. Dropped Fine reel weight 55→38 and bumped mid-tier weights (8bit 8→14, Corrupt 7→11, Professor 6→9, Divine 5→8, Real 4→5). eGirl and Ultimate stay at 3, so bonus trigger rate is unchanged at ~1 in 83. To preserve the ~97% total RTP, mid/high-tier payouts scaled up ~50-60%: 8bit 5OAK 250→450, Corrupt 5OAK 425→650, Professor 5OAK 725→1,150, Divine 5OAK 1,200→1,950, Real 5OAK 2,500→4,000, Ultimate 5OAK 5,000→8,000, eGirl 5OAK 2,500→4,000. Fine 4OAK 3→4, 5OAK 6→11. Verified by 500k-spin Monte Carlo: base 73% + bonus 25pp = total ~97% (matches the prior 97.8% baseline). **Non-Fine wins now appear in ~18% of winning spins** (up from 3.3%). Tradeoff: base-game win rate drops to ~54% per spin (was ~81%) — fewer winning spins overall, but the winners are more interesting. Sticky game mechanic, retrigger rule, animations, achievements, the admin force command, the bonus floor, and the bonus trigger rate are all unchanged.

## [0.5.3.182622052026]

### Changed
- **`/catslots_force_bonus` now takes an optional `user` parameter.** Targets someone else's next `/catslots` spin instead of the invoker's. Useful for set-up shots, gifting a guaranteed bonus, or testing without spinning yourself. Keyed off the target user's `id + guild_id`, same scheme as the existing self-queue. Defaults to the invoker if omitted. Manage-guild permission unchanged.

## [0.5.2.182422052026]

### Added
- **`/catslots` bonus payout floor.** Every bonus is now guaranteed to pay at least a tier-scaled multiple of the triggering spin's total bet, applied as a top-up after the natural spin payouts are summed: **tier 3 = 5× bet, tier 4 = 10× bet, tier 5 = 25× bet** (`CATSLOTS_BONUS_FLOORS` in `main.py`). Solves the "spent 13s on the letter-reveal animation and got 24 coins on a 20-coin bet" experience — small-bet bonuses now always feel like a real win. When the floor binds, the bonus summary surfaces it as `🛡️ Bonus floor: +N coins (guaranteed Xx bet minimum)`. The floor kicks in on ~74% of bonus triggers in simulation but rarely binds at max bet — natural variance still dominates the big wins. RTP impact: total RTP rises from ~94% to ~101%, making the slot effectively break-even on average (verified by 300k-spin Monte Carlo). This is the right trade for a closed-economy fun-game bot.

## [0.5.1.174022052026]

### Changed
- **Third `/catslots` retune — RTP regression finally fixed.** Monte Carlo verification after the second 2026-05-22 retune showed actual total RTP was **~190%**, not the design-stated 78-81% — the wild-substitution rule + frozen stickies still produced ~125pp of bonus RTP that the prior retunes never quantified. Three coordinated changes land total RTP in the **94% range** (Vegas penny-slot sweet spot), verified by 400k-spin Monte Carlo:
  - **Bonus eval no longer does wild substitution.** The bonus loop in `main.py:14080+` now uses the same straight-match rule as the base game — eGirls are treated literally, not as wilds that can substitute for any base symbol. Sticky cells still freeze in place at trigger time and still contribute to lines where they happen to be the leading run, but they don't prop up arbitrary bases anymore. This is the dominant lever; the multiplier knob now does what you'd intuitively expect.
  - **Bonus multipliers cut to 1.25 / 1.5 / 2** (from 2 / 2 / 3) for the 3 / 4 / 5-eGirl tiers respectively. `bonus_mult` is now a float (`float(cfg["multiplier"])`); line payouts are rounded to integer with `int(round(...))`. UI displays `{bonus_mult:g}×` so `2.0×` renders as `2×`.
  - **Base payouts rebalanced** to land base RTP at ~80% (up from ~66%). Fine 4OAK 2→3 and 5OAK 5→6 absorb most of the increase since P(c0=Fine) ≈ 60%; mid/high tiers bumped ~21% across the board. Worst-case base-game 5-of-a-kind line is now Ultimate 5,000× per_line = 500,000 coins at max bet.
- Per-tier averages at max bet (2,000 coins/spin) after the retune: tier 3 (~90% of bonuses) avg 15,135 coins, median 4,125; tier 4 (~9%) avg 79,375; tier 5 (~0.7%) avg 416,462. HUGE WIN ≥50k = 1 in ~700 spins, ≥500k = 1 in ~17,000 spins. Trigger rate unchanged at 1 in 83.
- Sticky game mechanic, retrigger rule, animations, achievements, the admin force command, and concurrency are all unchanged.

## [0.5.0.170522052026]

### Changed
- **`/catslots` eGirl Party opening animation rebuilt.** The old 5-frame quick-cut version was too fast to read. Replaced with a ~13s letter-by-letter reveal that spells out **EGIRL** and then **BONUS** one character at a time, each rendered as a 5×5 emoji bitmap (egirl emoji on, blank emoji off). Six stages: sparkle anticipation (3 frames), EGIRL letters (5 frames), pause, BONUS letters (5 frames), stats reveal (Free Spins / Multiplier / Sticky eGirls), and "PARTY STARTING". Five `BONUS_INTRO_*_DELAY` module-scope constants make the timing tunable in one place. New `LETTER_SHAPES` dict at module scope holds the bitmaps; new `_catslots_render_letter()` helper renders one letter. Bonus spin animation, sticky mechanic, payout math, wild substitution, and breakdown animation are all unchanged.

## [0.4.1.165622052026]

### Fixed
- **`/catslots` bonus-round grid alignment.** Sticky cells were being rendered as `✨{egirl_emoji}✨` while non-sticky cells rendered as a single emoji. The extra characters changed cell widths inconsistently, so columns visibly drifted across the 10 spins of an eGirl Party. The bonus renderer now emits one emoji per cell, exactly like the base game's `render_grid` (`main.py:13759`). Sticky status is now communicated below the grid as a `✨ Sticky eGirls: N/15` line in the settled-spin summary. Sticky game mechanic is unchanged — locked cells still act as wilds and still feed retrigger detection.

## [0.4.0.164722052026]

### Added
- **Cat Bot Store via Discord's native monetization** (`/store`). Discord-side checkout, SKU + entitlement based, fully optional. SKUs live in `config/store.json` with `kind` of `supporter` (grants `user.premium`) or `cosmetic` (recorded only). The command lists every configured SKU and renders Discord's official **Premium Button** (`ButtonStyle.premium`) per item — no custom URL, no Stripe, no Patreon, no aiohttp route. Owned items show a disabled "Owned ✓". Paginated 2-page help explains the wall and the checkout flow.
- **`on_entitlement_create` / `update` / `delete` event handlers** wire SKU ownership to `user.entitlements` (new JSONB list) and recompute `user.premium` as the OR of held supporter-tier SKUs. The existing `/editprofile`, `/customcat`, and blessing-anonymity gates continue to use `user.premium` and Just Work. Update events check `entitlement.ends_at` and dispatch to create-or-delete. Consumable SKUs are auto-consumed and logged with a TODO for cosmetic-grant wiring.
- **Startup reconciliation in `on_ready`** iterates `bot.entitlements(exclude_ended=True)` once, applies any drift to the DB, and removes stale SKUs that Discord no longer reports. Idempotent. `asyncio.sleep(0)` between users keeps the gateway heartbeat happy. Catches entitlement changes that happened while the bot was offline.
- **2 new achievements:** `store_first_purchase` (visible, 100 XP) on the first entitlement of any kind; `store_supporter` (visible, 250 XP) the first time `user.premium` flips on via a supporter SKU.
- **Env vars `store_enabled` and `support_invite`.** `store_enabled` default 0 — the `/store` command short-circuits to a friendly "not available" message when off, and the entitlement handlers + reconciliation pass become no-ops. `support_invite` default empty — wherever the upstream bot used to link to `discord.gg/staring`, the fork now uses this env var, with empty meaning the line or button is omitted.

### Changed
- **All live upstream-store references stripped from the bot surface.** `/editprofile`, `/customcat`, the `/rain` intro, `/rain` "not enough" error, `/inventory` blessings supporter prompt, the donor-channel rain DM, `on_guild_join` welcome, and the admin `cat!sweep` / `/reset` rollback hints all now key off `store_enabled` and `support_invite` instead of hardcoded upstream URLs. The 🛒 Store button on the `/rain` view (which pointed at `catbot.shop`) is removed entirely. The dormant `/news` body still contains historical upstream URLs in its early-returned code path — left alone per design intent.

### Internal
- Migration `017_store_entitlements.py` adds `user.entitlements jsonb NOT NULL DEFAULT '[]'`. Mirrored in `schema.sql`.
- New `config/store.json` catalog file (empty `skus[]` by default). New `config.STORE_ENABLED` and `config.SUPPORT_INVITE` in `config.py`. `config.store` loaded at module init alongside `config.battle` / `config.tuning` so it survives `cat!restart`.
- New helpers next to `_perks_*` in `main.py`: `_user_entitlements_load`, `_user_has_sku`, `_supporter_sku_ids`, `_store_sku_by_id`, `_recompute_premium`, `_apply_entitlement_create`, `_apply_entitlement_delete`. All idempotent; all no-op when `STORE_ENABLED` is off.

## [0.3.0.160922052026]

### Fixed
- **`/roulette` modal submit no longer 404s on slow DB / gateway lag.** RouletteModel.on_submit was doing `await user.refresh_from_db()` *before* `interaction.response.defer()`, so a slow DB query (or a gateway hiccup) could expire the interaction's 3-second response window. The defer call then raised `discord.errors.NotFound: Unknown interaction (10062)`. Restructured so cheap input-only validation runs first using `interaction.response.send_message`, defer is called immediately after to lock in the response window, and the DB-dependent affordability check uses `interaction.followup.send` for its error path.

## [0.3.0.102222052026]

### Changed
- **Second `/catslots` payout retune — wild substitution cap.** The previous retune underestimated wild substitution: when any eGirl lands on a line, the eval rule lets it stand in for any base, so Ultimate 4OAK at 8,000 and 5OAK at 60,000 were the practical ceiling on almost every line with a sticky eGirl. Flattened the top tiers so wild substitution can't print money:
  - 8bit `15/75/350 → 10/50/200`
  - Corrupt `25/125/600 → 15/75/350`
  - Professor `50/250/1250 → 25/125/600`
  - Divine `100/500/2500 → 50/250/1000`
  - Real `250/1500/10000 → 100/500/2000`
  - **Ultimate `1000/8000/60000 → 200/1000/4000`**
  - eGirl `100/1000/5000 → 100/500/2000`
  - Fine unchanged at `1/2/5`.
- Worst-case all-wild line during a bonus is now Ultimate 5OAK at 4,000× per_line × 3× bonus multiplier = 12,000× per_line. A natural Real 5OAK in regular play on a 20-line × 10-coin bet still pays 400,000 coins — "holy crap" territory, not "universe broke" territory.
- New total RTP estimate: **~78-81%** (base ~66%, bonus contribution ~12-15pp). More aggressive than Vegas penny-slot standard but conservative is correct given how hard it is to bound sticky-wild compounding analytically. Reel weights, sticky cap (frozen at trigger time, max 5), spin counts, multipliers, and wild substitution rule all unchanged.

## [0.3.0.100122052026]

### Fixed
- **EMERGENCY RETUNE — `/catslots` bonus round was paying ~150% RTP.** Live testing exposed that the wild-substitution rule (eGirl substitutes for any base symbol on a line) compounded catastrophically with the sticky-eGirl accumulation across bonus spins. Once the grid saturated with stickies, every line scored a 5OAK substitution at Ultimate or eGirl rates, and a forced 5-eGirl bonus on a 2-coin bet was paying out **10M+ coins**. Six coordinated changes pulled total RTP back to **~86%** (Vegas penny-slot range):
  - **Sticky_mask is now frozen at trigger time** — no in-bonus accumulation. Newly-landed eGirls still substitute as wilds for that one spin (so payouts and retrigger detection still work), but they do NOT lock for future spins. This is the primary fix.
  - **Bonus spin counts and multipliers slashed**: 3 eGirls → 5 spins × 2; 4 → 7 spins × 2 (was 10 × 3); 5 → 10 spins × 3 (was 18 × 5).
  - **eGirl base payouts slashed** — 3OAK 2,000 → 100; 4OAK 25,000 → 1,000; 5OAK 250,000 → 5,000. Wild-substitution made eGirl the de facto payout on most lines once any stickies existed, so the cut had to be aggressive. The bonus round IS the eGirl reward now; base-game eGirl line wins are small on purpose.
  - **Fine base payouts cut** — 3OAK stays at 1, 4OAK 4 → 2, 5OAK 10 → 5. Fine 5OAK fires on ~8% of line evaluations and was contributing ~80pp of RTP at the old 10× multiplier.
  - **Mid-tier payouts re-flattened** — 8bit, Corrupt, Professor, Divine, Real, Ultimate all retuned. Most see a small bump at 3-4OAK and the 5OAK level held roughly flat (Real 5OAK 10,000 unchanged; Ultimate 5OAK 75,000 → 60,000).
- A forced 5-eGirl bonus on a 20-line × 2-coin bet now typically pays in the **5,000 to 30,000** range, not the millions. Reel weights, trigger frequency (~1 in 84), wild substitution, animations, achievements, and the admin force command are all unchanged.

## [0.3.0.095022052026]

### Changed
- **`/catslots` retune for more bonus rounds, no schema change.** Three coordinated tweaks: eGirl reel weight bumped 2 → 3, eGirl base payouts cut ~73% (3OAK 7,500 → 2,000; 4OAK 100,000 → 25,000; 5OAK 1,000,000 → 250,000), bonus spin counts dropped 10/15/25 → 6/10/18 at the 3/4/5 eGirl tiers. Net effect: the 3+ trigger now lands ~1 in 84 spins (was ~1 in 246), base-game RTP stays at ~93%, and total effective RTP is ~105% (was ~100%). Multipliers, sticky-wild behavior, retrigger rule, animations, achievements, the admin force command, and concurrency are all untouched.

## [0.3.0.094822052026]

### Added
- **🎉 eGirl Party bonus round for `/catslots`.** After every settled regular spin, the bot counts eGirl symbols on the 5×3 grid. **3 or more triggers a free-spin bonus round** that runs immediately, with sticky-wild mechanics and a flat multiplier on top of base payouts. 3 eGirls → 10 spins × 2; 4 → 15 spins × 3; 5 → 25 spins × 5. Triggering eGirls start locked in place; any new eGirl that lands during the bonus also locks. During the bonus, eGirls act as wild substitutes and line evaluation picks the highest-paying interpretation (so `eGirl Real Real Fine X` pays Real-3OAK, not eGirl-1OAK). A bonus spin that lands 3+ newly-landed eGirls retriggers for +5 spins. The full sequence is animated: 5-frame opening transition (gold → hot pink), per-spin reel cycling (sticky cells visually frozen with ✨ marks), per-spin payout summary, and a final breakdown with a coin-counter tick-up and a per-round stat summary. Bonus payouts use their own lifetime counters (`catslots_bonus_triggers`, `catslots_bonus_coins_won`, `catslots_bonus_spins_total`), so the existing `/leaderboards type:Catslots` ranking stays stable.
- **2 new achievements:**
  - **EGIRL PARTY!** (`egirl_party`, Commands • 350 XP) — trigger the bonus round.
  - **Maximum Party** (`egirl_party_max`, Hard • 600 XP, hidden) — trigger with 5 eGirls.
- **Admin command `/catslots_force_bonus egirls:<3|4|5>`.** Manage-guild only. Queues a single-use override that overwrites N random visible cells with eGirl on the next spin so the bonus round triggers deterministically. The entry is popped the moment the spin reads it, so it always lasts exactly one spin.

### Internal
- Migration `016_catslots_bonus.py` adds 3 lifetime counters plus 2 ach booleans on `profile`. Idempotent per-column gate.
- New module-scope constants: `CATSLOTS_BONUS_TRIGGERS`, `CATSLOTS_BONUS_RETRIGGER_THRESHOLD`, `CATSLOTS_BONUS_RETRIGGER_REWARD`, `CATSLOTS_BONUS_COLOR_OPENING`, `CATSLOTS_BONUS_COLOR_PARTY`. New dict `catslots_force_bonus_users` next to `catslots_lock` / `catslots_last_bet`.
- Effective RTP rises into the **99 to 102%** band per the design doc, intentional and accepted for the closed-economy bot.

## [0.2.0.093122052026]

### Added
- **`/leaderboards type:Catslots` (🎰).** New per-server leaderboard ranking players by lifetime gross coins won at `/catslots` (`profile.catslots_coins_won`). Mirrors the "Job Coins" category pattern, no boolean column needed. Ranking by net (won minus bet) would expose the house edge and put most players in the red, which isn't a fun thing to rank by, so gross winnings it is. Available from the slash-command argument and the in-embed dropdown.

## [0.2.0.090422052026]

### Fixed
- **`/stocks` chart no longer crashes on macOS hosts** with `RuntimeError: Cannot create a GUI FigureManager outside the main thread using the MacOS backend`. matplotlib was autodetecting the macOS GUI backend, which is main-thread-only, but the chart renderer runs via `bot.loop.run_in_executor(...)` on a worker thread. Forced the non-interactive `Agg` backend at the top of `graph.py` before pyplot is imported. We only render PNG buffers anyway.

## [0.2.0.084522052026]

### Fixed
- **`achemb` no longer crashes when `bounty_novice` / `bounty_hunter` / `bounty_lord` unlocks via `on_message`.** The bounty callsites pass `send_type="followup"`, but `bounty()` is invoked from `on_message` paths with a `discord.Message` (which has no `.followup` — that's Interaction-only). Added a defensive fallback that mirrors the existing `reply → followup` fallback: when `send_type="followup"` is used on a non-Interaction object, the embed is sent via `channel.send` instead, with a warning log. The achievement still unlocks either way; only the failed embed send was crashing.

## [0.2.0.084322052026]

### Added
- **`/catstore` Extras now has two sub-pages: Rain *and* Packs.** Browsing Extras lands on a small sub-menu pointing at both. Navigation tree is now `landing → cats → cat_detail` and `landing → extras → rain` / `landing → extras → packs`; Back pops one level at a time.
- **📦 Packs in `/catstore` → Extras → Packs.** Stone, Bronze, Silver, Gold, Platinum, Diamond, and Celestial packs are sold for coins at face `pack["totalvalue"]` (with Cat Mafia discount/tax applied). **Wooden is excluded** — `/stocks` already provides a coins↔Wooden exchange at 100 coins/pack and selling Wooden here would duplicate that path. Each tier shows its current owned count next to its price; clicking Buy opens a quantity modal (max 99 per purchase). Packs land in the same `pack_{tier}` inventory columns as battlepass-rewarded packs — opening them with `/packs` is indistinguishable.
- **2 new achievements:**
  - **Pack Mule** (`catstore_pack_buyer`, Commands • 250 XP) — first pack purchase from /catstore.
  - **Stocked Up** (`catstore_pack_collector`, Hard • 500 XP, hidden) — bought at least one of every catstore pack tier (Stone, Bronze, Silver, Gold, Platinum, Diamond, Celestial). Backed by a new `profile.store_purchased_pack_tiers` JSONB array, parallel to the existing `store_purchased_rarities` (which still tracks cats only).
- **Existing catstore achievements also fire on pack purchases:** `catstore_first_buy` (any first /catstore purchase), `catstore_whale` (≥ 10k single transaction), `mafia_discount_max` (Lv10+), `mafia_tax_payer` (Lv0). `catstore_collector` is intentionally NOT touched — packs are not cat rarities.
- **Help pagination expanded to 5 pages.** Cat Store Overview / Cats / Extras Overview / Rain in the Store / Packs in the Store. The 💡 Help button now opens the right page based on which screen you clicked from.

### Changed
- **Extras landing tile reworded** from "Spend coins on rain…" to "Rain blocks and higher-tier packs." Emoji changed from ☔ to ✨ to reflect the broader scope.
- **`docs/design/economy.md`** — the "Before /catstore had two main sinks" framing is updated to acknowledge /catstore now spans three purchase shapes (targeted cats, ephemeral rain, non-targeted packs). New "Packs in /catstore" subsection with the full pricing table, design intent, no-arbitrage explanation against `/stocks`, and achievement integration.

### Internal
- Migration `015_store_pack_tiers.py` — adds `profile.store_purchased_pack_tiers jsonb NOT NULL DEFAULT '[]'::jsonb`. Existing profiles get `[]` — no backfill needed.
- New module-scope helpers in `main.py`: `CATSTORE_PACK_TIERS` tuple, `pack_buy_price(pack_name, mafia_discount_pct)`, `mark_pack_tier_purchased(profile, pack_name)` (parallel to `mark_store_purchased`).
- `gen_extras` renamed to `gen_rain`; new `gen_extras` is the sub-landing menu and a new `gen_packs` renders the pack catalog. `PackBuyModal` mirrors `/stocks` `WithdrawalModal`'s structure (int validation, max-count hint in the label).

## [0.1.0.081122052026]

### Fixed
- **`/catstore` no longer crashes the bot on load with `SyntaxError: name 'profile' is used prior to nonlocal declaration`.** Introduced in `0.1.0.080822052026`. Python requires `nonlocal` declarations to precede any use of the name in the function; the rain-purchase handler read `profile.refresh_from_db()` before its `nonlocal profile`. Moved the declaration to the top of the function.

## [0.1.0.080822052026]

### Added
- **`/catstore` is now a two-level menu with a new Extras → Rain item.** The command opens to a landing page that asks "what are you here for?" with two browses: **🐈 Cats** (the existing storefront, unchanged behavior) and **☔ Extras** (new). The cat list now sits one click deeper but gets a "← Back" button alongside the existing Help, so navigation feels lighter rather than heavier. Internally the command tracks state via a `mode` dict on the closure (mirroring `/jobs`), with screens `landing / cats / cat_detail / extras`.
- **☔ Rain blocks in `/catstore` → Extras.** A coin-purchased 15-second cat rain in the current channel. **The coins↔rain wall is now puncturable — but at a steep, exponentially-scaling tax.** Each block costs `12,000 × 1.5^blocks_bought_today` before the player's Cat Mafia discount/tax. The 8-block cumulative at Lv4 is ~591k coins; the 5th block alone is ~61k. Catches during bought rain count for everything battlepass-rain catches count for (quests, streaks, XP) — the price wall is what keeps the casino out of the rain economy, not a runtime gate. Daily counter resets at UTC midnight via lazy read; no cron. Buying into an already-running rain extends it by 15 seconds; buying into a quiet setupped channel kicks off `spawn_cat` + `rain_recovery_loop` like `/rain`. Operator constants `RAIN_BASE_PRICE`, `RAIN_SCALE`, `RAIN_BLOCK_SECONDS` live next to the other catstore helpers in `main.py`.
- **2 new rain-purchase achievements:**
  - **Cloud Seeder** (`catstore_rainmaker`, Commands • 300 XP) — buy any rain block.
  - **Acts of God** (`catstore_monsoon`, Hard • 500 XP, hidden) — buy 5 rain blocks in a single UTC day.
- **Existing catstore achievements also fire on qualifying rain purchases.** `catstore_whale` (≥ 10,000 coins) trips on block 1 already since the base price is 12k. `mafia_discount_max` and `mafia_tax_payer` apply the same way they do for cat purchases. `catstore_collector` is intentionally NOT touched — rain is not a rarity.
- **Context-aware `/catstore` help.** The 💡 Help button is now paginated and opens to the page that matches where you clicked from: landing → Cat Store Overview, Cats/Cat-detail → Cats help (existing text), Extras → Rain in the Store. Follows the same Prev/Next pattern as `_jobs_send_help`.

### Changed
- **`docs/design/economy.md`** — the "coins-vs-rain-minutes segregation is preserved" framing is reworded as **"preserved by pricing, not by hard prohibition"**, with a new `Rain in /catstore` subsection covering the formula, the cost curve, lazy UTC reset, active-rain stacking, achievement integration, and design intent.

### Internal
- Migration `014_rain_blocks.py` — adds `profile.rain_blocks_bought_today INTEGER NOT NULL DEFAULT 0` and `profile.rain_blocks_last_date TEXT` (nullable). NULL last-date naturally compares "not today" so the counter starts fresh on first purchase — no backfill needed.
- `_rain_blocks_today(profile)` — pure read; lazy UTC reset is computed at call time, not on write.
- The `/catstore` command body is now ~600 lines structured around the `mode` dict; cat-buy/sell modals and `gen_detail` are unchanged in behavior.

## [0.0.5.075722052026]

### Changed
- **All `/catstore` prices doubled.** Both buy and sell prices in the Cat Store now run through a new `CATSTORE_PRICE_MULTIPLIER` (currently `2`), via a `catstore_face_value(cat_type)` helper applied at all five store-side call sites (buy/sell price functions, the buy modal's "saved X" toast, the sell modal's "mafia took X" toast, and the detail view's face-value reference). Trades, gifts, and job reward valuations still use the unmultiplied `cat_value`. Percent-based discount/sell-cap math is untouched, so the round-trip anti-arbitrage spread (`sell_pct ≤ buy_pct - 5`) still holds with the new scale. Doubling the constant in `main.py` is the single point of control for future rescales.

## [0.0.5.073822052026]

### Fixed
- **`/jobs` no longer crashes with `TypeError: Model.collect() got an unexpected keyword argument 'fields'`.** Introduced in the duplicate-offer dedup fix (`0.0.5.060722052026`); `Model.collect()` doesn't accept a `fields=` projection (that's `filter`/`limit`/`collect_limit`). Dropped the projection — the per-window result set is at most a handful of rows, so the full-column fetch is negligible.

## [0.0.5.071722052026]

### Added
- **Two new battlepass misc quests for /catslots.** `catslots` — "Spin the /catslots machine 10 times" (150–250 XP, progress 10). `catslots_win` — "Win at /catslots" (250–350 XP, progress 1). Both live in the misc pool alongside the existing /slots and /roulette quests; they enter the random rotation immediately. Wired via `progress(message, profile, "catslots")` on every spin and `progress(message, profile, "catslots_win")` when `total_payout > 0`.

## [0.0.5.070422052026]

### Changed
- **`/catslots` per-line bet cap: 100 coins.** Total bet is implicitly capped at `max(lines) × max_per_line = 20 × 100 = 2,000 coins` per spin. Without this, a rich player could bet their entire wallet on one spin; an eGirl 5-of-a-kind would then pay billions and obliterate the economy. The cap bounds the worst-case jackpot at 100,000,000 coins. Enforced in the modal's `on_submit`; the modal label and stats embed surface the cap.

## [0.0.5.065822052026]

### Added
- **`/catslots` — a Vegas-style 5×3 slot machine.** Sits alongside `/slots` as a second casino game. Pick **lines** (1, 5, 9, or 20) and **coins per line** via a modal; total bet = lines × per_line. Each spin rolls 5 independent weighted reels (8 cat-rarity symbols from Fine → eGirl, with rarer cats weighted exponentially lower). Wins evaluate per active payline by counting consecutive matches from column 1 — 3-, 4-, or 5-of-a-kind pays a multiplier of `coins_per_line` from the payout table; multiple winning lines stack. **Big wins** (`total_payout ≥ 100 × total_bet`) flash a banner and fire the `big_win_catslots` achievement. Shares the **`coins`** wallet with `/roulette`/`/stocks`/`/packs`/`/catstore`; same debt rule (`max(coins, 100)` cap). No remove-debt button — the recovery path is `/jobs`. Target RTP ~93%. Lifetime stats (spins/wins/big_wins/coins_bet/coins_won) on the new `profile.catslots_*` columns; `catslots_lock` gates concurrency separately from `slots_lock`. See [`docs/design/economy.md`](docs/design/economy.md#catslots) for the full design.
- **Post-spin UX: Spin Again + Change Bet.** After every spin the result message shows two buttons. **Spin Again** repeats the just-completed bet, re-validating affordability against the player's current coins (in case they spent some elsewhere between clicks); if they can't afford the same bet anymore, an ephemeral nudge plus an updated view with Spin Again disabled. **Change Bet** opens the same modal pre-filled with the last-used lines and per-line values so only the changing field needs editing. Last-bet state lives in an in-memory `catslots_last_bet` dict keyed by `(user, server)`; it resets on bot restart.
- **4 new achievements** wired into `/catslots`:
  - **Cat Slots** (Commands • 150 XP) — spin `/catslots` once.
  - **Whisker Winner** (Commands • 250 XP) — win at `/catslots`.
  - **Furr Jackpot** (Commands • 400 XP) — trigger a big win at `/catslots`.
  - **Schrödinger's Spinner** (Commands • 200 XP, hidden) — try to spin while already spinning.
- **Casino quest progression.** `/catslots` spins count toward the `casino` extra-slot battlepass quest under the existing `slots` game bit. The `slots`/`slots2` catch quests stay scoped to `/slots`.

### Internal
- Migration `013_catslots.py` — 9 new `profile` columns (5 lifetime counters, 4 ach booleans). Idempotent per-column gate, marker file pattern.
- Module-scope constants in `main.py`: `CATSLOTS_SYMBOLS`, `CATSLOTS_WEIGHTS`, `CATSLOTS_ALLOWED_LINES`, `CATSLOTS_PAYLINES` (20 paylines, line 1 = middle row to match the rigged-eGirl override), `CATSLOTS_PAYOUTS`. `catslots_lock = []` next to `slots_lock`. `catslots_last_bet: dict[int, tuple[int,int]] = {}` for post-spin button state.
- `/catslots` honors the existing `rigged_users` list (forces a 5-eGirl line-1 jackpot for testing).

## [0.0.5.060722052026]

### Fixed
- **`/jobs` board no longer respawns a completed offer within the same window.** The "what's already used" dedup in `_jobs_refresh_offers_if_needed` queried `state = 'offered'` only, so a resolved/declined/expired template dropped out of the set and the deterministic offer generator happily re-emitted it the next time the slot count was below the cap — making a finished job appear to come right back. Fixed by deduping against *all* states in the current window. Slots empty out naturally as you work through them; window rollover refills.
- **Crew list on the `/jobs` Send screen now actually shows cat-rarity icons.** The block was wrapped in a triple-backtick code block for monospace alignment, but Discord doesn't render custom emojis inside code blocks (the user saw raw `<:finecat:1503…>` text). Now renders as a markdown bullet list — emojis show, with bold/italic distinguishing effective vs. raw SP.

### Changed
- **`/packs` "Open all" button appears at 2+ packs.** Threshold was previously >5, which hid the button in the common case of holding a handful of packs.

## [0.0.5.144121052026]

### Fixed
- **`/perks` no longer crashes with `Section.__init__() missing 1 required keyword-only argument: 'accessory'`.** Components V2 `Section` requires an accessory widget (Button/Thumbnail); the perk rows were info-only, so they now render as plain title + body strings instead of being wrapped in a Section. Same visual result.

### Added
- **Mafia favors (job perks) — full system.** A third reward axis on top of coins and cats/packs. **Every successful `/jobs` drops a perk** (chance is 1.0 across all tiers; tier shapes *which* perk you get rather than *whether* you get one). 31 perks across catch-loop, economy, pack, jobs-feedback, catnip-side, and quirky buckets, weighted so weak perks are common and capstone perks are rare. Each NPC has a personality-flavored pool: Whiskers does reliability + pack, Lucian Jr is impulsive + pack-heavy, Jinx is catnip-side, Jeremy is coins-everywhere, Lucian Sr is vendetta/rep, Sofia is the dealer, Big Score is capstone-rare. Perks live on `profile.job_perks` (JSONB), pruned lazily on read, **NOT** suspended by the Cat Police Pinch (unlike catnip perks — mafia perks were earned, so they keep working). Refresh-or-extend stacking; 5-perk cap with oldest-timed eviction; charge-based perks are sticky. New `/perks` command shows active favors with remaining time/charges. Result screen surfaces drop block + which perks fired this commit. Send screen shows active commit-time perks. Reroll Board adds a button to `/jobs` when active that re-rolls the current window's offer board. Tunable end-to-end via the admin webui under `/jobs` — drop chance, per-(NPC, tier) pools, and per-perk strength tables, with referential warnings on bad references. See `docs/design/jobs.md` for the system writeup.
- **Perks are previewed on the offer card.** The perk roll happens at offer-generation time, not outcome time, and is persisted on the `JobInstance` row. Every surface that shows a job shows the perk it's offering: `/jobs` board cards (🎁 line under the reward), the send screen ("You'll receive on success: …"), the public Accept embed posted to the channel, and the result screen. On near-miss or wipe the result screen shows a faded "💨 The bonus walks" line so the missed perk feels material. The roll uses an independent seeded RNG stream so the same window/slot deterministically produces the same perk preview, and tuning the perk pool won't shift difficulty/reward determinism.
- **6 job offers per window, paginated 3 per page.** `/jobs` now generates 6 contracts every 6h (up from 3). The board shows Page 1/2 with `← Prev` / `Next →` buttons next to Help. Pagination state survives accepts/declines; rerolling resets to page 1.
- **5 new achievements for the perks system:** First Favor (first perk received • 300 XP), Pocket Full of Favors (5 active at once • 400 XP), Made of Favors (hidden — receive every perk • 600 XP), Lucky Strike (hidden — fire Crew Insurance to convert a near-miss • 500 XP), Made Man (hidden — earn a Tier 5 perk from the Big Score • 700 XP).
- **`perk_user` battlepass extra quest** — have a job perk active • 220–280 XP. Fires from `/perks`, idempotent per quest period.
- **"Mafia Favors" leaderboard category** — ranks players by lifetime distinct perk IDs received. Backed by the new `profile.perks_received` JSONB column.
- **Jobs help page "Perks"** at `min_level_to_see: 3`. Reputation page amended with a note about per-NPC perk personalities.

### Changed
- **Job offers now display proper cat-rarity icons in the reward summary.** The reward line on every offer/send/result surface was using `get_emoji(<rarity_lower>)` instead of the convention `get_emoji(<rarity_lower>+"cat")`, which made every cat icon render as 🔳. One-line fix; affects all `/jobs` views.

### Internal
- Migrations: `010_jobs_perks.py` (`profile.job_perks JSONB`), `011_perks_received.py` (`profile.perks_received JSONB`), `012_jobs_perk_drop.py` (`jobinstance.perk_drop TEXT`).
- `_perks_*` helper namespace in `main.py` alongside `_jobs_*`. New constants: `PERKS_CATALOG`, `PERKS_DROP_POOLS`, `PERKS_DROP_CHANCE_BY_TIER`, `PERKS_MAX_ACTIVE`, `RESOLVING_PERKS`, `JOBS_BOARD_PAGE_SIZE`. Re-read on `cat!restart` like the other `JOBS_*` constants.
- 3 new webui sections under `/jobs` (drop chance + pools + catalog), 3 new templates, 9 new HTTP routes. `webui-sync` referential warnings extended to catch unknown perk IDs in pools, missing tier_table entries, and unreachable catalog perks.
- Catalog tuning during the launch arc: removed 7 perks that didn't fit current gameplay — the heat trio (Heat Shield, Heat Reset, Cooling Off) plus Combo Shield, Eagle Eye, Lightning Hands, and Bake.gg Comp. Hook code stays in place (inert without catalog entries) so any of them can be revived by a webui edit alone. Final live catalog: 31 perks.

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
