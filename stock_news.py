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

"""Per-ticker headline bank for the simulated stock market.

`pick_headline(ticker, event_type, impulse_pct)` returns one of the templated
strings for the given (ticker, event_type) bucket. Event types map to:

  - 'earnings_scheduled' — written when scheduling a future earnings event.
    Hidden in the news feed until the announce window opens (the event row's
    `time` field controls visibility).
  - 'earnings'           — the *fired* earnings impulse. Sign picks pos / neg.
  - 'surprise'           — unannounced one-tick move. Sign picks pos / neg.
  - 'crash'              — market-wide sharp drop. `ticker` should be None.
  - 'boom'               — market-wide sharp rally. `ticker` should be None.
  - 'dividend'           — dividend payout posted from `wait_and_do_stock`.

Templates are selected with `random.choice` against the global RNG — we don't
need determinism here, so callers don't need to thread a seed. If a ticker
doesn't have a per-ticker bank for an event type, we fall back to a generic
"_GENERIC" bank.
"""

from __future__ import annotations

import random


# ---------------------------------------------------------------------------
# Earnings — scheduled (pre-announce; hidden until the window opens)
# ---------------------------------------------------------------------------
_EARNINGS_SCHEDULED = {
    "PRSM": [
        "Prism Corp earnings on the calendar",
        "PRSM expected to report soon",
    ],
    "CTNP": [
        "Catnip Holdings prepping quarterly numbers",
        "CTNP analysts brace for the report",
    ],
    "PASS": [
        "Battlepass Inc. heading into earnings",
        "PASS shareholders await the next print",
    ],
    "ACHS": [
        "Achievements Co. earnings looming",
        "ACHS scheduled to report — guidance unclear",
    ],
    "RAIN": [
        "Rain Holdings forecast to report",
        "RAIN earnings on deck",
    ],
    "_GENERIC": [
        "{ticker} earnings on the calendar",
        "{ticker} expected to report next quarter",
    ],
}

# When the row crosses into the announce window we relabel the headline to
# something the news feed will actually surface. Identical structure to
# scheduled but louder.
_EARNINGS_ANNOUNCED = {
    "PRSM": [
        "PRSM reporting in 24h — magnitude undisclosed",
        "Prism Corp earnings tomorrow",
    ],
    "CTNP": [
        "CTNP earnings tomorrow",
        "Catnip Holdings reports in 24h",
    ],
    "PASS": [
        "PASS earnings tomorrow",
        "Battlepass Inc. reports tomorrow",
    ],
    "ACHS": [
        "ACHS earnings tomorrow — guidance: vibes",
        "Achievements Co. reports in 24h",
    ],
    "RAIN": [
        "RAIN earnings tomorrow",
        "Rain Holdings reports in 24h",
    ],
    "_GENERIC": [
        "{ticker} earnings tomorrow",
        "{ticker} reporting in 24h",
    ],
}

# ---------------------------------------------------------------------------
# Earnings — fired (split on sign)
# ---------------------------------------------------------------------------
_EARNINGS_FIRED_POS = {
    "PRSM": [
        "PRSM beats estimates — prism yields up across the board",
        "Prism Corp posts blowout quarter, analysts upgrade to mrrrp",
        "PRSM earnings: \"more rainbows than ever,\" CEO purrs",
    ],
    "CTNP": [
        "CTNP reports surplus catnip, demand outstrips supply",
        "Catnip Holdings beats — \"every cat is high,\" exec says",
        "CTNP earnings: margins fat as a winter cat",
    ],
    "PASS": [
        "Battlepass Inc. beats: subscriber count surges",
        "PASS reports record season-completion rates",
        "Battlepass Inc. raises guidance after blowout numbers",
    ],
    "ACHS": [
        "ACHS beats: achievement unlocks at all-time high",
        "Achievements Co. posts record print, shareholders unlock 🏆",
        "ACHS earnings: \"we ran out of trophies,\" filing notes",
    ],
    "RAIN": [
        "RAIN soaks the street — usage up double digits",
        "Rain Holdings beats, downpour minutes up sharply",
        "RAIN earnings: forecast remains stormy in a good way",
    ],
    "_GENERIC": [
        "{ticker} beats earnings expectations",
        "{ticker} posts strong quarter — shareholders rejoice",
    ],
}

_EARNINGS_FIRED_NEG = {
    "PRSM": [
        "PRSM misses: prism output flatlines, nobody knows why",
        "Prism Corp earnings disappointment, prism per cat in decline",
        "PRSM analysts: \"the spectrum has dimmed\"",
    ],
    "CTNP": [
        "CTNP misses on guidance, catnip glut weighs on margins",
        "Catnip Holdings: \"cats just aren't into it anymore\"",
        "CTNP earnings whiff, inventory write-down looms",
    ],
    "PASS": [
        "PASS misses — subscriber churn higher than expected",
        "Battlepass Inc. earnings miss, retention numbers ugly",
        "PASS guidance cut, \"too many tiers, not enough hours\"",
    ],
    "ACHS": [
        "ACHS misses: nobody is unlocking anything anymore",
        "Achievements Co. earnings flop, trophy demand softens",
        "ACHS guidance trimmed, \"the meta has shifted\"",
    ],
    "RAIN": [
        "RAIN misses — clear-sky forecast spooks shareholders",
        "Rain Holdings disappoints, downpour bookings down",
        "RAIN earnings whiff, drought concerns linger",
    ],
    "_GENERIC": [
        "{ticker} misses earnings expectations",
        "{ticker} disappoints, guidance cut",
    ],
}

