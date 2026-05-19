# Changelog

All notable user-facing changes to Cat Bot are tracked here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project does not currently version with semver tags; entries are grouped by release date or by "[Unreleased]" for the working branch.

The [`changelog-sync`](.claude/agents/changelog-sync.md) subagent updates the `[Unreleased]` section whenever bot-surface files change. Curated wording lives here; the agent appends drafts and flags entries with `> _draft_` until a human approves and de-drafts them.

## [Unreleased]

### Added
- **Fifth battlepass quest slot** (`challenge_quest`) for harder catch-condition quests. Wired through `generate_quest`, `refresh_quests` (season rollover + retired-quest cleanup), `progress()`, the /battlepass UI render, and DM reminders (with postpone button). Five challenge quests in the new `quests.challenge` config section:
  - `under3` — Catch a cat in under 3 seconds • 320–370 XP
  - `slow` — Catch after a cat has sat for a full minute • 250–290 XP
  - `legendary+` — Catch a Legendary or rarer cat • 380–400 XP
  - `catnip_catch` — Catch 10 cats while catnip is active • 280–340 XP • progress 10
  - `streak10` — Catch 10 cats in a row without missing • 320–380 XP • progress 10
  > _draft_
- **`define` misc quest** — Use /define once • 250–290 XP. Added to the `quests.misc` pool.
  > _draft_
- **`gift3` extra quest** — /Gift 3 different players in one quest window • 320–380 XP • progress 3. Tracks distinct recipients via a new `gift3_recipients` text column on profile (cleared on quest completion and season rollover).
  > _draft_
- **Third battlepass quest slot** (`extra_quest`) with four candidate quests:
  - `catnip_session` — activate /catnip (requires catnip access)
  - `casino` — play 3 different games of {slots, roulette, pig, cookieclicker}
  - `social` — complete a /gift to a player or /trade
  - `sacrifice` — gift the cat a cat; XP scales 25–300 by cat rarity, hidden from the user
- **Passive XP drips**: +50 XP for the first catch of the UTC day, +20 XP every 10-catch streak, +100 XP per catnip level-up (capped at 1000/season), +20 XP to prism owners when their prism boosts another user's catch.
- **`docs/design/`** evergreen design docs covering economy, battlepass, catnip, and achievements. Maintained by the `design-docs-sync` subagent.
- **`CHANGELOG.md`** with auto-draft maintenance via the `changelog-sync` subagent.
- **Pack rewards in battlepass level-up track.** One pack (Wooden through Celestial, scaling with tier) is now interspersed as a reward at specific levels across all 17 seasons. Season 1 has one of each tier across its 30 levels.
  > _draft_
- **Snowballer** catnip perk: each consecutive catch builds a combo stack (cap 30); per-stack % chance to trigger a double-catch feeds into the existing double pool. Stack resets to 1 after 5 minutes idle. Per-stack % by tier: 0.5 / 0.75 / 1.25 / 2.0 / 3.0. Maximum double-chance contribution at cap: 15 / 22.5 / 37.5 / 60 / 90%.
  > _draft_
- **Battlepass Booster** catnip perk: each catch has a % chance to grant +5 battlepass XP immediately (via the existing XP path; can trigger a level-up). Chance by tier: 5 / 8 / 12 / 20 / 30%.
  > _draft_
- **Bait & Switch** catnip perk: each catch has a % chance to immediately respawn a cat in the same channel. Does not fire during a rain. Chance by tier: 1 / 1.5 / 2.5 / 5 / 8%.
  > _draft_

### Changed
- **`user.vote_streak` renamed to `user.daily_catch_streak`**; `user.max_vote_streak` renamed to `user.max_daily_streak`. Data copied 1:1 by migration 004; old columns dropped. The counter increments on the first catch of each UTC day and resets if a day is skipped — semantics unchanged, name now accurate.
  > _draft_
- **"Voting Booster" catnip perk renamed to "Loyalty Streak"** (ID: `timer_add_streak` → `loyalty_streak`). Description updated to "Your daily catch streak (N) boosts catnip duration." Mechanic unchanged — still extends /catnip activation duration scaled to streak count.
  > _draft_
- **/stats display label** "Current vote streak" → "Current daily catch streak".
  > _draft_
- **/battlepass fire-emoji line** "N× catch streak" → "N-day catch streak". Previous label implied a per-catch streak; this aligns with the daily-reset semantics.
  > _draft_
