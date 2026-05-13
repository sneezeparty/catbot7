#!/usr/bin/env bash
# Stop hook: surfaces reminders for any pending sync queues (webui-sync,
# design-docs-sync, changelog-sync) so Claude invokes them on the next turn.
#
# Stop hooks can block completion by emitting JSON. We don't block — we just
# print "additional context" messages that the next turn sees.

set -e

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

emit() {
  local label="$1" file="$2" agent="$3"
  [ -s "$file" ] || return 0
  local count
  count=$(wc -l < "$file" | tr -d ' ')
  cat >&2 <<EOF
[$label] $count file(s) changed since last sync — invoke the $agent subagent.
Pending list: $file
EOF
}

emit "webui-sync"        "$ROOT/webui/.sync-pending"        "webui-sync"
emit "design-docs-sync"  "$ROOT/docs/design/.sync-pending"  "design-docs-sync"
emit "changelog-sync"    "$ROOT/docs/.changelog-pending"    "changelog-sync"

exit 0
