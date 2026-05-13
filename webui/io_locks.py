"""Per-file asyncio locks + atomic JSON writes."""

import asyncio
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_locks: dict[str, asyncio.Lock] = {}


def lock_for(path: str) -> asyncio.Lock:
    if path not in _locks:
        _locks[path] = asyncio.Lock()
    return _locks[path]


async def atomic_write_json(rel_path: str, data) -> None:
    """Write JSON to {rel_path}.tmp then os.replace onto rel_path.

    Atomic on POSIX. Caller must hold lock_for(rel_path).
    """
    target = REPO_ROOT / rel_path
    tmp = target.with_suffix(target.suffix + ".tmp")
    encoded = json.dumps(data, ensure_ascii=False, indent=2)
    tmp.write_text(encoded, encoding="utf-8")
    os.replace(tmp, target)


def read_json(rel_path: str):
    target = REPO_ROOT / rel_path
    return json.loads(target.read_text(encoding="utf-8"))
