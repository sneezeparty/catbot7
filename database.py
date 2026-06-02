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

import json

import catpg
import config


def _coerce_array(value):
    """asyncpg usually decodes JSONB to native types, but some configurations
    leave it as a string. Normalize to a Python list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value) or []
        except (ValueError, TypeError):
            return []
    return list(value)


async def _init_connection(conn):
    # Register a jsonb codec so list/dict values are auto-encoded on writes
    # and decoded to native types on reads. Without this, asyncpg's default
    # jsonb codec rejects non-string inputs and Profile.save() fails when
    # `unlocked_aches` is mutated to a Python list.
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def connect():
    await catpg.connect(
        user="cat_bot",
        password=config.DB_PASS,
        database="cat_bot",
        host=config.DB_HOST,
        port=config.DB_PORT,
        max_size=90,
        init=_init_connection,
    )


async def close():
    await catpg.close()


class Profile(catpg.Model):
    _capped_ints = [
        "cats_gifted",
        "cat_gifts_recieved",
        "cats_traded",
        "cat_Fine",
        "cat_Nice",
        "cat_Good",
        "cat_Rare",
        "cat_Wild",
        "cat_Baby",
        "cat_Epic",
        "cat_Sus",
        "cat_Brave",
        "cat_Rickroll",
        "cat_Reverse",
        "cat_Superior",
        "cat_Trash",
        "cat_Legendary",
        "cat_Mythic",
        "cat_8bit",
        "cat_Corrupt",
        "cat_Professor",
        "cat_Divine",
        "cat_Real",
        "cat_Terminator",
        "cat_Ultimate",
        "cat_eGirl",
        "cat_Shadow",
    ]

    def has_ach(self, ach_id: str) -> bool:
        """True if the user has unlocked the achievement.

        Reads the JSONB array. Falls back to a legacy boolean column if the
        ach isn't in the array but is still kept as a column (transition).
        """
        try:
            unlocked = _coerce_array(self.unlocked_aches)
        except KeyError:
            unlocked = []
        if ach_id in unlocked:
            return True
        try:
            return bool(self[ach_id])
        except KeyError:
            return False

    def unlock_ach(self, ach_id: str) -> bool:
        """Add to the JSONB array; also flip the legacy boolean column if it
        exists. Returns True if newly unlocked, False if already had it."""
        if self.has_ach(ach_id):
            return False
        try:
            unlocked = _coerce_array(self.unlocked_aches)
        except KeyError:
            unlocked = []
        self.unlocked_aches = unlocked + [ach_id]
        try:
            self[ach_id] = True
        except KeyError:
            pass
        return True


class User(catpg.Model):
    _primary_key = "user_id"
    _capped_ints = ["custom_num"]


class Channel(catpg.Model):
    _primary_key = "channel_id"


class Prism(catpg.Model):
    pass


class Reminder(catpg.Model):
    pass


class Server(catpg.Model):
    _primary_key = "server_id"


class Order(catpg.Model):
    pass


class PriceHistory(catpg.Model):
    pass


class PortfolioHistory(catpg.Model):
    pass


class Reward(catpg.Model):
    _primary_key = "ticker"


class JobInstance(catpg.Model):
    pass
