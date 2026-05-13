#!/usr/bin/env bash
# Stop hook: if there are pending bot-surface edits, surface a reminder so
# Claude invokes the webui-sync subagent on the next turn.
#
# Stop hooks can block completion by emitting JSON. We don't block — we just
# print an "additional context" message that the user / next turn sees.

set -e

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PENDING="$ROOT/webui/.sync-pending"

[ -s "$PENDING" ] || exit 0

# Count unique pending files.
count=$(wc -l < "$PENDING" | tr -d ' ')

# Print a non-blocking reminder to stderr (visible to Claude as system context).
cat >&2 <<EOF
[webui-sync] $count file(s) changed since last sync — invoke the webui-sync subagent to update the admin UI.
Pending list: $PENDING
EOF

exit 0