- Pack values rebalanced (+50% across all tiers) to suit the self-hosted instance's smaller economy.
- Catch streak (`profile.catch_streak`) now resets when the bot laughs at a missed catch.
- **Pack sub-1 fail behavior overhauled.** Instead of always giving a single Fine cat as consolation:
  - Wooden fails re-roll the cat type and run the lottery once more.
  - Stone+ fails open a pack one tier lower as consolation (with that pack's normal upgrade chain).
  - If the retry also fails, the consolation is 3 Fine cats instead of 1.
- **Random pack drops from catches now show a tier-themed embed** instead of a single inline line, with per-tier color, a quirky random opener, and tier-scaled hype text (chill for Wooden, full caps drama for Celestial).
- **Battlepass level-up bonus packs also get their own tier-themed embed** alongside the level-up reward embed. Same color palette as catch drops but distinct opener/vibe copy so the two are recognizable.
- **All catnip durations unified to 24 hours.** Previously levels 1–4 lasted 2–8 h; now every level runs for 24 h.
  > _draft_
> **REVERTED:** **Time Manipulator perk** timer-extend change superseded — perk has since been removed entirely (see Removed section).
- Catnip level names for L5–L11 updated to reflect the uniform duration (e.g. "Second Bounty", "Tougher Bounties", etc.); old names referenced duration increments that no longer apply.
  > _draft_
- **Plush promo footer removed** from bot messages; the `/plush` limited-time campaign has ended.
  > _draft_

### Removed
- **Time Manipulator** catnip perk retired. Weight set to 0; no new players will receive it. Existing holders: perk goes inert (no effect on catches). Entry kept in config for index-stability of stored perk references.
  > _draft_
- Hidden easter-egg achievements `website_user` (`cat!i_like_cat_website`) and `click_here` (`cat!i_clicked_there`) removed from the trigger list; the phrases no longer unlock anything.
  > _draft_
- **Discord-invite buttons removed from catch messages.** The four "Join our Discord" button variants ("Join our Discord!", "John Discord 🤠", "DAVE DISCORD 😀💀⚠️🥺", and "JOHN AND DAVE HAD A SON 💀🤠😀⚠️🥺") no longer appear under catch messages. The top.gg vote button and the dark-market shadow button are unaffected.
  > _draft_

### Internal
- `update_catch_streak()` now returns a `bool` indicating whether this was the first catch of the UTC day, used to gate the first-catch passive XP grant. Renamed to `update_daily_catch_streak()` to distinguish it from `profile.catch_streak` (the per-catch counter driving the `streak10` challenge quest).
  > _draft_
- New helpers in `main.py`: `progress_casino_quest`, `grant_catnip_levelup_xp`, `grant_first_catch_of_day_xp`, `grant_catch_streak_xp`.
- New profile columns: `extra_quest`, `extra_progress`, `extra_cooldown`, `extra_reward`, `catch_streak`, `casino_progress_temp`, `catnip_xp_awarded`. (Already applied to the live DB; mirrored in `schema.sql`.)
- `CATNIP_TIMER_EXTEND` and other tuning constants now read from `config/tuning.json` at module load; previously some were hardcoded literals.
  > _draft_
- New profile columns for the challenge slot: `challenge_quest`, `challenge_progress`, `challenge_cooldown`, `challenge_reward`, `reminder_challenge`, `gift3_recipients`. Added to `schema.sql`; backfilled by `migrations/003_challenge_slot.py` (idempotent ADD COLUMNs). `LEGENDARY_PLUS` frozenset constant added to `main.py` for the `legendary+` quest trigger.
  > _draft_
- New `profile.combo_stack` integer column (default 0) tracks Snowballer per-user stack. Added to `schema.sql`; backfilled by `migrations/002_combo_stack.py`.
  > _draft_
- `TriggerEngine` (from `ach_engine.py`) imported and constructed at module load; achievement trigger dispatch is now data-driven for aches with a `trigger` block in `config/aches.json`.
  > _draft_
- **Migration 004** (`migrations/004_voting_cleanup.py`): idempotent ADD new columns → backfill UPDATE → DROP old columns for the `vote_streak`/`daily_catch_streak` rename. Bot must be stopped to run.
  > _draft_
- **webui admin panel** user-table field whitelist (`webui/routes/user_table.py`) updated to expose `daily_catch_streak` and `max_daily_streak` in place of the dropped column names.
  > _draft_
- `CLAUDE.md` and `config.py` comments updated: "vote_streak is repurposed" note removed; `VOTING_ENABLED` documented as the dormant on/off switch for `/vote` + top.gg webhook.
  > _draft_

## Conventions

- **One bullet = one user-perceivable change.** Internal refactors that don't change behavior go under "Internal" and are optional.
- **Lead with the noun, not the verb.** "Third quest slot added" reads worse than "Third quest slot — adds…". Use "Added/Changed/Fixed/Removed" headers and a bulleted list.
- **Cross-link to design docs** when the entry is a balance change; cross-link to `docs/design/economy.md#xp--battlepass-currency` etc.
- **Numbers are config, not changelog.** "XP bonus per level changed from 50 to 100" belongs here; "level 7 reward is now 3 Rare cats" does not — that's just a config tune.
- **Squash trivia.** A series of "fix typo" / "tweak wording" commits should collapse into one line.
