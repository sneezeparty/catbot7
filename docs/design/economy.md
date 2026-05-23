# Economy

Cat Bot's economy is built around **per-server cat inventories**, **packs as the gacha layer**, and **XP as the meta-progression currency**. Everything else (catnip, prisms, stocks, casino) is a side-loop that converts between these.

## Cat rarities

Cats are weighted by `type_dict` in `main.py`. The weight is *inverse rarity* — higher weight = more common.

- The current rarity ladder spans **Fine (most common, weight 1000)** to **eGirl (rarest, weight 2)**.
- "Value" of a cat type is `sum(type_dict.values()) / type_dict[type]`. This is what trade/gift/inventory valuations use.
- Catches are per-server, not per-user. A user who plays in 10 servers has 10 independent inventories — this is core to the social loop.

**Design intent:** the ladder is roughly logarithmic. Rarer-than-Mythic is "trophy tier" — the bot is in 200k+ servers and Ultimates / eGirls should remain genuinely scarce. Don't introduce a new mid-rarity that compresses the gap; introduce it at the tails.

## Packs

Packs are the gacha layer. Each pack has:
- `value` — the expected cat-value of contents (rough)
- `upgrade` — the chance the pack rolls one tier above its declared rarity floor
- `totalvalue` — the *aggregate* value of all cats in the pack (used to size the contents)
- `special: True` — event packs (Christmas, Valentine, Chef, Birthday) that are time-gated

Pack tiers form their own ladder: Wooden → Stone → Bronze → Silver → Gold → Platinum → Diamond → Celestial. Celestial has `upgrade: 0` (the cap).

**Design intent:** packs exist to compress the long-tail catching grind. The expected value of a tier-N pack is calibrated so that a player who is many catches behind can "catch up" via packs without trivializing the grind for everyone else. **Don't add packs that pay out in non-cat currency** — that erodes the cat-as-currency design.

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

Daily XP for an active player is on the order of **600–1500 XP**, against level XP requirements of 550–1000. So one engaged session ≈ one battlepass level.

**Design intent:** XP should feel earned, not gifted. The passive drips were added explicitly because the old "vote XP" slot was retired with self-hosting — passive drips fill that gap without re-introducing third-party-dependent rewards. Keep the dominant share with quests; passive drips are sweeteners.

> **TODO(design):** there's no XP source for *opening* a pack yet. This was on the candidate list (idea #11) and was deliberately deferred — revisit if the pack drop rate from catches makes packs feel like a chore rather than a reward.

## Currency: coins and rain minutes

There are now two distinct currency pools:

- **Coins** are the single shared wallet for `/roulette`, `/catslots`, `/stocks`, `/packs`, and `/catstore`. New profiles start at **0 coins** (no default grant). The `failed_gambler` achievement still fires when coins go negative — only the underlying column changed, not the game mechanic. A player in debt can still bet up to 100 coins (`max(coins, 100)`) but cannot buy from `/stocks` or `/catstore` until they grind back to positive.
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

### Activity-driven market maker

On self-hosted instances with a small player base (~20 profiles), the original order-book design had no liquidity: prices sat at the 40-coin initial value forever because the legacy 10k-share upstream sell absorbed all buy demand before any fair-price ask could match.

The fix is a **bot-owned market maker** (MM) that runs each background-loop tick (~every 5 min). Each tick it:

1. Cancels its previous bid/ask for each ticker (refetching each order via `get_or_none` before deletion to detect races with `resolve_orders` — if an order is already gone a user trade consumed it, skip silently).
2. Derives a **fair price** from an in-game activity signal specific to that ticker.
3. Posts fresh bid/ask orders at `fair * (1 ± spread)`.
4. Writes a new `PriceHistory` row at the fair price so charts always have data even with zero user trades.

**Activity signals per ticker** (all queried at tick time):

| Ticker | Metric |
| ------ | ------ |
| PRSM | `Prism.count()` — total prisms outstanding |
| CTNP | `Profile.count("catnip_active > now")` — active catnip sessions |
| PASS | `AVG(battlepass) WHERE battlepass > 0` — avg level among started battlepasses |
| ACHS | `AVG(jsonb_array_length(unlocked_aches)) WHERE …` — avg achievements per active profile |
| RAIN | `User.sum("rain_minutes_bought")` — cumulative rain minutes purchased |

**Fair-price formula:** `clamp(base * ((metric + eps) / (baseline + eps)) ** alpha, floor, ceiling)`. The power-law (exponent `alpha`) keeps the price sublinear — doubling activity does not double price. `eps` prevents division blow-up when the metric is zero. All parameters are in `config/tuning.json["stock_market"]` and hot-reload on `cat!restart`.

**MM order identity:** MM orders use `user_id=<bot profile> AND time=0`. User orders always have `time > 0`. The 7-day stale-order sweep (`Order.filter("time > 0 AND ...")`) skips MM orders by design — never broaden that filter.

**Bot inventory as the MM capacity cap:** the sell side only posts what the bot currently holds; the buy side only posts what the bot can afford. Over time the bot accumulates coins from user buys and shares from user sells, which feeds back into MM capacity. No schema changes were required — the existing `order` and `pricehistory` tables already support it.

