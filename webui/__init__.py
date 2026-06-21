"""Cat Bot admin webui.

Bound to 192.168.1.155:9445 (LAN-only single-interface bind). NO AUTH — never
flip the bind to 0.0.0.0. Two sanctioned write surfaces exist (News editor,
Announcements broadcaster); everything else is GET-only.

The server lives in bot.py (not main.py) so it survives `cat!restart`.
All access to main.py / DB pool goes through webui.state to preserve lazy
lookup across reloads.
"""

from webui.server import start_server  # noqa: F401
