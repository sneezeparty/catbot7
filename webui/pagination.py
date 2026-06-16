"""Tiny pagination helper shared by every paginated dashboard page.

Each route builds a `pager` context dict (via `make_pager`) that the
`_pagination.html` partial renders as prev/next + page indicator.

Pages are 1-indexed. `per_page` is fixed per route — there's no UI for
changing it. The total-count flavor ("Page N of M") is used when the route
can cheaply compute a `total`; otherwise the look-ahead flavor
("Page N · Next? yes/no") is used.
"""

from urllib.parse import urlencode


def parse_page(request, key: str = "page") -> int:
    """Read `?<key>=N` from the request, clamp to >= 1."""
    raw = request.query.get(key, "1")
    try:
        page = int(raw)
    except (TypeError, ValueError):
        page = 1
    return page if page >= 1 else 1


def _build_qs(base_path: str, params: dict, page_key: str, page: int) -> str:
    kept = {k: v for k, v in params.items() if v not in (None, "")}
    kept[page_key] = page
    return f"{base_path}?{urlencode(kept)}"


def make_pager(
    request,
    *,
    page: int,
    per_page: int,
    total: int | None = None,
    has_next: bool | None = None,
    page_key: str = "page",
    base_path: str | None = None,
    params: dict | None = None,
    target: str | None = None,
) -> dict:
    """Build the dict the `_pagination.html` partial expects.

    Pass exactly one of `total` (lets the partial show "Page X of Y") or
    `has_next` (look-ahead flavor, used when computing a total would be
    too expensive).

    `target` is the CSS selector of the DOM element that wraps both the
    paginator and the rows it pages through (e.g. `"#pager-server"`).
    When set, the partial emits htmx attributes so clicking prev/next
    does an in-place partial swap instead of a full page navigation —
    which keeps scroll position. The `href` is still present as a JS-off
    fallback.
    """
    base_path = base_path or request.path
    params = params or {}
    total_pages = None
    if total is not None:
        total_pages = max(1, (total + per_page - 1) // per_page)
        has_next = page < total_pages
    if has_next is None:
        has_next = False
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": has_next,
        "prev_href": _build_qs(base_path, params, page_key, max(1, page - 1)),
        "next_href": _build_qs(base_path, params, page_key, page + 1),
        "first_href": _build_qs(base_path, params, page_key, 1),
        "last_href": _build_qs(base_path, params, page_key, total_pages) if total_pages else None,
        "target": target,
    }
