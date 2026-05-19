#!/usr/bin/env bash
# PostToolUse hook: records edits to README-relevant files in docs/.readme-pending.
#
# README has three owned sections (gameplay divergence, env-var table, migrations
# table), so the trigger list is narrower than changelog-sync's. Specifically:
#   - main.py / bot.py — for new slash commands or feature changes worth a bullet
#   - config.py — for env-var table changes
#   - migrations/*.py — for migration table changes
#   - schema.sql — for schema-level mechanics that might warrant a divergence note
#
# The Stop hook reads docs/.readme-pending and surfaces a reminder so Claude
# invokes the readme-sync subagent on the next turn.

set -e

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PENDING="$ROOT/docs/.readme-pending"

# Read stdin, pull file_path with jq (fall back to python if jq missing).
input="$(cat)"
if command -v jq >/dev/null 2>&1; then
  fp="$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')"
else
  fp="$(printf '%s' "$input" | /usr/bin/python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("tool_input",{}).get("file_path","") or "")')"
fi

[ -z "$fp" ] && exit 0

# Resolve to repo-relative path.
case "$fp" in
  "$ROOT"/*) rel="${fp#"$ROOT"/}";;
  /*)        rel="$fp";;
  *)         rel="$fp";;
esac

# Skip docs (README's own territory), .claude/ (agent infra), webui/ (admin-only),
# and README itself.
case "$rel" in
  docs/*|.claude/*|webui/*|README.md) exit 0;;
esac

# Trigger list — narrower than changelog-sync's. Only files that could plausibly
# warrant a touch in one of README's three owned sections.
case "$rel" in
  main.py|bot.py|config.py|schema.sql|migrations/*.py)
    mkdir -p "$ROOT/docs"
    # Append unique entry.
    if ! grep -Fxq "$rel" "$PENDING" 2>/dev/null; then
      printf '%s\n' "$rel" >> "$PENDING"
    fi
    ;;
esac

exit 0
