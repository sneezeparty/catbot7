<p align="center">
  <img src="images/derpycat.png" alt="Cat Bot" width="400">
</p>

# Cat Bot — self-hosted fork

A self-hosted fork of [milenakos/cat-bot](https://github.com/milenakos/cat-bot) (the public Cat Bot for Discord — [top.gg](https://top.gg/bot/966695034340663367), [wiki](https://wiki.minkos.lol)).

**Snapshot fork, not a tracking one.** Taken from upstream and diverged; no `upstream` remote, no merge-back plan. Upstream is credited but treated as a separate project.

Extras this fork adds on top of upstream: a localhost admin webui (`webui/`), evergreen design docs (`docs/design/`), `CHANGELOG.md`, a data-driven achievement engine (`ach_engine.py`), hot-reloadable tunables (`config/tuning.json`), idempotent migration scripts (`migrations/`), a podman/Postgres bootstrap (`setup-pg.sh`), and Claude Code sync agents (`.claude/`).

## What's different from upstream

- **Voting retired.** `/vote`, the top.gg webhook, vote replay, and the vote button are all gated behind `voting_enabled` (default `0`). The streak counter was renamed `daily_catch_streak` and now tracks per-day catches.
- **One `coins` wallet.** `/stocks`, `/packs`, `/roulette`, `/catstore`, `/catslots` all share `profile.coins`. The old "cat dollars" silo was merged in.
- **`/catstore`** — coins-to-cats marketplace with a discovery gate. Two-level menu (Cats, Extras → Rain + Packs). Buy prices scale with Cat Mafia level (−20% tax → +30% discount); sell prices capped 5pp below buy so round-trips always net negative.
- **`/jobs`** — Mafia Killings, PvE contracts. Six NPCs, deterministic 6h windows, three outcomes per send, complications (Cat Police raids, double-crosses, jackpots), diminishing returns on mono-rarity stacks, daily 3-commit cap, perks dropped on every successful job, once-per-season Big Score at Lv10. `/rep` shows standing. See `docs/design/jobs.md`.
- **`/catslots`** — 5×3 Vegas-style slot machine, weighted reels, 20 paylines, modal bet (lines × per_line, max 100/line). Shares the coins wallet; no remove-debt button (recovery is `/jobs`).
- **Activity-driven stock market.** Bot-owned market maker ticks prices from in-game metrics (prism count, active catnip, average BP level, etc.) instead of leaving an empty order book at the initial price.
- **Catnip perks reshuffled.** Time Manipulator retired (frozen to preserve indices), new perks: Snowballer, Battlepass Booster, Bait & Switch. Voting Booster → Loyalty Streak. All catnip sessions are 24h regardless of level.
- **5 quest slots per cycle** — vote, catch, misc, extra, plus a new `challenge` slot for harder catch-condition quests.
- **Passive XP drips** — first catch of day, every 10-catch streak, every catnip level-up, prism owners earn XP when their prism boosts someone else's catch.
- **Pack-open polish** — sub-1 fail handling cascades to a tier-lower pack with a 3-Fine-cat floor.

Mechanic details: `docs/design/`.

## Development

> Self-hosting is hacky and not supported by upstream. Expect to edit code.

Prereqs: Python 3.13+, PostgreSQL 17 (or `setup-pg.sh` for podman), a [Discord bot token](https://discord.com/developers/applications).

```bash
git clone <this-repo>
cd cat-bot
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt     # or requirements-gw.txt for the gateway-proxy build
```

Upload the [Cat Bot emoji pack](https://github.com/staring-cat/emojis/releases/latest/download/emojis.zip) to your application's **App Emojis** in the Developer Portal.

Bring up Postgres — either native (create user `cat_bot` + database `cat_bot`, then `psql -U cat_bot -d cat_bot -f schema.sql`) or `bash setup-pg.sh` (edit `PGPASS` first; starts Postgres 17 in podman on `127.0.0.1:5433` and applies the schema).

Set env vars and run `python bot.py`.

### Env vars (read in `config.py`)

| Variable | Required | Purpose |
|---|---|---|
| `TOKEN` | **yes** | Discord bot token |
| `psql_password` | **yes** | DB password |
| `psql_host` | default `127.0.0.1` | DB host |
| `psql_port` | default `5432` | DB port — set `5433` if using `setup-pg.sh` |
| `sentry_dsn` | no | Sentry DSN for error reporting |
| `webhook_verify` | no | top.gg vote webhook secret (dormant — voting retired). Without it the public aiohttp server on `0.0.0.0:8069` is not started. |
| `top_gg_modern_token` | no | top.gg v1 API token (dormant) |
| `wordnik_api_key` | no | `/define`. Without it the command is unregistered and its battlepass quest is auto-skipped. |
| `backup_channel_id` | no | DB backup channel ID |
| `donor_channel_id` | no | supporter images channel ID |
| `rain_channel_id` | no | rain log channel ID |
| `voting_enabled` | default `0` | re-enable the retired voting path. `daily_catch_streak` drives gameplay regardless. |

Tip: append `export VAR=value` lines to `venv/bin/activate` so they're set whenever the venv is active.

## Admin webui

Bot running? Browse `http://127.0.0.1:9445`. **Localhost-only, unauthenticated** — never expose it. Edits live game state and JSON configs.

## Migrations

Standalone scripts in `migrations/`. Expect the bot to be stopped, read the same env vars as `bot.py`, are idempotent via a `NNN.done` marker, append output to `NNN.log`. Delete the marker to re-run.

**Fresh `schema.sql` already includes every column** — migrations only matter when upgrading an existing database that predates a feature.

| # | What it does |
|---|---|
| 001 | Backfill `profile.unlocked_aches` JSONB from legacy per-ach boolean columns. |
| 002 | Add `profile.combo_stack` (Snowballer perk state). |
| 003 | Add the 5th `challenge` battlepass quest slot. |
| 004 | Rename `vote_streak` → `daily_catch_streak`, `max_vote_streak` → `max_daily_streak`. |
| 005 | Add `discovered_cats` + `store_purchased_rarities` for Cat Store; backfill discovery. |
| 006 | Merge `roulette_balance` into `coins` and drop the column. |
| 007 | Jobs foundation: 16 profile columns + `jobinstance` table + 2 indexes. |
| 008 | `perks_suspended_until` for the Cat Police pinch. |
| 009 | Job complications: `jobs_pending_difficulty_mult`, `jobs_pending_heat_bonus`, `jobinstance.complication`. |
| 010 | Job perks: `profile.job_perks` JSONB. |
| 011 | `profile.perks_received` JSONB (lifetime distinct perk IDs for the Mafia Favors leaderboard). |
| 012 | Move job perk roll to offer-generation: `jobinstance.perk_drop`. |
| 013 | `/catslots` state: 5 counter columns + 4 ach booleans. |
| 014 | Rain in catstore: `rain_blocks_bought_today` + `rain_blocks_last_date`. |
| 015 | Packs in catstore: `store_purchased_pack_tiers` JSONB. |

Run in numeric order; each is safe to re-run.

## License

AGPL v3 — see `LICENSE`. Deployment changes must be published if you run a public instance. `catpg.py` (the custom `asyncpg` wrapper) is MIT — license text at the top of the file.
