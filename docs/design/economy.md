# Economy

Cat Bot's economy is built around **per-server cat inventories**, **packs as the gacha layer**, and **XP as the meta-progression currency**. Everything else (catnip, prisms, stocks, casino) is a side-loop that converts between these.

## Cat rarities

Cats are weighted by `type_dict` in `main.py`. The weight is *inverse rarity* — higher weight = more common.

- The current rarity ladder spans **Fine (most common, weight 1000)** to **eGirl (rarest, weight 2)**.
- "Value" of a cat type is `sum(type_dict.values()) // type_dict[type]` (integer division). This is what trade/gift/inventory valuations use.
- Catches are per-server, not per-user. A user who plays in 10 servers has 10 independent inventories — this is core to the social loop.

**Design intent:** the ladder is roughly logarithmic. Rarer-than-Mythic is "trophy tier" — Ultimates / eGirls should remain genuinely scarce. New rarities go at the tails by preference (Terminator, weight 5, is the model case); a mid-ladder addition is acceptable only where the local weight gap is wide enough to absorb it without compressing its neighbors — Shadow (221, slotted into the Baby-230 → Epic-200 gap) is the deliberate exception that set this policy.

### Season-gated rarities

`config/tuning.json → rarity_min_season` maps rarity → minimum season number; a rarity listed there doesn't spawn, roll from packs, or appear in catnip pricing until the current season reaches its minimum. Two helpers enforce the gate: `_spawn_eligible_type_dict()` at spawn time (also drops rarities whose spawn image is missing from disk), and `_season_eligible_cattypes()` for pack-open rolls and catnip level pricing. Currently `{"Shadow": 2, "Terminator": 2}` — both already live.

**Design intent:** new rarities are *content drops*, and the gate lets a rarity ship in code/config ahead of its season without leaking early. Gating by season (not date) keeps it aligned with the wipe cadence: a rarity always debuts at a season boundary, when everyone's economy restarts together.

### Bonus cats

