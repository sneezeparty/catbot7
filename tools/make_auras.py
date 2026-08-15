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

"""Generate every cat type's aura emoji, with the halo fitted to each sprite.

WHAT THE AURA ART ACTUALLY IS
-----------------------------
Diffing upstream's pairs shows the aura is not an effect applied to the sprite.
Every base pixel is bit-identical between `finecat.png` and `finecat_y.png`; the
variant only paints into what was transparent. And that painted layer is the SAME
for all 22 upstream rarities -- compared across types, restricted to pixels where
both sprites are transparent, it differs by exactly 0.

Better still, the layer isn't a painted texture at all. It's a plain radial
gradient:

    alpha(r) = 255 * (1 - (r / R)**n)        r = distance from the disc centre

fitted against upstream's real files to a max error of 2.6/255 -- inside PNG
quantisation. RGB is flat (std 0.00) for four of the five tiers; only rainbow
varies, and that's a conic hue sweep, sampled per-degree in _rainbow_lut().

That matters for more than tidiness. The earlier version of this tool recovered
the halo as a BITMAP, by unioning the 22 upstream pairs -- which leaves ~32% of
the frame never observed (it's behind the sprites in every single one) and has to
invent those pixels with a Laplace fill. You cannot move or resize a
partly-invented bitmap without dragging the invented pixels into view. An analytic
halo has no unobserved region, so per-type placement is exact and free.

(An even earlier attempt to *model* the glow -- gaussian, dilate+blur, scaled
backdrop -- beat "no glow at all" by 17% at best. That road is closed for a
reason: the layer was never a blur of the sprite.)

WHY THE HALO MOVES PER TYPE
---------------------------
Upstream puts the same disc at the canvas centre behind every cat. But its sprites
are framed inconsistently (centre-x runs from 234 for 8bit to 271.5 for corrupt, a
37.5px spread) and have very different silhouettes, so the identical disc reads
differently behind each one -- anywhere from 32.4% of it visible (epic) to 71.0%
(gremlin). In a list of cats that shows up as glows that look off-centre and
different sizes.

So here the disc is placed per type instead:

  * centred on the sprite's own opaque bounding box, not the canvas
  * radius solved by bisection so the visible glow (summed alpha over pixels the
    sprite doesn't cover) matches a fixed reference -- `fine` at upstream's own
    parameters -- so every type shows the same amount of glow

The sprite itself is never moved or rescaled. It sits exactly where its plain
emoji has it, so a cat gaining an aura doesn't shift a pixel. (The previous
version DID re-frame our three fork sprites, which made them jump 8px left the
moment an aura appeared. Don't reintroduce that.)

Usage:
    python tools/make_auras.py --verify              # analytic model vs upstream art
    python tools/make_auras.py --report              # per-type centre/radius table
    python tools/make_auras.py --out images/auras/   # write the PNGs
    python tools/make_auras.py --out images/auras/ --types shadow baby
    python tools/make_auras.py --sheet sheet.png     # contact sheet, for eyeballing

Uploading them afterwards needs emojis.py --overwrite, since the regenerated art
keeps its name and would otherwise be skipped as already present. See that file.
"""

from __future__ import annotations

import argparse
import ast
import io
import os
import sys
import tempfile
import urllib.request

import numpy as np
from PIL import Image

RAW = "https://raw.githubusercontent.com/staring-cat/emojis/main/cattypes/default"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIZE = 512

# tier -> (upstream folder, flat RGB or None for the conic rainbow, radius, exponent)
#
# Fitted against upstream's real art; see --verify. Yellow/cyan/red share one
# profile, pink is the same size but falls off later, rainbow is smaller. Those
# aren't guesses -- they're what the files measure.
TIERS = {
    "y": ("yellow", (255, 255, 0), 267.0, 2),
    "c": ("cyan", (0, 248, 255), 267.0, 2),
    "p": ("pink", (244, 0, 255), 267.0, 4),
    "a": ("red", (255, 0, 2), 267.0, 2),
    "r": ("rainbow", None, 257.0, 4),
}

