<p align="center">
  <img src="images/derpycat.png" alt="Cat Bot" width="400">
</p>

# Cat Bot

A self-hosted Discord bot about catching cats. Spawns appear in setupped channels, players type `cat` to catch, per-server profiles track everything else (packs, the mafia, the catstore, the casino, achievements, a fake stock market, and so on).

<p align="center">
  <a href="https://discord.gg/GAv9umz5RB">
    <img src="https://img.shields.io/badge/Join-Cat%20Bot's%20Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Join Cat Bot's Discord">
  </a>
  &nbsp;&nbsp;
  <a href="https://top.gg/bot/1503024098412855458">
    <img src="https://img.shields.io/badge/Install-on%20your%20server-FF3366?style=for-the-badge" alt="Install on your server (top.gg)">
  </a>
  &nbsp;&nbsp;
  <a href="https://buymeacoffee.com/sneezeparty">
    <img src="https://img.shields.io/badge/Buy%20me-a%20coffee-FFDD00?style=for-the-badge&logo=buymeacoffee&logoColor=black" alt="Buy me a coffee">
  </a>
</p>

## What's different from upstream

| Area | Upstream | This fork |
|---|---|---|
| Voting | `/vote` earns rain minutes, drives streak counter | `/vote` re-implemented as a battlepass XP source (250–350 XP daily, 2× weekends). XP is auto-granted across all of a player's server profiles at vote time — no need to claim via `/battlepass` in each server. On by default; set `voting_enabled=0` to disable. Streak counter renamed to `daily_catch_streak` and tracks per-day catches independently of voting. The vote battlepass slot rolls a real vote quest ~1/3 of the time; the other ~2/3 it hosts a random misc-pool substitute quest instead (same XP tracking, no Top.gg interaction required for those rolls). |
| Wallet | Two silos, "cat dollars" for /roulette and "coins" for /stocks and /packs | One `coins` wallet shared across /stocks, /packs, /roulette, /catstore, /catslots |
| Marketplace | None | `/catstore` sells discovered cat rarities, plus an Extras sub-tree for paid rain blocks and Stone-through-Celestial packs |
| PvE | None | `/jobs` Mafia Killings, six NPCs, deterministic 6h contract windows, complications, job perks, daily commit cap, once-per-season Big Score, paid board reroll (level-scaled coin cost, escalates within the 12h window; also available via `/catstore`) |
| Mafia decay | None | **Respect** meter (0..100) ticks down 1/hr while idle, refills from job completions. At zero, catnip_level drops one per 6 zero-hours (floored at Lv4) along with its store discount. |
| Top-tier prices | Pre-rebalance Celestial = 3k coins, eGirl = ~4k | Celestial 21k (7×), Diamond 9k (5×), Platinum 4.8k (4×), Gold 1.8k (3×), Silver 600 (2×). Cat tier multipliers: Mythic 1.5×, Divine 4×, Real 5×, Ultimate 6×, eGirl 7×. Low-tier prices unchanged. |
| Prism crafting | One of every cat type, no coin cost | Cat recipe unchanged, plus a per-profile coin tax: **5k × 2^N** for your Nth prism on this server, capped at 320k. |
| Slots | `/slots` 3-reel | `/slots` plus `/catslots` 5×3 Vegas-style with 20 paylines and a per-line cap |
| Stock market | Static order book at the initial price | Simulated market with per-tick GBM noise + sector/market correlation + scheduled earnings, surprise headlines, crash/boom events, and dividend ex-div drops. Market trades fill instantly against the house at the bid/ask; limit orders rest in the book. Activity-derived fair value retained as a long-run mean-reversion anchor. |
| Catnip perks | Time Manipulator and the legacy lineup | Snowballer, Battlepass Booster, Bait & Switch added. Time Manipulator removed entirely (migration 020 remaps stored perk indices). Voting Booster renamed to Loyalty Streak |
| Catnip session length | Scales with level | Always 24h regardless of level |
| Battlepass quest slots | 4 (vote, catch, misc, extra) | 5, with a new `challenge` slot for harder catch-condition quests |
| Passive XP | None | XP drips on first catch of the UTC day, every 10-catch streak, every catnip level-up, and for prism owners when their prism boosts someone else's catch |
| Bonus cats | Bundled with a "late catching" companion mechanic and a per-server Legacy Catching toggle | Solo variant only — catching a cat can trigger a bonus-cat banner with a timed minigame for +3 more of that type (`profile.bonus_catches`). No late-catching mechanic, no `server.legacy_catching` toggle; `bonus_cat_chance_coef=0` in `config/tuning.json` is the kill switch |
| Battlepass overflow ("Extra Rewards") | 2000 XP per level once you pass the season's last level, granting a pack | Same idea but 3000 XP per level — this fork already drops a bonus pack on every level, so upstream's 2000 would roughly quadruple the post-cap pack faucet. Reward is a random "Mystery" pack weighted toward cheap tiers |
| Sub-1 pack fail | Always 3 Fine cats | Cascades to a tier-lower pack first, with a 3-Fine-cat floor |
| Profile card | None | `/catprofile [user]` shows a compact at-a-glance embed: mafia level/rank, cattlepass progress, cat count and collection value, prisms, coins, achievements, catch streak, pig high score, and cookies. Supports viewing other players. |
| Season warning + recap | None | Bot posts a "season ends tomorrow" embed to every setupped channel on the last day of the month, listing what the reset wipes vs keeps. On the 1st it follows up with a per-server recap leaderboard (top coins earned, roulette, stocks), a 🏆 Champions embed naming the top-3 in coins/cats/heists, then a "Season N starts now" greeting. Per-server opt-out via `/settings` (`server.season_announcements`); trophy awards always land regardless of the opt-out. |

