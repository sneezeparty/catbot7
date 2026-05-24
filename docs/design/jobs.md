# Jobs / Mafia Killings — design

The third pillar of late-game engagement, alongside [`/catstore`](economy.md#cat-store) (targeted coin spend) and the [`catnip`](catnip.md) perk loop. Jobs is the **agency** layer: the player picks a contract, picks a wager, and watches the dice.

> If `/catstore` is patient coin-grinding and packs are gacha, **jobs is the place where the player declares an intent and risks something specific to act on it.** The thrill lives in the send composition, not the wait — there isn't one, the roll is immediate.

This doc covers design intent. For runtime values (event weights, difficulty ranges, reward recipes, voice lines), the source of truth is [`config/jobs.json`](../../config/jobs.json). Help text the player actually reads is [`config/jobs_help.json`](../../config/jobs_help.json).

## Cadence: 12h windows

A single 12h cadence drives **both** the offer-pool refresh and the per-window commit cap. Windows are anchored at 00:00 and 12:00 UTC (43,200 evenly divides 86,400). One timer on the board, one moment when everything resets: new offers AND a fresh count toward the cap.

This matches the `quest_cooldown_seconds` (12h) used by `/battlepass`, so the two systems beat at the same pace from a player's perspective. The cap is **3 commits per 12h window** (configurable via `config/jobs.json → max_commits_per_window`), which works out to up to 6 commits per UTC day — slightly more generous than the prior 3/UTC day cap, in exchange for collapsing the two confusing timers (offer-refresh and daily-reset) into one.

History: before the 12h alignment, /jobs used a 6h offer refresh window AND a separate 24h commit cap anchored at UTC midnight. The board showed both timers and they drifted apart, so a player who'd played late in their local night (early UTC morning) would see "Refreshes in 5h" alongside "Daily limit hit, resets in 11h" on their next morning visit — two anchors, two countdowns, neither matching the player's wall clock.

## The two-die structure

Every commit rolls **two independent dice**:

1. **Success die.** Sigmoid of `crew_SP / difficulty − 1`, clamped 5–95%. Three outcomes: success, near-miss (10pp band above the threshold), total failure. See [`config/jobs.json → probability`](../../config/jobs.json).

2. **Complication die.** Independent roll. Base chance per tier × heat band multiplier × `(1 − rep insurance)`. If it hits, picks weighted from the per-tier event pool. See [`complications` and `complication_pools`](../../config/jobs.json).

### Why two dice

A single sigmoid clamped at 95% has a hard mathematical wall: at SP saturation `r < chance + band` requires `r ≥ 1.05`, which never happens with `r ∈ [0, 1)`. So **total failure becomes literally impossible** at high SP. Combined with the 3-commits-per-window cap (2 windows per UTC day, so ~6/day) and diminishing returns on mono-rarity stacking, a saturated player gets a "near-miss every ~3.4 days" annuity, not a gamble.

The complication die is the brake. A maxed-SP crew can still get **downgraded by `rival_crew`** (forced near-miss if effective SP doesn't clear a second 40%-of-difficulty wall), or **re-rolled by `boss_arrives`** (difficulty ×1.4, success die rolls fresh). High SP doesn't immunize you from a second source of variance.

### Why those specific events

Three buckets:

- **Teeth.** Outcome-modifying events that close the 95% loophole and add fear. `cat_police_raid`, `rival_crew`, `double_cross`, `boss_arrives`, `informant`.
- **Sweeteners.** Bonus events so the system reads as "anything can happen" rather than "things go wrong." `easy_mark`, `found_a_stash`, `sloppy_target`. T1's pool is **only** sweeteners — newbies always feel rewarded by complications.
- **Aftermath.** Costs paid on the next commit (`witness`, `loose_end`). Persistent state stored on `profile.jobs_pending_difficulty_mult` and `profile.jobs_pending_heat_bonus`, surfaced on the send screen *before* the next commit so the player can plan.

T5 (Big Score) deliberately has **no aftermath events** — the heist is once-per-season, so a +20% next-job effect would never trigger anything meaningful.

### Phases (order of operations)

`pre_roll` events fire **before** the success die rolls and can mutate the difficulty or short-circuit the outcome (`rival_crew` forces a near-miss if the SP wall fails). `post_roll` fires **after** and can downgrade the outcome (`informant`), modify the reward (`easy_mark`, `double_cross`, `found_a_stash`, `sloppy_target`), or add heat (`cat_police_raid`). `aftermath` writes to `profile.jobs_pending_*` columns; the **next** commit consumes them and resets to defaults.

Reward-modifying events are **gated to successful outcomes** — `easy_mark` doesn't fire on a wipe (would read as "rewards doubled!" next to "all cats destroyed", which is incoherent). On failures with no-meaningful-effect events, the complication is silently dropped — the result screen doesn't lie about what happened.

## Reputation's dual role

Rep is per-server, per-NPC, stored as `profile.faction_rep` (JSONB). Two distinct effects:

1. **Success bonus.** +0.075% per point with the offerer, cap ±12%. Whiskers at +100 = +7.5% on his jobs.
2. **Complication insurance.** -0.4% per point with the offerer, cap -40%. Whiskers at +100 turns a 25% T4 complication chance into 15%.

The insurance is **soft** — it does not zero out complications. A +100 Whiskers loyalist on a T4 still eats a complication on roughly 1 in 7 commits. That's the "not bullet-proof" design call: rep is a meaningful investment, not an escape hatch.

Rep with anyone other than the offerer does not help. The "I'm Whiskers's guy" identity is preserved by making rep insurance non-fungible across NPCs.

## Recipe philosophy: NPCs as mechanical archetypes

Pre-Phase-2 NPC differentiation came from a 5-enum `reward_bias` knob (`standard / coin_heavy / cat_heavy / coins_only / mid_rare_cats`) applied as a bulk transform. That made each NPC a knob position rather than a character — and the math drifted from the personality. Sofia "cat-heavy" was structurally similar to Lucian Sr "mid-rare cats" because both were just transforms on the same base table.

Phase 2 replaces it with **explicit per-(NPC, tier) reward recipes**. Each recipe is a weighted list of entries; each entry can specify any combination of `coins range`, `cats dict`, and a `pack tier`. The recipe table for each NPC encodes who they are:

- **Whiskers** — standard, balanced. Long-tail outcomes including a low-weight Silver-pack jackpot at T4. He's the reliable workhorse with the occasional surprise.
- **Lucian Jr** — coin-leaning, occasional Stone pack at T2. Reward_mult 1.3 already biases him toward more-coins-than-baseline; the recipes keep that going.
- **Jinx** — coin-heavy, rare cat drops. Most of her T1-3 weights are coin-only with the occasional small cat. Her cheap heat made her the safe grinder; not making her also the best cat source preserves the rotation.
- **Jeremy** — coin laundering character. Coin-only in most weights, with one ~15% weight entry that's coins + Stone pack. The "coin guy with a wink."
- **Lucian Sr** — Superior/Legendary specialist. Smaller coin amounts paired with mid-rare cats. He's the rare-cat dealer.
- **Sofia** — cat dealer with occasional packs at T3, escalating pack quality at T4. One low-weight (5%) T4 jackpot entry pays a Mythic + Silver pack.

`reward_mult` continues to apply as a global scalar on coins and cat counts (pack tier is unaffected) — that's the "I pay more" personality knob layered on top of the recipe shape.

The recipe is picked **at offer-generation time** and persisted into `reward_snapshot`. The offer board can't bait-and-switch: what you see is what you get.

### Packs go to inventory

Pack rewards land in `profile.pack_{tier}` (the existing pack columns), not auto-opened. This composes with the existing pack ritual: the player saves packs for events or opens at their own pace via `/packs`. Two moments of dopamine (winning the job, opening the pack) instead of one.

Complications that modify rewards (`easy_mark`, `double_cross`, `found_a_stash`, `sloppy_target`) operate on `reward_snapshot` at commit time — the modification is what gets granted. `sloppy_target` specifically **replaces** the cat reward with a pack one tier above the recipe's default ([`config/jobs.json → complications.sloppy_target_default_pack_tier_by_tier`](../../config/jobs.json)), capped at Celestial.

## Cat dialogue

After every resolve, one cat from the crew gets the last word on the result screen. **Survivors** for success/near_miss; **casualties** for total failure (posthumous). The picker weights candidates by `count × (1 / spawn_weight)` — rarer rarities have lower spawn weights in `type_dict`, so the eGirl talks when she comes home alive even from a 100-Fine crew.

Each rarity has a one-note voice ([`config/jobs.json → cat_voices`](../../config/jobs.json)) with lines per outcome. Sus says ඞ things, Rickroll quotes song lyrics, Professor is academic, eGirl is gen-Z, 8bit is glitched ASCII, Brave is stoic, Mythic is grandiose, Trash is self-deprecating, Ultimate is laconic. The structure lets the operator add more lines without touching code.

A small `complication_quips` block lets specific events pull a themed line in preference to the generic one — a Sus cat on a `cat_police_raid` says "i told you that guy was a fed" instead of the standard Sus success line. Fallback to generic when no thematic match exists.

The voice is rendered as a single quote block at the bottom of the result screen. **One line, always.** This is texture, not a wall of text.

## Perks — third reward axis

Job perks ("mafia favors") are a third reward bucket on top of coins and cats/packs. Every successful job rolls an independent **perk die** — separate from the success die and the complication die. If it fires, the NPC slips the player a card from a personality-flavored pool.

### Drop math

Two-stage roll on success:

1. **Drop die** — per-tier chance `perks.drop_chance_by_tier`. Defaults: T1 8%, T2 15%, T3 25%, T4 35%, T5 100% (Big Score *always* drops a capstone perk).
2. **Pool pick** — weighted choice from `perks.drop_pools[npc][tier]`. Each entry is `{id, weight}`. Weights are relative; they don't have to sum to anything specific.

The two dice are independent of the success die and the complication die. **Drops are success-only** — near-miss and total-failure never drop a perk (Crew Insurance is the exception: it's a *consumption* mechanic that converts an outcome, not a drop mechanic).

A perk drop failure inside `_jobs_apply_outcome` is caught and swallowed — the job resolution always wins.

### Perk personalities

Each NPC's perk pool encodes who they are (same idea as `reward_recipes`, expressed in a different reward axis):

- **Whiskers** — reliability + pack-flavor. heat_shield, complication_insurance, pack_tier_upgrade, pack_floor, crew_insurance, eagle_eye. The disciplinarian who teaches the player to be careful.
- **Lucian Jr** — impulsive, pack-heavy, mischievous. free_pack, pack_bonus_cat, pack_drop_boost, pack_tier_upgrade, daily_cap_extension, lightning_hands, reroll_board. The "dad doesn't know I'm doing this" guy.
- **Jinx** — chill, catnip-side. cooling_off, catnip_extension, free_catnip, streak_protector, combo_shield. Low-heat low-stress, pairs naturally with the cat side of the game.
- **Jeremy** — coins everywhere. roulette_luck, roulette_mercy, free_spin, catstore_sell_premium, cat_rain_coin_yield, stock_dividend_boost, bakery_discount. The money guy with a wink.
- **Lucian Sr** — vendetta / rarity / consequence. send_power_boost, rep_windfall, rarity_bump, bounty_boost, bounty_refresh, quest_xp_boost. Old-school don who knows the rep economy.
- **Sofia** — the dealer. catstore_discount_stack, pack_bonus_cat, discovery_shortcut, double_cat, perk_amplifier, catch_xp_boost. Catalog + cat-flavor perks.
- **Big Score (Whiskers, T5)** — capstone-flavored, all rare. heat_reset, crew_insurance, pack_tier_upgrade (Celestial cap), double_cat (24h variant). One-shot per season, so the pool is brutal.

A perk can appear in multiple NPCs' pools at different weights (e.g. `pack_tier_upgrade` is in both Whiskers and Lucian Jr — but it's Whiskers's main pack tool and only one entry in Lucian Jr's pack-heavy stable).

### Tier scaling

T2 is the baseline. T3 is ~1.5–2× baseline. T4 is ~3× baseline. T5 (Big Score variants) is a one-shot capstone — `double_cat` at T5 is 24h, `pack_tier_upgrade` at T5 uncaps to Celestial.

Missing tier entries fall back to T2 automatically (see `_perks_tier_entry`), so adding a single T2 entry to a new perk is enough to make it grantable at any tier.

### Stacking + lifetime

- **Refresh-or-extend, never stack.** Granting the same perk again resets its timer / refills its charges. Two consecutive Double Cat drops give you one fresh 2h window, not 4h.
- **Five-perk cap.** A 6th distinct grant evicts the oldest *timed* perk. Charge-based perks are sticky — they never get evicted to make room, because that would steal a one-shot the player hasn't used yet.
- **Lifetime tracking.** `profile.perks_received` records every distinct perk ID the player has ever gotten. Backs the `perk_collector` hidden ach (own them all) and the Mafia Favors leaderboard.

### Pinch immunity (the asymmetry with catnip)

The Cat Police Pinch (`perks_suspended_until`) suspends **catnip** perks. It does **not** suspend job perks. Mafia perks were earned in the field — they're not a side benefit of an active session that gets paused, they're a card in your pocket. The asymmetry is intentional and gives the two perk systems distinct identity.

`_jobs_perks_suspended` only checks catnip; `_perks_*` helpers never check it. This is doc'd in CLAUDE.md as well so it doesn't accidentally get "consistency-fixed."

### Whiskers's Favor coexistence

Phase 2 Whiskers's Favor (Whiskers ≥+100 rep → next pack-open upgrades one tier, season-gated, uncapped to Diamond) is **kept** as a separate mechanic. The two perk systems coexist:

- **`pack_tier_upgrade`** (job perk) is the everyday version — charge-based, drops from Whiskers + Lucian Jr at T2/T3/T4, caps at Silver (T2), Gold (T3), Platinum (T4), or Celestial (T5 only).
- **Whiskers's Favor** (rep reward) is the rep capstone version — once-per-season, no per-pack-tier cap (any → Diamond), unlocked by sustained rep work rather than a single job drop.

Favor is bigger, season-gated. `pack_tier_upgrade` is smaller, capped, drops constantly. Both can be active at the same time.

## Respect — the decay meter

Pre-rebalance, once a player maxed `catnip_level` they could coast indefinitely. Heat decayed on its own, the store discount was permanent, and Tier‑4 access never expired. **Respect** adds active pressure: you LOSE respect over time and GAIN it from completing jobs. Hit 0 and your catnip level starts dropping — losing tier access AND the store discount that came with it.

### State

Two columns on `profile`:

- `respect` (int, 0..100, default 50) — current standing with the family
- `respect_last_tick` (bigint, unix seconds, default 0) — timestamp of the last decay settle. `0` means "never ticked"; the first interaction stamps it and skips decay so freshly-migrated profiles aren't punished retroactively.

### Rules

- **Passive decay** of `decay_per_hour` (default 1) each hour since `respect_last_tick`. Capped at 0.
- **Job completion grant** based on tier: `T1 +10, T2 +25, T3 +50, T4 +100, Big Score +200`. Capped at `max` (100).
- **At respect == 0**, accumulating `hours_at_zero_per_level_loss` (default 6) zero-hours costs **−1 catnip_level**. After the loss, respect resets to `level_loss_grace_respect` (default 25) so the player has a runway before the next loss.
- **Floor**: catnip_level cannot decay below `level_loss_floor` (default 4). Tier‑2 jobs always remain accessible as a recovery path, so a returning player from a long absence can always rebuild.
- **Store discount tracks current catnip_level.** When a level is lost to decay, the discount drops with it (Lv10's +30% reverts to Lv9's +25%, etc.). This is the same code path catnip already used — no extra wiring.

### Equilibrium math

At default tuning, 1 hour of inactivity = −1 respect, so:

| Catnip level / typical play | Hours-to-zero | Hours of zero before level loss | Total idle hours per level lost |
| --------------------------- | ------------- | ------------------------------- | ------------------------------- |
| Lv10, default respect (50)  | 50            | 6                               | 56 (then 31 per next level)     |
| Lv6, default respect (50)   | 50            | 6                               | 56 (then 31 per next level)     |

So a maxed Lv10 player going completely idle drops to the Lv4 floor in roughly `56 + 5 × 31 = 211 hours` (~8.8 days). After the first loss each subsequent one happens faster because respect resets to 25, not 50. A player who commits even one T2 (`+25`) per day from a 50-respect start sustains forever.

### Implementation pattern (lazy compute on read)

`_respect_settle(profile, now)` is called wherever respect is read or mutated:

- `/jobs` board open (after `_jobs_apply_heat_decay`, same pattern)
- `/jobs` result screen path (top of the success branch in `_jobs_apply_outcome`, so any level loss is reflected in the result UI)
- `/catnip` command entry (so /catnip-only players see decay and level losses too)

The helper iterates hour-by-hour from `last_tick` to `now`, applying decay and (if needed) level-loss cycles. **60-day per-call cap** keeps any single settle bounded; profiles dormant longer settle in chunks across subsequent interactions. No background task — purely on-demand. Returns the number of catnip levels lost on this call so the caller can surface a toast.

### UI

- **Status line** on the /jobs board: `🟢/🟡/🔴 Respect: N/100` alongside the existing `Heat` line. Bands at 67+ green, 26-66 yellow, ≤25 red.
- **Result screen** shows `🤝 Respect: +25 (now 72/100)` on a successful commit.
- **Level loss notice**: when settle drops a catnip level, the /jobs board (next open) and /catnip command surface a one-shot warning so the player isn't surprised.
- **Zero respect warning** in the board status block: when respect is 0 AND the player is above the floor, an explicit "catnip level will drop" banner.

### Tuning

Everything lives in `config/tuning.json → respect` and is hot-reloadable via `cat!restart`:

```json
"respect": {
  "max": 100,
  "default": 50,
  "decay_per_hour": 1,
  "job_reward": {"1": 10, "2": 25, "3": 50, "4": 100, "5": 200},
  "hours_at_zero_per_level_loss": 6,
  "level_loss_floor": 4,
  "level_loss_grace_respect": 25
}
```

### Design intent

The pre-rebalance coast — max catnip once, profit forever — broke the "active commitment" implicit in /jobs being a contract system. Respect fixes that with a mechanic that's visible (a clear meter, not silent rot), predictable (deterministic decay rate, easily-grokked thresholds), and recoverable (floor at Lv4, grace bump after each loss). No catch-up penalty for returning players; they just see decay running. Combined with the top-tier price increases and the prism coin tax, the loop becomes: do jobs to keep respect → keep respect to keep discount → keep discount to afford the new top-tier prices → top-tier purchases now actually feel like they cost something.

The asymmetry with Heat is intentional: Heat punishes *over-commitment* (too many high-risk jobs in a row), Respect punishes *under-commitment* (going idle). Together they sandwich the player into a healthy middle: keep playing, but don't burn yourself out chasing T4s every window.

## Open questions

> **TODO(design):** the recipe weights for Sofia T4 include a 5%-weight "Mythic + Silver pack" jackpot. If post-launch data shows it firing often enough to bend the late-game cat supply, drop it to weight 2-3 or move it to a separate `jackpot_pool` that requires +50 Sofia rep to unlock. Right now any Lv8 Sofia commit can hit it.

> **TODO(design):** `informant` currently only downgrades success → near_miss. If success was a near-miss already (or worse), the event silently no-ops. Worth confirming this reads correctly to players. Alternative: have `informant` always force a wipe regardless of the success die. That's harsher but more legible.

> **TODO(design):** complication insurance is offerer-only. Should rep with the *target* also help — e.g., +100 Whiskers reduces complications on his jobs AND -100 Wilson reduces complications on jobs against Wilson (you know his outfit's weaknesses)? Probably no — keeps things simple — but worth a second look once players accumulate enough rep to test.
