# Cat Bot design docs

These docs capture the **design intent** behind Cat Bot's systems — *why* a mechanic exists, *how* it's balanced, and *what shape* future changes should preserve. They are intentionally separate from `CLAUDE.md` (which is a code-navigation guide) and from in-code comments (which explain local choices).

Read these when:
- Adding a new game feature and you want to know whether it'll clash with existing balance.
- Tuning a number and you want to understand what the number is doing in the broader economy.
- Onboarding to a subsystem and you want the "what is this for" before diving into 10k lines of `main.py`.

## The docs

| Doc | What it covers |
| --- | --- |
| [economy.md](economy.md) | Cats, packs, XP, currency, pricing & balance philosophy |
| [battlepass.md](battlepass.md) | Seasons, five quest slots, level rewards, XP curves |
| [catnip.md](catnip.md) | Catnip levels, bounties, perks, hibernation, decay |
| [achievements.md](achievements.md) | Trigger engine vs hardcoded grants, unlock storage, balance |

## Conventions

- **Be evergreen.** If a value will change every season, link to the config rather than inlining it. Inline only numbers that encode a design decision (e.g., "Fine cats are the most common at weight 1000" is design; "season 2 level 7 rewards 3 Rare cats" is config).
- **Explain the why.** Mechanics are obvious from the code; intent isn't. If you can derive a statement by reading `main.py`, it probably doesn't belong here.
- **Cross-link.** Use `[…](other-doc.md#anchor)` to point at related sections rather than re-explaining.
- **Mark open questions.** Use `> **TODO(design):** …` blocks for known unresolved tensions; the design-docs-sync agent will preserve and surface them.

## Maintenance

The [`design-docs-sync`](../../.claude/agents/design-docs-sync.md) subagent reconciles these docs against the codebase on every change to bot-surface files (see `.claude/hooks/design-docs-sync-on-edit.sh`). It will:
- Flag stale claims (e.g., "Cat Bot has 22 cat rarities" when `type_dict` now has 23).
- Surface unrepresented systems (a new minigame command that no doc mentions).
- Refuse to silently delete sections; orphans get a `> **STALE:**` marker.

It does **not** rewrite design intent on its own. If a number changed but the design philosophy didn't, the agent just updates the number. If the philosophy changed (e.g., XP economy was rebalanced), it surfaces that for a human to write up.
