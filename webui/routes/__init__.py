"""Route registration. Add new sections here."""

from aiohttp import web

from webui.routes import (
    actions,
    battlepass,
    catnip,
    channel_table,
    commands,
    dashboard,
    jobs,
    jobs_help,
    order_table,
    prism_table,
    profile_table,
    server_table,
    tuning,
    user_table,
)


def register(app: web.Application) -> None:
    dashboard.register(app)
    tuning.register(app)
    battlepass.register(app)
    catnip.register(app)
    jobs.register(app)
    jobs_help.register(app)
    commands.register(app)
    server_table.register(app)
    channel_table.register(app)
    profile_table.register(app)
    user_table.register(app)
    prism_table.register(app)
    order_table.register(app)
    actions.register(app)
