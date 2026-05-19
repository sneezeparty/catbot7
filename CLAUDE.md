# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Discord bot ("Cat Bot") written in Python with `discord.py`. Cats spawn in setupped channels; users type `cat` to catch. Per-server profiles, packs, prisms, a fake stock market, battlepass, achievements, casino, etc. Public bot is in 200k+ servers.

License: GNU AGPL v3 for everything except `catpg.py` (MIT). AGPL means deployment changes must be published.

## Commands

```bash
pip install -r requirements.txt          # default
pip install -r requirements-gw.txt       # uses milenakos's discord.py fork that talks to gateway-proxy
python bot.py                            # run the bot (TOKEN env var required)
```

Database is PostgreSQL. User `cat_bot`, database `cat_bot`. Bootstrap either with `psql -U cat_bot` and paste `schema.sql`, or run `bash setup-pg.sh` which spins up a podman container (Postgres 17) on `127.0.0.1:5433`, applies `schema.sql`, and persists data in the `cat-bot-pgdata` volume. Edit `PGPASS` at the top of the script before running. App Emojis must be uploaded to the Discord Dev Portal from <https://github.com/staring-cat/emojis> — the bot fetches them via `bot.fetch_application_emojis()` on startup.

One-off migrations live in `migrations/` as standalone scripts (e.g. `001_unlocked_aches.py`). They expect the bot to be **stopped**, read the same env vars as `bot.py`, are idempotent via a `NNN.done` marker file, and append per-run output to `NNN.log`. Delete the marker to re-run.

There is **no test suite, no linter config, no formatter config, no CI**. Don't invent commands for these.

## Configuration (`config.py`)

All read from env vars. `TOKEN` and `psql_password` are required; everything else is optional and disabling the env var disables the related feature:
- `sentry_dsn` — Sentry error reporting (filtered list of inactionable errors in `bot.py`)
- `webhook_verify` — top.gg vote webhook secret. **If unset, the public-facing aiohttp web server (on `0.0.0.0:8069`) is not started at all.** (The admin webui on localhost is independent — see Architecture.)
- `top_gg_modern_token` — top.gg v1 API (stats, command list, vote replay fallback)
- `wordnik_api_key` — `/define`
- `voting_enabled` — `"1"` re-enables voting. While `0` (default), `/vote`, the top.gg webhook route, vote-replay/reminder loops, and the catch-message vote button are all skipped. Voting is permanently off on this self-hosted instance; the daily catch streak (`user.daily_catch_streak`) is the only streak counter that actually drives gameplay (it scales the Loyalty Streak catnip perk).

`BACKUP_ID`, `DONOR_CHANNEL_ID`, `RAIN_CHANNEL_ID` are hardcoded Discord channel IDs (not env vars).

Two more module-attached config values are populated at `main.py` import time (so they survive `cat!restart`):
- `config.battle` — parsed `config/battlepass.json`.
- `config.tuning` — parsed `config/tuning.json`. Named aliases (`QUEST_COOLDOWN`, `PRISM_BOOST_*`, `MAIN_LOOP_INTERVAL`, etc.) are re-read from this dict at the top of `main.py` on every reload, so edits to `tuning.json` apply on the next `cat!restart` without a process restart.

## Architecture

The whole bot is essentially two files: `bot.py` (entry/lifecycle) and `main.py` (~10k lines — every command, event, view, helper, and the webhook server).

### Entry & hot reload

`bot.py` builds an `AutoShardedBot` with intents trimmed to `messages | message_content | guilds`, `chunk_guilds_at_startup=False`, and `MemberCacheFlags.none()`. It exposes `bot.cat_bot_reload_hook(reload_db: bool)` which unloads/reloads the `main` extension (and optionally re-imports `database` + `catpg` and reconnects the pool). The owner triggers this in chat with `cat!restart` / `cat!restart db`. Other owner-only chat backdoors: `cat!eval`, `cat!print`, `cat!news`, `cat!custom`, `cat!sweep`, `cat!rain`.

`setup_hook` in `bot.py` does three things in order: connect the DB pool, load the `main` extension, and `asyncio.create_task(start_server(bot))` for the admin webui. The webui is intentionally mounted from `bot.py` (not `main.py`) so it survives `cat!restart` — see the Admin webui section below.

### The `main.py` placeholder-bot trick (important)

`main.py` creates its own throwaway `AutoShardedBot` at module load and decorates every command with `@bot.tree.command`. The real bot lives in `bot.py`. Inside `main.setup(bot2)`:
1. Walks `bot.tree` and copies every command onto `bot2.tree`.
2. Reassigns event handlers (`on_message`, `on_ready`, `on_connect`, `on_guild_join`, `on_error`, `on_interaction`) onto `bot2`.
3. Reassigns the module-level `bot` global to `bot2` so all helpers act on the real bot afterward.
4. If `WEBHOOK_VERIFY` is set, starts the aiohttp web server on `0.0.0.0:8069` with routes `POST /` (vote), `GET /supporter`, `POST /bakegg`.
5. Calls `bot.tree.sync()` and caches `RAIN_ID` / `PLUSH_ID` from the resolved command IDs (these are referenced by name in catch messages).

