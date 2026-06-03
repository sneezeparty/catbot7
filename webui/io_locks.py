"""Atomic JSON writes for the webui's one editable surface (the News editor).

The rest of the dashboard is read-only; this exists solely so the News editor
can rewrite config/news.json safely. Writes go to a temp file in the same
directory and are renamed into place (os.replace is atomic on POSIX), so the
bot's get_news() never reads a half-written file.
"""

import asyncio
import json
import os
from pathlib import Path

# repo root = webui/..
_ROOT = Path(__file__).resolve().parent.parent

# one lock per relative path so concurrent saves to the same file serialize
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(rel_path: str) -> asyncio.Lock:
    if rel_path not in _locks:
        _locks[rel_path] = asyncio.Lock()
    return _locks[rel_path]


async def atomic_write_json(rel_path: str, data) -> None:
    """Atomically write `data` as pretty JSON to <repo>/<rel_path>."""
    target = _ROOT / rel_path
    tmp = target.with_suffix(target.suffix + ".tmp")
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    async with _lock_for(rel_path):
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