Design docs for each system live in `docs/design/`.

## Development

> Self-hosting is hacky. Expect to edit code.

Prereqs: Python 3.13+, PostgreSQL 17 (or `setup-pg.sh` for podman), a [Discord bot token](https://discord.com/developers/applications).

```bash
git clone https://github.com/sneezeparty/catbot7.git
cd catbot7
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt    # or requirements-gw.txt for the gateway-proxy build
```

Upload the [Cat Bot emoji pack](https://github.com/staring-cat/emojis/releases/latest/download/emojis.zip) to your application's **App Emojis** in the Discord Developer Portal.

Bring up Postgres. Either native (create user `cat_bot` and database `cat_bot`, then `psql -U cat_bot -d cat_bot -f schema.sql`), or run `bash setup-pg.sh` after editing `PGPASS` inside it. The script starts Postgres 17 in podman on `127.0.0.1:5433` and applies the schema.

Set env vars, then run `python bot.py`. The easiest way is to copy `.env.example` to `.env` in the project root and fill in your values — `config.py` reads it on startup (existing shell env vars always win). Alternatively, append `export VAR=value` lines to `venv/bin/activate` as before.

### Env vars (read in `config.py`)

| Variable | Required | Purpose |
|---|---|---|
| `TOKEN` | **yes** | Discord bot token |
| `psql_password` | **yes** | DB password |
| `psql_host` | default `127.0.0.1` | DB host |
| `psql_port` | default `5432` | DB port. Set `5433` if using `setup-pg.sh` |
| `sentry_dsn` | no | Sentry DSN for error reporting |
| `webhook_verify` | no | top.gg vote webhook HMAC secret. Without it the public aiohttp server on `0.0.0.0:8069` is not started; voting still works via polling fallback |
| `top_gg_modern_token` | no | top.gg v1 API token. Used for the vote-replay polling loop (required if `voting_enabled=1` and you can't expose port 8069) and for stats/command-list posting |
| `wordnik_api_key` | no | `/define`. Without it the command is unregistered and its battlepass quest is auto-skipped |
| `backup_channel_id` | no | DB backup channel ID |
| `donor_channel_id` | no | supporter images channel ID |
| `rain_channel_id` | no | rain log channel ID |
| `voting_enabled` | default `1` | top.gg voting is on by default. Set to `0` to disable `/vote`, the webhook route, vote-replay/reminder loops, the `/battlepass` "Vote on Top.gg" daily quest (250–350 XP, 2x weekends), and the catch-message vote button |
| `store_enabled` | default `0` | enable the `/store` slash command, entitlement event handlers, and the startup reconciliation pass. SKUs live in `config/store.json` |
| `support_invite` | default empty | invite to your support / community Discord. Used wherever the upstream bot used to link to its own server. Empty means the link is omitted entirely |

## Admin webui

If the bot is running, browse `http://127.0.0.1:9445`. It is **localhost-only and unauthenticated**, so never expose it. The webui edits live game state and JSON configs.

## Cat Bot Store

This fork ships its own optional `/store` command backed by **Discord's native monetization system** (SKUs and entitlements). The fork is not affiliated with the upstream bot's store. To enable it: set `store_enabled=1`, create your SKUs in the Discord Developer Portal under **Monetization**, then paste their numeric ids into `config/store.json` with a `kind` of `supporter` (grants `user.premium`) or `cosmetic` (recorded without changing premium). Discord handles checkout. The bot reconciles entitlement state on every startup so changes that happen offline are not lost.

## Migrations

Standalone scripts in `migrations/`. They expect the bot to be stopped, read the same env vars as `bot.py`, are idempotent via a `NNN.done` marker, and append output to `NNN.log`. Delete the marker to re-run.

A fresh `schema.sql` already includes every column, so migrations only matter when upgrading an existing database that predates a feature.

| # | What it does |
|---|---|
| 001 | Backfill `profile.unlocked_aches` JSONB from the legacy per-ach boolean columns |
| 002 | Add `profile.combo_stack` (Snowballer perk state) |
| 003 | Add the 5th `challenge` battlepass quest slot |
| 004 | Rename `vote_streak` to `daily_catch_streak`, `max_vote_streak` to `max_daily_streak` |
| 005 | Add `discovered_cats` and `store_purchased_rarities` for Cat Store, backfill discovery |
| 006 | Merge `roulette_balance` into `coins` and drop the column |
| 007 | Jobs foundation, 16 profile columns plus `jobinstance` table and 2 indexes |
| 008 | `perks_suspended_until` for the Cat Police pinch |
| 009 | Job complications, `jobs_pending_difficulty_mult`, `jobs_pending_heat_bonus`, `jobinstance.complication` |
| 010 | Job perks, `profile.job_perks` JSONB |
| 011 | `profile.perks_received` JSONB (lifetime distinct perk IDs for the Mafia Favors leaderboard) |
| 012 | Move job perk roll to offer-generation, `jobinstance.perk_drop` |
| 013 | `/catslots` state, 5 counter columns plus 4 ach booleans |
| 014 | Rain in catstore, `rain_blocks_bought_today` and `rain_blocks_last_date` |
| 015 | Packs in catstore, `store_purchased_pack_tiers` JSONB |
| 016 | `/catslots` eGirl bonus round counters and ach booleans |
| 017 | Cat Bot Store, `user.entitlements` JSONB |
| 018 | Respect meter (`profile.respect`, `respect_last_tick`) + prism craft counter (`profile.prisms_crafted`). Backfills the counter from existing prism rows. |
| 019 | `profile.season_reset_pending` flag for the one-shot "your season just reset" notice. |
| 020 | Removes the `timer_add` "Time Manipulator" catnip perk and remaps stored perk indices ≥ 12 down by one across `profile.perks`/`perk1`/`perk2`/`perk3`. |
| 021 | Add `server.season_announcements BOOLEAN DEFAULT true` (per-server opt-out for the season-end warning). No backfill needed. Safe to re-run. |
| 022 | Add six `profile` columns for the season-recap leaderboard: `coins_earned`, `roulette_coins_won`, `roulette_coins_bet`, `stock_coins_earned`, `stock_coins_spent` (all `bigint DEFAULT 0`) and `season_stat_baseline` (`jsonb DEFAULT '{}'`). No backfill needed — defaults are correct for all existing rows. Safe to re-run. |
| 023 | Add `profile.job_rerolls_window` (`integer DEFAULT 0`) and `profile.job_rerolls_window_idx` (`bigint DEFAULT 0`) for the paid `/jobs` board reroll price-escalation counter. No backfill needed. Idempotent (per-column gated). Bot must be stopped before running. |
| 024 | Add `profile.season_trophies` (`jsonb DEFAULT '[]'`) — append-only trophy records awarded at season rollover to the top-3 players per category (coins earned, cats caught, heists completed) per server. Displayed on `/catprofile`. No backfill needed. |
| 025 | Add `profile."cat_Shadow"` and `profile."cat_Terminator"` (integer DEFAULT 0) — per-server catch counters for the two new rarities. No backfill needed. |
| 026 | Reset `user.news_state` to `''` for all users — clears stale read-state from the hardcoded news list so the new `config/news.json`-driven articles show as unread for everyone. |
| 027 | Add `profile.last_job_time` (`bigint DEFAULT 0`) — UNIX timestamp of the player's most recent committed job; shields mafia level from both decay systems for 24h after a job. Backfills from each profile's most recent resolved `jobinstance` row. |
| 028 | Add `profile.vote_quest` (`VARCHAR(30) DEFAULT ''`) — tracks which misc-pool substitute quest (if any) is currently occupying the vote battlepass slot. Empty string means the slot is showing the real Top.gg vote quest. |
| 029 | Add `server.name` (`VARCHAR(100) DEFAULT '' NOT NULL`) — cached guild display name populated by the snapshot loop / `on_guild_join`. Creates `public.metric_snapshot` (hourly-bucketed aggregate counters) used by the admin dashboard's Activity page for time-series deltas. Idempotent. No backfill. |
| 030 | Stock Market 2.0 schema break. Creates `public.newsevent` (+ sequence) for the persisted news feed, cancels and refunds every live limit order (`order.time > 0` — coins for buys, shares for sells, with `c`/`C` activity-log rows), and deletes every market-maker order (`order.time = 0`). User holdings (`profile.stock_*`) and `pricehistory` are untouched. Bot must be stopped. Not safe to re-run (data mutation). |
| 031 | Announcements broadcaster schema. Creates `public.announcement` (+ sequence) — one row per operator-authored broadcast sent from the webui Announcements section, tracking body text, `one_per_server` dedupe flag, status (`pending` / `sending` / `sent` / `failed`), and per-broadcast target/sent/failed/skipped counts. |
| 032 | Add `profile.bonus_catches` (`integer DEFAULT 0`) — counts successful bonus-cat minigame wins, shown in `/profile` stats and the webui profile browser. No backfill. |
| 033 | Add `profile.fish_caught` (`integer DEFAULT 0`) and `profile.rarest_fish` (`varchar(15) DEFAULT ''`) for the new `/fish` command. No backfill. |
| 034 | Add `profile.weekly_quest` (`varchar(10) DEFAULT ''`), `weekly_progress` (`smallint DEFAULT 0`), `weekly_cattypes` (`smallint[] DEFAULT '{}'`), and `scratchcards` (`smallint DEFAULT 0`) for weekly quests and the new `/scratch` scratchcards. No backfill. |

Run in numeric order. Each script is idempotent via its `.done` marker. Most are also safe to re-run after deleting the marker — **except `020` and `030`, which mutate data in place** and would double-apply if re-run; restore the pre-migration data before re-running them.

## License

Cat Bot is licensed under the GNU Affero General Public License v3.0. See `LICENSE`. AGPL means deployment changes must be published, so if you run a public instance, the source corresponding to what you deploy needs to stay public.

`catpg.py`, the custom `asyncpg` wrapper, is licensed under MIT. License text is at the top of that file.

Fork modifications are Copyright © 2026 sneezeparty, released under the same AGPL v3. The original upstream copyright notices are preserved in the source file headers as the license requires.

This codebase is a fork of [milenakos/cat-bot](https://github.com/milenakos/cat-bot) by Lia Milenakos — the original, public Cat Bot. Huge thanks to Lia and the Cat Bot contributors for building and open-sourcing it.