# upstream's rarities -- the art we fit the model against, and the source sprites
# for the 21 rarities this fork shares with it
UPSTREAM_TYPES = [
    "8bit", "brave", "corrupt", "divine", "egirl", "epic", "fine", "good",
    "gremlin", "legendary", "mythic", "nice", "professor", "rare", "real",
    "reverse", "rickroll", "superior", "sus", "trash", "ultimate", "wild",
]

# Fork-only rarities and the EMOJI art to build them from.
#
# Not images/spawn/*.png — those are the big photo-style pictures posted when a
# cat spawns, a completely different asset from the pixel-art emoji. Compositing
# the halo behind one gives you a glowing photograph of a cat's face.
#
#   shadow, terminator — the 32x32 emoji actually uploaded for this fork. Tiny,
#                        but they scale to 512 by an exact 16x nearest-neighbour
#                        step, which is lossless for pixel art.
#   baby               — upstream drew this before deleting it; recovered from
#                        staring-cat/emojis at f0015758, spawning/default/babycat.png.
LOCAL_SPRITES = {
    "baby": "images/new cats/babycat.png",
    "shadow": "images/new cats/shadowcat.png",
    "terminator": "images/new cats/terminatorcat.png",
}

_Y, _X = np.mgrid[0:SIZE, 0:SIZE]
_mem_cache: dict[str, Image.Image] = {}
_lut_cache: np.ndarray | None = None


def fetch(path: str) -> Image.Image:
    """Grab one file from upstream's emoji repo, cached on disk between runs."""
    if path not in _mem_cache:
        disk = os.path.join(tempfile.gettempdir(), "cat-bot-aura-src", path.replace("/", "_"))
        os.makedirs(os.path.dirname(disk), exist_ok=True)
        if not os.path.exists(disk):
            with urllib.request.urlopen(f"{RAW}/{path}", timeout=30) as r:
                data = r.read()
            with open(disk, "wb") as f:
                f.write(data)
        _mem_cache[path] = Image.open(disk).convert("RGBA")
    return _mem_cache[path]


