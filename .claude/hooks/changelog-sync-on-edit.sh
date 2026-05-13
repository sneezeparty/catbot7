#!/usr/bin/env bash
# PostToolUse hook: records edits to bot-surface files in docs/.changelog-pending.
#
# Receives the hook JSON on stdin. Pulls `tool_input.file_path` and, if the
# file is in the trigger list and NOT inside docs/ or .claude/ or webui/, appends
# it to the pending-list. The Stop hook reads this file and surfaces a reminder
# to Claude to invoke the changelog-sync subagent.

set -e

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PENDING="$ROOT/docs/.changelog-pending"

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

# Skip docs (changelog's own territory + design docs aren't user-facing),
# .claude/ (agent infra), and webui/ (admin-only, not user-facing).
case "$rel" in
  docs/*|.claude/*|webui/*|CHANGELOG.md) exit 0;;
esac

# Trigger list — bot-surface files whose changes may be user-facing.
case "$rel" in
  main.py|bot.py|config.py|catpg.py|database.py|schema.sql|ach_engine.py|msg2img.py|graph.py|config/aches.json|config/battlepass.json|config/catnip.json|config/tuning.json)
    mkdir -p "$ROOT/docs"
    # Append unique entry.
    if ! grep -Fxq "$rel" "$PENDING" 2>/dev/null; then
      printf '%s\n' "$rel" >> "$PENDING"
    fi
    ;;
esac

exit 0
