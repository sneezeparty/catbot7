import os

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
# cat!rain commands here can be used without author check and will dm reciever a thanks message
RAIN_CHANNEL_ID = int(os.environ["rain_channel_id"]) if os.environ.get("rain_channel_id") else None

# Voting is permanently retired on this self-hosted instance. The flag is kept
# so the gated /vote command + top.gg webhook + vote-replay loop can be flipped
# back on without code surgery if you ever decide to re-list. Daily catch
# streaks live in `user.daily_catch_streak` regardless of this setting.
VOTING_ENABLED = os.environ.get("voting_enabled", "0") == "1"
