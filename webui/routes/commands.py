"""Read-only listing of slash commands registered on bot.tree.

Walks bot.tree.walk_commands() per request so any reload reflects immediately.
"""

import aiohttp_jinja2
import discord
from aiohttp import web

from webui import state


def _summarize(cmd) -> dict:
    is_group = isinstance(cmd, discord.app_commands.Group)
    base = {
        "name": cmd.name,
        "qualified_name": cmd.qualified_name,
        "description": getattr(cmd, "description", "") or "",
        "guild_only": bool(getattr(cmd, "guild_only", False)),
        "is_group": is_group,
        "parameters": [],
    }
    if not is_group:
        for p in getattr(cmd, "parameters", []) or []:
            base["parameters"].append({
                "name": p.name,
                "type": str(p.type).split(".")[-1] if p.type else "",
                "required": p.required,
                "description": p.description or "",
            })
    return base


async def index(request):
    bot = state.get_bot()
    rows: list[dict] = []
    groups: list[dict] = []
    if bot is not None:
        for cmd in sorted(bot.tree.walk_commands(), key=lambda c: c.qualified_name):
            if isinstance(cmd, discord.app_commands.Group):
                groups.append(_summarize(cmd))
            else:
                rows.append(_summarize(cmd))
    context_menus: list[dict] = []
    if bot is not None:
        for cmd in bot.tree.walk_commands(type=discord.AppCommandType.message):
            context_menus.append({"name": cmd.name, "kind": "message"})
        for cmd in bot.tree.walk_commands(type=discord.AppCommandType.user):
            context_menus.append({"name": cmd.name, "kind": "user"})
    return aiohttp_jinja2.render_template(
        "commands.html",
        request,
        {
            "title": "Commands",
            "active_section": "commands",
            "rows": rows,
            "groups": groups,
            "context_menus": context_menus,
            "total": len(rows) + len(groups),
        },
    )


def register(app: web.Application) -> None:
    app.router.add_get("/commands", index)
