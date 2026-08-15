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

"""Upload Cat Bot's emojis to your bot application instead of doing it by hand.

Adapted from upstream's emojis.py, with the differences this fork needs:

  * Only uploads what's MISSING. Upstream re-POSTs every file every run and
    prints an error for each one that already exists; with 100+ aura variants
    that's unreadable. Existing names are fetched once and skipped.
  * Filters cat emoji to this fork's own rarity list, read out of main.py. That
    drops upstream's `gremlin` (which this fork doesn't have) and pulls in the
    locally generated Baby/Shadow/Terminator art.
  * --dry-run, because uploading 100+ emoji to a live 200k-server bot is not
    something to trigger by accident.
  * Awaits the git clone. Upstream fires it off and starts uploading from a
    directory that may still be filling up.

Aura variants (<type>cat_y/_c/_p/_a/_r) are what the aura feature renders. If
some are missing the bot degrades quietly — get_aura_emoji falls back to the
plain cat — so a partial run is safe to resume.

Generate the three fork-only rarities first:

    python tools/make_auras.py --out emoji_out/
    python emojis.py --dry-run --extra emoji_out/
    python emojis.py --extra emoji_out/
"""

import argparse
import asyncio
import os
import sys
import tempfile

import discord

# NOT imported at module scope: config.py reads os.environ["TOKEN"] on import, so
# importing it here makes `--help` and every offline check die with a bare
# KeyError before argparse ever runs. Imported inside main() instead.

EMOJI_REPO = "https://github.com/staring-cat/emojis"

# Discord's per-application emoji cap
EMOJI_LIMIT = 2000

# the non-themed icons — achievements, packs, prisms, /fish icons, etc.
UPLOAD_BASE_EMOJIS = True

# Which spawn-emoji themes to upload. "default" holds the plain cats in normal/
# plus the five aura colours in yellow/ cyan/ pink/ red/ rainbow/.
SPAWN_EMOJI_THEMES = {
    "default": True,
    "birthday": False,
    "halloween": False,
    "old": False,
    "fish": False,
}

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def fork_cattypes():
    """This fork's rarity list, read from main.py without importing it.

    Importing main builds a whole discord bot; parsing the literal is enough.
    """
    import ast

    tree = ast.parse(open(os.path.join(REPO_ROOT, "main.py"), encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "type_dict" for t in node.targets):
            return [k.lower() for k in ast.literal_eval(node.value)]
    raise SystemExit("could not find a module-level type_dict in main.py")


def collect(folder, cattypes=None):
    """Map emoji name -> file path for every image under `folder`.

    Walks subfolders, since base/ is split into categories and cattypes/default/
    is split by aura colour. When `cattypes` is given, files whose name doesn't
    resolve to one of this fork's rarities are dropped.
    """
    found = {}
    for root, _dirs, filenames in os.walk(folder):
        for filename in sorted(filenames):
            name, ext = os.path.splitext(filename)
            if ext.lower() not in (".png", ".gif"):
                continue
            path = os.path.join(root, filename)
            if os.path.islink(path):
                print(f"  skipping symlink: {path}")
                continue
            if cattypes is not None:
                # "<type>cat" or "<type>cat_<aura>"
                stem = name.split("_")[0]
                if not stem.endswith("cat") or stem[: -len("cat")] not in cattypes:
                    continue
            found[name] = path
    return found


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="list what would be uploaded and exit")
    ap.add_argument("--extra", action="append", default=[], metavar="DIR",
                    help="extra local folder to upload from (e.g. tools/make_auras.py output)")
    ap.add_argument("--replace", action="store_true", help="delete every existing application emoji first")
    args = ap.parse_args()

    if not os.environ.get("TOKEN"):
        print("ERROR: TOKEN env var required (the same one bot.py uses)", file=sys.stderr)
        return 2
    import config  # noqa: E402 — see the note by the discord import

    cattypes = fork_cattypes()
    print(f"fork rarities ({len(cattypes)}): {', '.join(cattypes)}\n")

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    with tempfile.TemporaryDirectory(prefix="cat-bot-emojis-") as clone_dir:
        print(f"cloning {EMOJI_REPO} ...")
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", EMOJI_REPO, clone_dir,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        # upstream doesn't wait here and can start uploading from a half-cloned tree
        _, err = await proc.communicate()
        if proc.returncode != 0:
            print(f"git clone failed: {err.decode(errors='replace').strip()}", file=sys.stderr)
            return 1

        wanted = {}
        if UPLOAD_BASE_EMOJIS:
            base = collect(os.path.join(clone_dir, "base"))
            if not base:
                print("no base emojis found — does the cloned branch still have base/?", file=sys.stderr)
                return 1
            wanted.update(base)
            print(f"  base/                     {len(base):>4} emoji")

        for theme, enabled in SPAWN_EMOJI_THEMES.items():
            if not enabled:
                continue
            path = os.path.join(clone_dir, "cattypes", theme)
            themed = collect(path, cattypes=cattypes)
            if not themed:
                print(f"no emojis for the '{theme}' theme — does cattypes/{theme}/ exist?", file=sys.stderr)
                return 1
            wanted.update(themed)
            print(f"  cattypes/{theme + '/':<17}{len(themed):>4} emoji  (fork rarities only)")

        for extra in args.extra:
            local = collect(extra if os.path.isabs(extra) else os.path.join(REPO_ROOT, extra))
            if not local:
                print(f"nothing to upload from {extra}", file=sys.stderr)
                return 1
            # local art wins: these are the fork-only rarities upstream never drew
            wanted.update(local)
            print(f"  {extra + '/':<26}{len(local):>4} emoji  (local)")

        try:
            # login() authenticates the HTTP session; no gateway connection needed
            await client.login(config.TOKEN)

            if args.replace and not args.dry_run:
                existing = await client.fetch_application_emojis()
                for emoji in existing:
                    await emoji.delete()
                print(f"\ndeleted all {len(existing)} existing application emojis")
                have = set()
            else:
                have = {e.name for e in await client.fetch_application_emojis()}

            missing = {n: p for n, p in wanted.items() if n not in have}
            already = len(wanted) - len(missing)

            print(f"\nalready on the application : {len(have)}")
            print(f"selected for upload        : {len(wanted)}  ({already} already present, {len(missing)} missing)")
            after = len(have) + len(missing)
            print(f"after this run             : {after} / {EMOJI_LIMIT}")
            if after > EMOJI_LIMIT:
                print(f"\nthat exceeds Discord's {EMOJI_LIMIT}-emoji limit — trim SPAWN_EMOJI_THEMES", file=sys.stderr)
                return 1

            auras = sorted(n for n in missing if "_" in n and n.split("_")[-1] in ("y", "c", "p", "a", "r"))
            if auras:
                print(f"\nof which aura variants     : {len(auras)}")

            if args.dry_run:
                print("\n--dry-run, uploading nothing. would upload:")
                for name in sorted(missing):
                    print(f"  {name}")
                return 0

            if not missing:
                print("\nnothing to do.")
                return 0

            print()
            uploaded = failed = 0
            for name, path in sorted(missing.items()):
                try:
                    with open(path, "rb") as f:
                        await client.create_application_emoji(name=name, image=f.read())
                    uploaded += 1
                    print(f"  uploaded {name}")
                except discord.HTTPException as e:
                    failed += 1
                    print(f"  FAILED   {name}: {e}")

            print(f"\nuploaded {uploaded}, failed {failed}")
            return 0 if failed == 0 else 1
        finally:
            await client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