**Legacy cleanup:** the first MM tick after deploy cancels the upstream-style `_init_stock_orders` order (10k shares @ 40, `time=0`) and returns the shares to the bot's inventory. Re-running is a no-op.

**Design intent:** prices are now correlated with actual in-game activity, not a random walk. The goal is that a server where people actively use prisms, catnip, and the battlepass sees meaningfully different ticker prices than an empty server. The stock loop should still **not** become the primary coin source — if MM spread or order quantity are too generous, tighten `spread` and `mm_order_quantity` in `tuning.json` rather than changing the fair-price formula.

## Trades & gifts

- `/trade` is a two-party negotiation, used to move cats/packs between players.
- `/gift` is unilateral, with a 20% tax on cat gifts ≥ 5 cats. Gifting to the bot itself is a *sacrifice* (no recipient).

**Design intent:** the gift tax is the friction that prevents alt-account farming. If alt-farming becomes a problem, raise the tax, don't add account verification (this is Discord — verification is a UX disaster).

## Balance guardrails

When adding a new XP source, new pack tier, or new currency interaction, sanity-check against:

- **Daily XP ceiling:** even a degenerate player shouldn't break ~3000 XP/day. That's ~5 battlepass levels per day; the season-long curve assumes much less.
- **Pack inflation:** total in-circulation packs should grow sub-linearly with catches. If a feature gives N packs per catch (vs the current ~0.01-ish), it's overpowered.
- **Per-currency monopoly:** if a feature creates a new way to convert coins into rain minutes (or vice versa), the surviving segregation rule is breaking. Either widen the segregation or pick a different reward. Note: coins↔roulette_balance arbitrage is no longer a concern — those two pools were merged in migration 006.

> **STALE:** new mechanic `bakery` / `brew` / `cookie` (from `main.py`) is not represented in design docs. The `/bakery` command is a weekly Bake.gg integration: users accumulate cookies (via `/cookie`), coffees (via `/brew`), and Nice cats, then deliver a "bakery order" to receive a Silver Pack and a Bake.gg Cat Egg. The Cat Egg can be opened on Bake.gg for a Chef Pack back in Cat Bot (one per user per week). This introduces two new resource sinks (`cookies`, `coffees`) and an external partner economy loop that the segregation rules above don't currently account for.

## Cat Store

`/catstore` is the primary direct coin sink: players spend coins to buy specific cat rarities, or sell cats back for coins, without the randomness of packs.

### Pricing model

Each cat type has a **base value** derived from the same formula `/trade` and `/gift` have always used: `cat_value(type) = sum(type_dict.values()) // type_dict[type]`. The integer division (`//`) intentionally rounds down, keeping values consistent with trade valuation across the bot.

The store applies a **`CATSTORE_PRICE_MULTIPLIER`** on top of `cat_value` (currently `2`) when computing every price it displays. This multiplier is scoped to catstore code only — trade/gift valuations and job reward magnitudes still use the unmultiplied `cat_value`. The store's working "face value" is therefore `catstore_face_value(type) = cat_value(type) * CATSTORE_PRICE_MULTIPLIER`. Doubling the multiplier doubles both sides of the storefront in lockstep, preserving the percentage-based discount/sell-cap math without touching arbitrage guards.

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

`/catstore` exposes a second top-level browse, **Extras**, with a single item: **rain minutes**. Each purchase adds **1 minute** to the buyer's cross-server `user.rain_minutes` inventory. The buyer then triggers rain later via `/rain` — the catstore button does NOT fire rain in the current channel.

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

**Inventory mechanics**. The purchase debits `profile.coins` (per-server wallet) and credits both `user.rain_minutes` (the consumable inventory that `/rain` spends) and `user.rain_minutes_bought` (the lifetime cumulative tracker that the blessings system reads). Cross-server: a player can buy rain minutes on one server and spend them on another. No channel-side validation — no need to be in a setupped channel, no "rain disabled" check, no "live cat" gate. Those concerns move to `/rain` time, where they already existed.

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
raw      = pack["totalvalue"]
adjusted = raw * (1 - mafia_discount_pct / 100)
price    = max(1, ceil(adjusted))
```

| Pack      | totalvalue | Lv0 (-20%) | Lv4 (0%) | Lv10 (+30%) |
| --------- | ---------- | ---------- | -------- | ----------- |
| Stone     | 150        | 180        | 150      | 105         |
| Bronze    | 195        | 234        | 195      | 137         |
| Silver    | 300        | 360        | 300      | 210         |
| Gold      | 600        | 720        | 600      | 420         |
| Platinum  | 1,200      | 1,440      | 1,200    | 840         |
| Diamond   | 1,800      | 2,160      | 1,800    | 1,260       |
| Celestial | 3,000      | 3,600      | 3,000    | 2,100       |

`pack["totalvalue"]` is the same field `/stocks` deposit uses to reward Wooden equivalents; pricing at exactly that value makes the buy-then-deposit round trip net-zero. **Buy-then-open** is gacha-negative in expectation because pack `value` (expected contents) is less than `totalvalue` (aggregate), exactly the same way a real-world card pack works. Cat Mafia rank tilts that math: at Lv10 the +30% discount makes opening packs much more favorable; at Lv0 the −20% tax makes it much worse.

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
