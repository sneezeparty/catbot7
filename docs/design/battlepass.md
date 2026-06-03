# Battlepass

The battlepass is Cat Bot's meta-progression layer: catch cats → do quests → gain XP → claim level rewards → repeat. Seasons reset monthly. Per-user, per-server (a player has a separate battlepass in each server they play in).

## Seasons

- **Season number** = months elapsed since `2026-04-01` (the self-hosted instance's epoch).
- Rollover happens on the 1st of each calendar month, UTC (with a +4h offset to match the bot's day boundary).
- Each season has its own level ladder defined in `config/battlepass.json` under `seasons["<n>"]`. **Season 1 has 30 levels (legacy onboarding shape). Seasons 2 and up have 40 levels.**

When season rolls over (lazy, on the user's next interaction that calls `refresh_quests`), the wipe is broad. The user's prior season is appended to `profile.bp_history` as a `"season,level,progress;"` string, then the following state is reset on the profile:

**Always wiped (existing behavior):**
- Battlepass level + progress (`battlepass`, `progress`)
- All four quest slots (catch / misc / extra / challenge): quest, progress, cooldown, reward
- `casino_progress_temp`, `gift3_recipients`, `catnip_xp_awarded`

**Wiped per the 0.6.5 economy-reset design:**
- **Coins** (`coins → SEASON_STARTING_COINS`, default **100** — a starting allowance, not a full reset to 0; value is `config.tuning["season_starting_coins"]`)
- **All catnip state**: `catnip_level`, `catnip_active`, `catnip_total_cats`, `catnip_amount`, `catnip_price`, all `bounty_*` slots, all stored catnip perks (`perks`, `perk1/2/3`, `reroll`, `reroll_level`)
- **All operational jobs state**: `heat`, `respect`, `faction_rep`, `job_perks`, `perks_suspended_until`, `jobs_pending_*`, `big_score_season`, `whiskers_favor_active`, `whiskers_favor_season`
- **All pack inventories**: every `pack_*` column (Wooden through Celestial + event packs)

**Always preserved:**
- All `cat_<Rarity>` inventories (player history)
- Stocks (`stock_*`)
- Prisms (separate table)
- `discovered_cats`, `store_purchased_rarities`, `store_purchased_pack_tiers`
- All achievement booleans + `unlocked_aches`
- Lifetime stats (`jobs_completed`, `big_score_wins`, `catnip_activations`, `highest_catnip_level`, `bounties_complete`, etc.)
- **Season-recap lifetime counters** (`coins_earned`, `roulette_coins_won`, `roulette_coins_bet`, `stock_coins_earned`, `stock_coins_spent`, and the `catslots_coins_*` / `catslots_bonus_coins_won` columns) — these are never wiped; only the baseline snapshot is updated.
- `season_stat_baseline` (JSONB) — re-captured at rollover to the current lifetime-counter values; this is the new season's "starting line" for "this season" diff queries.
- Daily catch streak (cross-server, on `user` not `profile`)
- Rain minutes (`rain_minutes`, `rain_blocks_*`), `combo_stack`, `prisms_crafted`
- Tutorial / first-time flags (`tutorial_errand_complete`, `jobs_send_screen_seen`, etc.)

**Reset notice.** After the wipe, `profile.season_reset_pending` is set to true. The next time the player runs `/battlepass`, `/catnip`, `/jobs`, `/catstore`, `/stats`, or `/inventory`, a one-shot **ephemeral** embed renders ("Cattlepass Season N just started…") and the flag clears. The notice is intentionally private — other players in the channel don't see it.

**Pre-rollover warning.** On the last calendar day of the month (same UTC+4 clock as `refresh_quests`), a standalone background task (`_season_announcement_loop`, started in `setup()` and stored on `config.season_announce_task`) broadcasts a one-shot warning embed to every setupped channel. The embed lists what will be wiped (coins, battlepass, catnip/mafia, jobs, packs) and what will be kept (cats, prisms, stocks, discovered cats, achievements, streaks), and names the upcoming season's level count. The broadcast fires at most once per season — dedup is a `season_warn.txt` marker file that stores the last-warned season number and is re-read on `cat!restart`. Per-channel failures (missing permissions, deleted channel) are skipped silently.

Servers can opt out via `/settings` → **season announcements** toggle, which maps to `server.season_announcements` (boolean, default `true`). The toggle is checked once per guild and cached for the duration of each broadcast. The task is reload-safe: `setup()` cancels the old task handle before creating a new one, so `cat!restart` does not spawn duplicate loops.

**Season recap leaderboard.** On the 1st of each calendar month (season start), the bot broadcasts a per-server end-of-season recap embed to every setupped channel, honoring the same `season_announcements` opt-out as the warning. The recap lists the top 5 players in 7 categories:

| Category | Metric |
| --- | --- |
| Cattlepass | battlepass level + progress (snapshot; lazy wipe means this is still queryable on the 1st) |
| Mafia | `catnip_level` at end of season |
| Biggest Earner | `coins_earned` this season |
| Cats Caught | `total_catches` this season |
| Heists | `jobs_completed` this season |
| Gambling | net: (`roulette_coins_won` − `roulette_coins_bet`) + (`catslots_coins_won` + `catslots_bonus_coins_won` − `catslots_coins_bet`) |
| Stock profit | `stock_coins_earned` − `stock_coins_spent` this season |

**Why snapshot instead of live query.** The season wipe in `refresh_quests` is lazy — it runs on a player's first post-rollover interaction, not at midnight. A live query on the 1st would therefore zero out the most-active players (who triggered their wipe earliest) while retaining stale data for inactive players. The fix is a pre-rollover snapshot: during the season's last calendar day the `_season_announcement_loop` calls `_capture_season_recap_snapshot()` on every hourly tick, overwriting `season_recap.json` each time. The final tick before rollover wins. On the 1st, `_broadcast_season_recap()` reads the JSON and posts. A separate dedup cursor (`season_recap.txt`, mirrors `season_warn.txt`) ensures the broadcast fires at most once per season across restarts.

**"This season" totals via baseline-diff.** The categories above that say "this season" are computed as `lifetime_counter − season_stat_baseline[counter]`. The new lifetime counter columns (`coins_earned`, `roulette_coins_won`, `roulette_coins_bet`, `stock_coins_earned`, `stock_coins_spent`) are instrumented at every coin-gain, roulette, and stock path in `main.py` and accumulate forever. At each season rollover (in the `refresh_quests` wipe block) `season_stat_baseline` is set to the player's current lifetime-counter values; the next season's diff then reads `current − baseline`. For Season 1 the baseline is `{}` (treated as 0), so the value equals the full lifetime — correct, since this instance launched at Season 1 start. The `catslots_coins_*` and `catslots_bonus_coins_won` columns were added earlier (migration 013 / 016) and are included in the baseline.

These counters and `season_stat_baseline` require migration 022. Code paths that write the counters use a `_bump()` guard (catches `KeyError` on missing columns) and snapshot/broadcast paths call `_recap_columns_present()` (a probe-read cached on `config`) to no-op cleanly on an un-migrated database.

**Season trophies.** Three of the recap categories — `earner` (most coins earned), `cats` (most cats caught), `heists` (most heists completed) — also award **permanent trophies** to their top 3 finishers per server. When `_broadcast_season_recap()` posts a guild's recap embed, it also (a) writes a record `{"season": N, "category": ..., "rank": 1|2|3}` to each winner's `profile.season_trophies` (JSONB, migration 024), and (b) posts a second "🏆 Season N Champions" embed naming the winners with 🥇🥈🥉 medals. Trophies are append-only and never expire; they render on `/catprofile` as a compact medal list sorted newest-season-first (with overflow at 12 entries). The award and the ceremony embed both gate on the guild's `season_announcements` opt-in, so an opted-out server gets neither — keeping the silent-trophy case from ever existing. Idempotency: before appending to `season_trophies`, the writer checks for an existing `(season, category, rank)` match, so a crash-and-replay mid-broadcast doesn't duplicate trophies. Servers with fewer than 3 active players in a category award fewer medals (top 1 or 2, or none if no one earned anything).

**Design intent:** the recap leaderboard is a snapshot — interesting for a minute, then forgotten. Trophies are the *receipt*: a player who finishes top 3 in their server walks away with something they can show off on their profile card forever. The three categories are deliberately drawn from the three core gameplay axes (economy, catching, mafia jobs) so different play styles can each chase a different medal. The medal collection becomes the long-term motivator that the monthly wipe otherwise erases.

**Design intent:** monthly cadence is short enough to feel achievable but long enough that missing a few days isn't catastrophic. The reset is full on the active-economy axis (coins + catnip + jobs + packs) so every player starts the month on the same baseline; collection assets (cats, prisms, stocks, aches) accumulate forever because that's a separate dimension of progression that shouldn't be punished for playing long.

## Level rewards

Each level has a fixed reward: cats, packs, or rain minutes. Past the final level, the ladder enters an "Extra Rewards" tier: every 6000 XP grants one Stone pack indefinitely. Code that reads level count uses `len(config.battle["seasons"][str(user.season)])` everywhere — adding or trimming levels per season is purely a JSON change.

**XP cost curves:**

- **Season 1 (Levels 1–30):** ramp from 550 to 1000 XP per level. Total: 23,250 XP.
- **Seasons 2+ (Levels 1–30):** three levels per step, stepping up from 1100 to 2000 XP (blocks of 3: 1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800, 1900, 2000). Total to reach Level 30: **44,500 XP**.
- **Seasons 2+ (Levels 31–40):** one level per step — 2400, 2800, 3200, 3600, 4000, 4400, 4800, 5200, 5600, 6000. Total for Levels 31–40: 38,000 XP. **Combined Level 40 total: 82,500 XP. Full pass (Level 40 + one Extra Rewards tick): 88,500 XP.**

**Design calibration (seasons 2+):** an average daily player completing 4 quest slots per day (vote slot is uncompletable on this instance) plus passive XP is expected to reach approximately Level 30 over a 30-day season; a very engaged player grinding all four slots at maximum XP is expected to reach approximately Level 40.

**Design intent:** the reward curve is *front-loaded with variety* (early levels mix cat tiers and packs) and *back-loaded with packs* (later levels lean into pack tiers since those are scaling rewards). The Stone-pack-forever tail exists so engaged players past the final level don't feel like they hit a wall. The 31–40 tail added in seasons 2+ replaces the early Stone-pack farm with more meaningful per-level rewards, capped by a per-season capstone (typically a Celestial pack at level 40), with the Stone-pack tail still kicking in past level 40 for the very-engaged.

## Quest slots

Each user has five quest slots active concurrently. Quests refresh on a per-slot **`QUEST_COOLDOWN`** timer after completion (defined in `config/tuning.json`).

### Catch slot

Catch-event-driven quests. Examples: "Catch 2 Fine cats", "Catch a Rare or better", "Catch in under 10 sec". Defined under `quests.catch` in `battlepass.json`.

XP range: ~250–400.

**Design intent:** these are the always-on quests — they make every cat catch feel like progress, not just a number going up.

### Misc slot

Action-driven quests outside the catch loop. Examples: "/Gift someone", "Spin the slot machine 10 times", "Spin the /catslots machine once/twice/3 times/10 times", "Win at /catslots", "Read a /news article", "/Define a word". Defined under `quests.misc`.

XP range: ~120–350.

**Design intent:** these push the user to explore commands they wouldn't otherwise touch. The lower XP ceiling reflects that they're often "one click" tasks.

### Extra slot

Added in May 2026 to replace the retired vote quest. Defined under `quests.extra`. Current options:

| Quest | Reward | Description / Gating |
| --- | --- | --- |
| `catnip_session` | ~280–340 XP | activate `/catnip` once; requires catnip unlocked (skipped in `generate_quest` if `catnip_level == 0`) |
| `casino` | ~180–240 XP × 3 | 3 different games of {slots, catslots, roulette, pig, cookieclicker} (progress=3, tracked via `casino_progress_temp` bitmask) |
| `social` | ~220–290 XP | one /gift or /trade with another player |
| `sacrifice` | **dynamic** (25–300, hidden) | /gift the bot a cat; XP scales with cat rarity |
| `gift3` | ~320–380 XP | /gift 3 *distinct* players in a single quest cycle |
| `job_easy` | ~200–280 XP | complete any mafia job (Tier 1+); implicitly gated on catnip ≥ 2 (T1 job requirement) |
| `job_hard` | ~360–420 XP | complete a Tier 4+ mafia job; implicitly gated on catnip ≥ 8 (T4 job requirement) |
| `store_buy` | ~240–300 XP | buy any cat from `/catstore` |
| `store_sell` | ~220–280 XP | sell any cat to `/catstore` |
| `store_spree` | ~320–400 XP | spend ≥ 5,000 coins on a single `/catstore` purchase |
| `perk_user` | ~220–280 XP | have a job perk active when the trigger fires; implicitly gated on having received at least one job perk drop |

**Design intent:** the extra slot is the *novel-mechanic* slot — quests here probe parts of the bot that catch/misc don't cover (catnip, social, casino variety, jobs, the Cat Store). The `sacrifice` quest is intentionally opaque: users see "reward depends on the cat" and don't see the per-cat table. This creates a small thrill of "which cat is worth sacrificing" without turning into a spreadsheet exercise.

The sacrifice XP table caps at **300 even when multiplied by amount**, so gifting 100 Fine cats is the same 300 XP as gifting one eGirl. This prevents farm-spamming.

The `gift3` quest tracks distinct recipients in `profile.gift3_recipients` (comma-separated IDs). The field is cleared on quest completion and on season rollover, mirroring `casino_progress_temp`. Gifting the bot itself does not count.

> **TODO(design):** `job_easy`, `job_hard`, and `perk_user` have no eligibility skips in `generate_quest` even though they have implicit prerequisites (catnip ≥ 2, catnip ≥ 8, and "has ever received a job perk" respectively). A player who rolls one of these without the prereq can't progress until they hit the gate — the quest just sits in the slot. This may be intentional (a soft nudge toward those systems) or an oversight; decide whether to add skip rules (mirroring the `catnip_session` skip) or document this as the intended "the bot tells you what to try next" behavior.

### Challenge slot

Added alongside `gift3` in May 2026. A 5th peer slot — not gated on catnip, vote status, or any other prerequisite. Every user gets one quest from the challenge pool each cycle. Defined under `quests.challenge`.

| Quest | Reward | Condition axis |
| --- | --- | --- |
| `under3` | ~320–370 XP | Speed: catch in under 3 seconds |
| `slow` | ~250–290 XP | Patience: catch after the cat has waited ≥ 60 seconds |
| `legendary+` | ~380–400 XP | Rarity: catch a Legendary or rarer cat (`LEGENDARY_PLUS` frozenset, defined from `cattypes` in `main.py`) |
| `catnip_catch` | ~280–340 XP | Context: catch 10 cats while catnip is active (progress=10) |
| `streak10` | ~320–380 XP | Streak: catch_streak crosses a multiple of 10 (progress=1, fires from the streak-XP boundary in `progress()`) |

**Design intent:** the challenge slot is the *skill-ceiling* slot. All five quests are catch-condition flavored — they reward players who are fast, patient, lucky, or consistent — but the bar is deliberately higher than the base catch slot (250–400 XP vs. the catch slot's 230–400 XP, with harder trigger conditions). Having five distinct axes (speed, patience, rarity, catnip-context, streak) means any given cycle will test one aspect of how you play, not just "catch more".

`streak10` uses `progress=1` and fires once when `catch_streak` crosses a multiple of 10, rather than counting increments. This keeps it from needing to stay in sync with streak resets (a reset just means the player has to rebuild the streak).

`slow` is intentionally omitted from the belated-catch path (which requires <3 s response): a belated catch cannot satisfy the 60-second patience condition, so the branch is skipped rather than awarding credit.

## Quest selection

`generate_quest()` in `main.py` picks a random quest from the slot's pool, with these eligibility checks:

- `slots`, `reminder`, `plush` — retired, always skipped.
- `prism` — skipped if the user's prism-boost chance is below `PRISM_BOOST_FLOOR` (would be unwinnable).
- `news` — skipped if the user has read every news article and the latest 4 are all read.
- `achievement` — skipped if the user already has >30 visible achievements.
- `catnip_session` — skipped if user has no catnip access.

The challenge slot has no per-quest eligibility skips — all five quests are completable by any player (Legendary+ cats are rare but spawnable by any server with cat spawning enabled).

**Design intent:** the bot should never assign a quest a user *cannot* complete. The skip list grows organically — when a quest type becomes infeasible for some users, add it here.

## Passive XP sources

Quests are the primary XP source; the [economy doc](economy.md#xp--battlepass-currency) lists the passive drips. Key constraint: passive XP must **route through `grant_achievement_xp()`** so level-up embeds and bonus-pack drops fire correctly. Don't write `user.progress += N` directly.

## Level-up flow

When a quest completes (or passive XP rolls over a level boundary):

1. Increment `user.battlepass`, deduct level XP, add reward to inventory.
2. Roll a **bonus pack** via `grant_bonus_pack()` — a random-tier pack on top of the named reward. Weights live in `config.tuning["pack_tier_weights"]`.
3. Emit a level-up embed.
4. If carry-over XP still exceeds the next level's threshold, loop.

**Design intent:** the bonus pack is the secret-sauce reward — it makes every level-up feel slightly different and gives even repeated levels a small randomized payoff. Don't remove it without replacing it with an equivalent randomness sweetener.

## Anti-griefing & guardrails

- Quest cooldowns are stored per-slot (`catch_cooldown`, `misc_cooldown`, `extra_cooldown`, `challenge_cooldown`). A cooldown of 0 means "in progress"; non-zero is the unix timestamp of completion.
- Quest progress is **wiped on season rollover**, not on quest completion (completed quest just sets cooldown to `now`).
- `casino_progress_temp` is the bitmask state for the casino extra quest. It resets when the quest completes (or season rolls).
- `gift3_recipients` is the comma-separated list of distinct recipient IDs for the `gift3` extra quest. It resets on completion and on season rollover.
- `reminder_challenge` drives DM reminders for the challenge slot (same pattern as `reminder_catch`, `reminder_misc`, `reminder_extra`). Postpone uses the `challenge_` prefix in the button custom ID.

If you add a new quest slot, mirror this pattern: `<slot>_quest`, `<slot>_progress`, `<slot>_cooldown`, `<slot>_reward`, plus any quest-specific temp state. The challenge slot's migration is `migrations/003_challenge_slot.py` (idempotent ALTER TABLE per column, `.done` marker).
