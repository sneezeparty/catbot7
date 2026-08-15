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

import datetime
import io
import os
import re

import discord
import requests
from PIL import Image, ImageColor, ImageDraw, ImageFont
from pilmoji import Pilmoji
from pilmoji.helpers import getsize as pilmoji_getsize


def getsize(font, token):
    # thanks pillow
    left, top, right, bottom = font.getbbox(token)
    return right - left, bottom - top


# --- text measurement, used by /inventory's two-column layout ---------------
#
# Discord renders embeds in a proportional font, so laying out two columns means
# knowing how wide a line will actually be in pixels. Everything below measures
# at 32px, i.e. double Discord's 16px body text — the padding characters the
# caller picks from have integer widths at that scale, so working at 1x would
# round them all into each other.
#
# Upstream downloads Discord's real gg sans woff2 at import time. We use the
# copy already in fonts/ instead: no network call on startup, and so no way for
# a rotated Discord asset URL to take the bot down.
#
# All four styles map to the same face deliberately. The repo has no real gg
# sans Bold, and substituting whitneysemibold measured WORSE, not better: across
# all 24 cat type names the row-width error spread is 6px with ggsans-Medium
# throughout versus 10px with whitneysemibold for the bold run. Spread is what
# matters — a constant offset cancels out, since every row is padded to the same
# target. 6px here is 3px as rendered, under half a space.
FONT_SIZE = 32
EMOJI_SCALE = 1.4

_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_BODY_FACE = ImageFont.truetype(os.path.join(_FONT_DIR, "ggsans-Medium.ttf"), FONT_SIZE)
body_fonts = {"normal": _BODY_FACE, "bold": _BODY_FACE, "italic": _BODY_FACE, "bold_italic": _BODY_FACE}

_MD_RE = re.compile(r"\*\*\*(.+?)\*\*\*|\*\*(.+?)\*\*|\*(.+?)\*")


def _parse_markdown(text: str) -> list[tuple[str, str]]:
    """Split text into (style, chunk) pairs so bold runs measure as bold."""
    segments: list[tuple[str, str]] = []
    pos = 0
    for m in _MD_RE.finditer(text):
        if m.start() > pos:
            segments.append(("normal", text[pos : m.start()]))
        if m.group(1) is not None:
            segments.append(("bold_italic", m.group(1)))
        elif m.group(2) is not None:
            segments.append(("bold", m.group(2)))
        else:
            segments.append(("italic", m.group(3)))
        pos = m.end()
    if pos < len(text):
        segments.append(("normal", text[pos:]))
    return segments


def _measure(text: str, font: ImageFont.FreeTypeFont) -> int:
    """Rendered width of `text` in px, counting <:name:id> emoji as one glyph."""
    return pilmoji_getsize(text, font, emoji_scale_factor=EMOJI_SCALE)[0] if text else 0


def markdown_width(text: str) -> int:
    """Rendered width of a line that may contain **bold** and custom emoji."""
    return sum(_measure(chunk, body_fonts[style]) for style, chunk in _parse_markdown(text))


