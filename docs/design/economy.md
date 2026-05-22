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

**Target RTP ~93%**, Vegas-standard, slightly favoring the house. The payout table is shaped so that:
- the common Fine 3-of-a-kind returns less than 1× per line (a 1× payout on a winning line gives back 1 coin per line, but most spins of an active line don't hit at all),
- the rarest eGirl 5-of-a-kind is the lottery hit (1,000,000× per line — possible but improbable),
- and the middle tiers (Corrupt → Real) provide the bulk of the per-spin variance.

A spin is flagged a **big win** when `total_payout >= 100 × total_bet`. This is a high but not lottery-only threshold: a 5-of-a-kind on most symbols at most line counts will clear it. Big wins fire the `big_win_catslots` achievement and increment `profile.catslots_big_wins`.

**Per-line bet cap: `CATSLOTS_MAX_PER_LINE = 100` coins.** Total bet is therefore implicitly capped at `max(lines) × max_per_line = 20 × 100 = 2,000 coins` per spin. This bounds the worst-case eGirl 5-of-a-kind payout to **100,000,000 coins** (1,000,000× × 100 per_line), which is still enormous on a small instance but no longer unbounded. The cap is enforced in the modal's `on_submit`; players who want a bigger total bet must use more lines, not more coins per line.

Lifetime stats live in five `profile.catslots_*` columns (`spins`, `wins`, `big_wins`, `coins_bet`, `coins_won`). `catslots_coins_bet`/`coins_won` use `bigint` since aggregate lifetime turnover can exceed int32 quickly at high stakes. Concurrency is gated by a separate `catslots_lock` list (mirroring `slots_lock`); the rigged-user override forces a 5-of-a-kind eGirl on line 1 (middle row).

Casino quest progression: `/catslots` spins count toward the existing `casino` extra-slot quest under the `slots` game bit. The dedicated `slots` / `slots2` battlepass quests remain scoped to `/slots` only.

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

`/catstore` touches `profile.coins` only. Since migration 006 merged `roulette_balance` into `coins`, this means roulette winnings can now be spent in the store — that is an accepted consequence of the merge. The coins↔rain-minutes wall remains intact; the store does not interact with rain minutes.

Before `/catstore`, coins had two main sinks: depositing into `/stocks` (volatile speculation) and spending via `/packs` (gacha lottery). Neither let a player target a specific rarity. `/catstore` is the intentional targeted coin sink the economy was missing.

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

`/catstore` exposes a second top-level browse, **Extras**, with a single item: **rain blocks**. Each block is `RAIN_BLOCK_SECONDS = 15` seconds of cat rain in the current channel. Players spend `coins`. This intentionally punctures the coins↔rain wall — but only at extreme cost.

**Pricing (`main.py:rain_block_price`)**:

```
raw      = RAIN_BASE_PRICE * (RAIN_SCALE ** blocks_bought_today)
adjusted = raw * (1 - mafia_discount_pct / 100)
```

Defaults: `RAIN_BASE_PRICE = 12_000`, `RAIN_SCALE = 1.5`. The mafia discount uses the same `store_discount` field from `config/catnip.json` that drives cat-buy pricing. Job perks **do not** apply to rain (the buy-side perks are scoped to cats, so the displayed price equals the charged price).

**Cost curve at mafia Lv4 (0% adjustment):**

| Block | Cost     | Cumulative |
|-------|----------|------------|
| 1     | 12,000   | 12,000     |
| 2     | 18,000   | 30,000     |
| 3     | 27,000   | 57,000     |
| 4     | 40,500   | 97,500     |
| 5     | 60,750   | 158,250    |
| 6     | 91,125   | 249,375    |
| 7     | 136,688  | 386,063    |
| 8     | 205,031  | 591,094    |

**Lazy UTC daily reset**. `profile.rain_blocks_bought_today` (INT) holds the counter; `profile.rain_blocks_last_date` (TEXT, e.g. `"2026-05-22"`) holds the UTC date the counter was last incremented. On every read (`_rain_blocks_today`), the stored date is compared against today's UTC date; on mismatch, the read returns 0. On the next successful purchase, both columns are written with `count=1` and today's date. No cron, no scheduled task.

**Active-rain stacking**. Buying a block while a rain is already running in the channel adds `RAIN_BLOCK_SPAWNS = ceil(15 / 2.75) = 6` to `channel.cat_rains` without restarting the recovery loop. Buying into an inactive channel sets `channel.cat_rains`, resets `channel.yet_to_spawn`, kicks off `spawn_cat` + `rain_recovery_loop` (mirrors `/rain`).

**Quest / streak / XP**. Catches during bought rain behave identically to catches during battlepass-earned rain — full quest progress, catch streaks, XP. The price wall (≥ 12k coins for 15 s) is steep enough that arbitrage doesn't pay even at the discount cap.

**Gating**. The Extras purchase button is only meaningful in a setupped channel with `server.do_rain = true` and no active spawn — the handler ephemeral-rejects all three failure modes before deducting any coins.

**Achievements**:
- `catstore_rainmaker` (visible, 300 XP) — first rain block purchase.
- `catstore_monsoon` (hidden, 500 XP) — `rain_blocks_bought_today >= 5` in a single UTC day.

Existing catstore achievements that also fire on qualifying rain purchases:
- `catstore_whale` — any block ≥ 10,000 coins (so block 1 already trips it at the base price).
- `mafia_discount_max` (+30% discount) and `mafia_tax_payer` (Lv0 buyer) — apply the same way they do for cat purchases.

Explicitly **not** fired by rain: `catstore_collector` (which counts distinct cat rarities purchased — rain is not a rarity, and `store_purchased_rarities` is not touched by the rain path).

**Design intent**. The coins↔rain wall is preserved by pricing, not prohibition. A casual player will never break it; a coin-rich player gets a luxury escape valve at a punishing markup. The exponential per-block scaling specifically prevents whale players from bulk-converting in a single day — the 8th block of a day costs ≈ 17× the first, and the cumulative-to-block-8 already exceeds half a million coins.