When adding a new slash command in `main.py`, just use `@bot.tree.command(...)` — the copy-on-setup machinery handles the rest. `teardown()` cleans up the web server.

### Background loop

There is **no `tasks.loop`**. Maintenance runs in `background_loop()` and is kicked off from `on_message` whenever `time.time() > last_loop_time + 300`. So the loop only fires while messages are flowing. It updates presence (with /plush pledge count), posts top.gg metrics & command list, replays missed votes via cursor stored in `cursor.txt`, cleans `temp_belated_storage`, etc.

### Data layer (`catpg.py` + `database.py`)

`catpg` is a hand-rolled asyncpg "ORM" with one global `pool`. Each `Model` subclass is named after its lowercase table; columns are accessed as attributes; `save()` only writes dirty fields tracked via `__setattr__`. Key API:

- `await Model.get_or_create(**filters)` — upsert + lock row. When called inside `async with transaction() as conn:` and passed `connection=conn`, returns a row locked `FOR UPDATE` for the duration.
- `await Model.get(...)`, `get_or_none(...)`, `create(...)`, `delete()`, `save()`, `refresh_from_db()`
- `Model.filter(filter_sql, *args, fields=[...])` and `Model.limit(...)` are async generators; `collect`/`collect_limit` materialize lists. `filter` defaults to `refetch=True` which re-queries each row by primary key — pass `refetch=False` for hot paths.
- Aggregates: `sum`, `max`, `min`, `count(filter_sql, *args)`. Filter strings are raw SQL with `$1, $2, …` placeholders. Use `RawSQL("...")` to inject raw SQL into a `fields=` list (e.g. computed columns).
- `_capped_ints` on a Model clamps assignments into int32 range (`Profile` caps the per-rarity catch counters; `User` caps `custom_num`). Without the cap, big users overflow Postgres `integer`.

`database.Profile` represents (user, guild) pairs — **cats are scoped per-server, not global**, which is core to the game. `User` is per-Discord-user (cross-server stuff: votes, DMs, supporter status). `Channel` is per-channel spawn state. `Server` is per-guild settings.

Achievement unlocks are stored two ways during a rollout: a legacy boolean column per ach on `profile` (one column per ID in `config/aches.json`), and a newer `profile.unlocked_aches` JSONB array backfilled by `migrations/001_unlocked_aches.py`. Both representations are kept in sync; the legacy columns are not dropped yet. Renaming or deleting an ach ID is therefore schema-coupled.

### Achievement trigger engine (`ach_engine.py`)

`TriggerEngine` is a data-driven dispatcher for achievements that opt in via a `trigger` block in `config/aches.json` (`{"event": "...", "condition": {"type": "...", ...}}`). On import, `main.py` constructs `ach_engine = TriggerEngine(ach_list)` and call sites do `await ach_engine.evaluate(event_name, profile, ctx, message=..., achemb=achemb, ...)`. The engine indexes by event for O(1) dispatch, skips aches the profile already has, and calls `achemb(...)` for each newly-satisfied condition.

Aches *without* a `trigger` field are still awarded by hand-written `achemb(...)` calls scattered through `main.py` — the engine and the hardcoded paths coexist. Condition evaluators live in the `_evaluators` registry in `ach_engine.py`; add new ones with the `@_evaluator("name")` decorator. After `cat!restart` the engine is reconstructed, so JSON edits to triggers take effect on reload.

### Spawning & catching

- `spawn_cat(channel_id, localcat=None, force_spawn=None)` — random rarity weighted by `type_dict`, sends image from `images/spawn/<type>_cat.png`, stamps message ID into `Channel.cat`. Uses `temp_spawns_storage` list to avoid double-spawn races.
- `on_message` does the catch detection plus an enormous pile of easter-egg/achievement matching. `temp_catches_storage` and `temp_belated_storage` similarly debounce double-catches and grant late battlepass progress.
- `Channel.cat` is the message ID of the live spawn (0 = none); `cattype` is its rarity; `yet_to_spawn` is a unix-time floor for the next spawn.

### Other modules

- `graph.py` — matplotlib stock price charts (transparent background, brown line). Aggregates samples into time buckets.
- `msg2img.py` — renders a `discord.Message` as a PNG that mimics Discord's UI (used by the `catch` context-menu command). Uses Pilmoji + PIL with `fonts/`. Synchronous (calls `requests.get`); fine because it's spawned per-interaction.
- `config/aches.json`, `battlepass.json`, `catnip.json` — game data. Loaded once at module import in `main.py`.
- `facts.txt`, `fanhalo.txt` — line-delimited text used by various commands.
- `stats` and `exportbackup` modules are optional closed-source helpers; both imports are wrapped in `try/except ImportError`.

