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

- **Coins** are the single shared wallet for `/roulette`, `/stocks`, `/packs`, and `/catstore`. New profiles start at **0 coins** (no default grant). The `failed_gambler` achievement still fires when coins go negative — only the underlying column changed, not the game mechanic. A player in debt can still bet up to 100 coins (`max(coins, 100)`) but cannot buy from `/stocks` or `/catstore` until they grind back to positive.
- **Rain minutes** are channel-affecting (`/rain` triggers a multi-cat spawn event). They're gift-able and accumulate from battlepass + supporters.

**Design intent:** the coins-vs-rain-minutes segregation is preserved — if someone could convert /roulette winnings directly into rain minutes, the casino would dominate. Coins stay in the coins silo; rain minutes stay in the rain silo.

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

Each cat type has a **face value** derived from the same formula `/trade` and `/gift` have always used: `cat_value(type) = sum(type_dict.values()) // type_dict[type]`. The integer division (`//`) intentionally rounds down, keeping values consistent with trade valuation across the bot.

- **Sell price** = face value, always. Selling is never modified by rank.
- **Buy price** = `max(1, ceil(face_value * (1 - discount_pct / 100)))`. When `discount_pct` is negative (lower ranks), this is a surcharge — the buyer pays *more* than face value.

The asymmetry is intentional: the discount only benefits buyers, never sellers. This prevents a high-mafia player from farming the sell side to extract a spread.

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