# ---------------------------------------------------------------------------
# Surprise (unannounced one-tick moves)
# ---------------------------------------------------------------------------
_SURPRISE_POS = {
    "PRSM": [
        "rumor of a prism shortage drives PRSM higher",
        "viral catch streak boosts prism demand, PRSM rallies",
        "PRSM up sharply on unconfirmed crafting buff",
    ],
    "CTNP": [
        "catnip shortage drives CTNP demand",
        "CTNP spikes — \"new strain just dropped\"",
        "unnamed cat eats entire shipment, CTNP rallies on scarcity",
    ],
    "PASS": [
        "PASS jumps after a season-extension rumor",
        "battlepass tier leak boosts PASS",
        "PASS pops on unconfirmed XP buff chatter",
    ],
    "ACHS": [
        "ACHS climbs on rumor of new achievements next patch",
        "viral trophy unlock thread sends ACHS higher",
        "ACHS up sharply, trophy hunters bid",
    ],
    "RAIN": [
        "a single cat caught all the rain today, RAIN limit-up",
        "RAIN rallies — downpour bookings spike",
        "freak weather event drives RAIN higher",
    ],
    "_GENERIC": [
        "{ticker} rallies on unconfirmed report",
        "{ticker} climbs sharply, no clear catalyst",
    ],
}

_SURPRISE_NEG = {
    "PRSM": [
        "PRSM slips after a crafting nerf rumor",
        "prism supply glut hits PRSM",
        "PRSM down on patch-notes leak",
    ],
    "CTNP": [
        "CTNP drops — \"the cats are sober now\"",
        "catnip oversupply weighs on CTNP",
        "CTNP slips on health-warning chatter",
    ],
    "PASS": [
        "PASS slides on subscriber-churn rumor",
        "battlepass fatigue meme hits PASS",
        "PASS down sharply, season-burnout posts trending",
    ],
    "ACHS": [
        "ACHS dips, trophy fatigue setting in",
        "ACHS drops after a leaked nerf to ach XP",
        "ACHS slides — unlocks reportedly slowing",
    ],
    "RAIN": [
        "clear skies forecast hits RAIN",
        "RAIN drops on drought speculation",
        "RAIN slides after a viral \"dry season\" thread",
    ],
    "_GENERIC": [
        "{ticker} slides on no clear catalyst",
        "{ticker} drops sharply, traders blame vibes",
    ],
}

# ---------------------------------------------------------------------------
# Crash / Boom (market-wide; ticker is None when posted)
# ---------------------------------------------------------------------------
_CRASH = [
    "market in freefall — analysts blame the bakery",
    "every ticker red, cat bot stares into the abyss",
    "🚨 MARKET CRASH — every ticker in freefall",
    "panic selling across all tickers, no bottom in sight",
    "circuit breakers tripped (we don't have those), market dumps",
    "crash: \"the cats are not okay\" — anonymous analyst",
]

_BOOM = [
    "mystery rally — all five stocks up double digits, no one knows why",
    "🎉 MARKET BOOM — green across the board",
    "every ticker ripping, traders unsure what's happening",
    "broad rally lifts all tickers, vibes immaculate",
    "boom: bull market just dropped, all stocks limit-up",
    "across-the-board rally — cat bot purring",
]

# ---------------------------------------------------------------------------
# Dividends
# ---------------------------------------------------------------------------
_DIVIDEND = {
    "PRSM": [
        "PRSM declares dividend — shareholders paid in catnip-flavored cash",
        "Prism Corp distributes profits, ex-div effective immediately",
    ],
    "CTNP": [
        "CTNP returns capital to shareholders",
        "Catnip Holdings dividend hits accounts, share price adjusts",
    ],
    "PASS": [
        "PASS declares quarterly dividend",
        "Battlepass Inc. returns capital, ex-div now",
    ],
    "ACHS": [
        "ACHS pays dividend — trophy yield activated",
        "Achievements Co. cash distribution complete",
    ],
    "RAIN": [
        "RAIN pours cash on holders — ex-div effective",
        "Rain Holdings dividend hits, downpour of coins",
    ],
    "_GENERIC": [
        "{ticker} declares dividend, ex-div effective immediately",
        "{ticker} cash distribution to shareholders",
    ],
}


_BANKS = {
    "earnings_scheduled": _EARNINGS_SCHEDULED,
    "earnings_announced": _EARNINGS_ANNOUNCED,
    "earnings_pos": _EARNINGS_FIRED_POS,
    "earnings_neg": _EARNINGS_FIRED_NEG,
    "surprise_pos": _SURPRISE_POS,
    "surprise_neg": _SURPRISE_NEG,
    "dividend": _DIVIDEND,
}


def pick_headline(ticker: str | None, event_type: str, impulse_pct: float) -> str:
    """Return a templated headline string.

    `event_type` is one of: 'earnings_scheduled', 'earnings', 'surprise',
    'crash', 'boom', 'dividend'. For 'earnings' and 'surprise' the sign of
    `impulse_pct` selects pos vs neg banks. Crash/boom are market-wide and
    ignore ticker.
    """
    if event_type == "crash":
        return random.choice(_CRASH)
    if event_type == "boom":
        return random.choice(_BOOM)

    if event_type == "earnings":
        bank_key = "earnings_pos" if impulse_pct >= 0 else "earnings_neg"
    elif event_type == "surprise":
        bank_key = "surprise_pos" if impulse_pct >= 0 else "surprise_neg"
    else:
        bank_key = event_type

    bank = _BANKS.get(bank_key)
    if bank is None:
        return f"{ticker or 'Market'} — {event_type}"

    candidates = bank.get(ticker) if ticker else None
    if not candidates:
        candidates = bank.get("_GENERIC", [f"{ticker or 'Market'} — {event_type}"])

    return random.choice(candidates).format(ticker=ticker or "Market")
