"""Route registration. Read-only dashboard + DB browsers — no mutations."""

from aiohttp import web

from webui.routes import (
    activity,
    activity_server,
    activity_user,
    channel_table,
    commands,
    dashboard,
    economy,
    leaderboards,
    news,
    order_table,
    prism_table,
    profile_table,
    server_table,
    user_table,
)


def register(app: web.Application) -> None:
    # Insights
    dashboard.register(app)
    activity.register(app)
    activity_server.register(app)
    activity_user.register(app)
    economy.register(app)
    leaderboards.register(app)
    commands.register(app)
    # Database (read-only browsers)
    server_table.register(app)
    channel_table.register(app)
    profile_table.register(app)
    user_table.register(app)
    prism_table.register(app)
    order_table.register(app)
    # Manage (the one editable section)
    news.register(app)
