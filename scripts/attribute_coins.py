#!/usr/bin/env python3
"""Attribute large coin balances to their source.

Read-only. Connects to the same Postgres the bot uses (cat_bot/cat_bot on
config.DB_HOST:DB_PORT, password from .env or the psql_password env var) and,
for every profile above a coin threshold, breaks the balance down by faucet
using the counters the season-recap already maintains:

    roulette_net  = roulette_coins_won - roulette_coins_bet
    catslots_net  = catslots_coins_won + catslots_bonus_coins_won - catslots_coins_bet
    stock_net     = stock_coins_earned - stock_coins_spent
    other_earned  = coins_earned - (roulette_won + catslots_won + catslots_bonus_won + stock_earned)
                    (i.e. jobs / packs / rain / quests)

Counters are LIFETIME, not season-scoped, so magnitudes can exceed the current
(this-season) `coins` balance — but the dominant term still reveals the
mechanism. If stock_net dominates, use the printed profile id to inspect
PortfolioHistory (WHERE user_id = <that id>).

Usage:
    python scripts/attribute_coins.py [threshold]

`threshold` defaults to 100000000 (100M). Run from the repo root.
"""

import asyncio
import os
import sys
from pathlib import Path

import asyncpg

# --- resolve DB params the same way config.py does, WITHOUT importing config
# (config.py hard-requires TOKEN; this diagnostic shouldn't need the bot token).
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _, _value = _line.partition("=")
        os.environ.setdefault(_key.strip(), _value.strip().strip('"').strip("'"))

DB_HOST = os.environ.get("psql_host", "127.0.0.1")
DB_PASS = os.environ.get("psql_password", "")
DB_PORT = int(os.environ.get("psql_port", "5432"))

# Faucet counters we want, if the migrations that add them are present.
COUNTERS = [
    "coins_earned",
    "roulette_coins_won", "roulette_coins_bet",
    "catslots_coins_won", "catslots_bonus_coins_won", "catslots_coins_bet",
    "stock_coins_earned", "stock_coins_spent",
    "job_coins_won",
]


def _fmt(n: int) -> str:
    return f"{int(n):+,}"


async def main() -> None:
    threshold = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000_000

    print(f"connecting to cat_bot@{DB_HOST}:{DB_PORT}/cat_bot …")
    conn = await asyncpg.connect(
        user="cat_bot", password=DB_PASS, database="cat_bot",
        host=DB_HOST, port=DB_PORT,
    )
    try:
        # Which counters actually exist on this deployment's profile table?
        present = {
            r["column_name"]
            for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'profile'"
            )
        }
        missing = [c for c in COUNTERS if c not in present]
        if missing:
            print(f"note: absent columns treated as 0 -> {missing}\n")

        select = ["id", "user_id", "guild_id", "COALESCE(coins,0) AS coins"]
        for c in COUNTERS:
            select.append(f"COALESCE({c},0) AS {c}" if c in present else f"0 AS {c}")

        rows = await conn.fetch(
            f"SELECT {', '.join(select)} FROM profile "
            f"WHERE COALESCE(coins,0) > $1 ORDER BY coins DESC LIMIT 25",
            threshold,
        )
        if not rows:
            print(f"no profiles above {threshold:,} coins; showing top 10 overall:\n")
            rows = await conn.fetch(
                f"SELECT {', '.join(select)} FROM profile ORDER BY coins DESC LIMIT 10"
            )
    finally:
        await conn.close()

    print(f"{len(rows)} profile(s):\n")
    for r in rows:
        roulette_net = r["roulette_coins_won"] - r["roulette_coins_bet"]
        catslots_net = (
            r["catslots_coins_won"] + r["catslots_bonus_coins_won"] - r["catslots_coins_bet"]
        )
        stock_net = r["stock_coins_earned"] - r["stock_coins_spent"]
        other_earned = (
            r["coins_earned"]
            - r["roulette_coins_won"]
            - r["catslots_coins_won"]
            - r["catslots_bonus_coins_won"]
            - r["stock_coins_earned"]
        )
        sources = {
            "roulette": roulette_net,
            "catslots": catslots_net,
            "stocks": stock_net,
            "other (jobs/packs/rain/quests)": other_earned,
        }
        top = max(sources, key=lambda k: sources[k])

        print(f"profile id={r['id']}  user_id={r['user_id']}  guild_id={r['guild_id']}")
        print(f"  coins now (this season):        {r['coins']:,}")
        print(f"  --- lifetime net by source ---")
        print(f"  roulette (won-bet):             {_fmt(roulette_net)}")
        print(f"  catslots (base+bonus-bet):      {_fmt(catslots_net)}")
        print(f"  stocks (earned-spent):          {_fmt(stock_net)}")
        print(f"  other earned (jobs/packs/rain): {_fmt(other_earned)}")
        print(f"    (of which job_coins_won:      {_fmt(r['job_coins_won'])})")
        print(f"  >> dominant source: {top}")
        if top == "stocks":
            print(f"     inspect trades: SELECT * FROM portfoliohistory WHERE user_id = {r['id']} ORDER BY time DESC;")
        print()


if __name__ == "__main__":
    asyncio.run(main())
