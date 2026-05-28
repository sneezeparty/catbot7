# Catnip

Catnip is Cat Bot's late-game endgame loop, themed as a "cat mafia". It's the primary cat sink for high-volume players and the source of the most powerful perks in the game.

## The shape

A catnip "session" is a temporary buff window:

- The user runs `/catnip` once they've unlocked it (catching enough cats flips `profile.dark_market_active`).
- Each session has a **level** (1–10) and a finite **duration** (`catnip_active` is the unix expiry).
- During a session, catches roll for perk-driven effects (doubled catches, tripled catches, timer extensions, etc.).
- Levels increase by completing **bounties** and **paying a price** (cats of a specific rarity).
- Failing the bounty window before the session expires drops the user one level and revokes their last perk.
- Level 10 is repeatable but caps out — extra runs just add 24h to `catnip_active`.

**Design intent:** catnip is the **active-engagement reward**. The buff window is short enough that a player can't just hoard catnip and afk through it. The level-up loop creates a "feed the slot machine" tension — keep catching to keep the buff alive.

> **STALE:** all catnip levels 1–10 now have `"duration": 24` in `config/catnip.json`, meaning each session lasts a full 24 hours. The "short enough to prevent AFKing" framing above no longer reflects the current config. The design intent around session length should be revisited and rewritten to match the 24h cadence.

## Level structure

Defined in `config/catnip.json` under `levels[<n>]`. Each level has:
- `bonus`: a special bounty (e.g., catch a specific cat type)
- `price`: cat type + amount required to advance
- `perks`: list of allowed perk indices for the random-3 picker
- `store_discount`: integer percent applied to `/catstore` buy prices for players at this rank. Negative values are a surcharge (Lv0 Newbie = -20%); positive values are a discount (Lv10 El Patrón = +30%). Sell prices are always face value regardless of rank. See the [Cat Store section of economy.md](economy.md#cat-store) for the full table and pricing formula.

Perks themselves are defined separately under `perks` with rarity tiers (Common, Uncommon, Rare, Epic, Legendary) and effect values.

## Perks

When a user pays up to level N, they pick **one of three random perks** (with rarities weighted by level). Perks stack across levels — by level 10 a user can have 10 perks active simultaneously.

Effects include:
- Double/triple catches with some probability
- Timer extensions on catches (extending the session)
- Pack drops on catch (Wooden through Platinum)
- Streak-scaled bonuses — **Loyalty Streak (`loyalty_streak`):** timer extension proportional to `user.daily_catch_streak` (UTC-day granularity streak, distinct from `profile.catch_streak` which is the per-catch counter)
- Rain triggers on catch
- **Snowballer (`combo`):** each consecutive catch within 5 minutes adds 1 to `profile.combo_stack` (cap 30); per-stack % feeds into the double-catch pool. Values [0.5, 0.75, 1.25, 2.0, 3.0]% per stack. Weight 8, non-exclusive.
- **Battlepass Booster (`bp_xp`):** per-catch % chance of +5 battlepass XP via `grant_achievement_xp`. Level-up embeds are sent inline if the XP pushes a level boundary. Values [5, 8, 12, 20, 30]%. Weight 10, non-exclusive.
- **Bait & Switch (`respawn`):** per-catch % chance of immediately spawning an additional cat in the same channel via `spawn_cat`. Guarded against rain channels (`channel.cat_rains == 0`). Values [1, 1.5, 2.5, 5, 8]%. Weight 5, non-exclusive.

**Time Manipulator (`timer_add`) was removed entirely** in 0.6.7. It had long been inert (weight 0, all values zeroed), then was deleted from the `perks` array. Because stored perks reference entries by 1-indexed array position, deleting the entry shifts every later perk down by one — so removal was paired with `migrations/020_remove_timer_add.py`, which decrements every stored index ≥ 12 by one across `profile.perks`/`perk1`/`perk2`/`perk3`. **The general rule still holds: never insert, remove, or reorder a perk without a matching remap migration** (only appending to the end is index-safe).

**Design intent:** the perk grid is intentionally not balanced for "best build" — every perk is useful, but the random-3 picker means users can't optimize. The cap is the picker, not the perks themselves. The three new perks (Snowballer, Battlepass Booster, Bait & Switch) each target a different engagement axis: sustained-session play, cross-system progression spillover, and spawn density respectively.

## Bounties

Each level has 0–3 bounties (`bounty_one`, `bounty_two`, `bounty_three`) with rarities and target counts. The user advances toward a bounty by catching matching cats during the session.

**Design intent:** bounties are time pressure. The "complete bounties before the session expires" loop is what keeps catnip from being passive.

## Catnip XP

Each level-up grants **+100 XP** to the battlepass, capped at **1000 XP per season** (tracked via `profile.catnip_xp_awarded`). That matches the 10-level ceiling exactly — a player who maxes catnip from 0 → 10 in one season gets the full 1000.

**Design intent:** the cap exists so that grinding catnip up-and-down for XP isn't more efficient than questing. A repeated 10 → 9 → 10 → 9 loop would let a single user drain unlimited XP without it; with the cap, catnip XP is a *first-time-this-season* reward.

## Hibernation

When the user advances a level, `profile.hibernation = True`. This pauses catnip's effects until they pick their perk. It prevents weird interactions where a level-up happens mid-catch.

## Decay

If a session expires with bounties incomplete: level drops by 1, last perk removed, `catnip_active = 0`. The user keeps their position in `bp_history`, just loses a level.

**Design intent:** failure must be visible and felt. Without the level-drop, catnip would be free progress — players would just camp at level 1 and ignore bounties.

## Cutscenes

There are two scripted cutscenes (`mafia_cutscene` at level 8 first-reach, `mafia_cutscene2` at level 10 first-reach). They unlock achievements (`thanksforplaying`, `mafia_win`) and lore.

**Design intent:** cutscenes are *one-shot* rewards for crossing a threshold. They're not meant to gate gameplay — if a user skips them somehow, nothing breaks.

## Quest interaction

The `catnip_session` extra-slot battlepass quest fires on successful `/catnip` activation (a paid level-up). It's gated to users with `catnip_level > 0` so freshly-unlocked players who haven't paid yet don't get assigned an unwinnable quest.

> **TODO(design):** consider adding a separate "complete a bounty" quest. Currently bounty completion isn't directly XP-rewarded outside the level-up grant.
