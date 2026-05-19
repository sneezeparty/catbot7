<p align="center">
  <img src="images/derpycat.png" alt="Cat Bot" width="400">
</p>

# Cat Bot — self-hosted fork

A self-hosted variant of [milenakos/cat-bot](https://github.com/milenakos/cat-bot) (the public Cat Bot for Discord — [top.gg](https://top.gg/bot/966695034340663367), [wiki](https://wiki.minkos.lol)).

This is a **snapshot fork**, not a tracking one — taken from upstream and then diverged. There is no `upstream` git remote configured and no plan to merge new upstream changes back in. The divergence is intentional: this instance has removed voting, restructured the catnip perks and battlepass quests, added a webui, and made schema changes that wouldn't cleanly merge anyway. Upstream is credited and linked, but treated as a separate project from here on.

On top of that snapshot, this fork layers on extras useful for running a private/small instance:

- **`webui/`** — local-only admin panel on `127.0.0.1:9445` for editing live game state and JSON configs.
- **`docs/design/`** — evergreen design docs for the economy, battlepass, catnip, and achievements.
- **`CHANGELOG.md`** — `Keep a Changelog`-style history of user-facing changes.
- **`ach_engine.py`** — data-driven achievement trigger dispatcher.
- **`config/tuning.json`** — hot-reloadable tunables (quest cooldowns, prism boosts, main-loop interval, etc.).
- **`migrations/`** — standalone, idempotent migration scripts.
- **`setup-pg.sh`** — one-shot podman/Postgres bootstrap.
- **`.claude/`** — Claude Code agents (`webui-sync`, `design-docs-sync`, `changelog-sync`) and `PostToolUse` hooks that keep the webui/docs/changelog aligned with bot-surface edits.

Upstream remains the canonical source for the public bot; this repo is a separate project for the self-hosted scenario.

## What's different on this fork

Operator-visible game changes that drift from upstream:

- **Voting is permanently retired.** `/vote`, the top.gg webhook, vote-replay, and the catch-message vote button are all gated behind `voting_enabled` (default `0`) and treated as dormant scaffolding. The vote-streak counter was renamed `daily_catch_streak` and now tracks a per-day catch streak.
- **One unified `coins` wallet.** `/stocks`, `/packs`, `/roulette`, and `/catstore` all spend the same `profile.coins`. The original "cat dollars" gambling silo was merged into coins (see `docs/design/economy.md`).
- **/catstore** — a coins-to-cats marketplace with a discovery gate and a catnip-level-driven discount that runs from −20% (Newbie tax) to +30% (El Patrón discount). Sells are always at face value.
- **Activity-driven stock market.** Prices for PRSM/CTNP/PASS/ACHS/RAIN now follow in-game metrics (prism count, active catnip sessions, average battlepass level, etc.) via a bot-owned market maker that ticks each background loop, instead of sitting flat on a dormant order book.
- **Catnip perks reshuffled** — Time Manipulator retired (frozen in place to preserve stored-perk indices); three new perks added: Snowballer, Battlepass Booster, Bait & Switch. Voting Booster renamed to Loyalty Streak. All sessions are now 24h regardless of level.
- **5 quest slots per cycle** — vote, catch, misc, extra, and a new `challenge` slot for harder catch-condition quests (`under3`, `slow`, `legendary+`, `catnip_catch`, `streak10`).
- **Passive XP drips** — first catch of the UTC day, every 10-catch streak, every catnip level-up, and prism owners get XP when their prism boosts another user's catch.
- **Pack-open polish** — sub-1 fail handling cascades to a tier-lower pack (with a 3-Fine-cat floor); catch-drop and battlepass-bonus packs render as tier-themed embeds.

The corresponding gameplay docs are in `docs/design/`.

# Development

> **Self-hosting is hacky and isn't supported upstream.** These instructions are for testing/development. You'll likely need to fiddle with the code.

## Prerequisites

- Python 3.13+
- PostgreSQL 17 (or use `setup-pg.sh` to run it in podman)
- A Discord bot application + token from the [Discord Developer Portal](https://discord.com/developers/applications)

## Setup

1. Clone the repo.

2. Install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
   (Use `requirements-gw.txt` instead if you want the `gateway-proxy` build of `discord.py`.)

3. Upload the [Cat Bot emoji pack](https://github.com/staring-cat/emojis/releases/latest/download/emojis.zip) to your application's **App Emojis** section in the Discord Developer Portal.

4. Bring up Postgres. Either:
   - **Native Postgres:** create user `cat_bot` and database `cat_bot`, then `psql -U cat_bot -d cat_bot -f schema.sql`.
   - **Podman one-liner:** edit `PGPASS` in `setup-pg.sh`, then `bash setup-pg.sh`. This starts Postgres 17 in a container on `127.0.0.1:5433`, persists data in the `cat-bot-pgdata` volume, and applies `schema.sql`.

5. Configure the bot via environment variables. All config is read in `config.py`:

   | Variable | Required? | Purpose |
   |---|---|---|
   | `TOKEN` | **yes** | Discord bot token |
   | `psql_password` | **yes** | DB password |
   | `psql_host` | no (default `127.0.0.1`) | DB host |
   | `psql_port` | no (default `5432`) | DB port — set to `5433` if using `setup-pg.sh` |
   | `sentry_dsn` | no | Sentry DSN for error reporting |
   | `webhook_verify` | no | top.gg vote webhook secret. **Voting is permanently retired on this fork**; this is dormant scaffolding kept in case you ever want to re-enable. Without it the public aiohttp server on `0.0.0.0:8069` is not started. |
   | `top_gg_modern_token` | no | top.gg v1 API token (also dormant — voting-related) |
   | `wordnik_api_key` | no | needed for `/define`. Without it, the `/define` slash command is not registered and the matching battlepass quest is auto-skipped. |
   | `backup_channel_id` | no | channel ID for DB backups |
   | `donor_channel_id` | no | channel ID for supporter images |
   | `rain_channel_id` | no | channel ID for rain logs |
   | `voting_enabled` | no (default `0`) | reverse-switch for the retired voting path. Set to `1` to re-register `/vote`, the top.gg webhook route, vote-replay, and the catch-message vote button. The daily catch streak (`profile.daily_catch_streak`) is the only streak counter that drives gameplay regardless. |

   A convenient pattern is to append `export VAR=value` lines to the end of `venv/bin/activate` so they're set whenever the venv is active.

6. Run:
   ```bash
   python bot.py
   ```

## Admin webui

If the bot is running, browse to `http://127.0.0.1:9445` to access the admin panel. It is **localhost-only and unauthenticated** — never expose it. The webui edits live game state and JSON configs.

## Migrations

One-off migration scripts live in `migrations/`. They:
- Expect the bot to be **stopped**.
- Read the same env vars as `bot.py`.
- Are idempotent via a `NNN.done` marker file.
- Append per-run output to `NNN.log`.

Delete the marker to re-run a script.

After bringing up a fresh database from `schema.sql`, you should not need to run any of them — the schema already includes every column. The migrations only matter when **upgrading an existing database** that pre-dates a feature. Current set:

| # | What it does |
|---|---|
| 001 | Backfill `profile.unlocked_aches` JSONB from the legacy per-ach boolean columns. |
| 002 | Add `profile.combo_stack` (Snowballer perk state). |
| 003 | Add the 5th `challenge` battlepass quest slot columns. |
| 004 | Rename voting columns: `user.vote_streak` → `daily_catch_streak`, `user.max_vote_streak` → `max_daily_streak`. |
| 005 | Add `profile.discovered_cats` + `profile.store_purchased_rarities` for the Cat Store; backfill discovery from existing per-type cat counters. |
| 006 | Merge `profile.roulette_balance` into `profile.coins` and drop the column. |

Run them in numeric order. Each one is safe to re-run if it failed partway.

# License

Cat Bot is licensed under the GNU Affero General Public License v3.0 — see `LICENSE`. AGPL means deployment changes must be published, so if you run a public instance of this fork, the source corresponding to what you deploy needs to stay public.

`catpg.py`, the custom `asyncpg` wrapper, is licensed under MIT (license text is at the top of the file).
