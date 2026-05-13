"""Cat Bot admin webui.

Bound to 127.0.0.1:9445. NO AUTH — never flip the bind to 0.0.0.0; the UI
edits live game state and JSON configs without credentials.

The server lives in bot.py (not main.py) so it survives `cat!restart`.
All access to main.py / configs / DB pool goes through webui.state to
preserve lazy lookup across reloads.
"""

from webui.server import start_server  # noqa: F401