### Admin webui (`webui/`)

A second aiohttp server bound to **`127.0.0.1:9445`** (distinct from the public top.gg webhook server on `0.0.0.0:8069`). Localhost-only and **unauthenticated** — never change the bind to `0.0.0.0`; the UI edits live game state and JSON configs. Mounted from `bot.py` setup_hook so it survives `cat!restart`.

Reload-safety pattern: webui modules **must not** do `from main import X` at import time, because `main` is unloaded/reimported on `cat!restart`. Instead, `webui/state.py` exposes lazy accessors (`get_main()`, `get_pool()`, `get_catnip()`, `get_tuning()`, …) that resolve to the live module on each call. `webui.state.init(bot)` is called once from `build_app`.

Section layout is declared in `webui/manifest.py` — a dict mapping section name → `{source, routes, templates, references}`. The `references` list encodes cross-section invariants (e.g. "deleting a battlepass quest is blocked if any `profile.catch_quest` still points to it"); save handlers consult these to refuse breaking edits.

### `webui-sync` subagent and edit hook

`.claude/hooks/webui-sync-on-edit.sh` is a `PostToolUse` hook that records edits to bot-surface files (the list in `webui/manifest.py:TRIGGER_PATHS`: `main.py`, `bot.py`, `config.py`, `catpg.py`, `database.py`, `schema.sql`, the four `config/*.json` files) by appending them to `webui/.sync-pending`. On the next turn, the `webui-sync` subagent reads that file, diffs the current state against `webui/manifest.py`, and updates templates/routes/manifest to keep the admin panel in sync — then clears `.sync-pending` and appends a one-line entry to `webui/.sync-log`.

Hard rules for the subagent: it only writes inside `webui/`, never touches bot code, never restarts the bot, never deletes a section without confirmation. If you're editing bot-surface files and `.sync-pending` is non-empty, expect the agent to run on the next turn (or invoke it explicitly with `/sync-webui`).

### Other sync subagents

Three more sync subagents follow the same pattern as `webui-sync`: a `PostToolUse` hook records edits to bot-surface files in a `.{name}-pending` queue, and the agent processes that queue on the next turn. **None of them commit or push** — commits and pushes are manual. They only edit working-tree files within their owned scope.

- **`changelog-sync`** — owns `CHANGELOG.md`'s `[Unreleased]` section. Adds `> _draft_` entries for user-facing changes; humans de-draft on review. Hook: `.claude/hooks/changelog-sync-on-edit.sh`. Queue: `docs/.changelog-pending`. Slash command: `/sync-changelog`.
- **`design-docs-sync`** — owns `docs/design/`. Updates evergreen design docs to reflect mechanics changes. Hook: `.claude/hooks/design-docs-sync-on-edit.sh`. Queue: `docs/design/.sync-pending`. Slash command: `/sync-design-docs`.
- **`readme-sync`** — owns three specific sections of `README.md` (the "What's different on this fork" bullet list, the env-vars table, and the migrations table). Conservative — skips edits when the change doesn't affect those sections. Hook: `.claude/hooks/readme-sync-on-edit.sh`. Queue: `docs/.readme-pending`. Slash command: `/sync-readme`.

All four hooks (the three above plus `webui-sync`) are registered in `.claude/settings.json` under `PostToolUse`. A single `Stop` hook (`sync-reminders-stop.sh`) surfaces any non-empty queues so the next turn knows which agents to invoke.

### Reload-safety

Because `cat!restart` re-imports `main` (and optionally `database`/`catpg`), avoid stashing state on module globals you expect to survive a reload. Cross-reload state lives on the `config` module (e.g. `config.cat_cought_rain`, `config.rain_starter`, `config.HARD_RESTART_TIME`, `config.SOFT_RESTART_TIME`) — `bot.py` initializes those before `bot.run` and `setup` updates `SOFT_RESTART_TIME`.

## Conventions worth knowing

- Code uses 4-space indent, double quotes, type hints sporadic. Match the surrounding style; this is not a strictly-typed codebase.
- The `command_prefix` is a rickroll URL on purpose — Cat Bot is slash-commands-only. The `cat!` owner backdoors are matched by string in `on_message`, not by the command framework.
- Comments throughout are casual/jokey ("WELCOME TO THE TEMP_.._STORAGE HELL"). Don't sanitize them away on unrelated edits.
- AutoShardedBot, but the bot does **not** chunk guilds and caches no members — `member.X` access on arbitrary users will trigger fetches, so prefer `bot.fetch_user(id)` / `guild.fetch_member(id)` and cache results yourself when needed.