def fork_cattypes() -> list[str]:
    """This fork's rarity list, read from main.py without importing it."""
    tree = ast.parse(open(os.path.join(REPO_ROOT, "main.py"), encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "type_dict" for t in node.targets):
            return [k.lower() for k in ast.literal_eval(node.value)]
    raise SystemExit("could not find a module-level type_dict in main.py")


def sprite(cat: str) -> Image.Image:
    """The 512x512 plain emoji for one rarity, however it's sourced."""
    if cat in LOCAL_SPRITES:
        im = Image.open(os.path.join(REPO_ROOT, LOCAL_SPRITES[cat])).convert("RGBA")
        if im.size != (SIZE, SIZE):
            # NEAREST: the 32x32 ones go up by an exact 16x and any smooth filter
            # would just turn crisp pixel-art blocks to mush
            im = im.resize((SIZE, SIZE), Image.NEAREST)
        return im
    return fetch(f"normal/{cat}cat.png")


_LUT_ANGLES, _LUT_RINGS = 360, 64


def _rainbow_lut() -> np.ndarray:
    """Rainbow's RGB as a function of (angle, radius/R) about the disc centre.

    Rainbow is the one tier whose colour isn't flat. The hue sweeps conically --
    RGB walks 300deg -> 5deg -> 69deg -> 163deg as you go round -- but it also
    drifts along the radius, so a purely angular table leaves a visible error
    (measured: 25/255 mean, 75 worst). Indexing by normalised radius as well
    fixes that, and it means the sweep SCALES with the disc when a type gets a
    bigger or smaller one, which is what you'd want anyway.

    Sampled straight out of upstream's real art rather than fitted to a formula.
    """
    global _lut_cache
    if _lut_cache is not None:
        return _lut_cache
    radius = TIERS["r"][2]
    total = np.zeros((_LUT_ANGLES, _LUT_RINGS, 3))
    count = np.zeros((_LUT_ANGLES, _LUT_RINGS))
    ang = (np.degrees(np.arctan2(_Y - 256.0, _X - 256.0)) % 360).astype(int) % _LUT_ANGLES
    ring = np.clip((np.hypot(_X - 256.0, _Y - 256.0) / radius * _LUT_RINGS).astype(int), 0, _LUT_RINGS - 1)
    for cat in UPSTREAM_TYPES:
        base = np.array(fetch(f"normal/{cat}cat.png"))
        var = np.array(fetch(f"rainbow/{cat}cat_r.png"))
        m = (base[:, :, 3] == 0) & (var[:, :, 3] > 16)
        np.add.at(total, (ang[m], ring[m]), var[m][:, :3].astype(float))
        np.add.at(count, (ang[m], ring[m]), 1.0)

    lut = np.where(count[:, :, None] > 0, total / np.maximum(count, 1)[:, :, None], np.nan)
    # The inner rings are behind the sprites in all 22 references, so they're never
    # observed. Extend each angle's nearest observed ring into them -- along a ray
    # the hue barely moves, so this is interpolation along the flat axis rather
    # than invention. Angles with no data at all (a few fall between silhouettes)
    # borrow from their neighbour.
    for a in range(_LUT_ANGLES):
        seen = np.nonzero(~np.isnan(lut[a, :, 0]))[0]
        if not len(seen):
            continue
        for r in range(_LUT_RINGS):
            if np.isnan(lut[a, r, 0]):
                lut[a, r] = lut[a, seen[np.argmin(np.abs(seen - r))]]
    empty = np.isnan(lut[:, 0, 0])
    if empty.all():
        raise SystemExit("no rainbow samples at all — is the rainbow/ folder still there?")
    for a in np.nonzero(empty)[0]:
        for step in range(1, _LUT_ANGLES):
            for probe in ((a - step) % _LUT_ANGLES, (a + step) % _LUT_ANGLES):
                if not empty[probe]:
                    lut[a] = lut[probe]
                    break
            else:
                continue
            break
    _lut_cache = lut
    return _lut_cache


def halo(center: tuple[float, float], radius: float, tier: str) -> np.ndarray:
    """The glow disc, as a float RGBA array."""
    _folder, rgb, _R, exponent = TIERS[tier]
    cx, cy = center
    r = np.hypot(_X - cx, _Y - cy)
    alpha = np.clip(255.0 * (1.0 - (r / radius) ** exponent), 0.0, 255.0)
    if rgb is None:
        ang = (np.degrees(np.arctan2(_Y - cy, _X - cx)) % 360).astype(int) % _LUT_ANGLES
        ring = np.clip((r / radius * _LUT_RINGS).astype(int), 0, _LUT_RINGS - 1)
        colour = _rainbow_lut()[ang, ring]
    else:
        colour = np.broadcast_to(np.array(rgb, dtype=float), (SIZE, SIZE, 3))
    return np.dstack([colour, alpha])


def compose(sprite_im: Image.Image, disc: np.ndarray) -> Image.Image:
    layer = Image.fromarray(np.clip(disc, 0, 255).astype(np.uint8), "RGBA")
    return Image.alpha_composite(layer, sprite_im)


def sprite_center(alpha: np.ndarray) -> tuple[float, float]:
    """Centre of the sprite's opaque bounding box."""
    ys, xs = np.nonzero(alpha > 0)
    return (float(xs.min() + xs.max()) / 2.0, float(ys.min() + ys.max()) / 2.0)


def visible_mass(alpha: np.ndarray, center: tuple[float, float], radius: float, exponent: int) -> float:
    """Total glow the viewer actually sees: disc alpha where the sprite isn't."""
    cx, cy = center
    r = np.hypot(_X - cx, _Y - cy)
    disc = np.clip(255.0 * (1.0 - (r / radius) ** exponent), 0.0, 255.0)
    return float((disc * (alpha == 0)).sum())


def solve_radius(alpha: np.ndarray, center: tuple[float, float], exponent: int, target: float) -> float:
    """Radius giving `target` visible glow. Monotonic in radius, so bisect."""
    lo, hi = 60.0, 700.0
    for _ in range(50):
        mid = (lo + hi) / 2.0
        if visible_mass(alpha, center, mid, exponent) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def reference_mass(tier: str) -> float:
    """How much glow `fine` shows under upstream's own parameters.

    Anchoring to a real upstream type means the normalisation doesn't drift off
    to some arbitrary new look -- it picks the canonical cat and matches it.
    """
    _folder, _rgb, radius, exponent = TIERS[tier]
    alpha = np.array(sprite("fine"))[:, :, 3]
    return visible_mass(alpha, (256.0, 256.0), radius, exponent)


def plan_for(cat: str) -> dict:
    """Where this type's disc goes, and how big, for each tier."""
    alpha = np.array(sprite(cat))[:, :, 3]
    center = sprite_center(alpha)
    radii = {}
    for tier in TIERS:
        _folder, _rgb, _R, exponent = TIERS[tier]
        radii[tier] = solve_radius(alpha, center, exponent, reference_mass(tier))
    return {"center": center, "radii": radii, "alpha": alpha}


def _premul(img: np.ndarray) -> np.ndarray:
    """RGBA -> premultiplied RGB + alpha, so invisible pixels compare equal."""
    a = img[:, :, 3:4] / 255.0
    return np.dstack([img[:, :, :3] * a, img[:, :, 3]])


def verify() -> int:
    """Check the analytic halo reproduces upstream's real art.

    Regenerates every upstream variant at UPSTREAM's parameters (disc on the
    canvas centre, fitted R/n) and diffs against the real PNG. This is the gate:
    if the model can't reproduce what upstream shipped, nothing downstream of it
    is trustworthy either.
    """
    print("regenerating upstream's aura art from the analytic model\n")
    worst = 0.0
    worst_where = ""
    rows = []
    for tier, (folder, _rgb, radius, exponent) in TIERS.items():
        errs = []
        for cat in UPSTREAM_TYPES:
            base = sprite(cat)
            real = np.array(fetch(f"{folder}/{cat}cat_{tier}.png")).astype(float)
            mine = np.array(compose(base, halo((256.0, 256.0), radius, tier))).astype(float)

            opaque = np.array(base)[:, :, 3] == 255
            if not np.array_equal(real[opaque], mine[opaque]):
                print(f"  FAIL {cat}cat_{tier}: sprite pixels differ — the glow is not staying behind the cat")
                return 1

            # premultiply before diffing: RGB under a fully transparent pixel is
            # arbitrary in a PNG and invisible on screen, so comparing it raw
            # reports a 255 error for pixels nobody can see
            err = float(np.abs(_premul(real) - _premul(mine)).max())
            errs.append(err)
            if err > worst:
                worst, worst_where = err, f"{cat}cat_{tier}"
        rows.append((tier, float(np.mean(errs)), max(errs)))

    print(f"  {'tier':<6}{'mean max-err':>14}{'worst':>9}")
    for tier, mean_err, mx in rows:
        print(f"  _{tier:<5}{mean_err:>14.2f}{mx:>9.1f}")
    print(f"\nworst single pixel: {worst:.1f}/255 on {worst_where}")
    if worst > 8:
        print("that's too high to ship — the model needs refitting")
        return 1
    print("within PNG quantisation. model is a faithful replacement for upstream's art.")
    return 0


def report(cats: list[str]) -> int:
    """Before/after table for the two numbers this whole tool exists to fix."""
    print(f"{'type':<14}{'centre':>16}{'radius _y':>11}{'glow before':>13}{'glow after':>12}")
    print("-" * 66)
    before, after = [], []
    for cat in cats:
        p = plan_for(cat)
        alpha = p["alpha"]
        _f, _c, upstream_R, exponent = TIERS["y"]
        b = visible_mass(alpha, (256.0, 256.0), upstream_R, exponent)
        a = visible_mass(alpha, p["center"], p["radii"]["y"], exponent)
        before.append(b)
        after.append(a)
        cx, cy = p["center"]
        print(f"{cat:<14}({cx:6.1f},{cy:6.1f}){p['radii']['y']:>11.1f}{b / 1e6:>12.2f}M{a / 1e6:>11.2f}M")
    spread = lambda v: (max(v) - min(v)) / (sum(v) / len(v)) * 100
    print()
    print(f"glow spread before: {spread(before):5.1f}%   after: {spread(after):5.1f}%")
    return 0


def sheet(path: str, cats: list[str], px: int) -> int:
    """Contact sheet at Discord's real display size, for eyeballing."""
    pad = 6
    cols = len(TIERS) + 1
    img = Image.new("RGBA", (cols * (px + pad) + pad, len(cats) * (px + pad) + pad), (54, 57, 63, 255))
    for row, cat in enumerate(cats):
        p = plan_for(cat)
        base = sprite(cat)
        cells = [base] + [compose(base, halo(p["center"], p["radii"][t], t)) for t in TIERS]
        for col, cell in enumerate(cells):
            img.alpha_composite(cell.resize((px, px), Image.LANCZOS), (pad + col * (px + pad), pad + row * (px + pad)))
    img.save(path)
    print(f"wrote {path}  ({len(cats)} types x {cols} cells at {px}px)")
    return 0


def generate(out_dir: str, cats: list[str]) -> int:
    os.makedirs(out_dir, exist_ok=True)
    written = 0
    for cat in cats:
        p = plan_for(cat)
        base = sprite(cat)
        # the plain emoji too, for the fork-only types: their uploaded art came
        # from a 32x32 (or a recovered file) and we want the aura'd version to
        # match it exactly, which is only guaranteed if both come from here
        if cat in LOCAL_SPRITES:
            base.save(os.path.join(out_dir, f"{cat}cat.png"))
            written += 1
        for tier in TIERS:
            out = compose(base, halo(p["center"], p["radii"][tier], tier))
            out.save(os.path.join(out_dir, f"{cat}cat_{tier}.png"))
            written += 1
        cx, cy = p["center"]
        print(f"  {cat:<14} centre ({cx:6.1f},{cy:6.1f})  radii " + " ".join(f"{t}={p['radii'][t]:.0f}" for t in TIERS))
    print(f"\nwrote {written} files to {out_dir}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify", action="store_true", help="check the analytic model against upstream's art")
    ap.add_argument("--report", action="store_true", help="print the per-type centre/radius/glow table")
    ap.add_argument("--out", metavar="DIR", help="write the emoji PNGs here")
    ap.add_argument("--sheet", metavar="PNG", help="write a contact sheet here")
    ap.add_argument("--px", type=int, default=48, help="contact sheet cell size (default 48)")
    ap.add_argument("--types", nargs="+", metavar="T", help="limit to these rarities (default: all 24)")
    args = ap.parse_args()

    cats = args.types or fork_cattypes()
    unknown = [c for c in cats if c not in LOCAL_SPRITES and c not in UPSTREAM_TYPES]
    if unknown:
        print(f"no sprite source for: {', '.join(unknown)}", file=sys.stderr)
        return 1

    if not (args.verify or args.report or args.out or args.sheet):
        ap.print_help()
        return 1

    if args.verify and verify() != 0:
        return 1
    if args.report:
        report(cats)
    if args.sheet:
        sheet(args.sheet, cats, args.px)
    if args.out:
        generate(args.out, cats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