def msg2img(message: discord.Message, member: discord.Member):
    move = 0
    is_bot = member.bot
    is_pinged = message.mention_everyone
    text = message.clean_content
    if not text:
        text = message.system_content
    nick = member.display_name
    color = (member.color.r, member.color.g, member.color.b)
    if color == (0, 0, 0):
        color = (255, 255, 255)
    if not nick:
        nick = member.name

    custom_image = None
    for i in message.attachments:
        if not i.content_type or "image" not in i.content_type:
            continue

        try:
            custom_image = Image.open(requests.get(i.url, stream=True, timeout=10).raw).convert("RGBA")
        except Exception:
            continue

        max_width = 930
        width, height = custom_image.size

        if max_width >= width:
            # no rescaling needed
            calculated_height = height
            break

        # calculate the height
        decrease_amount = width / max_width
        calculated_height = int(height / decrease_amount)

        custom_image = custom_image.resize((max_width, calculated_height))

        break

    def break_text(text, font, max_width):
        lines = []
        pings = []

        pilmoji_inst = Pilmoji(Image.new("RGBA", (1, 1)), emoji_scale_factor=45 / 33)

        if not text:
            return lines, pings
        for txt in text.split("\n"):
            width_of_line = 0
            line = ""
            for token in txt.split():
                start_x = width_of_line
                start_y = len(lines) * 37
                token = token + " "
                token_width = pilmoji_inst.getsize(token, font)[0]
                if width_of_line + token_width < max_width:
                    line += token
                    width_of_line += token_width
                elif token_width >= max_width:
                    in_word_width = width_of_line
                    part_moved = ""
                    saved_width_of_line = 0
                    for i in token:
                        in_word_width += pilmoji_inst.getsize(i, font)[0]
                        if in_word_width < max_width:
                            part_moved += i
                        else:
                            lines.append(part_moved)
                            if not saved_width_of_line:
                                saved_width_of_line = in_word_width - pilmoji_inst.getsize(i, font)[0] + 7
                            in_word_width = 0
                            width_of_line = 0
                            part_moved = ""
                    line = part_moved
                    width_of_line = saved_width_of_line
                else:
                    lines.append(line)
                    line = token
                    width_of_line = token_width
                    start_x = 0
                    start_y = (len(lines) * 37) - 37
                if token[0] == "@":
                    pings.append([start_x, start_y, width_of_line, start_y + 37])

            lines.append(line)
        return lines, pings

    font = ImageFont.truetype(os.path.abspath("./fonts/whitneysemibold.otf"), 32)  # load fonts
    font2 = ImageFont.truetype(os.path.abspath("./fonts/ggsans-Medium.ttf"), 32)  # load fonts
    font3 = ImageFont.truetype(os.path.abspath("./fonts/whitneysemibold.otf"), 23)  # load fonts

    text_temp = ""
    lines, pings = break_text(text, font2, 930)
    for line in lines:
        text_temp += line + "\n"
    text = text_temp[:-2]

    the_size_and_stuff = 0
    for i in text.split("\n"):
        the_size_and_stuff += 36
    if custom_image:
        if text:
            previous_size = the_size_and_stuff + 55 + 18
            the_size_and_stuff += 18
        else:
            previous_size = 55
            the_size_and_stuff -= 36
        the_size_and_stuff += calculated_height

    if isinstance(color, str):
        color = ImageColor.getrgb(color)

    bg_color = (49, 51, 56)
    if is_pinged:
        bg_color = (73, 68, 60)
    new_img = Image.new("RGBA", (1067, 75 + the_size_and_stuff), bg_color)
    pencil = ImageDraw.Draw(new_img)
    try:
        pfp = requests.get(member.display_avatar.url, stream=True, timeout=10).raw
        im2 = Image.open(pfp).resize((800, 800)).convert("RGBA")  # resize user avatar
    except Exception:  # if the pfp is bit too silly
        new_url = "https://cdn.discordapp.com/embed/avatars/0.png"
        pfp = requests.get(new_url, stream=True, timeout=10).raw
        im2 = Image.open(pfp).resize((800, 800)).convert("RGBA")  # resize user avatar

    mask_im = Image.new("L", (800, 800), 0)  # make a mask image for making pfp circle
    draw = ImageDraw.Draw(mask_im)  # enable drawing mode on mask
    draw.ellipse((0, 0, 800, 800), fill=255)  # draw circle on mask
    newer_img = Image.new("RGBA", (800, 800), bg_color)
    newer_img.paste(im2, (0, 0), mask_im)  # apply mask to avatar
    newer_img = newer_img.resize((80, 80), Image.Resampling.LANCZOS)
    new_img.paste(newer_img, (12, 12), newer_img)

    if member.avatar_decoration:
        try:
            pfp = requests.get(member.avatar_decoration.url, stream=True, timeout=10).raw
            im2 = Image.open(pfp).resize((96, 96), Image.Resampling.LANCZOS).convert("RGBA")
            new_img.paste(im2, (4, 4), im2)
        except Exception:
            pass

    if custom_image:
        new_img.paste(custom_image, (122, previous_size), custom_image)

    for ping in pings:
        pencil.rounded_rectangle(
            (ping[0] + 122, ping[1] + 57, ping[2] + 115, ping[3] + 57),
            fill=ImageColor.getrgb("#414675"),
            radius=7,
        )

    if is_pinged:
        pencil.rectangle((0, 0, 0, 65 + the_size_and_stuff), fill=ImageColor.getrgb("#FAA81A"))

    pencil.text((122, 8), nick, font=font, fill=color)  # draw author name

    icon_offset = 0
    if isinstance(member, discord.Member) and member.display_icon and isinstance(member.display_icon, discord.Asset):
        try:
            pfp = requests.get(member.display_icon.url, stream=True, timeout=10).raw
            im2 = Image.open(pfp).resize((30, 30), Image.Resampling.LANCZOS).convert("RGBA")
            new_img.paste(im2, (10 + 122 + getsize(font, nick)[0] + move, 13), im2)
            icon_offset = 35
        except Exception:
            pass

    if is_bot or (member.primary_guild and member.primary_guild.tag):
        botfont = ImageFont.truetype(os.path.abspath("./fonts/whitneysemibold.otf"), 20)

        letters = "APP" if is_bot else member.primary_guild.tag

        pencil.rounded_rectangle(
            (
                129 + getsize(font, nick)[0] + 5 + icon_offset,
                8 + 5,
                129 + getsize(font, nick)[0] + 14 + getsize(botfont, letters)[0] + icon_offset,
                10 + 6 + 25,
            ),
            fill=(88, 101, 242) if is_bot else (70, 70, 77),
            radius=3,
        )

        pencil.text(
            (131 + getsize(font, nick)[0] + 8 + icon_offset, 10 + 4),
            letters,
            font=botfont,
            fill=(255, 255, 255),
        )
        move = getsize(botfont, letters)[0] + 20

    with Pilmoji(new_img) as pilmoji2:
        pilmoji2.text((122, 55), text.strip(), (255, 255, 255), font2, emoji_scale_factor=45 / 33)

    if message.created_at.date() == datetime.datetime.now().date():
        twentyfourhour = message.created_at.strftime("%H:%M")
    else:
        twentyfourhour = message.created_at.strftime("%d.%m.%Y, %H:%M")

    pencil.text(
        (13 + 122 + getsize(font, nick)[0] + move + icon_offset, 17),
        twentyfourhour,
        font=font3,
        fill=ImageColor.getrgb("#A3A4AA"),
    )  # draw time

    with io.BytesIO() as image_binary:
        new_img.save(image_binary, "PNG")
        image_binary.seek(0)
        return discord.File(fp=image_binary, filename="catch.png")


# italic          https://discord.com/assets/7f18f1d5ab6ded7cf71bbc1f907ee3d4.woff2
# bold            https://discord.com/assets/f9e7047f6447547781512ec4b977b2ab.woff2
# bold and italic https://discord.com/assets/21070f52a8a6a61edef9785eaf303fb8.woff2
