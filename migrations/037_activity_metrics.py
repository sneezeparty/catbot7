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

"""Feature-usage metrics for the dashboard's "Last 24 hours" panel.

The Activity page derives 24h event counts by differencing two `metric_snapshot`
rows. That only works for counters the snapshot actually stores, so this
migration widens `metric_snapshot` with per-feature lifetime sums, and adds the
five `profile` counters that had no lifetime counter at all.

--- profile: five new lifetime counters ----------------------------------

  bonus_offered          integer DEFAULT 0   bonus-cat prompts shown
  bonus_played           integer DEFAULT 0   bonus minigames actually submitted
  scratchcards_scratched integer DEFAULT 0   /scratch cards spent
  scratchcards_earned    integer DEFAULT 0   /scratch cards granted
  chaos_clicks           integer DEFAULT 0   /chaos button presses

`profile.scratchcards` is a *balance* (and is wiped at season rollover), and
`profile.bonus_catches` only counts bonus minigames WON — none of them can
answer "how many were offered / handed out / spent today". Hence the new pair
of monotonic counters for each.

NOTE: these five start at zero. There is no backfill and none is possible —
the events were never recorded. The dashboard tiles fed by them read 0 until
the bot has been running with this migration applied for a full window.

--- metric_snapshot: per-feature lifetime sums ---------------------------

27 bigint columns, each the SUM of the matching `profile` counter at snapshot
time. All DEFAULT 0 so existing rows stay valid; those rows keep reading 0,
which the dashboard's max(0, latest - previous) differencing renders as "no
activity" rather than a negative spike.

Idempotent — ADD COLUMN IF NOT EXISTS; a second run logs and exits.

Depends on migrations 022 (recap coin counters), 029 (metric_snapshot),
032 (bonus_catches), 033 (fish_caught) and 034 (scratchcards) — the aggregate
this feeds SUMs those columns. The bot probes for `total_coins_earned` before
using any of the new columns, so an un-migrated DB degrades to the old
17-column snapshot instead of erroring.

Bot MUST be stopped before running this. Run with the same env vars as bot.py:

    psql_password=... python migrations/037_activity_metrics.py
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

MARKER = REPO_ROOT / "migrations" / "037.done"
LOGFILE = REPO_ROOT / "migrations" / "037.log"

# New lifetime counters on profile — the events that had no counter at all.
PROFILE_COLUMNS = (
    "bonus_offered",
    "bonus_played",
    "scratchcards_scratched",
    "scratchcards_earned",
    "chaos_clicks",
)

# New aggregate columns on metric_snapshot, paired with the profile column each
# one sums. Deliberately duplicated from main._FEATURE_METRICS rather than
# imported: a migration is a point-in-time artifact and must keep doing what it
# did on the day it ran, even after the live list is edited.
SNAPSHOT_COLUMNS = (
    # (metric_snapshot column, profile source column)
    # minigames
    ("total_bonus_offered", "bonus_offered"),
    ("total_bonus_played", "bonus_played"),
    ("total_bonus_won", "bonus_catches"),
    ("total_scratchcards_earned", "scratchcards_earned"),
    ("total_scratchcards_scratched", "scratchcards_scratched"),
    ("total_chaos_clicks", "chaos_clicks"),
    ("total_fish_caught", "fish_caught"),
    ("total_ttt_played", "ttt_played"),
    # casino
    ("total_catslots_spins", "catslots_spins"),
    ("total_catslots_bet", "catslots_coins_bet"),
    ("total_catslots_won", "catslots_coins_won"),
    ("total_catslots_bonus_triggers", "catslots_bonus_triggers"),
    ("total_slot_spins", "slot_spins"),
    ("total_roulette_spins", "roulette_spins"),
    ("total_roulette_bet", "roulette_coins_bet"),
    ("total_roulette_won", "roulette_coins_won"),
    ("total_gambles", "gambles"),
    # coins
    ("total_coins_earned", "coins_earned"),
    ("total_job_coins_won", "job_coins_won"),
    ("total_stock_coins_earned", "stock_coins_earned"),
    ("total_stock_coins_spent", "stock_coins_spent"),
    # progression & social
    ("total_quests_completed", "quests_completed"),
    ("total_catnip_activations", "catnip_activations"),
    ("total_rain_participations", "rain_participations"),
    ("total_cats_gifted", "cats_gifted"),
    ("total_trades_completed", "trades_completed"),
    ("total_prisms_crafted", "prisms_crafted"),
)

# Source columns the snapshot aggregate reads. Probed (not created) — if one is
# missing the operator has skipped an earlier migration and needs to know before
# the bot starts writing half-empty rows.
REQUIRED_PROFILE_COLUMNS = (
    "bonus_catches",
    "fish_caught",
    "ttt_played",
    "catslots_spins",
    "catslots_coins_bet",
    "catslots_coins_won",
    "catslots_bonus_triggers",
    "slot_spins",
    "roulette_spins",
    "roulette_coins_bet",
    "roulette_coins_won",
    "gambles",
    "coins_earned",
    "job_coins_won",
    "stock_coins_earned",
    "stock_coins_spent",
    "quests_completed",
    "catnip_activations",
    "rain_participations",
    "cats_gifted",
    "trades_completed",
    "prisms_crafted",
)

PROFILE_DDL = "".join(
    f"ALTER TABLE public.profile ADD COLUMN IF NOT EXISTS {c} integer DEFAULT 0 NOT NULL;\n"
    for c in PROFILE_COLUMNS
)

SNAPSHOT_DDL = "".join(
    f"ALTER TABLE public.metric_snapshot ADD COLUMN IF NOT EXISTS {c} bigint DEFAULT 0 NOT NULL;\n"
    for c, _src in SNAPSHOT_COLUMNS
)

# Seed EVERY pre-existing snapshot row with today's lifetime totals.
#
# This matters more than it looks. The dashboard reads a 24h figure as
# latest_row - row_from_24h_ago. Left at the DEFAULT 0, the first row the bot
# writes after this migration would carry the full lifetime total while the row
# a day earlier still carried 0 — and the panel would proudly report every
# catslots spin in Cat Bot's history as having happened today. Seeding the old
# rows to the same totals makes that first difference ~0, i.e. "unknown, assume
# nothing happened", and every subsequent one is real.
#
# No bot-user exclusion here (the snapshot tick has one). The bot's own profile
# rows barely move these counters, and erring high means the first delta lands
# slightly negative and gets clamped to 0 — the safe direction. A spike would
# not be.
BACKFILL_SQL = (
    "UPDATE metric_snapshot SET "
    + ", ".join(
        f"{col} = (SELECT COALESCE(SUM({src}), 0) FROM profile)"
        for col, src in SNAPSHOT_COLUMNS
    )
)


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


async def column_exists(conn: asyncpg.Connection, table: str, column: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2",
        table,
        column,
    )
    return row is not None


async def table_exists(conn: asyncpg.Connection, table: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = $1",
        table,
    )
    return row is not None


async def main() -> int:
    if MARKER.exists():
        log(f"marker {MARKER} exists — migration already applied. Delete it to re-run.")
        return 0

    LOGFILE.write_text("", encoding="utf-8")
    log("starting migration 037_activity_metrics")

    conn = await asyncpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
    )
    try:
        if not await table_exists(conn, "metric_snapshot"):
            log("ERROR: metric_snapshot table missing — run migration 029 first. Aborting.")
            return 1

        # Prerequisite check. Missing sources mean an earlier migration was
        # skipped; the snapshot aggregate would fail on every tick.
        missing = [
            c for c in REQUIRED_PROFILE_COLUMNS
            if not await column_exists(conn, "profile", c)
        ]
        if missing:
            log(f"ERROR: profile is missing source columns {missing} — run the earlier")
            log("       migrations (022 / 032 / 033 / 034) first. Aborting, nothing changed.")
            return 1
        log(f"all {len(REQUIRED_PROFILE_COLUMNS)} source columns present")

        for col in PROFILE_COLUMNS:
            if await column_exists(conn, "profile", col):
                log(f"profile.{col} already exists, skipping")
            else:
                log(f"adding profile.{col}")
        await conn.execute(PROFILE_DDL)

        added = 0
        for col, _src in SNAPSHOT_COLUMNS:
            if await column_exists(conn, "metric_snapshot", col):
                log(f"metric_snapshot.{col} already exists, skipping")
            else:
                added += 1
        await conn.execute(SNAPSHOT_DDL)
        log(f"metric_snapshot: {added} of {len(SNAPSHOT_COLUMNS)} columns added")

        # Seed history so the first post-migration 24h delta isn't the whole
        # lifetime total. See BACKFILL_SQL for why this is not optional.
        snap_rows = await conn.fetchval("SELECT COUNT(*) FROM metric_snapshot")
        await conn.execute(BACKFILL_SQL)
        log(f"seeded {snap_rows} existing metric_snapshot row(s) with current lifetime totals")
        log("(so the first 24h delta reads ~0 instead of replaying all of history)")

        sample = await conn.fetchrow(
            "SELECT total_catslots_spins, total_coins_earned, total_bonus_won "
            "FROM metric_snapshot ORDER BY bucket_time DESC LIMIT 1"
        )
        if sample:
            log(
                f"sanity: catslots_spins={sample['total_catslots_spins']:,} "
                f"coins_earned={sample['total_coins_earned']:,} "
                f"bonus_won={sample['total_bonus_won']:,}"
            )
        log("the five NEW profile counters start at 0 — no backfill is possible for them")

        MARKER.write_text(
            json.dumps({"completed_at": time.time()}, indent=2),
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
