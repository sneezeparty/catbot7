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
| PvE | None | `/jobs` Mafia Killings, six NPCs, deterministic 6h contract windows, complications, job perks, daily commit cap, once-per-season Big Score, paid board reroll (level-scaled coin cost, escalates within the 12h window; also available via `/catstore`). Crew-builder uses on-message steppers (rarity focus select, -5/-1/+1/+5/+Max) plus three auto-fill buttons (Good odds ~70% / Lock ~90% / Max ~95%) that value-weight the spread across your cheapest rarities instead of dumping one type, always keeping ≥1 of every owned rarity in reserve for prism-making |
| Mafia decay | None | **Respect** meter (0..100) ticks down 1/hr while idle, refills from job completions. At zero, catnip_level drops one per 6 zero-hours (floored at Lv4) along with its store discount. |
| Top-tier prices | Pre-rebalance Celestial = 3k coins, eGirl = ~4k | Celestial 21k (7×), Diamond 9k (5×), Platinum 4.8k (4×), Gold 1.8k (3×), Silver 600 (2×). Cat tier multipliers: Mythic 1.5×, Divine 4×, Real 5×, Ultimate 6×, eGirl 7×. Low-tier prices unchanged. |
| Prism crafting | One of every cat type, no coin cost | Cat recipe unchanged, plus a per-profile coin tax: **5k × 2^N** for your Nth prism on this server, capped at 320k. |
| Slots | `/slots` 3-reel | `/slots` plus `/catslots` 5×3 Vegas-style with 20 paylines and a per-line cap |
| Stock market | Static order book at the initial price | Simulated market with per-tick GBM noise + sector/market correlation + scheduled earnings, surprise headlines, crash/boom events, and dividend ex-div drops. Market trades fill instantly against the house at the bid/ask; limit orders rest in the book. Activity-derived fair value retained as a long-run mean-reversion anchor. |
| Catnip perks | Time Manipulator and the legacy lineup | Snowballer, Battlepass Booster, Bait & Switch added. Time Manipulator removed entirely (migration 020 remaps stored perk indices). Voting Booster renamed to Loyalty Streak |
| Catnip session length | Scales with level | Always 24h regardless of level |
| Battlepass quest slots | 4 (vote, catch, misc, extra) | 5, with a `challenge` slot for harder catch-condition quests (pool grown to 10). Daily slots (catch/misc/extra/challenge/vote) now reset once per day regardless of completion — incomplete quests don't carry over; weekly is unaffected. Needs migration 036. |
| Passive XP | None | XP drips on first catch of the UTC day, every 10-catch streak, every catnip level-up, and for prism owners when their prism boosts someone else's catch |
| Bonus cats | Bundled with a "late catching" companion mechanic and a per-server Legacy Catching toggle | Solo variant only — catching a cat can trigger a bonus-cat banner with a timed minigame for +3 more of that type (`profile.bonus_catches`). No late-catching mechanic, no `server.legacy_catching` toggle; `bonus_cat_chance_coef=0` in `config/tuning.json` is the kill switch |
| Mystery rewards | "Mystery pack" = always a random pack; overflow tier only, 2000 XP per level | Levels 31–39 of every 40-level season are Mysteries too (level 40 stays a guaranteed Celestial), and the overflow tier costs 3000 XP (this fork already drops a bonus pack on every level). A Mystery now grants a held box instead of resolving instantly — open it later via the "Open Mystery" button in `/packs`, where it's usually a pack (~68%) but can be rain time, coins, XP, a scratchcard, a Double Mystery, or a one-shot voucher (Double Pack / Bounty Skip / eGirl Bonus). Held boxes wipe with packs at season rollover. Weights in `config/tuning.json → mystery_outcomes`; needs migration 035 |
| Sub-1 pack fail | Always 3 Fine cats | Cascades to a tier-lower pack first, with a 3-Fine-cat floor |
| Bakery | `/bakery` delivers weekly orders to the Bake.gg partner API for Cat Eggs / Chef Packs | Disabled (stub like `/plush`) — Bake.gg only authorizes the public bot's token, so deliveries always 401 on a fork. `/cookie` and `/brew` remain as vanity clickers feeding their misc quests |
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

## License

Cat Bot is licensed under the GNU Affero General Public License v3.0. See `LICENSE`. AGPL means deployment changes must be published, so if you run a public instance, the source corresponding to what you deploy needs to stay public.

`catpg.py`, the custom `asyncpg` wrapper, is licensed under MIT. License text is at the top of that file.

Fork modifications are Copyright © 2026 sneezeparty, released under the same AGPL v3. The original upstream copyright notices are preserved in the source file headers as the license requires.

This codebase is a fork of [milenakos/cat-bot](https://github.com/milenakos/cat-bot) by Lia Milenakos — the original, public Cat Bot. Huge thanks to Lia and the Cat Bot contributors for building and open-sourcing it.
