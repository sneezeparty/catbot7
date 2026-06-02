# Cat Bot - A Discord bot about catching cats.
# Copyright (C) 2026 Lia Milenakos & Cat Bot Contributors
# Copyright (C) 2026 sneezeparty
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Remap stored catnip perk indices after deleting the "Time Manipulator" perk.

Catnip perks are stored on `profile` as strings shaped `"{rarity}_{index}"`
where `index` is the 1-based POSITION of the perk in `config/catnip.json ->
perks` (see get_perks/select_perk/begin_bounties in main.py). They live in:

  - profile.perks   (varchar[] — the player's selected perk history)
  - profile.perk1   (varchar  — currently-offered / selected perk slot)
  - profile.perk2   (varchar)
  - profile.perk3   (varchar)

0.6.7 removes the long-retired `timer_add` "Time Manipulator" perk, which sat
at 1-based position 11. Deleting its array element shifts every later perk
down by one, so this migration rewrites stored references to match the new
list:

  index <  11  ->  unchanged
  index == 11  ->  timer_add itself. It was never obtainable (weight 0, all
                   values 0, so get_perks always skipped it via effect==0), so
                   this should match ZERO rows. If any are found they are
                   cleared (scalar -> '', array element dropped) and logged
                   loudly, since the perk no longer exists to point at.
  index >  11  ->  decremented by one (12->11, 13->12, ... 17->16)

NOT idempotent by computation — re-running would decrement a second time.
The NNN.done marker is the only guard; do not delete it unless you also
restore the pre-migration data. Bot MUST be stopped before running. Run with
the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/020_remove_timer_add.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import asyncpg  # noqa: E402

import config  # noqa: E402

MARKER = REPO_ROOT / "migrations" / "020.done"
LOGFILE = REPO_ROOT / "migrations" / "020.log"

# 1-based list position of the perk being removed.
REMOVED_INDEX = 11


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def remap_one(perk: str) -> tuple[str | None, bool]:
    """Remap a single stored perk string.

    Returns (new_value, changed). new_value is None to signal "drop this entry"
    (only happens for an orphaned reference to the removed perk).
    """
    if not perk:
        return perk, False
    parts = perk.split("_")
    if len(parts) != 2:
        # Shapes we don't recognise are left untouched.
        return perk, False
    try:
        rarity = int(parts[0])
        idx = int(parts[1])
    except ValueError:
        return perk, False

    if idx == REMOVED_INDEX:
        # An orphaned reference to the removed perk — should not exist.
        return None, True
    if idx > REMOVED_INDEX:
        return f"{rarity}_{idx - 1}", True
    return perk, False


def remap_scalar(perk: str, profile_id: int, slot: str) -> tuple[str, bool]:
    new, changed = remap_one(perk)
    if not changed:
        return perk, False
    if new is None:
        log(f"  WARNING profile id={profile_id} {slot} held removed perk {perk!r} -> cleared")
        return "", True
    return new, True


def remap_array(perks: list[str], profile_id: int) -> tuple[list[str], bool]:
    out: list[str] = []
    changed = False
    for perk in perks:
        new, did = remap_one(perk)
        if did:
            changed = True
            if new is None:
                log(f"  WARNING profile id={profile_id} perks[] held removed perk {perk!r} -> dropped")
                continue
            out.append(new)
        else:
            out.append(perk)
    return out, changed


async def main() -> int:
    if MARKER.exists():
        log(f"marker {MARKER} exists — migration already applied. Delete it to re-run.")
        return 0

    LOGFILE.write_text("", encoding="utf-8")
    log("starting migration 020_remove_timer_add")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        rows = await conn.fetch(
            "SELECT id, perks, perk1, perk2, perk3 FROM profile "
            "WHERE perks <> '{}' OR perk1 <> '' OR perk2 <> '' OR perk3 <> ''"
        )
        log(f"scanning {len(rows)} profiles with non-empty perk fields")

        updated = 0
        async with conn.transaction():
            for row in rows:
                pid = row["id"]
                arr = list(row["perks"] or [])
                new_arr, arr_changed = remap_array(arr, pid)

                new1, c1 = remap_scalar(row["perk1"] or "", pid, "perk1")
                new2, c2 = remap_scalar(row["perk2"] or "", pid, "perk2")
                new3, c3 = remap_scalar(row["perk3"] or "", pid, "perk3")

                if not (arr_changed or c1 or c2 or c3):
                    continue

                await conn.execute(
                    "UPDATE profile SET perks = $2, perk1 = $3, perk2 = $4, perk3 = $5 WHERE id = $1",
                    pid,
                    new_arr,
                    new1,
                    new2,
                    new3,
                )
                updated += 1

        log(f"remapped perk indices on {updated} profiles")

        MARKER.write_text(
            json.dumps({"completed_at": time.time(), "profiles_updated": updated}, indent=2),
            encoding="utf-8",
        )
        log(f"DONE. marker at {MARKER}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    if not os.environ.get("psql_password"):
        print("ERROR: psql_password env var required (see bot.py)", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main()))
