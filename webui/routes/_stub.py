"""Helper: render a simple coming-soon page so unfinished sections don't 404."""

import aiohttp_jinja2


def stub(section_name: str, label: str):
    async def handler(request):
        return aiohttp_jinja2.render_template(
            "stub.html",
            request,
            {"title": label, "active_section": section_name, "label": label},
        )

    return handler
