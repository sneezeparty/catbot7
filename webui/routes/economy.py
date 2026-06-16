"""Economy & market: coins in circulation, gambling volume, stock prices.

Read-only. Price-history charts are bucketed hourly in SQL and emitted as JSON
for Chart.js (no matplotlib in the request path).
"""

import time

import aiohttp_jinja2
from aiohttp import web

from webui import names, state

# ticker -> display name (mirrors main.stock_data; hardcoded so the webui has
# no import-time dependency on main, which reloads on cat!restart)
TICKERS = [
    ("PRSM", "Prisms"),
    ("CTNP", "Catnip"),
    ("PASS", "Cattlepass"),
    ("ACHS", "Achievements"),
    ("RAIN", "Rain"),
]

PRICE_LOOKBACK_DAYS = 3
BUCKET_SECONDS = 3600  # hourly


async def index(request):
    pool = state.get_pool()
    now = int(time.time())

    # Only positive-by-construction metrics. Lifetime "earned" counters
    # (coins_earned, stock_coins_earned/spent) are NOT shown — the bot bumps
    # them in both directions and rebaselines at season rollover, so summed
    # across players they can go negative and read like a bug on a dashboard.
    econ = {
        "coins": 0,
        "roulette_won": 0, "roulette_bet": 0,
        "catslots_won": 0, "catslots_bet": 0,
    }
    order_notional = 0        # SUM(quantity*price) of open orders (always >= 0)
    prices: list = []         # [(ticker, name, price)]
    series: dict = {}         # ticker -> {"labels": [...], "data": [...]}
    by_ticker: list = []      # order-book buy/sell counts

    bot_id = state.bot_user_id_or_zero()

    if pool is not None:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                  COALESCE(SUM(GREATEST(coins, 0)), 0)  AS coins,
                  COALESCE(SUM(roulette_coins_won), 0)  AS roulette_won,
                  COALESCE(SUM(roulette_coins_bet), 0)  AS roulette_bet,
                  COALESCE(SUM(catslots_coins_won), 0)  AS catslots_won,
                  COALESCE(SUM(catslots_coins_bet), 0)  AS catslots_bet
                FROM profile
                WHERE user_id <> $1
                """,
                bot_id,
            )
            econ = {k: int(row[k] or 0) for k in econ}
            # order.user_id is profile.id, not Discord user_id, so the bot
            # exclusion needs to subselect the bot's profile rows.
            order_notional = int(
                await conn.fetchval(
                    'SELECT COALESCE(SUM(quantity * price), 0) FROM "order" '
                    'WHERE user_id NOT IN (SELECT id FROM profile WHERE user_id = $1)',
                    bot_id,
                ) or 0
            )

            latest = await conn.fetch(
                "SELECT DISTINCT ON (ticker) ticker, price, time "
                "FROM pricehistory ORDER BY ticker, time DESC"
            )
            price_by_ticker = {r["ticker"]: int(r["price"]) for r in latest}
            prices = [(t, name, price_by_ticker.get(t)) for t, name in TICKERS]

            hist = await conn.fetch(
                """
                SELECT ticker, (time / $2) * $2 AS bucket_ts, AVG(price)::float AS price
                FROM pricehistory
                WHERE time > $1
                GROUP BY ticker, bucket_ts
                ORDER BY ticker, bucket_ts
                """,
                now - PRICE_LOOKBACK_DAYS * 86400, BUCKET_SECONDS,
            )
            import datetime
            for r in hist:
                t = r["ticker"]
                s = series.setdefault(t, {"labels": [], "data": []})
                label = datetime.datetime.fromtimestamp(
                    int(r["bucket_ts"]), datetime.timezone.utc
                ).strftime("%m-%d %H:00")
                s["labels"].append(label)
                s["data"].append(round(float(r["price"]), 2))

            by_ticker = await conn.fetch(
                'SELECT ticker, '
                "  COUNT(*) FILTER (WHERE type_buy)     AS buy_count, "
                "  COUNT(*) FILTER (WHERE NOT type_buy)  AS sell_count "
                'FROM "order" '
                'WHERE user_id NOT IN (SELECT id FROM profile WHERE user_id = $1) '
                'GROUP BY ticker ORDER BY ticker',
                bot_id,
            )

    await names.refresh_guild_name_cache()
    return aiohttp_jinja2.render_template(
        "economy.html",
        request,
        {
            "title": "Economy",
            "active_section": "economy",
            "econ": econ,
            "order_notional": order_notional,
            "prices": prices,
            "series": series,
            "by_ticker": by_ticker,
            "tickers": TICKERS,
        },
    )


def register(app: web.Application) -> None:
    app.router.add_get("/economy", index)