After a normal catch (July 2026, ported from upstream's "june update"), a rarity-scaled roll can turn the catch into a **bonus cat 🎁**: `chance = bonus_cat_chance_coef × log2(sum(type_dict.values()) / type_weight − 0.7)` — roughly 3.7% at Fine up to ~22% at eGirl. The catcher gets a **30-second, one-attempt** solo minigame (one distinct puzzle per cattype — `play_minigame()` in `main.py`) worth **+3 cats of that type** on success. During a cat rain the payout is a flat +1 instead (rains move too fast for modal UI). `profile.bonus_catches` counts lifetime successes; the [Gift Catcher catnip perk](catnip.md#perks) raises the roll chance, capped at 2×. Setting `bonus_cat_chance_coef` to `0` in `config/tuning.json` disables the whole mechanic (note this makes the week-3 [weekly quest](battlepass.md#weekly-quest-track) uncompletable).

**Design intent:** the roll is rarity-scaled rather than flat so the payoff lands where it's felt — +3 Fine cats is noise, +3 eGirls is an event, and the scaling makes rare catches doubly exciting without inflating commons. It's a *skill* gate (puzzle, deadline, one attempt) rather than a second RNG roll, because the catch itself was already the luck. This fork runs the **solo variant**: upstream couples bonus cats to a late-catching mechanic that lets bystanders join the minigame; we deliberately dropped late catching, so only the catcher plays. Bonus grants are flat (+3, unboosted) and sit outside the prism/catnip multiplier stack — they never compound with doublers.

## Packs

Packs are the gacha layer. Each pack has:
- `value` — the expected cat-value of contents (rough)
- `upgrade` — the chance the pack rolls one tier above its declared rarity floor
- `totalvalue` — the *aggregate* value of all cats in the pack (used to size the contents)
- `special: True` — event packs (Christmas, Valentine, Chef, Birthday) that are time-gated

Pack tiers form their own ladder: Wooden → Stone → Bronze → Silver → Gold → Platinum → Diamond → Celestial. Celestial has `upgrade: 0` (the cap).

**Design intent:** packs exist to compress the long-tail catching grind. The expected value of a tier-N pack is calibrated so that a player who is many catches behind can "catch up" via packs without trivializing the grind for everyone else.

The old constraint here was "don't add packs that pay out in non-cat currency." The pack coin variant (below) deliberately superseded it: coins are now an accepted secondary payout **as long as total pack worth stays constant** — the split changes the *form* of the payout, never its size. The surviving rule: a pack's expected value is denominated in cat-value, and any non-cat payout must be an equal-value substitution inside that budget, not a bonus on top.

### Pack coin variant

Each pack open has a **50% chance** (tunable `pack_coin_variant_chance`) of becoming a **"coin crate"**: the pack's `goal_value` is split so the cat side rolls at `goal_value * (1 - coin_ratio)` and the remaining `totalvalue * coin_ratio` is paid out directly as coins. The coin ratio is **tier-scaled** via `_pack_coin_ratio(level_idx)`: linear interpolation from `PACK_COIN_RATIO_WOODEN` (0.5) at Wooden down to `PACK_COIN_RATIO_CELESTIAL` (0.2) at Celestial. **Special packs** (Christmas, Valentine, Chef, Birthday) always open as regular cat packs — `_pack_coin_ratio` returns 0 for them, coin variant is a no-op.

`get_pack_rewards` returns a 5-tuple `(chosen_type, cat_amount, upgrades, verbal, coin_amount)`. Both callers (`open_pack`, `process_pack_opening`) handle the coin side. Job perks (Crate Polish, No Fines, Padded Crate) apply to the cat side only; the coin side is independent. Cascade re-opens pass the `coin_variant` flag through; the consolation tier's coin amount is what gets credited. Coins credited via coin variants count toward the season-recap `coins_earned` counter.

### Sub-1 fail handling

When a pack's randomly-picked cat type is so rare that even one cat exceeds the pack's `value` budget (e.g., a Wooden pack rolling eGirl), the open enters a sub-1 lottery: `P(success) = pack_value / per_cat_value`. On lottery success, you get one cat of that type. On lottery fail, the consolation is **tier-dependent**:

- **Wooden** — re-roll the cat type once and run the lottery again. If the re-roll *also* fails, the consolation is **3 Fine cats**.
- **Stone+** — cascade: a pack one tier lower opens automatically as the consolation. That pack runs its own normal upgrade chain and cat pick. If the cascade *also* sub-1 fails, the consolation is **3 Fine cats** (no double-cascade).

**Design intent:** the old "1 Fine cat" consolation made high-tier opens feel awful (~2% of Diamond opens) and low-tier opens demoralizing (~28% of Wooden opens). The cascade preserves the lottery's "what if" thrill while making the failure mode feel like a second chance instead of a slap. The 3-Fine-cats floor exists so that even back-to-back fails still leave you with *something more than nothing*. Cascade depth is bounded at 1 — no infinite chains. See [pack opening flow](../../main.py) (`get_pack_rewards`) for the implementation.

> **TODO(design):** the recent re-tuning increased pack values ~50% (e.g., Wooden 65 → 98). The current values reflect the self-hosted instance's smaller player base. If/when the public bot adopts these, re-tune downward.

## XP & battlepass currency

XP funnels into [battlepass](battlepass.md) levels. There are three XP sources at the time of writing:

1. **Quest XP** — completing per-cycle quests (catch / misc / extra slots). The dominant source.
2. **Passive XP drips** — first catch of day (+50), 10-catch streak (+20), catnip level-up (+100 capped at 1000/season), prism boost owner (+20).
3. **Achievement XP** — each ach with an `xp` field grants it on unlock; routed through `grant_achievement_xp` in `main.py`.

Daily XP for an active player is on the order of **600–1500 XP**. Season 1 level requirements range 550–1000 XP, so one engaged session ≈ one battlepass level. Seasons 2+ requirements range 1150–6300 XP (see [battlepass.md XP cost curves](battlepass.md#level-rewards)), so the later levels require multiple sessions each.

**Design intent:** XP should feel earned, not gifted. Passive drips were added when the vote slot was inactive on this self-hosted instance; they fill the gap for players on instances where `voting_enabled=0` (now an explicit opt-out; the default is `"1"`). Passive drips and vote XP coexist. Keep the dominant share with quests; passive drips are sweeteners, and vote XP is a bonus that is active on all standard-config instances.

> **TODO(design):** there's no XP source for *opening* a pack yet. This was on the candidate list (idea #11) and was deliberately deferred — revisit if the pack drop rate from catches makes packs feel like a chore rather than a reward.

## Currency: coins and rain minutes

There are now two distinct currency pools:

- **Coins** are the single shared wallet for `/roulette`, `/catslots`, `/stocks`, `/packs`, and `/catstore`. New profiles start at **0 coins** (no default grant). At each **season rollover**, coins are reset to `SEASON_STARTING_COINS` (default **100**, tunable via `config.tuning["season_starting_coins"]`) rather than to 0 — a small starting allowance so players can make at least one low-stakes move on day 1 of the new season. The `failed_gambler` achievement still fires when coins go negative — only the underlying column changed, not the game mechanic. A player in debt can still bet up to 100 coins (`max(coins, 100)`) but cannot buy from `/stocks` or `/catstore` until they grind back to positive.
- **Rain minutes** are channel-affecting (`/rain` triggers a multi-cat spawn event). They're gift-able and accumulate from battlepass + supporters.

**Design intent:** the coins-vs-rain-minutes segregation is preserved **by pricing, not by hard prohibition.** With the addition of `/catstore` → Extras → Rain (see [Rain in /catstore](#rain-in-catstore) below), a coin-rich player *can* convert coins to rain blocks — but the per-block scaling tax is steep enough that casual conversion never pays off, and the casino doesn't dominate the rain economy for any player who isn't sitting on a six-figure coin balance.

### Historical note: the cat-dollars / coins merge (migration 006)

The original upstream design (and this fork before migration 006) segregated two coin-like currencies:

- **Cat dollars** (`profile.roulette_balance`) — roulette-only, default 100, isolated recovery loop.
- **Coins** (`profile.coins`) — stocks / packs / catstore, no direct gambling use.

The stated intent was to prevent arbitrage: winnings from `/roulette` could not be spent in `/packs` or `/catstore`, and a bankruptcy at the roulette table would not drain the pack economy.

**Migration 006 merged these.** Reasons specific to this self-hosted fork:

1. A small self-hosted instance (~20 profiles) has far fewer arbitrage concerns than the 200k-server public bot the segregation was designed for.
2. Players preferred a unified wallet — tracking two separate "money" numbers was confusing with no visible benefit.
3. The `roulette_balance` default of 100 meant every new profile had a free 100-coin head-start for gambling that didn't apply elsewhere; removing it levels the starting field.

**The trade-off is intentional and accepted:** gambling losses now reduce a player's stock/store buying power, and roulette winnings can be spent anywhere. This is the direct consequence of merging, not an oversight.

Existing `roulette_balance` values were summed into `coins` (not replaced), so no player lost earned currency. Negative balances (gambling debt) were also preserved additively. The `roulette_balance` column was then dropped from `profile`.

### Coins leaderboard

`/leaderboards type:Coins` (emoji 🪙) replaced the old "Roulette Dollars" leaderboard category. It ranks all profiles with a non-zero coins balance, ordered descending. The special-case that includes debtors (non-positive balances still appear; only the exact-zero score is suppressed) is preserved from the original "Roulette Dollars" implementation — gambling debt is real information and is worth ranking.

### /catslots

A second slot machine alongside `/slots`, but Vegas-style: 5 columns × 3 rows, 8 cat-rarity symbols (Fine → eGirl) drawn from weighted reels, 20 selectable paylines, and a multi-line bet structure. The player picks **lines** (1, 5, 9, or 20) and **coins per line**; total bet = lines × per_line. Each active payline pays a multiplier on `coins_per_line` when its first N symbols match consecutively (3-, 4-, or 5-of-a-kind) — multiple winning lines add up.

`/catslots` shares the **`coins`** wallet with `/roulette` and `/stocks`/`/packs`/`/catstore`. The same debt rule applies: a player at zero or negative coins can still place a bet up to 100 coins (`max(coins, 100)`). **There is no "remove debt" button on `/catslots`** — that mechanic stays on `/slots`. The expected path out of debt is `/jobs` (the mafia contract system), not free undos at the casino.

**Target total RTP ~97%** (base ~73%, bonus contribution ~25pp, floor bumps the bonus contribution a few pp). Right in the Vegas penny-slot sweet spot. Verified by 500k-spin Monte Carlo. The payout table after the **variety retune (2026-05-22)** is shaped so that:
- Fine reel weight dropped 55→38, mid-tier weights bumped (8bit 8→14, Corrupt 7→11, Professor 6→9, Divine 5→8, Real 4→5). eGirl/Ultimate kept at 3 so bonus trigger rate is unchanged.
- Fine 3-of-a-kind (still the most common hit) returns 1× per line. Fine 4OAK 4× and 5OAK 11×.
- Mid-tier payouts scaled up ~50-60% to compensate for the reduced Fine-share of RTP: 8bit 5OAK now 450×, Corrupt 650×, Professor 1,150×, Divine 1,950×, Real 4,000×, Ultimate 8,000×, eGirl 4,000×.
- ~18% of winning spins now have a non-Fine win (up from 3% pre-variety-retune).

**Base-game win rate** is now ~54% per spin (down from ~81% pre-retune). The lower frequency is the price of the variety — fewer Fine cells means fewer wins overall. The bonus floor still guarantees the bonus rounds always feel like wins.

A spin is flagged a **big win** when `total_payout >= 100 × total_bet`. This is a high but not lottery-only threshold: a 5-of-a-kind on most symbols at most line counts will clear it. Big wins fire the `big_win_catslots` achievement and increment `profile.catslots_big_wins`.

**Per-line bet cap: `CATSLOTS_MAX_PER_LINE = 100` coins.** Total bet is therefore implicitly capped at `max(lines) × max_per_line = 20 × 100 = 2,000 coins` per spin. The worst-case base-game 5-of-a-kind line is Ultimate 8,000× × 100 per_line = **800,000 coins** for a single line. The cap is enforced in the modal's `on_submit`.

Lifetime stats live in five `profile.catslots_*` columns (`spins`, `wins`, `big_wins`, `coins_bet`, `coins_won`). `catslots_coins_bet`/`coins_won` use `bigint` since aggregate lifetime turnover can exceed int32 quickly at high stakes. Concurrency is gated by a separate `catslots_lock` list (mirroring `slots_lock`); the rigged-user override forces a 5-of-a-kind eGirl on line 1 (middle row).

Casino quest progression: `/catslots` spins count toward the existing `casino` extra-slot quest under the `slots` game bit. The dedicated `slots` / `slots2` battlepass quests remain scoped to `/slots` only.

#### eGirl Party bonus round

After every settled regular spin, the bot counts eGirl symbols visible on the 5×3 grid. **3 or more triggers a free-spin bonus round** that runs immediately, with sticky-wild mechanics and a flat multiplier on top of base payouts.

| Trigger | Free spins | Multiplier |
| ------- | ---------- | ---------- |
| 3 eGirls | 5 | ×1.25 |
| 4 eGirls | 7 | ×1.5 |
| 5 eGirls | 10 | ×2 |

**Trigger frequency.** The eGirl reel weight is 3 (out of 91), so ~3.3% per cell. With 15 visible cells per spin the **3+ trigger lands ~1 in 83 spins**. Per-tier shares of triggers: ~90.1% are 3-eGirl, ~9.2% are 4-eGirl, ~0.7% are 5-eGirl.

**Sticky_mask is frozen at trigger time.** This was the primary fix from the first 2026-05-22 emergency retune. Newly-landed eGirls during a bonus spin do NOT lock for future spins. Without this rule, sticky cells compound across spins, the grid saturates with eGirls, and the bonus runs away.

**Sticky behavior.** The triggering eGirls start locked in place. Every bonus spin, those locked cells skip the reel animation and remain as eGirl. Stickies help a line only when they happen to be the leading consecutive run from column 0 — they're real cells, not wilds. As of the **third 2026-05-22 retune** the bonus eval uses straight-match (the same rule as the base game), not wild substitution. A line like `eGirl Real Real Fine X` no longer scores as 3× Real — eGirl is treated literally, so this line scores 1× eGirl 1OAK (i.e., nothing).

**Retrigger.** If a bonus spin lands 3 or more *newly-landed* eGirls (not counting the pre-sticky ones), the remaining-spin count gains +5. The multiplier does not change. No cap on retriggers, though in practice they're vanishingly rare (avg ~0.03 per bonus, ~3pp of bonus contribution to RTP).

**Bonus payout floor.** Every bonus is guaranteed to pay at least a tier-scaled multiple of the triggering spin's total bet, applied as a top-up after the natural spin payouts are summed: **tier 3 = 5× bet, tier 4 = 10× bet, tier 5 = 25× bet** (`CATSLOTS_BONUS_FLOORS`). Without the floor, ~half of tier-3 bonuses naturally pay below 1× bet at small stakes — the 13-second opening animation followed by a 24-coin payout on a 20-coin bet was a real player complaint that drove this fix. With the floor, the bonus *always* feels like a win: a tier-3 trigger on a 20-coin bet guarantees 100 coins, a tier-5 trigger guarantees 500 coins. The floor kicks in on ~74% of bonus triggers but rarely binds at max bet (median tier-3 bonus at 2,000-coin bet is ~4,125 coins, already well above the 10,000-coin floor for most natural payouts). RTP impact: bumps total RTP from ~94% to ~101% (verified by 300k-spin Monte Carlo), making the slot effectively break-even on average. This is the right trade for a closed-economy fun-game bot.

**Stats.** Three lifetime counters track the bonus round independently of the base game: `catslots_bonus_triggers`, `catslots_bonus_coins_won`, `catslots_bonus_spins_total`. The base-game `catslots_coins_won` does NOT include bonus payouts, so the existing `/leaderboards type:Catslots` ranking is stable. Two new achievements fire on first trigger (`egirl_party`, visible, 350 XP) and on a 5-eGirl trigger (`egirl_party_max`, hidden, 600 XP).

**RTP contribution.** After the third 2026-05-22 retune the bonus naturally contributes **~14 percentage points** to total RTP (~94% total). The 2026-05-22 floor (5×/10×/25×) lifts total RTP to **~101%** — the slot is effectively break-even, the floor mostly tops up disappointing tier-3 bonuses at small stakes. At max bet (2,000 coins/spin), a HUGE WIN ≥50,000 coins occurs ~1 in 700 spins, ≥100k ~1 in 1,400, ≥500k ~1 in 17,000. By tier: tier-3 (90% of bonuses) averages 15,135 coins at max bet, tier-5 averages 416,462. The dopamine sits in the trigger, the rare 5-eGirl tier, the long tail, and the guarantee that the bonus is *always* a win.

**History — the 190% RTP regression and the three-pass fix.** The original bonus design accumulated sticky eGirls across the round. Combined with the wild-substitution rule that lets eGirl substitute for any base symbol on a line, sticky-saturated grids paid 5OAK on most paylines via Ultimate substitution. A 5-eGirl trigger × 5× bonus multiplier × 18 spins × sticky saturation produced 10M+ coin payouts on small bets.

The **first emergency retune** (`0.3.0.100122052026`) was four-fold: freeze sticky_mask at trigger time (no in-bonus accumulation), shrink bonus spin counts to 5/7/10, drop multipliers to 2×/2×/3×, and slash base payouts across the board. Stated target ~86% RTP.

The **second retune** (`0.3.0.102222052026`) flattened top-tier 4OAK and 5OAK payouts because the first pass underestimated wild-substitution's effects. Stated target moved to ~78-81%. Monte Carlo verification afterward showed the actual was **~190% total RTP** — the wild-substitution rule plus frozen stickies still produced ~125pp of bonus RTP. The target was never hit.

The **third retune** (`0.5.1.<this>`) **removed wild substitution from the bonus eval entirely** (the bonus loop now uses the same straight-match rule as the base game) and rebalanced base payouts to land base RTP at ~80%. Multipliers were also cut to 1.25/1.5/2. With wild substitution gone, the multiplier knob now does what you'd intuitively expect, and total RTP settled at ~94% — Vegas penny-slot range. Verified by Monte Carlo before shipping.

**Admin override.** `/catslots_force_bonus egirls:<3|4|5> [user:<member>]` (manage-guild only) queues a single-use override that overwrites N random visible cells with eGirl on the next spin. The optional `user` parameter targets someone else's next spin — useful for set-up shots, giveaways, or testing. Defaults to the invoker if omitted. The entry is popped on read, so it always lasts exactly one spin.

## Catnip as the late-game money sink

Catnip is the late-game money sink: cats go in, perks come out. See [catnip.md](catnip.md). The relevant economic constraint is that catnip costs scale with level and rarity, so high-level users must keep catching to feed it. This is what keeps Ultimate / eGirl cats *consumed* rather than just hoarded.

## Stocks

A fake market with 5 tickers (PRSM, CTNP, PASS, ACHS, RAIN). Stocks are pure speculation — you buy in coin, sell back to coin. Stocks exist for engagement, not progression.

### Simulated market

The activity-driven market maker that preceded this (every 5 min, bot-owned bid/ask at a fair price derived from in-game metrics) is gone. It produced near-zero price movement on a small instance — the fair price barely budged tick to tick, so the displayed price drifted only when someone happened to trade. Players said it was boring; it was right.

The replacement is a **simulated market**: a geometric-Brownian-motion price model with sector correlation, scheduled and surprise events, market-wide crashes/booms, and a gentle mean-reversion anchor. The bot is no longer a counterparty with finite inventory; market trades fill instantly against "the house," which has unlimited virtual capacity. The bid/ask spread is the friction. Limit orders still rest in the order book, match user-to-user first, and are auto-filled against the house once the price ticks through them.

**Tick cadence.** One `_run_stock_tick` runs each `background_loop` cycle (~`MAIN_LOOP_INTERVAL` seconds, default 300, so ≈288 ticks/day). Each tick produces a new price per ticker and writes one `pricehistory` row per ticker.

**Per-tick log-return formula:**

```
log_return = drift
           + ticker_shock              # N(0, σ_ticker)
           + sector_beta · sector_shock # N(0, σ_sector), one draw per sector per tick
           + market_beta · market_shock # N(0, σ_market), one draw per tick
           + mean_reversion             # -λ · log(price / fair_value)
           + event_impulse              # sum of newsevent rows firing this tick

new_price = clamp(round(price · exp(log_return)), floor, ceiling)
```

**Sectors.** Three buckets, declared in `config/tuning.json["stock_market"]["tickers"][T]["sector"]`:

| Sector | Tickers |
| ------ | ------- |
| `catch_engine` | PRSM, RAIN |
| `progression` | PASS, ACHS |
| `consumable` | CTNP |

Sector shocks are drawn once per tick and applied to every member of the sector with that member's `sector_beta`. Market shocks apply to every ticker scaled by its `market_beta`. So "a good day for `catch_engine`" lifts PRSM and RAIN together; "a bad day for the market" dings everyone.

**Mean reversion is a long-run anchor, not the price.** `_compute_fair_price(ticker)` is retained — same activity-signal power-law it always was (`base · ((metric + eps) / (baseline + eps)) ^ alpha`, per-ticker `base`/`baseline`/`alpha` in tuning). It feeds the `-λ · log(price/fair)` term in the formula. With default `λ ≈ 0.005`, the noise dominates day-to-day and reversion only matters over weeks. A server with high catch activity still sees a higher fair value for PRSM/RAIN, which over time biases their prices upward; in the short run the noise + events are what the player feels.

**Events live in `newsevent`.** The new table (migration 030):

```
newsevent (
  id           serial pk,
  time         bigint,             -- when shown in the feed (announce time for earnings)
  fires_at     bigint,             -- when the impulse hits the price
  ticker       varchar(10) NULL,   -- NULL for market-wide
  event_type   text,               -- earnings | surprise | crash | boom | dividend | system
  headline     text,
  impulse_pct  real,               -- signed log-return delta applied at fires_at
  applied      bool
)
```

Four kinds (plus `system` for the migration-seed rows):

- **Earnings** — every ticker schedules its next earnings event every ~`earnings_interval_seconds` (default 3 days, ±25% jitter). The row is written at schedule time with `time = fires_at - earnings_announce_lead_seconds` (default 24h) so the news feed (which filters `time <= now`) doesn't surface it until the announce window opens. At `fires_at` the tick draws `impulse_pct ~ N(0, σ_earnings)` (default σ=0.08), updates the row to `applied=true`, and applies the impulse this tick. Magnitude is hidden in the headline until fire.
- **Surprise** — per-tick per-ticker roll at `surprise_chance_per_tick` (default 0.001 ≈ 1.4/day across all 5 tickers). Hit writes `applied=false` with `fires_at=now` and `impulse_pct ~ N(0, σ_surprise)`; consumed in the same tick.
- **Crash / Boom** — per-tick global rolls at `crash_chance_per_tick` / `boom_chance_per_tick` (defaults 0.00035 each ≈ once every ~8 days each). On hit, write a `ticker=NULL` market-wide row with `impulse_pct` drawn from `[crash_impulse_range]` / `[boom_impulse_range]` (defaults ±[0.08, 0.15]). Consumed by every ticker this tick.
- **Dividend** — when `wait_and_do_stock` pays out, it writes a dividend row with `impulse_pct = STOCK_DIVIDEND_EX_DIV_IMPULSE` (default -0.015). The next tick consumes it as a small ex-div price drop — real cashflow leaving the company. The Jeremy `stock_dividend_boost` job perk path is **unchanged**; it adds a per-holder bonus on top of the bulk payout, just like before.

The tick aggregates every unapplied row with `fires_at <= now` per ticker, applying market-wide rows to all tickers exactly once and marking them `applied=true` at the end. Earnings rows that just fired get a sign-aware headline updated in place.

**Trading model.**

- **Market Buy / Market Sell** — `execute_market_trade(profile, ticker, side, qty)`. Atomic: refetches the profile under a transaction (catpg's `get_or_create(connection=conn, …)` takes an `ON CONFLICT DO UPDATE` row-level lock that holds for the transaction's lifetime), validates coins/shares, debits/credits, writes a `b`/`s` PortfolioHistory row at the fill price. Buys fill at `get_stock_ask(ticker)` = `round(mid · (1 + spread/2))` with a 1-coin minimum gap; sells at `get_stock_bid(ticker)` = `round(mid · (1 - spread/2))` with the same minimum. House has infinite virtual capacity — no per-row inventory.
- **Limit Buy / Limit Sell** — `place_limit_order(profile, ticker, side, qty, price)`. Same escrow + Order row as before. The existing `resolve_orders` user-vs-user matcher runs first; whatever survives rests in the book.
- **House sweep** — `_sweep_crossed_limits(ticker)` runs at the end of each per-ticker tick. Resting buys with `price >= ask` fill at `min(order.price, ask)` (overpriced buys get the difference refunded). Resting sells with `price <= bid` fill at `max(order.price, bid)`. Limits still expire after 7 days via the existing `background_loop` sweep.

**Why this stays "not the primary coin source."** Bid/ask spread (default 2%) plus the dividend ex-div drop are the structural drags. Crashes/booms cancel each other on expectation. Drift is set tiny per ticker. Aggregate player P&L over many ticks is approximately the spread cost — same intent as the old MM design (stocks are engagement, not progression), just with movement that's actually interesting to watch.

**Tunables.** Everything is in `config/tuning.json["stock_market"]` and hot-reloads on `cat!restart`. Headline keys: `spread`, `price_floor`, `price_ceiling`, `mean_reversion_lambda`, `sigma_market`, `sigma_sector`, `earnings_*`, `sigma_earnings`, `surprise_chance_per_tick`, `sigma_surprise`, `crash_chance_per_tick`, `crash_impulse_range`, `boom_chance_per_tick`, `boom_impulse_range`, `dividend_ex_div_impulse`. Per-ticker: `base`, `baseline`, `alpha` (the fair-value anchor), `drift`, `sigma_ticker`, `sector`, `sector_beta`, `market_beta`.

The `mm_order_quantity` key remains in the file for back-compat but is unused by the v2 engine.

## Trades & gifts

- `/trade` is a two-party negotiation, used to move cats/packs between players.
- `/gift` is unilateral, with a 20% tax on cat gifts ≥ 5 cats. Gifting to the bot itself is a *sacrifice* (no recipient).

**Design intent:** the gift tax is the friction that prevents alt-account farming. If alt-farming becomes a problem, raise the tax, don't add account verification (this is Discord — verification is a UX disaster).

## Balance guardrails

When adding a new XP source, new pack tier, or new currency interaction, sanity-check against:

- **Daily XP ceiling:** even a degenerate player shouldn't break ~3000 XP/day. That's ~5 battlepass levels per day; the season-long curve assumes much less.
- **Pack inflation:** total in-circulation packs should grow sub-linearly with catches. If a feature gives N packs per catch (vs the current ~0.01-ish), it's overpowered.
- **Per-currency monopoly:** if a feature creates a new way to convert coins into rain minutes (or vice versa), the surviving segregation rule is breaking. Either widen the segregation or pick a different reward. Note: coins↔roulette_balance arbitrage is no longer a concern — those two pools were merged in migration 006.

### Bakery loop (clicker sinks)

`/bakery` is a weekly Bake.gg partner integration: players accumulate **cookies** (`/cookie`) and **coffees** (`/brew`) via idle clicking plus a few Nice cats, then deliver a bakery order for a Silver Pack and a Bake.gg Cat Egg (redeemable on Bake.gg for a Chef Pack back in Cat Bot, once per user per week).

**Design intent:** cookies and coffees are *deliberately worthless* clicker counters — the bakery order is the only sink, it's weekly-capped, and the pack payout is modest. The loop exists to give the joke commands a point and to cross-pollinate with the partner site, not to be an income source; the weekly cap is what keeps an unbounded clicker from ever mattering to the economy. (The `cookie`/`coffee` misc quests layer on the same counters — they pay quest XP for clicking, which is bounded by the misc slot's own cooldown.)

### Flavor commands (zero-economy loops)

`/fish` and `/chaos` are engagement content with **no economy footprint** — by design, neither grants cats, packs, coins, or XP directly (only their misc quests pay, through the normal quest budget):

- **`/fish`** — cast, wait 10–30s, then a 5-second "Pull!" window. Fish rarity rolls from the same `type_dict` weights as spawns, but a fish is just a trophy: `profile.fish_caught` / `profile.rarest_fish` feed two achievements (`fisherman`, `pro_fisher` at Legendary+) and the "Catch 5 /fish" misc quest. Reusing the cat rarity ladder makes fish rarity legible for free — players already know what a Legendary is worth emotionally.
- **`/chaos`** — one global counter; every click adds a random 0–1000. Stored in a sentinel profile row (`guild_id=666`, owned by the bot user, reusing the `cookies` column) so it survives restarts with zero schema; the webui excludes bot-owned rows from every aggregate, so the sentinel never pollutes stats.

**Design intent:** these are the "low-stakes discovery" tier — commands whose whole reward is the moment itself. Keeping them economy-free means they can be arbitrarily silly without balance review, and the misc quests pointing at them are the only bridge back into progression.

## Cat Store

`/catstore` is the primary direct coin sink: players spend coins to buy specific cat rarities, or sell cats back for coins, without the randomness of packs.

### Pricing model

Each cat type has a **base value** derived from the same formula `/trade` and `/gift` have always used: `cat_value(type) = sum(type_dict.values()) // type_dict[type]`. The integer division (`//`) intentionally rounds down, keeping values consistent with trade valuation across the bot.

The store applies a **`CATSTORE_PRICE_MULTIPLIER`** on top of `cat_value` (currently `2`) when computing every price it displays. This multiplier is scoped to catstore code only — trade/gift valuations and job reward magnitudes still use the unmultiplied `cat_value`. The store's working "face value" is therefore `catstore_face_value(type) = cat_value(type) * CATSTORE_PRICE_MULTIPLIER * tier_mult(type)`, where `tier_mult` is the per-rarity multiplier from `config/tuning.json → catstore_tier_mult`. Doubling the base multiplier doubles both sides of the storefront in lockstep, preserving the percentage-based discount/sell-cap math without touching arbitrage guards.

**Per-rarity tier multiplier (`catstore_tier_mult`)** scales top-tier prices non-linearly so a single eGirl isn't a sub-day purchase for a maxed mafia player. The base multiplier is `1.0` for every rarity not in the table; the design-decision multipliers are:

| Rarity   | Multiplier |
| -------- | ---------- |
| Mythic   | 1.5×       |
| Divine   | 4×         |
| Real     | 5×         |
| Ultimate | 6×         |
| eGirl    | 7×         |

Actual coin prices are derived live: `face = (sum(type_dict.values()) // weight) × CATSTORE_PRICE_MULTIPLIER × tier_mult(type)`, so every rarity added to `type_dict` shifts all faces slightly. Only the multipliers above encode a decision — see `config/tuning.json → catstore_tier_mult` for the live values and `/catstore` for current prices. (Ballpark for intuition: the eGirl face lands in the tens of thousands of coins — a week-scale purchase — while unmultiplied mid-rarities stay in the hundreds.)

(Sell prices follow automatically because `store_sell_price` is a percentage of face.) The rebalance was driven by the same income/sink imbalance that motivated the pack price increases: at the pre-rebalance prices, eGirl cost ~4,100 coins, so a Tier‑4 player on a normal day could buy 3 of them. Bumping the top five rarities by 1.5× → 7× turns them into multi-day or week-scale purchases.

> **TODO(design):** Terminator (weight 5) ties Real (weight 5) in `cat_value`, so both yield the same base price at the store (866 coins face value). Real has a `catstore_tier_mult` of 5× making it cost ~8,660 coins; Terminator has no entry in `catstore_tier_mult` so it defaults to 1×, costing only ~1,732 coins. Decide whether Terminator should have its own `catstore_tier_mult` entry to differentiate it from Real at the store. The current 1× default may be intentional (Terminator is a trophy-tier rarity but a new addition, so accessibility is reasonable) or an oversight.

- **Buy price** = `max(1, ceil(face_value * (1 - discount_pct / 100)))`. When `discount_pct` is negative (lower ranks), this is a surcharge — the buyer pays *more* than face value. Ranges from 120% face at Newbie to 70% face at El Patrón.
- **Sell price** = `face_value * sell_pct // 100`, where `sell_pct = min(natural, buy_pct - 5)`. The "natural" curve is `50 + level * 5` (Newbie 50%, El Patrón would-be 100%) but it is capped at 5 percentage points below the buy curve to guarantee every round-trip nets at least −5 percentage points. The cap kicks in at Lv7 and squeezes downward from there.

  Effective sell rate by level: 50, 55, 60, 65, 70, 75, 80, 80, 75, 70, 65, 65. The non-monotonicity is intentional — once `buy_pct` starts dropping (high ranks), `sell_pct` is dragged down to keep the floor below it.

The buy and sell curves are **asymmetric on purpose**: at every level, the sell price sits at least 5 points below the buy price. Round-trips always net negative, so a high-mafia player cannot farm the store. The sell penalty at low ranks doubles the punishment for selling early — Newbies who try to liquidate get the worst rate. **The trade-off:** El Patrón doesn't get 100% face back as the headline suggests; their sell rate is capped at 65% to maintain the anti-arbitrage spread. This was a deliberate design choice over making sells flat / matching upstream behavior.

### Catnip-level discount (store_discount)

Each level entry in `config/catnip.json` carries a `store_discount` field (integer, percent). Negative values are a tax; positive values are a discount:

| Level | Name | store_discount |
| ----- | ---- | -------------- |
| 0 | Newbie | -20% (tax) |
| 1 | Lurker | -15% |
| 2 | Associate | -10% |
| 3 | Soldier | -5% |
| 4 | Capo | 0% |
| 5 | Consigliere | +5% |
| 6 | Underboss | +10% |
| 7 | Boss | +15% |
| 8 | Godfather | +20% |
| 9 | Kingpin | +25% |
| 10 | El Patrón | +30% (cap) |
| 11 | Most Wanted | +30% (cap, matches Lv10) |

The cap at +30% is the `mafia_discount_max` achievement trigger. If the `store_discount` key is absent from a level entry, the code defaults to 0% rather than crashing.

### Discovery gate

A player can only buy or sell rarities listed in `profile.discovered_cats` — a JSONB array of rarity names that records every type the player has ever owned at least one of in that server. Discovery is **lifetime per (user, server)**; selling all cats of a rarity does not remove it from the catalog.

The `mark_discovered(profile, cat_type)` helper is idempotent and is called from every cat-acquisition path:

- The catch handler in `on_message`
- Single pack open (`open_pack`) and multi-pack open (`process_pack_opening`)
- Both battlepass level-up cat reward sites (in `grant_achievement_xp` and `progress()`)
- Trade settlement (both participants)
- Gift recipient side
- The `/catstore` buy handler itself

Existing users were backfilled from their `cat_<Type>` counters by migration 005.

### Currency

`/catstore` touches `profile.coins` only. Since migration 006 merged `roulette_balance` into `coins`, this means roulette winnings can now be spent in the store — that is an accepted consequence of the merge. The coins↔rain-minutes wall is now puncturable via the Rain sub-page in Extras (see below), but the puncture is gated by a steep, exponentially-scaling tax rather than removed outright.

Before `/catstore`, coins had two main sinks: depositing into `/stocks` (volatile speculation) and spending via `/packs` (gacha lottery). `/catstore` was originally added as **the third — the *targeted* sink** the economy was missing: pick the rarity you want, pay coins. The Extras sub-tree extends that with two non-targeted coin sinks: **ephemeral rain** (no inventory, just channel-spawn cats) and **random-roll packs** (`/catstore`'s gacha path, kept deliberately at face `totalvalue` so it adds no arbitrage versus `/stocks`). Packs in /catstore are *intentionally non-targeted* — they're a coin-sink convenience for coin-rich players, not a correction of the original "targeted sink" design.

### Achievement integration

Five achievements unlock inline in the buy handler via `achemb()` calls:

| Achievement ID | Trigger |
| -------------- | ------- |
| `catstore_first_buy` | Any store purchase |
| `catstore_whale` | Single transaction totalling ≥ 10,000 coins |
| `catstore_collector` | `len(set(store_purchased_rarities)) == len(type_dict)` (one of every rarity bought) |
| `mafia_discount_max` | Buying at ≥ +30% discount (Lv10+) |
| `mafia_tax_payer` | Buying at Lv0 (Newbie, -20% tax) |

`profile.store_purchased_rarities` (JSONB array) backs `catstore_collector`; duplicates are allowed and `set()` deduplication happens at check time.

### Out of scope

No cross-server store. No packs in the catalog. No custom cat support. The buy modal is the confirmation step (matching `/stocks` UX); there is no separate confirmation dialog. (The historical "no coins↔roulette_balance bridge" note is obsolete — `roulette_balance` no longer exists as a separate column; see the [currency merge history](#historical-note-the-cat-dollars--coins-merge-migration-006) above.)

### Rain in /catstore

`/catstore` exposes a second top-level browse, **Extras**, with a single item: **rain minutes**. Each purchase adds **1 minute** to the buyer's **per-server** `profile.rain_minutes` inventory (NOT the cross-server `user.rain_minutes`). The buyer then triggers rain later via `/rain` — the catstore button does NOT fire rain in the current channel. Server isolation is intentional: each server's coin economy stays its own, so coins earned in one server can't be converted into rain on another.

**Pricing (`main.py:rain_block_price`)**:

```
raw      = RAIN_BASE_PRICE * (RAIN_SCALE ** blocks_bought_today)
adjusted = raw * (1 - mafia_discount_pct / 100)
```

Defaults after the **2026-05-23 retune**: `RAIN_BASE_PRICE = 3_000` (was 12,000 — a 75% reduction), `RAIN_SCALE = 1.5`. The mafia discount uses the same `store_discount` field from `config/catnip.json` that drives cat-buy pricing. Job perks **do not** apply to rain (the buy-side perks are scoped to cats, so the displayed price equals the charged price).

**Cost curve at mafia Lv4 (0% adjustment):**

| # bought today | Cost   | Cumulative |
|----------------|--------|------------|
| 1              | 3,000  | 3,000      |
| 2              | 4,500  | 7,500      |
| 3              | 6,750  | 14,250     |
| 4              | 10,125 | 24,375     |
| 5              | 15,188 | 39,563     |
| 6              | 22,781 | 62,344     |
| 7              | 34,172 | 96,516     |
| 8              | 51,258 | 147,773    |

**Lazy UTC daily reset**. `profile.rain_blocks_bought_today` (INT) holds the per-day counter; `profile.rain_blocks_last_date` (TEXT, e.g. `"2026-05-23"`) holds the UTC date the counter was last incremented. On every read (`_rain_blocks_today`), the stored date is compared against today's UTC date; on mismatch, the read returns 0. On the next successful purchase, both columns are written with `count=1` and today's date. No cron, no scheduled task.

**Inventory mechanics**. The purchase debits `profile.coins` (per-server wallet) and credits `profile.rain_minutes` (the per-server "bonus minutes" column that `/rain` consumes **before** the cross-server `user.rain_minutes`). The blessings lifetime tracker `user.rain_minutes_bought` is also incremented — it's a cross-server cumulative for blessings-rewards math, independent of the consumable inventory. Per-server isolation means coins earned on one server can't convert into rain on another; each server's economy stays its own. No channel-side validation at purchase — no "setupped channel" / "rain disabled" / "live cat" gate. Those concerns move to `/rain` time, where they already existed.

**Quest / streak / XP**. Catches during the rain you eventually spawn via `/rain` behave identically to catches during battlepass-earned rain — full quest progress, catch streaks, XP.

**Achievements**:
- `catstore_rainmaker` (visible, 300 XP) — first rain block purchase.
- `catstore_monsoon` (hidden, 500 XP) — `rain_blocks_bought_today >= 5` in a single UTC day.

Existing catstore achievements that also fire on qualifying rain purchases:
- `catstore_whale` — any block ≥ 10,000 coins (so block 1 already trips it at the base price).
- `mafia_discount_max` (+30% discount) and `mafia_tax_payer` (Lv0 buyer) — apply the same way they do for cat purchases.

Explicitly **not** fired by rain: `catstore_collector` (which counts distinct cat rarities purchased — rain is not a rarity, and `store_purchased_rarities` is not touched by the rain path).

**Design intent**. The coins↔rain wall is preserved by pricing, not prohibition. A casual player will never break it; a coin-rich player gets a luxury escape valve at a punishing markup. The exponential per-block scaling specifically prevents whale players from bulk-converting in a single day — the 8th block of a day costs ≈ 17× the first, and the cumulative-to-block-8 already exceeds half a million coins.

### Packs in /catstore

`/catstore` → Extras → Packs sells **Stone through Celestial** packs at face `totalvalue` (with Cat Mafia discount applied). The store gives players a way to spend a surplus coin pile on pack content without going through `/stocks` or waiting for the battlepass.

**Wooden is excluded.** `/stocks` already exposes a coins↔Wooden exchange at `COIN_PER_PACK = 100` via the deposit/withdraw flow. Selling Wooden in /catstore would duplicate that path with no benefit and create a second price reference. The Stone-and-up tiers are a genuine economic addition because `/stocks` doesn't sell them.

**Pricing (`main.py:pack_buy_price`)**:

```
raw      = pack.get("store_price", pack["totalvalue"])
adjusted = raw * (1 - mafia_discount_pct / 100)
price    = max(1, ceil(adjusted))
```

`store_price` was added to `pack_data` so the **catstore buy price** can diverge from `totalvalue` (which still drives the `/stocks` deposit payout and the `/trade` value display). Silver and up were inflated by a per-tier multiplier; Stone and Bronze are unchanged so low-tier packs remain accessible.

| Pack      | totalvalue | store_price | Mult | Lv0 (-20%) | Lv4 (0%) | Lv10 (+30%) |
| --------- | ---------- | ----------- | ---- | ---------- | -------- | ----------- |
| Stone     | 150        | 150         | 1×   | 180        | 150      | 105         |
| Bronze    | 195        | 195         | 1×   | 234        | 195      | 137         |
| Silver    | 300        | 600         | 2×   | 720        | 600      | 420         |
| Gold      | 600        | 1,800       | 3×   | 2,160      | 1,800    | 1,260       |
| Platinum  | 1,200      | 4,800       | 4×   | 5,760      | 4,800    | 3,360       |
| Diamond   | 1,800      | 9,000       | 5×   | 10,800     | 9,000    | 6,300       |
| Celestial | 3,000      | 21,000      | 7×   | 25,200     | 21,000   | 14,700      |

**Round-trip economics changed.** Pre-rebalance the round trip was net-zero — `store_price == totalvalue`, so buying a pack and immediately depositing it via `/stocks` returned the same coins. Post-rebalance, **Silver and up are net-negative**: buying a Celestial for 21,000 coins and depositing it pays back only 3,000. This is intentional — top-tier packs are meant to be opened, not flipped. Buy-then-**open** remains gacha-negative on expectation (pack `value` < `totalvalue` < `store_price`), but Cat Mafia rank still tilts the math; at Lv10 the +30% discount makes opening Celestial dramatically more favorable than at Lv0.

**Design intent of the rebalance.** A maxed Tier‑4 jobs player nets ~13,800 coins/day. At the pre-rebalance Celestial price of 3,000, they could buy 4-5 Celestials a day — high-tier packs were impulse buys. Bumping `store_price` 5–7× for the top tiers turns Celestial into a multi-day grind even for whales, restoring the "aspirational" feel without crushing new players who still want a Stone/Bronze without thinking. Wooden continues to live only in `/stocks` — no catstore entry — because the existing coins↔Wooden exchange already serves the cheap-pack-on-demand role.

**Quantity per purchase** is capped at 99 by the modal (`max_length=2`). Players who want more can transact twice — same convention as `/stocks` withdraw.

**Pack contents and opening** are identical to packs earned from the battlepass. The pack columns (`profile.pack_{tier}`) are shared inventory; a `/catstore`-bought pack and a battlepass-rewarded pack of the same tier are indistinguishable once they land. All existing pack-opening achievements and quest progress fire the same way.

**Achievements**:
- `catstore_pack_buyer` (visible, 250 XP) — first pack purchase from /catstore.
- `catstore_pack_collector` (hidden, 500 XP) — bought at least one of every Stone-through-Celestial tier. Backed by `profile.store_purchased_pack_tiers` (JSONB array). Wooden is intentionally NOT in this set since it isn't sold here.

Existing catstore achievements that fire on qualifying pack purchases:
- `catstore_first_buy` — first /catstore purchase of any kind (cat OR pack).
- `catstore_whale` — single transaction ≥ 10,000 coins. Trips on 6× Diamond (10,800 at Lv4), or much sooner if the player buys multiple Platinum+.
- `mafia_discount_max` (Lv10+) and `mafia_tax_payer` (Lv0) — apply the same way they do for cats.

Explicitly **not** fired: `catstore_collector` (counts cat rarities, not packs — `store_purchased_rarities` and `store_purchased_pack_tiers` are independent arrays).

**Design intent**. /catstore packs are a *non-targeted* sink — the gacha path the original "targeted sink" design avoided. They're a convenience for coin-rich players, included because the store needed something to do at the high end without inflating the cat catalog. The face-value pricing guarantees no arbitrage vs `/stocks` deposit; the cat-side targeted purchases remain the cheaper and more reliable use of /catstore.

## Prism crafting (coin tax)

Pre-rebalance, prisms cost only cats — one of every rarity — and nothing else. Combined with the cheap top-tier packs and cheap eGirl/Ultimate cats, players who maxed `catnip_level` could turn job income into a prism every two days indefinitely. The coin tax adds a third axis on top of the cat recipe.

**Cost formula (`main.py:prism_craft_coin_cost`)**:

```
cost = first                        (if prisms_crafted == 0)
cost = min(cap, base * growth^n)    (if prisms_crafted > 0, n = prisms_crafted)
```

with defaults `first = 1,000`, `base = 5,000`, `growth = 2`, `cap = 320,000` (in `config/tuning.json → prism_craft_coin_cost`). `prisms_crafted` is per-profile (per user, per server), counted from the `prism` table's `creator` column at migration time. The ramp:

| Craft # | Cost      |
| ------- | --------- |
| 1st     | 1,000     |
| 2nd     | 10,000    |
| 3rd     | 20,000    |
| 4th     | 40,000    |
| 5th     | 80,000    |
| 6th     | 160,000   |
| 7th+    | 320,000   |

**Confirm dialog enforces it.** The craft button is disabled when `profile.coins < cost`, and the cost is shown alongside the recipe so players see what they're committing to before pressing Craft. A re-check at commit time prevents the "stay on confirm, spend coins elsewhere, then craft" race.

**Per-profile (not per-user globally, not per-server-shared).** A returning player who opens a fresh server starts at the 5,000-coin first craft regardless of how many prisms they've crafted elsewhere. Conversely, a player on a server where other people have crafted prisms pays their own ramp, not the server's. This matches the codebase's per-server gameplay-state philosophy.

**Achievements unchanged.** `prism` (first craft) and `collecter` (collecting every cat type, the recipe checker) still fire exactly as before. The coin tax is a separate concern from achievement gating.
