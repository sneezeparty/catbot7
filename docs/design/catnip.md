# Catnip

Catnip is Cat Bot's late-game endgame loop, themed as a "cat mafia". It's the primary cat sink for high-volume players and the source of the most powerful perks in the game.

## The shape

A catnip "session" is a temporary buff window:

- The user runs `/catnip` once they've unlocked it: past 50 total catches, a catch has a 1-in-11 chance of showing a "You see a shadow..." button, and pressing it sets `profile.dark_market_active`. Servers can switch the whole system off with the `do_catnip` setting.
- Each session has a **level** (1–10) and a finite **duration** (`catnip_active` is the unix expiry).
- During a session, catches roll for perk-driven effects (doubled catches, tripled catches, pack drops, etc.).
- Levels increase by completing **bounties** and **paying a price** (cats of a specific rarity).
- Failing the bounty window before the session expires drops the user one level and revokes their last perk.
- Level 10 is repeatable but caps out — extra runs just add 24h to `catnip_active`.

**Design intent:** catnip is the **active-engagement reward**, paced on a **daily cadence**. All current levels have `"duration": 24` in `config/catnip.json`, so each session covers a full 24-hour window aligned with the bot's daily rhythm (daily catch streak, UTC+4 day boundary). The cadence is "log in once a day, activate, work the bounty list, come back tomorrow." AFKing across the window is fine — the pressure comes from the **bounties**, not the duration ceiling: fail to complete them before expiry and the level + last perk are stripped. The 24h window deliberately gives a player a full day's catching to clear their bounties, so a busy schedule doesn't punish them; the "feed the slot machine" tension lives in the level-up loop, not in a hourglass.

## Level structure

