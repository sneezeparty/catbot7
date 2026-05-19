# Jobs / Mafia Killings — design

The third pillar of late-game engagement, alongside [`/catstore`](economy.md#cat-store) (targeted coin spend) and the [`catnip`](catnip.md) perk loop. Jobs is the **agency** layer: the player picks a contract, picks a wager, and watches the dice.

> If `/catstore` is patient coin-grinding and packs are gacha, **jobs is the place where the player declares an intent and risks something specific to act on it.** The thrill lives in the send composition, not the wait — there isn't one, the roll is immediate.

This doc covers design intent. For runtime values (event weights, difficulty ranges, reward recipes, voice lines), the source of truth is [`config/jobs.json`](../../config/jobs.json). Help text the player actually reads is [`config/jobs_help.json`](../../config/jobs_help.json).

## The two-die structure

Every commit rolls **two independent dice**:

1. **Success die.** Sigmoid of `crew_SP / difficulty − 1`, clamped 5–95%. Three outcomes: success, near-miss (10pp band above the threshold), total failure. See [`config/jobs.json → probability`](../../config/jobs.json).

2. **Complication die.** Independent roll. Base chance per tier × heat band multiplier × `(1 − rep insurance)`. If it hits, picks weighted from the per-tier event pool. See [`complications` and `complication_pools`](../../config/jobs.json).

### Why two dice

A single sigmoid clamped at 95% has a hard mathematical wall: at SP saturation `r < chance + band` requires `r ≥ 1.05`, which never happens with `r ∈ [0, 1)`. So **total failure becomes literally impossible** at high SP. Combined with the 3-commits-per-day cap and diminishing returns on mono-rarity stacking, a saturated player gets a "near-miss every ~6.7 days" annuity, not a gamble.

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

## Open questions

> **TODO(design):** the recipe weights for Sofia T4 include a 5%-weight "Mythic + Silver pack" jackpot. If post-launch data shows it firing often enough to bend the late-game cat supply, drop it to weight 2-3 or move it to a separate `jackpot_pool` that requires +50 Sofia rep to unlock. Right now any Lv8 Sofia commit can hit it.

> **TODO(design):** `informant` currently only downgrades success → near_miss. If success was a near-miss already (or worse), the event silently no-ops. Worth confirming this reads correctly to players. Alternative: have `informant` always force a wipe regardless of the success die. That's harsher but more legible.

> **TODO(design):** complication insurance is offerer-only. Should rep with the *target* also help — e.g., +100 Whiskers reduces complications on his jobs AND -100 Wilson reduces complications on jobs against Wilson (you know his outfit's weaknesses)? Probably no — keeps things simple — but worth a second look once players accumulate enough rep to test.
