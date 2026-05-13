# Changelog

All notable user-facing changes to Cat Bot are tracked here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project does not currently version with semver tags; entries are grouped by release date or by "[Unreleased]" for the working branch.

The [`changelog-sync`](.claude/agents/changelog-sync.md) subagent updates the `[Unreleased]` section whenever bot-surface files change. Curated wording lives here; the agent appends drafts and flags entries with `> _draft_` until a human approves and de-drafts them.

## [Unreleased]

### Added
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

### Changed
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
- **Time Manipulator perk** (activated during a catnip session) now extends the timer by **30 minutes** (up from 5). The catch message also shows the computed value rather than a hardcoded "+5 minutes".
  > _draft_
- Catnip level names for L5–L11 updated to reflect the uniform duration (e.g. "Second Bounty", "Tougher Bounties", etc.); old names referenced duration increments that no longer apply.
  > _draft_
- **Plush promo footer removed** from bot messages; the `/plush` limited-time campaign has ended.
  > _draft_

### Removed
- Hidden easter-egg achievements `website_user` (`cat!i_like_cat_website`) and `click_here` (`cat!i_clicked_there`) removed from the trigger list; the phrases no longer unlock anything.
  > _draft_

### Internal
- `update_catch_streak()` now returns a `bool` indicating whether this was the first catch of the UTC day, used to gate the first-catch passive XP grant.
- New helpers in `main.py`: `progress_casino_quest`, `grant_catnip_levelup_xp`, `grant_first_catch_of_day_xp`, `grant_catch_streak_xp`.
- New profile columns: `extra_quest`, `extra_progress`, `extra_cooldown`, `extra_reward`, `catch_streak`, `casino_progress_temp`, `catnip_xp_awarded`. (Already applied to the live DB; mirrored in `schema.sql`.)
- `CATNIP_TIMER_EXTEND` and other tuning constants now read from `config/tuning.json` at module load; previously some were hardcoded literals.
  > _draft_
- `TriggerEngine` (from `ach_engine.py`) imported and constructed at module load; achievement trigger dispatch is now data-driven for aches with a `trigger` block in `config/aches.json`.
  > _draft_

## Conventions

- **One bullet = one user-perceivable change.** Internal refactors that don't change behavior go under "Internal" and are optional.
- **Lead with the noun, not the verb.** "Third quest slot added" reads worse than "Third quest slot — adds…". Use "Added/Changed/Fixed/Removed" headers and a bulleted list.
- **Cross-link to design docs** when the entry is a balance change; cross-link to `docs/design/economy.md#xp--battlepass-currency` etc.
- **Numbers are config, not changelog.** "XP bonus per level changed from 50 to 100" belongs here; "level 7 reward is now 3 Rare cats" does not — that's just a config tune.
- **Squash trivia.** A series of "fix typo" / "tweak wording" commits should collapse into one line.
