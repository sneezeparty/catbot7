import os
from pathlib import Path

# Optional .env loader. Lines like `key=value` are pushed into os.environ
# before any reads below, so secrets can live in a gitignored project file
# instead of being exported in your shell. Existing env vars always win,
# so `voting_enabled=0 python bot.py` still overrides .env for one-offs.
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _, _value = _line.partition("=")
        os.environ.setdefault(_key.strip(), _value.strip().strip('"').strip("'"))

# discord bot token
TOKEN = os.environ["TOKEN"]

# db password for postgres
# user - cat_bot, database - cat_bot, port - default
DB_HOST = os.environ.get("psql_host", "127.0.0.1")
DB_PASS = os.environ.get("psql_password", "")
DB_PORT = int(os.environ.get("psql_port", "5432"))

#
# all the following are optional (setting to None will disable the feature)
#

# dsn of a sentry-compatible service for error logging
SENTRY_DSN = os.environ.get("sentry_dsn")

# top.gg vote webhook verification secret, setting this to None disables all voting stuff
WEBHOOK_VERIFY = os.environ.get("webhook_verify")

# top.gg modern (v1) token to post stats, commands and fetch fallback votes
TOP_GG_MODERN_TOKEN = os.environ.get("top_gg_modern_token")

# wordnik api key for /define command
WORDNIK_API_KEY = os.environ.get("wordnik_api_key")

# only post stats if server count is above this, to prevent wrong stats
MIN_SERVER_SEND = 200_000

# channel id for db backups, private extremely recommended
BACKUP_ID = int(os.environ["backup_channel_id"]) if os.environ.get("backup_channel_id") else None

# channel to store supporter images, can also be used for moderation purposes
DONOR_CHANNEL_ID = int(os.environ["donor_channel_id"]) if os.environ.get("donor_channel_id") else None

# cat bot will also log all rain uses/movements here
# cat!rain commands posted here (by the owner or an id in RAIN_AUTOMATION_IDS)
# grant rain/premium to the target user and dm them a thanks message
RAIN_CHANNEL_ID = int(os.environ["rain_channel_id"]) if os.environ.get("rain_channel_id") else None

# Discord user/webhook ids (comma- or space-separated) allowed to trigger the
# `cat!rain` fulfillment hook in RAIN_CHANNEL_ID besides the owner. Set to your
# payment-automation bot/webhook id(s); empty means owner-only.
RAIN_AUTOMATION_IDS = {int(x) for x in os.environ.get("rain_automation_ids", "").replace(",", " ").split()}

# Discord user ids (comma/space-separated) treated as economy outliers — excluded
# from the admin dashboard's "coins in circulation" total + graph so a handful of
# admin-granted/test wallets don't dwarf the real economy. Display-only: does NOT
# affect gameplay. Read by the webui and by the metric_snapshot writer.
ECONOMY_OUTLIER_USER_IDS = {int(x) for x in os.environ.get("economy_outlier_user_ids", "").replace(",", " ").split()}

# top.gg voting: /vote, the vote battlepass quest, catch-message vote button,
# webhook + vote-replay loop. On by default; set voting_enabled=0 to turn off.
# Registering votes still needs top_gg_modern_token (polling) or webhook_verify
# (webhook on port 8069). Daily catch streaks live in `user.daily_catch_streak`
# regardless of this setting.
VOTING_ENABLED = os.environ.get("voting_enabled", "1") == "1"

# Cat Bot Store (Discord native monetization). When enabled, the /store
# command registers, on_entitlement_* event handlers wire SKU ownership to
# user.premium and user.entitlements, and on_ready reconciles state via the
# Discord API. SKUs themselves live in config/store.json — create them in the
# Discord Developer Portal under Monetization first, then add their ids there.
STORE_ENABLED = os.environ.get("store_enabled", "0") == "1"

# Optional invite to the operator's support / community Discord. Used wherever
# the upstream bot used to link to its own support server. Empty string = the
# link or button is omitted entirely from those surfaces.
SUPPORT_INVITE = os.environ.get("support_invite", "")

# Bot operator's Discord user ID — the only account allowed to run `cat!*`
# backdoors (cat!restart, cat!eval, cat!news, cat!custom, cat!print, cat!sweep,
# cat!rain). Auto-overridden in on_ready() from the Discord application's
# team-owner / owner. This value is the fallback that's live in the brief
# window between startup (or `cat!restart`) and on_ready completing. Defaults to
# 0 (fail-closed: authorizes nobody) — set owner_id in .env to your Discord id.
OWNER_ID = int(os.environ.get("owner_id", "0"))
