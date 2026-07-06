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

Each level has a configured reward: cats, packs, rain minutes, or a **Mystery** (resolved at grant time by `resolve_mystery()` — see the outcome table below). Past the final level, the ladder enters an "Extra Rewards" tier: every 3,000 XP grants one Mystery — a random non-special pack tier, weighted `1/totalvalue` toward the cheap tiers (`grant_mystery_pack()` in `main.py`) — indefinitely. (Tunable via `config/tuning.json → extra_level_xp` / `extra_level_reward`; previously a flat Stone pack every 6,300 XP, before the overflow-reward retune.) Code that reads level count uses `len(config.battle["seasons"][str(user.season)])` everywhere — adding or trimming levels per season is purely a JSON change.

**XP cost curves:**

- **Season 1 (Levels 1–30):** ramp from 550 to 1000 XP per level. Total: 23,250 XP.
- **Season 2 (Levels 1–30):** three levels per step, stepping up from 900 to 1800 XP (blocks of 3: 900, 1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800). Total to reach Level 30: **40,500 XP**. (Earlier S2 builds used the 1150→2050 curve totaling 48,000 XP; that left returning veterans who'd already harvested the early-game achievement XP front-load missing L30 by a single day. The flatter curve gives engaged voters ~4 days of real-life slack while still keeping L30 out of reach for casual play.)
- **Seasons 3+ (Levels 1–30):** unchanged from the original 1150→2050 curve (48,000 XP). Per-season values live in `config/battlepass.json` and can be retuned individually.
- **Seasons 2+ (Levels 31–40):** one level per step — 2500, 2950, 3350, 3800, 4200, 4600, 5050, 5450, 5900, 6300. Total for Levels 31–40: 44,100 XP. **Combined Level 40 total in S2: 84,600 XP** (40,500 + 44,100). **Full pass (Level 40 + one Extra Rewards tick): 87,600 XP** (84,600 + 3,000, after the Mystery-pack overflow-reward retune; the tick was 6,300 XP — total 90,900 — before that change).

**Design calibration (Season 2, post-rebalance):** an average engaged player completing all five daily quest slots (catch, misc, extra, challenge, plus vote when `voting_enabled=1`) yields ~1,450–1,560 XP/day, putting a *returning* engaged voter at L30 by ~day 26 (4-day slack vs. the 30-day season), a *fresh* engaged voter (with ~11.6k XP front-load from early achievements) at L30 by ~day 19, and a casual 3/4-quest player at ~33 days (still misses L30). A Tier-4-perks grinder is expected to reach approximately Level 40 in-season. When `voting_enabled=0`, the vote slot still yields its substitute-quest XP, so calibration shifts only modestly.

**Design intent:** the reward curve is *front-loaded with variety* (early levels mix cat tiers and packs) and *back-loaded with lottery* — as of July 2026, levels 31–39 in every 40-level season are **Mysteries** (9 lottery pulls), and level 40 is the one *guaranteed* per-season capstone (a Celestial pack; S3's also carries +1 rain minute). This trades guaranteed tail value for variance on purpose: the fixed Bronze→Diamond ramp it replaced was worth more in expected pack value, but every level was a foregone conclusion — the Mystery tail makes each late level a slot-machine moment, the per-level `grant_bonus_pack` drop still guarantees something every level, and the level-40 Celestial anchors the season goal. The Mystery-forever Extra Rewards tier (every 3,000 XP; previously a flat Stone per 6,300) continues past level 40 so the very-engaged never hit a wall.

### What a Mystery resolves to

`resolve_mystery()` (main.py, shared by both level-up paths so odds can't drift) rolls the outcome table in `config/tuning.json → mystery_outcomes`. At the shipped weights: a **5% pre-roll** first (a *Double Mystery* — two outcomes; nested rolls skip the pre-roll, so no chains), then per roll: **pack 72%** (the classic 1/totalvalue pull — plain single pack overall = 68.4%), **rain time 9%** (15s/30s/60s, banked in `profile.rain_seconds`, auto-rolls into a real bonus rain minute at 60s), **XP 7.5%** (250/500/1000), **coin pouch 7%** (500–2,500), **voucher 3%** (Double Pack 1.8% / Bounty Skip 0.96% / eGirl Bonus 0.24% ≈ 1-in-417 — the rarest thing in the box), **scratchcard 1.5%**. EV ≈ 345 pack-value vs 292 for the old all-pack Mystery — the sweetener is carried entirely by rare outcomes; "usually it's just a pack" is the protected experience.

**Vouchers 🎟️** are one-shot effects on `profile.vouchers` (JSONB, migration 035): *Double Pack* doubles the full contents (cats + coin variant) of the next pack opened (first pack of an Open All batch, matching the charge-perk precedent); *eGirl Bonus* forces the next `/catslots` spin to trigger the tier-3 eGirl Party (rides the same grid-overwrite mechanism as the admin force command; consumption is persisted before the spin animation); *Bounty Skip* autocompletes the first incomplete catnip bounty on the holder's next catch, with the normal completion toast/counter/achievements. Held vouchers render in `/battlepass`; they wipe at season rollover (pack-adjacent value) while the rain-seconds bank is preserved (it's rain, and rain survives the wipe).

**Three invariants for tuners:** (1) the max XP tier — doubled — must stay below the cheapest Mystery-bearing level cost (2,500 XP at S2+ L31), or an XP outcome could chain levels faster than it costs them; (2) Mystery XP is folded into the level loop's *local* accumulator — never route it through `grant_achievement_xp` (reentrancy would double-count levels; see `resolve_mystery`'s docstring); (3) `"Mystery"` is valid only as a level's **primary** reward — the `extra_reward` stack has no Mystery branch and would crash on it.

## Quest slots

Each user has five quest slots active concurrently. As of the July 2026 daily-reset rework (migration 036), every slot rerolls **once per day** at the daily boundary — `int((time.time() + 4*3600) // 86400)`, the same +4h clock used by the season and weekly rollovers — regardless of whether the slot was completed. Incomplete quests do **not** carry over: `refresh_quests` compares the profile's stored `quests_day` against today's day-index and, on a mismatch, force-rerolls every daily slot (catch/misc/extra/challenge/vote) by stamping its cooldown to the `1` sentinel and zeroing progress. This replaces the older model, where a slot rerolled `QUEST_COOLDOWN` (12h) after *completion* and an unfinished quest could otherwise sit in its slot indefinitely. `QUEST_COOLDOWN` (`config/tuning.json`) still exists but is now only consulted for (a) the real-vote eligibility gate (see Vote slot, below) and (b) a legacy fallback cadence on any profile that predates migration 036 (`quests_day` column absent) — the rollout is backward compatible without a hard cutover.

### Catch slot

Catch-event-driven quests. Examples: "Catch 2 Fine cats", "Catch a Rare or better", "Catch in under 10 sec". Defined under `quests.catch` in `battlepass.json`.

XP range: ~250–400.

**Design intent:** these are the always-on quests — they make every cat catch feel like progress, not just a number going up.

### Misc slot

Action-driven quests outside the catch loop. Examples: "/Gift someone", "Spin the /catslots machine once/twice/3 times/10 times", "Win at /catslots", "Catch 5 /fish", "Cause /chaos 50 times", "Bake 50 /cookies", "/Brew 50 coffees", "/Roll a 6 on default dice", "Win 3 /roulette bets", "Get 50+ score in /pig", "/Define a word". The live catalog is `config/battlepass.json → quests.misc`.

The July 2026 catalog refresh (alongside the upstream feature port) retired the one-click filler quests that had aged badly (`slots`, `slots2`, `news`, `reminder`, `rate`, `catball`) and added quests wired to the new commands (`fish`, `chaos`, `cookie`, `coffee`) plus harder skill variants of surviving quests (`roll6`, `roulette3`, `pig50`, and the reworked `ttc` — see below). `gift`, `ping`, and `tiktok` were deliberately kept even though upstream removed them: they feed the vote-substitute pool, which needs a healthy supply of single-action quests.

The `ttc` quest is "Tie against Cat Bot 3 times in /tictactoe" — only ties against the bot itself count. The minimax bot always ties under perfect play, so this is a genuine skill check; the old "play a game" version was trivially farmable via self-play, which no longer counts for anything.

XP range: ~120–350.

**Design intent:** these push the user to explore commands they wouldn't otherwise touch. The lower XP ceiling reflects that they're often "one click" tasks; the multi-step additions (`cookie`/`coffee`/`chaos` at ×50, `fish` at ×5) sit at the top of the range because they ask for a real session, not a single click.

### Extra slot

Added in May 2026. Originally added when the vote quest was inactive on this self-hosted instance; the vote quest slot has since been re-enabled (gated on `config.VOTING_ENABLED`). Defined under `quests.extra`. Current options:

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
| `store_spree` | ~320–400 XP | spend ≥ 2,500 coins on a single `/catstore` purchase |
| `perk_user` | ~220–280 XP | have a job perk active at the moment a successful `/jobs` commit lands (fires from the commit block in `main.py` when `_perks_active_ids(profile)` is non-empty); also fires from `/perks` for players who hold a perk but don't commit another job; implicitly gated on having received at least one job perk drop |

**Design intent:** the extra slot is the *novel-mechanic* slot — quests here probe parts of the bot that catch/misc don't cover (catnip, social, casino variety, jobs, the Cat Store). The `sacrifice` quest is intentionally opaque: users see "reward depends on the cat" and don't see the per-cat table. This creates a small thrill of "which cat is worth sacrificing" without turning into a spreadsheet exercise.

The sacrifice XP table caps at **300 even when multiplied by amount**, so gifting 100 Fine cats is the same 300 XP as gifting one eGirl. This prevents farm-spamming.

The `gift3` quest tracks distinct recipients in `profile.gift3_recipients` (comma-separated IDs). The field is cleared on quest completion and on season rollover, mirroring `casino_progress_temp`. Gifting the bot itself does not count.

> **TODO(design):** `job_easy`, `job_hard`, and `perk_user` have no eligibility skips in `generate_quest` even though they have implicit prerequisites (catnip ≥ 2, catnip ≥ 8, and "has ever received a job perk" respectively). A player who rolls one of these without the prereq can't progress until they hit the gate — the quest just sits in the slot. This may be intentional (a soft nudge toward those systems) or an oversight; decide whether to add skip rules (mirroring the `catnip_session` skip) or document this as the intended "the bot tells you what to try next" behavior.

### Challenge slot

Added alongside `gift3` in May 2026. A 5th peer slot — not gated on catnip, vote status, or any other prerequisite. Every user gets one quest from the challenge pool each cycle. Defined under `quests.challenge`.

| Quest | Reward | Condition axis |
| --- | --- | --- |
| `under3` | ~320–370 XP | Speed: catch in under 3 seconds |
| `under2` | ~340–390 XP | Speed: catch in under 2 seconds |
| `under5` | ~280–330 XP | Speed: catch in under 5 seconds |
| `slow` | ~250–290 XP | Patience: catch after the cat has waited ≥ 60 seconds |
| `legendary+` | ~380–400 XP | Rarity: catch a Legendary or rarer cat (`LEGENDARY_PLUS` frozenset, defined from `cattypes` in `main.py`) |
| `epic3` | ~300–360 XP | Rarity: catch 3 Epic-or-rarer cats (progress=3, `EPIC_PLUS` frozenset) |
| `catnip_catch` | ~280–340 XP | Context: catch 10 cats while catnip is active (progress=10) |
| `streak10` | ~320–380 XP | Streak: catch_streak crosses a multiple of 10 (progress=1, fires from the streak-XP boundary in `progress()`) |
| `bonus_win` | ~300–360 XP | Execution: solve a [bonus-cat](economy.md#bonus-cats) minigame correctly (fires from the same `progress(interaction, profile, "bonus_win")` call site as the weekly `bonus` quest) |
| `variety5` | ~300–360 XP | Variety: catch 5 distinct cat types since the last daily reset (progress=5, tracked in `profile.quests_variety_types`, cleared by the daily reset — migration 036) |

**Design intent:** the challenge slot is the *skill-ceiling* slot. All ten quests are catch-condition flavored — they reward players who are fast, patient, lucky, or consistent — but the bar is deliberately higher than the base catch slot (250–400 XP vs. the catch slot's 230–400 XP, with harder trigger conditions). The pool spans several distinct axes (speed — `under2`/`under3`/`under5`; patience — `slow`; rarity — `legendary+`/`epic3`; catnip-context; streak; execution; variety), so any given cycle will test one aspect of how you play, not just "catch more". (The pool grew from the original 5 to 10 quests in the July 2026 upstream feature port; the speed and rarity axes now each carry more than one entry.)

`streak10` uses `progress=1` and fires once when `catch_streak` crosses a multiple of 10, rather than counting increments. This keeps it from needing to stay in sync with streak resets (a reset just means the player has to rebuild the streak).

`slow` is intentionally omitted from the belated-catch path (which requires <3 s response): a belated catch cannot satisfy the 60-second patience condition, so the branch is skipped rather than awarding credit.

### Vote slot

A separate quest slot that is **gated on `config.VOTING_ENABLED`** — when `voting_enabled=0` the slot is inert and `/vote` returns "voting isn't enabled on this instance." When enabled, the `/vote` command surfaces the player's vote status and reward info.

**Slot composition (1/2 real vote, 1/2 substitute).** When `generate_quest("vote")` runs, it rolls `random.randint(1, 2)`:
- **Pass (== 1, ~50%):** the slot is set to the real "Vote on Top.gg" quest, drawn from `quests.vote` in `battlepass.json`. XP range is **300–450 XP base** with a **2× weekend multiplier**. The reward is randomized and stored in `user.vote_reward`.
- **Fail (~50%):** a single-action misc quest is picked at random from a filtered subset of `quests.misc` (see substitute pool rules below) and stored in `user.vote_quest` (the `profile.vote_quest` text column, default `''`). The substitute's XP range comes from the misc quest's own `xp_min/xp_max`. XP and claim timestamp still ride on `vote_reward` and `vote_cooldown` — no extra columns needed.

If the substitute pool is empty (extreme config drift), the slot falls back to the real vote quest.

**Substitute pool filtering rules.** A misc quest is eligible for the substitute pool only if:
- `progress == 1` (single-action completion — multi-step quests cannot substitute because the vote slot has no progress counter wired to misc actions outside `/battlepass` itself).
- Not in the retired set: `slots`, `reminder`, `plush`.
- Not `define` when `WORDNIK_API_KEY` is unset.
- Not equal to the player's currently-active `misc_quest` (avoids the same quest appearing in two slots simultaneously).

**Refresh ordering.** `refresh_quests` regenerates the vote slot **last** (after catch, misc, extra, and challenge) so that the freshly-rolled `misc_quest` is visible when the substitute pool filters out duplicates.

**Claim mechanics.**

*Real vote path:* unchanged — `do_vote()` auto-grants XP at vote time and the `/battlepass` safety-net catch path coexist (see below for the short-circuit). When `user.vote_quest != ""` (substitute is active), the `quest == "vote"` branch in `progress()` returns early without crediting, so `do_vote()` never double-pays a substitute slot.

*Substitute path:* when `user.vote_quest` is set and equals the quest name passed to `progress()`, the slot completes as a single-action misc quest. `quest_xp_boost` job-perk multiplier applies (it is a misc-pool quest). `vote_cooldown` is stamped to `now()` as the claim time. The voter does not need to /vote — the substitute is triggered by performing the underlying misc action (e.g., `/gift`, `/catslots`, `/news`).

**Auto-grant at vote time (real vote only).** On this self-hosted fork, real-vote XP is **auto-granted at vote time** rather than requiring the player to manually run `/battlepass` in each server. When `do_vote()` fires (either via top.gg webhook or vote-replay polling), it iterates `Profile.collect("user_id = $1", user_id)` and calls `progress(None, profile, "vote")` for every profile the voter has. The `quest == "vote"` branch short-circuits immediately if `user.vote_quest != ""` (substitute is active), so only profiles in a real-vote cycle receive the auto-grant.

The `/battlepass` open claim gate (`progress(message, user, "vote")` when `vote_time_topgg + QUEST_COOLDOWN > now`) is preserved as an **idempotent safety net** for profiles that didn't exist at vote time.

`progress()` accepts a `None` interaction for this purpose — the call skips embed logic when the first argument is `None`.

**Column.** `profile.vote_quest` (text, default `''`): holds the substitute misc quest id. Empty string means "real vote cycle." Added by migration `028_vote_substitute_slot.py`. On season rollover / `refresh_quests` sanity pass, `vote_quest` is cleared if the stored quest id no longer exists in `quests.misc` or if `define` loses its API key.

**Catch-message vote button.** Appears when `VOTING_ENABLED` is true, conditioned in two stages: first `random.randint(1, 40) == 1` (roughly 1-in-40 catches), then `vote_time_topgg + 43200 < now` (player is eligible to vote). Both must hold; the random gate is the binding constraint on most catches.

The **`/battlepass` vote quest line** renders "Vote on Top.gg" as a clickable markdown link to `TOP_GG_VOTE_URL` for the real-vote cycle; substitute slots render the misc quest description instead.

**Env-var requirements:** voting is **on by default** (`voting_enabled` defaults to `"1"`); set `voting_enabled=0` to disable. `top_gg_modern_token` is required for vote-replay fallback polling and stats posting. `webhook_verify` is optional — starts the public `0.0.0.0:8069` webhook server; without it, only the vote-replay polling path is active.

**Design intent:** voting is now more enticing — the real-vote XP (300–450 base) is competitive with other daily quest rewards (not the smallest), reflecting an intent to make voting feel genuinely worthwhile rather than just a token bonus. The substitute mechanic still preserves a 5th XP source on no-vote days so the slot never sits dead: on non-vote cycles players receive an activity-driven misc quest instead. The substitute is constrained to single-action misc quests so it can complete naturally as a side effect of normal play, not as an extra obligation. The 50/50 split (down from 1/3 real / 2/3 substitute) means the real vote quest appears roughly every other cycle. The `refresh_quests` last-ordering rule ensures no quest appears in two slots at once, preserving the economy design of five distinct daily tasks.

### Weekly quest track

A sixth quest track (ported from upstream cattlepass v2.1, July 2026), rendered above the daily slots in `/battlepass`. Unlike the five daily slots — which reroll once per day at the daily boundary (see [Quest slots](#quest-slots), above) — the weekly track has its own independent cadence: the season-month is divided into four fixed 7-day windows (`start_time`/`end_time` seconds-from-month-start in `quests.weekly`, on the same UTC+4 clock as the season rollover), and each window hosts exactly one fixed quest, completable once. Days 28 through end-of-month are a deliberate dead zone (the `""` sentinel window) — no weekly quest is active and the section disappears from `/battlepass`.

The live rotation: week 1 `catch` (catch 70 cats), week 2 `brave+` (10 cats rarer than Brave), week 3 `bonus` (succeed in 4 bonus-cat minigames — depends on [bonus cats](economy.md#bonus-cats), so a `bonus_cat_chance_coef = 0` kill-switch makes that week uncompletable), week 4 `different` (13 distinct cattypes, deduped via `profile.weekly_cattypes`).

Reward is a flat **`weekly_quest_xp` (2,000) + `weekly_quest_scratchcards` (1)** per completion (`config/tuning.json`), deliberately **not** run through `_qxp_bonus` perk scaling or weekend doubling — at 2,000 XP a multiplier would swing more than every daily quest combined, so the marquee reward stays fixed. Weekly state (`weekly_quest`, `weekly_progress`, `weekly_cattypes`, `scratchcards` — migration 034) is wiped at season rollover with the other slots; unspent scratchcards are wiped too, because they are pack-lottery tickets and the season wipe empties packs (carrying them over would leak pack value across the economy reset).

**`/scratch`** spends one scratchcard on a 5×5 pick-10 pair-matching board paying packs (Wooden through Celestial) or per-server bonus rain minutes, weighted heavily toward the cheap tiers. The payout is precomputed from the shuffle before the player picks a single tile — the board is theater. This is upstream's deliberate "can't lose to a slow connection" safety net: a player who falls asleep mid-board still gets everything their card rolled.

**Design intent:** the weekly track is the *retention cadence* between the once-daily slot rhythm and the monthly season arc — one chunky goal per week that survives a few missed days, where the daily slots don't. The reward routes through a lottery minigame instead of a direct payout because a fixed weekly payout would be pure homework; the scratch card converts the same expected value into a moment of variance the player initiates. Scratchcards have exactly one source (weekly completion) so the card economy can't inflate.

## Quest selection

`generate_quest()` in `main.py` picks a random quest from the slot's pool, with these eligibility checks:

- `prism` — skipped if the user's prism-boost chance is below `PRISM_BOOST_FLOOR` (would be unwinnable).
- `achievement` — skipped if the user already has >30 visible achievements.
- `catnip_session` — skipped if user has no catnip access.
- `define` — skipped when `WORDNIK_API_KEY` is unset (the command isn't registered without it).

Retiring a quest means **deleting it from the catalog**, not skip-listing it: `random.choice` over the catalog keys can't pick what isn't there, and the retired-quest guards in `refresh_quests` re-roll any profile still holding a deleted id. (The old `slots`/`reminder`/`plush` skip list and the `news` eligibility branch were removed with the July 2026 catalog refresh — they guarded quests that no longer exist.)

The challenge slot has no per-quest eligibility skips — all ten quests are completable by any player (Legendary+/Epic+ cats are rare but spawnable by any server with cat spawning enabled; `bonus_win` shares the weekly `bonus` quest's `bonus_cat_chance_coef` kill-switch caveat above).

The vote slot's substitute pool has its own filtering rules (documented under the Vote slot section above): `progress == 1`, not retired, not `define` without API key, not the current `misc_quest`.

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

- Quest cooldowns are stored per-slot (`catch_cooldown`, `misc_cooldown`, `extra_cooldown`, `challenge_cooldown`, plus `vote_cooldown`). A cooldown of 0 means "in progress"; non-zero is the unix timestamp of completion; `cooldown == 1` is the force-reroll sentinel, shared by the retired-quest guards and the daily reset below.
- Quest progress is **wiped on season rollover, and — since migration 036 — once per calendar day** regardless of completion (see [Quest slots](#quest-slots), above). On a profile that predates migration 036 (`quests_day` column absent), the daily reset doesn't run and a slot instead rerolls the legacy way: `QUEST_COOLDOWN` after the quest was completed, with an unfinished quest sitting until the season rolls.
- **Daily reset (migration 036).** `refresh_quests` stamps `profile.quests_day` with the current day-index (`int((time.time() + 4*3600) // 86400)`) every time it runs. When the stored value doesn't match today's, every daily slot (catch/misc/extra/challenge/vote) is force-rerolled — cooldown set to the `1` sentinel, progress zeroed — regardless of whether it was completed that day. `profile.quests_variety_types` (smallint[]) tracks distinct cat-rarity indices caught since the last daily reset, backing the `variety5` challenge quest; it's cleared by the same reset. The weekly track is exempt (it's month-windowed, not daily).
- `casino_progress_temp` is the bitmask state for the casino extra quest. It resets when the quest completes (or season rolls, or the daily reset fires).
- `gift3_recipients` is the comma-separated list of distinct recipient IDs for the `gift3` extra quest. It resets on completion, on season rollover, and on the daily reset.
- The weekly track has no cooldown column at all — completion is gated by `weekly_progress` reaching the quest target, and rotation is gated by the calendar windows. One completion per window by construction.
- Quest-reminder DMs exist only for the **vote** slot. The per-slot reminder columns (`reminder_catch`, `reminder_misc`, `reminder_challenge`) are inert leftovers in `schema.sql` from the removed multi-slot reminder system — no code reads or writes them, and `postpone_reminder()` only handles the `vote` custom_id.

The vote slot additionally uses `profile.vote_quest` (text, default `''`) to hold the current substitute misc quest id. Empty string means "real vote cycle." The substitute completes single-action — no `vote_progress` column exists; the `progress=1` pool constraint removes the need for one.

If you add a new quest slot, mirror this pattern: `<slot>_quest`, `<slot>_progress`, `<slot>_cooldown`, `<slot>_reward`, plus any quest-specific temp state. The challenge slot's migration is `migrations/003_challenge_slot.py` (idempotent ALTER TABLE per column, `.done` marker). The vote substitute column is `migrations/028_vote_substitute_slot.py`. The daily-reset columns (`quests_day`, `quests_variety_types`) are `migrations/036_quests_day.py`.
