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

"""Background broadcaster for /announce. Spawned as a task from the webui
route — runs on the bot's asyncio loop so it has direct access to bot.get_channel().

Mirrors _broadcast_season_warning() in main.py: iterate channel rows, look up
cached channel objects, skip uncached / no-guild, send, sleep 0.1s, swallow
per-channel exceptions. Counts are written back to the announcement row at the
end (and once at start to set status='sending')."""

import asyncio
import logging
import time

from webui import state

CHANNEL_SEND_DELAY = 0.1  # seconds between channels — same as season warning


async def broadcast_announcement(announcement_id: int) -> None:
    pool = state.get_pool()
    bot = state.get_bot()
    if pool is None or bot is None:
        logging.error("announce: pool or bot missing, aborting %d", announcement_id)
        return

    # Load + mark sending.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, body, one_per_server FROM announcement WHERE id = $1",
            announcement_id,
        )
        if row is None:
            logging.error("announce %d: row vanished", announcement_id)
            return
        await conn.execute(
            "UPDATE announcement SET status = 'sending' WHERE id = $1",
            announcement_id,
        )
        body = row["body"]
        one_per_server = row["one_per_server"]

    sent = 0
    failed = 0
    skipped = 0
    target = 0
    seen_guilds: set[int] = set()
    error_msg = ""

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT channel_id FROM channel ORDER BY channel_id"
            )

        for r in rows:
            if bot.is_closed():
                error_msg = "bot disconnected mid-broadcast"
                break
            target += 1
            cid = int(r["channel_id"])
            try:
                ch_obj = bot.get_channel(cid)
                if ch_obj is None or getattr(ch_obj, "guild", None) is None:
                    skipped += 1
                    continue
                if one_per_server:
                    gid = ch_obj.guild.id
                    if gid in seen_guilds:
                        skipped += 1
                        continue
                    seen_guilds.add(gid)
                await ch_obj.send(body)
                sent += 1
                await asyncio.sleep(CHANNEL_SEND_DELAY)
            except Exception:
                failed += 1
                logging.debug("announce %d: send failed for channel %d", announcement_id, cid, exc_info=True)
    except Exception as e:
        error_msg = f"broadcast loop crashed: {e!r}"
        logging.exception("announce %d: broadcast loop crashed", announcement_id)

    final_status = "sent" if not error_msg else "failed"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE announcement
            SET status = $2, sent_at = $3, target_count = $4,
                sent_count = $5, failed_count = $6, skipped_count = $7,
                error = $8
            WHERE id = $1
            """,
            announcement_id, final_status, int(time.time()),
            target, sent, failed, skipped, error_msg,
        )
    logging.info(
        "announce %d done: status=%s target=%d sent=%d failed=%d skipped=%d",
        announcement_id, final_status, target, sent, failed, skipped,
    )
