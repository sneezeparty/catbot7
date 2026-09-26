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

"""Add the Hobo and Doll rarities, and remap everything stored by position.

Hobo goes in right after Trash and Doll right after Ultimate, so every rarity
from Legendary onward moves up one or two slots in main.cattypes. Three
columns store rarities by POSITION rather than by name, and would silently
point at the wrong cat without a remap:

  - profile.cat_auras            char(1)[], 1-based, one slot per cattype.
                                 Unmapped, an eGirl rainbow aura (permanent)
                                 would become a Doll rainbow.
  - profile.weekly_cattypes      smallint[] of 0-based cattypes indices, for
                                 the "catch N different types" weekly quest.
  - profile.quests_variety_types smallint[] of 0-based indices, for the
                                 daily variety5 challenge quest.

Each is rebuilt by NAME: old position -> type name -> new position. New
rarities get an empty aura slot. Everything else keyed by name
(discovered_cats, cat_milestone_base, bounty_type_*, rarest_fish, ...) needs
nothing.

Also adds profile."cat_Hobo" / "cat_Doll" and bumps cat_auras' column default
to the new length.

Must run BEFORE the bot starts on the new code, with the bot stopped: the new
code reads cat_auras at the new positions and would render the old layout
wrong in the meantime. Everything runs in one transaction, so a failure
leaves the database untouched. Run with the same env vars as bot.py:

    TOKEN=... psql_password=... python migrations/041_doll_hobo_cats.py
"""

from __future__ import annotations

import ast
import asyncio
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import asyncpg  # noqa: E402

import config  # noqa: E402

MARKER = REPO_ROOT / "migrations" / "041.done"
LOGFILE = REPO_ROOT / "migrations" / "041.log"

# main.type_dict's key order before this migration — the layout existing rows
# were written in. Frozen here on purpose: it's the thing being migrated FROM.
OLD_CATTYPES = [
    "Fine", "Nice", "Good", "Rare", "Wild", "Baby", "Shadow", "Epic", "Sus",
    "Brave", "Rickroll", "Reverse", "Superior", "Trash", "Legendary", "Mythic",
    "8bit", "Corrupt", "Professor", "Divine", "Real", "Terminator", "Ultimate",
    "eGirl",
]  # fmt: skip

NEW_COLUMNS = ["cat_Hobo", "cat_Doll"]


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_cattypes() -> list[str]:
    """main.py's type_dict key order, read from the AST (no bot import)."""
    tree = ast.parse((REPO_ROOT / "main.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "type_dict" for t in node.targets):
            return list(ast.literal_eval(node.value).keys())
    raise RuntimeError("could not find a module-level type_dict in main.py")


def check_number_is_ours() -> None:
    """Refuse to run if another migration already owns this number (see 038)."""
    number = os.path.basename(__file__).split("_")[0]
    siblings = sorted(
        p for p in os.listdir(REPO_ROOT / "migrations")
        if p.startswith(f"{number}_") and p.endswith(".py") and p != os.path.basename(__file__)
    )
    if siblings:
        raise SystemExit(f"migration number {number} is already used by {', '.join(siblings)} — renumber this script")


def aura_remap_sql(new: list[str]) -> str:
    """cat_auras rebuilt slot by slot in the new order (PG arrays are 1-based)."""
    slots = []
    for t in new:
        if t in OLD_CATTYPES:
            slots.append(f"COALESCE(cat_auras[{OLD_CATTYPES.index(t) + 1}], ' ')")
        else:
            slots.append("' '")
    # Rows already at the new length were migrated (or created by new code):
    # leave them alone so a re-run can't shift them twice.
    return f"""
        UPDATE profile
        SET cat_auras = ARRAY[{", ".join(slots)}]::character(1)[]
        WHERE cat_auras IS NOT NULL
          AND COALESCE(array_length(cat_auras, 1), 0) <= {len(OLD_CATTYPES)}
    """


def index_remap_sql(column: str, new: list[str]) -> str:
    """A smallint[] of 0-based indices, each mapped old -> new, order kept."""
    whens = " ".join(f"WHEN {i} THEN {new.index(t)}" for i, t in enumerate(OLD_CATTYPES) if new.index(t) != i)
    return f"""
        UPDATE profile
        SET {column} = ARRAY(
            SELECT (CASE x {whens} ELSE x END)::smallint
            FROM unnest({column}) WITH ORDINALITY AS u(x, n)
            ORDER BY n
        )
        WHERE cardinality({column}) > 0
    """


async def column_exists(conn: asyncpg.Connection, column: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'profile' AND column_name = $1",
        column,
    )
    return row is not None


async def main() -> int:
    check_number_is_ours()

    if MARKER.exists():
        log(f"marker {MARKER} exists — migration already applied. Delete it to re-run.")
        return 0

    LOGFILE.write_text("", encoding="utf-8")
    log("starting migration 041_doll_hobo_cats")

    new = load_cattypes()
    log(f"{len(new)} cat types in main.type_dict: {', '.join(new)}")
    missing = [t for t in OLD_CATTYPES if t not in new]
    if missing:
        raise SystemExit(f"main.type_dict is missing old rarities {missing} — refusing to remap")
    added = [t for t in new if t not in OLD_CATTYPES]
    if sorted(added) != ["Doll", "Hobo"]:
        raise SystemExit(f"expected exactly Doll and Hobo to be new, got {added} — refusing to remap")
    moved = {t: (OLD_CATTYPES.index(t), new.index(t)) for t in OLD_CATTYPES if OLD_CATTYPES.index(t) != new.index(t)}
    log("positions moving: " + ", ".join(f"{t} {a}->{b}" for t, (a, b) in moved.items()))

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        async with conn.transaction():
            for col in NEW_COLUMNS:
                if await column_exists(conn, col):
                    log(f"profile.{col} already exists, skipping ADD")
                    continue
                log(f"adding profile.{col}")
                await conn.execute(f'ALTER TABLE profile ADD COLUMN "{col}" integer DEFAULT 0')

            status = await conn.execute(aura_remap_sql(new))
            log(f"cat_auras remapped: {status}")
            await conn.execute(
                f"ALTER TABLE profile ALTER COLUMN cat_auras SET DEFAULT array_fill(' '::character(1), ARRAY[{len(new)}])"
            )
            log(f"cat_auras default now {len(new)} slots")

            for col in ("weekly_cattypes", "quests_variety_types"):
                status = await conn.execute(index_remap_sql(col, new))
                log(f"{col} remapped: {status}")

            # sanity: no eGirl rainbow may have landed on Doll's slot
            doll_r = await conn.fetchval(f"SELECT count(*) FROM profile WHERE cat_auras[{new.index('Doll') + 1}] <> ' '")
            if doll_r:
                raise RuntimeError(f"{doll_r} profiles have a Doll aura right after the remap — aborting (rolled back)")
    finally:
        await conn.close()

    MARKER.write_text(time.strftime("%Y-%m-%d %H:%M:%S") + "\n", encoding="utf-8")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