Defined in `config/catnip.json` under `levels[<n>]`. There are twelve entries, 0 (Newbie) through 11. Entry 11 ("Most Wanted") is not a reachable rank: a level's bounties are generated from the *next* entry (`get_bounties` reads `levels[level + 1]`), so level 10 needs something above it. Each entry has:
- `name`, `change`: the rank name and the one-line "what this level adds" blurb shown on the panel
- `duration`: session length in hours, applied when the player begins bounties
- `cost`: a value budget for the pay-up price, not a fixed cat. `set_mafia_offer` picks a random quest-eligible rarity whose per-cat value fits inside the budget and asks for as many of it as add up to `cost`, so the same level can ask for a different cat type each time
- `bounty_amount`, `bounty_difficulty`, `max_amount`: how many bounties, roughly how many catches each should take, and the cap on a single bounty's target
- `bonus`: 0/1, whether the level carries a bonus bounty (see [Bounties](#bounties))
- `weights`: rarity weights for the random-3 perk picker
- `store_discount`: integer percent applied to `/catstore` buy prices for players at this rank. Negative values are a surcharge (Lv0 Newbie = -20%); positive values are a discount (Lv10 El Patrón = +30%). Sell prices scale with rank too: 50% of face value at Newbie, rising 5 points per level but capped 5 points under the buy price, so they top out around 65% at El Patrón. See the [Cat Store section of economy.md](economy.md#cat-store) for the full table and pricing formula.

Perks themselves are defined separately under `perks` with rarity tiers (Common, Uncommon, Rare, Epic, Legendary) and effect values.

## Perks

When a user pays up to level N (N ≥ 2), they pick **one of three random perks**, with rarities drawn from the new level's `weights`. The first pay-in (0 → 1) grants no perk, so perks stack to `catnip_level − 1`: 9 at level 10, all active simultaneously.

Perks only fire while the session is live (`catnip_active > now`), and they are suspended during a Cat Police Pinch (`perks_suspended_until`, see [jobs.md](jobs.md#pinch-immunity-the-asymmetry-with-catnip)).

**Perk-count invariant.** `len(profile.perks) + 1 == catnip_level` is reconciled every time `/catnip` opens, one step per open: a player with too few perks gets a **Select Perk** button (`perk_selected = False`), and a player with too many loses the last one. This is how anything that moves `catnip_level` without touching `perks` gets squared up. A respect-driven level loss (see [jobs.md](jobs.md#respect--the-decay-meter)) strips its perk here, and a level raised directly in the database is owed its picks here. The catch path doesn't check the level, so a surplus perk keeps firing until that next `/catnip`.

Effects include:
- Double/triple catches with some probability
- Pack drops on catch (Wooden through Platinum). These are budgeted by `profile.pack_attempts`: set to the session length in minutes when bounties begin, spent one per catch, and pack rolls stop at zero.
- Streak-scaled bonuses — **Loyalty Streak (`loyalty_streak`):** adds time to the session when bounties begin, scaled by `user.daily_catch_streak` (UTC-day granularity streak, distinct from `profile.catch_streak` which is the per-catch counter). One minute per streak day for the first 100 days, then half that for the next 100, a third for the next, and so on.
- Rain triggers on catch
- **Snowballer (`combo`):** each consecutive catch within 5 minutes adds 1 to `profile.combo_stack` (cap 30); per-stack % feeds into the double-catch pool. Values [0.5, 0.75, 1.25, 2.0, 3.0]% per stack. Weight 8, non-exclusive.
- **Battlepass Booster (`bp_xp`):** per-catch % chance of +5 battlepass XP via `grant_achievement_xp`. Level-up embeds are sent inline if the XP pushes a level boundary. Values [5, 8, 12, 20, 30]%. Weight 10, non-exclusive.
- **Bait & Switch (`respawn`):** per-catch % chance of immediately spawning an additional cat in the same channel via `spawn_cat`. Guarded against rain channels (`channel.cat_rains == 0`). Values [1, 1.5, 2.5, 5, 8]%. Weight 5, non-exclusive.
- **Gift Catcher (`bonus_catcher`):** increases the rarity-scaled [bonus-cat 🎁](economy.md#bonus-cats) roll chance by the perk %, capped at a 2× total multiplier so a stacked/amplified perk can't make bonus cats routine. Values [10, 15, 25, 50, 75]%. Weight 5, non-exclusive. Its addition was paid for by trimming Lucky Catcher's weight from 25 to 22.5 (mirroring upstream's rebalance intent — shave the dominant generic perk to fund the new one).

**Time Manipulator (`timer_add`) was removed entirely** in 0.6.7. It had long been inert (weight 0, all values zeroed), then was deleted from the `perks` array. Because stored perks reference entries by 1-indexed array position, deleting the entry shifts every later perk down by one — so removal was paired with `migrations/020_remove_timer_add.py`, which decrements every stored index ≥ 12 by one across `profile.perks`/`perk1`/`perk2`/`perk3`. **The general rule still holds: never insert, remove, or reorder a perk without a matching remap migration** (only appending to the end is index-safe).

**Design intent:** the perk grid is intentionally not balanced for "best build" — every perk is useful, but the random-3 picker means users can't optimize. The cap is the picker, not the perks themselves. The fork-added perks each target a different engagement axis: Snowballer sustained-session play, Battlepass Booster cross-system progression spillover, Bait & Switch spawn density, and Gift Catcher (July 2026) the bonus-cat minigame loop.

## Bounties

Each level carries 1–3 regular bounties and, from level 7, one **bonus bounty**. State lives in four slots on `profile` (`bounty_{id,type,total,progress}_{one,two,three,bonus}`), with `profile.bounties` holding the regular count. There are three shapes: catch N cats, catch N of one type, and catch N of a rarity or rarer. The user advances a bounty by catching matching cats once they've begun the level (see [Hibernation](#hibernation)).

- **Regular bounties gate the level-up.** Pay Up! stays disabled until all of them are complete.
- **The bonus bounty is optional.** Completing it grants one perk **reroll**: pick any perk slot and replace it with one of three fresh options. An unused reroll is forfeited on level-up (the panel warns first). `profile.reroll` is the *spent* flag, so `False` means a reroll is available.
- Targets are drawn only from quest-eligible rarities, as is the pay-up price, so a rarity that debuted this season is never demanded. "Or rarer" bounties still count it when caught.
- Count and difficulty come from the *next* level's config entry (see [Level structure](#level-structure)).

**Design intent:** bounties are time pressure. The "complete bounties before the session expires" loop is what keeps catnip from being passive.

## Catnip XP

Each level-up grants **+100 XP** to the battlepass, capped at **1000 XP per season** (tracked via `profile.catnip_xp_awarded`). That matches the 10-level ceiling exactly — a player who maxes catnip from 0 → 10 in one season gets the full 1000.

**Design intent:** the cap exists so that grinding catnip up-and-down for XP isn't more efficient than questing. A repeated 10 → 9 → 10 → 9 loop would let a single user drain unlimited XP without it; with the cap, catnip XP is a *first-time-this-season* reward.

## Hibernation

`profile.hibernation` is the pause between levels. It is set by a paid level-up and by a bounty-deadline level-down (a respect-driven level loss doesn't touch it), and it is cleared only when the player presses **Begin Bounties**. While it is set:

- the new level's bounties and pay-up price are hidden, and catches don't advance bounties;
- the bounty deadline isn't running, so the level can't be lost to it;
- perks keep firing only for whatever is left on the previous `catnip_active` (nothing after a level-down, which zeroes it).

Begin Bounties clears the flag and **overwrites** `catnip_active` with a fresh full `duration` (plus any Loyalty Streak bonus) rather than adding to it. That is why the panel asks for confirmation when the player has banked more time than a fresh run would give.

Perk choice is tracked separately by `profile.perk_selected`. It goes `False` on a level-up to 2 or higher and blocks the *next* pay-up until a perk is picked. It does not block Begin Bounties.

> **TODO(design):** this section used to say hibernation "pauses catnip's effects until they pick their perk" to prevent "weird interactions where a level-up happens mid-catch". The code doesn't work that way (perks keep firing, and the gate is Begin Bounties, not the perk pick), so that rationale was dropped in the October 2026 audit. The intent behind letting players choose when the clock starts still needs writing down.

## Decay

If a session expires with bounties incomplete: level drops by 1, last perk removed, `catnip_active = 0`, and fresh bounties and a fresh price are rolled for the lower level, which starts in [hibernation](#hibernation). The drop is applied lazily, on the player's next `/catnip`. Because the lower level starts hibernating, an abandoned session costs exactly one level however long the player is away; ongoing decay during a long absence comes from the separate [Respect](jobs.md#respect--the-decay-meter) meter instead. The user keeps their position in `bp_history`, just loses a level.

**Design intent:** failure must be visible and felt. Without the level-drop, catnip would be free progress — players would just camp at level 1 and ignore bounties.

### Job-grace shield

Doing `/jobs` (any outcome: success, near-miss, or total failure) stamps `profile.last_job_time`. While `now − last_job_time < CATNIP_JOB_GRACE_SECONDS` (tunable via `config/tuning.json → catnip_job_grace_hours`, default 24h), two decay paths are suppressed:

1. **Bounty-deadline level-down** (`/catnip`): if the session has expired with incomplete bounties but grace is active, the level-drop is skipped. A notice is shown instead ("job is protecting your mafia level — safe until ..."), and `catnip_active`/bounties are left untouched. Once the grace window passes without a new `/jobs` commit, the next `/catnip` invocation drops the level as usual.

2. **Respect-driven level strip** (`_respect_settle`): when respect reaches 0 and the `hours_at_zero_per_level_loss` threshold is met, the level-strip cycle is skipped while grace is active. The respect **meter** still decays and refills normally — only the level-loss step is suppressed. The Lv4 floor and the level-loss-grace-respect reset are unchanged.

The two decay systems are made congruent: a player who commits at least one `/jobs` per day is provably safe under both. The "engagement, not winning" framing is deliberate — the protection comes from the act of playing jobs, not from succeeding at them.

**What the shield does NOT protect:** leveling up still requires bounties + pay; catnip perks still require an active session (`catnip_active > now`); grace protects the level, not perk uptime. The respect meter decays normally throughout.

The stamping is migration-guarded (`profile.last_job_time` added in migration 027). Before that migration is applied, `_job_grace_active` returns `False` and both decay paths behave exactly as before.

## Season reset

Catnip is active-economy state, so it is wiped at every monthly season rollover: level back to 0, and perks, bounties, price and session timer cleared. Lifetime stats (`highest_catnip_level`, `bounties_complete`, `catnip_bought`, `catnip_activations`) survive, as do the unlock (`dark_market_active`) and the cutscene flag. See [battlepass.md → Seasons](battlepass.md#seasons) for the full wipe list and the reasoning.

## Cutscenes

There are two scripted cutscenes: `mafia_cutscene` the first time a player reaches level 8, and `mafia_cutscene2` the first time they pay up *at* level 10 (completing a level-10 run, not arriving at 10). Each advances `profile.cutscene` (1, then 2). They unlock achievements (`thanksforplaying`, `mafia_win`) and lore.

**Design intent:** cutscenes are *one-shot* rewards for crossing a threshold. They're not meant to gate gameplay — if a user skips them somehow, nothing breaks.

## Quest interaction

The `catnip_session` extra-slot battlepass quest fires on successful `/catnip` activation (a paid level-up). It's gated to users with `catnip_level > 0` so freshly-unlocked players who haven't paid yet don't get assigned an unwinnable quest.

> **TODO(design):** consider adding a separate "complete a bounty" quest. Currently bounty completion isn't directly XP-rewarded outside the level-up grant.
