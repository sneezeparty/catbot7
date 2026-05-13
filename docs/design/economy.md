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

## Currency: cat dollars, coins, rain minutes

These are *not* interchangeable on purpose:

- **Cat dollars** are roulette-only. Bottoming out has its own ach (`failed_gambler`) and isolated recovery loop. Don't let cat dollars buy anything outside `/roulette`.
- **Coins** are pack-economy (used by `/packs` flow). They're soft-capped by pack drop rate.

> **STALE:** an earlier version of this line read "…and bakery", implying `/bakery` uses coins. In fact `/bakery` is a Bake.gg integration that costs cookies (`/cookie`), coffees (`/brew`), and Nice cats — not coins. The bakery mechanic is not covered by any design doc yet; the most relevant home would be a new section in economy.md or a standalone doc. A `> **STALE:** new mechanic` note follows.
- **Rain minutes** are channel-affecting (`/rain` triggers a multi-cat spawn event). They're gift-able and accumulate from battlepass + supporters.

**Design intent:** the segregation prevents arbitrage. If someone could convert /roulette winnings into rain minutes, the casino would dominate. Each currency stays in its silo.

## Catnip as the late-game money sink

Catnip is the late-game money sink: cats go in, perks come out. See [catnip.md](catnip.md). The relevant economic constraint is that catnip costs scale with level and rarity, so high-level users must keep catching to feed it. This is what keeps Ultimate / eGirl cats *consumed* rather than just hoarded.

## Stocks

A fake market with 5 tickers (PRSM, CTNP, PASS, ACHS, RAIN). Stocks are pure speculation — you buy in coin, sell back to coin. They are intentionally **not** correlated with anything real in the bot; the price is a random walk. Stocks exist for engagement, not progression.

**Design intent:** the stock loop should not become the primary coin source. If it does, the random-walk variance is too generous and needs damping.

## Trades & gifts

- `/trade` is a two-party negotiation, used to move cats/packs between players.
- `/gift` is unilateral, with a 20% tax on cat gifts ≥ 5 cats. Gifting to the bot itself is a *sacrifice* (no recipient).

**Design intent:** the gift tax is the friction that prevents alt-account farming. If alt-farming becomes a problem, raise the tax, don't add account verification (this is Discord — verification is a UX disaster).

## Balance guardrails

When adding a new XP source, new pack tier, or new currency interaction, sanity-check against:

- **Daily XP ceiling:** even a degenerate player shouldn't break ~3000 XP/day. That's ~5 battlepass levels per day; the season-long curve assumes much less.
- **Pack inflation:** total in-circulation packs should grow sub-linearly with catches. If a feature gives N packs per catch (vs the current ~0.01-ish), it's overpowered.
- **Per-currency monopoly:** if a feature creates a new way to convert currency A → currency B, the segregation rule above is breaking. Either widen the segregation or pick a different reward.

> **STALE:** new mechanic `bakery` / `brew` / `cookie` (from `main.py`) is not represented in design docs. The `/bakery` command is a weekly Bake.gg integration: users accumulate cookies (via `/cookie`), coffees (via `/brew`), and Nice cats, then deliver a "bakery order" to receive a Silver Pack and a Bake.gg Cat Egg. The Cat Egg can be opened on Bake.gg for a Chef Pack back in Cat Bot (one per user per week). This introduces two new resource sinks (`cookies`, `coffees`) and an external partner economy loop that the segregation rules above don't currently account for.
