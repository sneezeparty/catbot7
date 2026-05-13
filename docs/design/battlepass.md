# Battlepass

The battlepass is Cat Bot's meta-progression layer: catch cats → do quests → gain XP → claim level rewards → repeat. Seasons reset monthly. Per-user, per-server (a player has a separate battlepass in each server they play in).

## Seasons

- **Season number** = months elapsed since `2026-04-01` (the self-hosted instance's epoch).
- Rollover happens on the 1st of each calendar month, UTC (with a +4h offset to match the bot's day boundary).
- Each season has its own 30-level ladder defined in `config/battlepass.json` under `seasons["<n>"]`.

When season rolls over, **all per-user quest state is wiped** (catch/misc/extra cooldowns reset; passive XP counters like `catnip_xp_awarded` reset to 0). The user's prior season is appended to `profile.bp_history` as a `"season,level,progress;"` string.

**Design intent:** monthly cadence is short enough to feel achievable but long enough that missing a few days isn't catastrophic. The reset is full — no "season pass" carry-over — because hoarding XP across seasons would invalidate the per-season balance.

## Level rewards

Each of the 30 levels has a fixed reward: cats, packs, or rain minutes. The reward XP cost climbs from 550 to 1000 across the season. Past level 30, the ladder enters an "Extra Rewards" tier: every 1500 XP grants one Stone pack indefinitely.

**Design intent:** the reward curve is *front-loaded with variety* (early levels mix cat tiers and packs) and *back-loaded with packs* (later levels lean into pack tiers since those are scaling rewards). The Stone-pack-forever tail exists so engaged players past level 30 don't feel like they hit a wall.

## Quest slots

Each user has three quest slots active concurrently. Quests refresh on a per-slot **`QUEST_COOLDOWN`** timer after completion (defined in `config/tuning.json`).

### Catch slot

Catch-event-driven quests. Examples: "Catch 2 Fine cats", "Catch a Rare or better", "Catch in under 10 sec". Defined under `quests.catch` in `battlepass.json`.

XP range: ~250–400.

**Design intent:** these are the always-on quests — they make every cat catch feel like progress, not just a number going up.

### Misc slot

Action-driven quests outside the catch loop. Examples: "/Gift someone", "Spin the slot machine 10 times", "Read a /news article". Defined under `quests.misc`.

XP range: ~120–350.

**Design intent:** these push the user to explore commands they wouldn't otherwise touch. The lower XP ceiling reflects that they're often "one click" tasks.

### Extra slot

Added in May 2026 to replace the retired vote quest. Defined under `quests.extra`. Current options:

| Quest | Reward | Gating |
| --- | --- | --- |
| `catnip_session` | ~280–340 XP | requires catnip unlocked (level ≥ 1) |
| `casino` | ~180–240 XP × 3 | 3 different games of {slots, roulette, pig, cookieclicker} |
| `social` | ~220–290 XP | one /gift or /trade with another player |
| `sacrifice` | **dynamic** (25–300, hidden) | gift the bot a cat; XP scales with cat rarity |

**Design intent:** the extra slot is the *novel-mechanic* slot — quests here probe parts of the bot that catch/misc don't cover (catnip, social, casino variety). The `sacrifice` quest is intentionally opaque: users see "reward depends on the cat" and don't see the per-cat table. This creates a small thrill of "which cat is worth sacrificing" without turning into a spreadsheet exercise.

The sacrifice XP table caps at **300 even when multiplied by amount**, so gifting 100 Fine cats is the same 300 XP as gifting one eGirl. This prevents farm-spamming.

> **TODO(design):** if `extra` adds a 5th candidate quest, watch the rotation distribution — with 4 candidates, eligibility-gated quests (catnip_session) effectively become the only choice for non-catnip-unlocked users. Consider weighting.

## Quest selection

`generate_quest()` in `main.py` picks a random quest from the slot's pool, with these eligibility checks:

- `slots`, `reminder`, `plush` — retired, always skipped.
- `prism` — skipped if the user's prism-boost chance is below `PRISM_BOOST_FLOOR` (would be unwinnable).
- `news` — skipped if the user has read every news article and the latest 4 are all read.
- `achievement` — skipped if the user already has >30 visible achievements.
- `catnip_session` — skipped if user has no catnip access.

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

- Quest cooldowns are stored per-slot (`catch_cooldown`, `misc_cooldown`, `extra_cooldown`). A cooldown of 0 means "in progress"; non-zero is the unix timestamp of completion.
- Quest progress is **wiped on season rollover**, not on quest completion (completed quest just sets cooldown to `now`).
- `casino_progress_temp` is the bitmask state for the casino extra quest. It resets when the quest completes (or season rolls).

If you add a new quest slot, mirror this pattern: `<slot>_quest`, `<slot>_progress`, `<slot>_cooldown`, `<slot>_reward`, plus any quest-specific temp state.
