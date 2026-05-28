# Achievements

Achievements are persistent per-(user, server) unlockables. They're the bot's discovery-and-completionism layer: every "weird thing you might try" rewards a unique ach.

## Categories

Defined in `config/aches.json`. Each ach has a category, title, description, and (optionally) an `xp` field for battlepass XP on unlock.

Categories (heuristic, not enforced):
- **Catching** — catch counts, specific rarities, time-based.
- **Catnip** — mafia-loop unlocks.
- **Casino** — slots/roulette/pig milestones.
- **Hidden** — Easter eggs and weird message triggers.
- **Social** — gifting, trading, blessing.
- **Misc** — everything else.

`Hidden` category aches don't count toward the "have 30 achs" misc quest threshold, on purpose — those should feel like secret discoveries.

> **STALE:** the category names above no longer match `config/aches.json`. The actual categories in the file are `Cat Hunt`, `Commands`, `Hard`, `Random`, `Silly`, and `Hidden`. The list above (`Catching`, `Catnip`, `Casino`, `Social`, `Misc`) is from an older naming scheme. The `Hidden` exclusion from the 30-ach quest threshold still holds (backed by the `ach_list[k]["category"] != "Hidden"` check in `main.py`), but all the other category names and their descriptions should be rewritten to match the current config.

## Storage

Two-layer, transitional:

1. **Legacy:** one boolean column per ach on `profile` (e.g., `profile.first`, `profile.lucky`).
2. **Modern:** a JSONB array `profile.unlocked_aches` containing ach IDs.

Both are written on unlock — see `Profile.unlock_ach()` in `database.py`. Reads prefer the JSONB array, fall back to the boolean column.

**Design intent:** the legacy columns are an unmigrated relic. We *can't* drop them without coordinated migration because old code paths still read `profile.<ach_id>` directly. The JSONB layer was added so that new achs don't require a schema migration.

> **TODO(design):** finish the migration off legacy columns. Concretely: audit all `user[<ach_id>]` and `profile.<ach_id>` accesses; replace with `user.has_ach(<ach_id>)`. Then add a migration that drops the boolean columns.

## Two unlock pathways

### Hardcoded sites

Most achs are unlocked via direct `await achemb(message, "<ach_id>", "send")` calls scattered through `main.py`. These predate the trigger engine and remain for legacy / weird-condition aches.

**When to use:** unique single-site conditions ("user typed `cat!coupon jr0f-pzka`"), or conditions that depend on local state at the call site (specific computed values, runtime context).

### Trigger engine (`ach_engine.py`)

Data-driven dispatcher. Aches with a `trigger` block in `aches.json` auto-fire when the named event runs and the condition evaluates true.

Example:
```json
"sussy": {
  "title": "sus",
  "trigger": {
    "event": "catch",
    "condition": {"type": "cat_type_equals", "value": "Sus"}
  }
}
```

Events currently registered: `catch`, `gift`, `trade`, `pig_play`. Adding a new event = `await ach_engine.evaluate("event_name", profile, ctx, ...)` at the relevant call site.

> **STALE:** `main.py` also calls `ach_engine.evaluate` with event names `message_text`, `prism`, and `command` that are not listed above. These event types are not represented in the achievements doc; if any aches use them as triggers, add a brief description of each event here.

Condition types are pluggable via `@_evaluator("name")` in `ach_engine.py`. Adding a new condition type = decorate a new evaluator function.

**When to use:** any new ach that can be expressed as "event X with condition Y". Always prefer the trigger engine for new aches.

## XP rewards

Aches with an `xp` field in `aches.json` grant that many battlepass XP on unlock, via `grant_achievement_xp()` in `main.py`. Range is typically 50–500.

**Design intent:** XP-bearing aches are the "you've discovered something meaningful" tier. Trivial discovery aches (saying "cat" for the first time) shouldn't bear XP — they're discovery rewards, not progression rewards.

## Display

Embed format is consistent across the bot:
- Normal: green embed, "Achievement get!" header, footer = "Unlocked by <user>" + " • +N XP" suffix if XP-bearing.
- Demonic (`thanksforplaying`): special golden header, plays through a flicker animation.

**Design intent:** the demonic flicker is reserved for *one* ach — don't generalize it. The whole point is that it's an event that happens once and is talked about.

## Auto-delete

If `server.auto_delete_achievements` is set, achievement embeds delete after 10 seconds. The `curious` ach is special-cased to always delete after 30 seconds regardless (it's intentionally a "did you see that?" ach).

## Adding a new ach

1. Add the entry to `config/aches.json` with a unique ID, title, desc, category. Add `xp` if it should grant battlepass XP. Add a `trigger` block if it can be data-driven.
2. **Do not** add a new boolean column to `schema.sql` for it — rely on `unlocked_aches`.
3. If the unlock condition isn't expressible as a `trigger`, find the right call site and `await achemb(...)` from there.
4. The `design-docs-sync` agent will catch new achs whose IDs aren't yet referenced anywhere — that's a hint that you forgot the wiring.
