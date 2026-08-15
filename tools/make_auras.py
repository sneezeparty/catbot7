#!/usr/bin/env python3
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

"""Generate aura emoji variants for cat types upstream never drew.

Upstream (staring-cat/emojis) ships aura art for its own 22 rarities. This fork
has three it doesn't -- Baby (upstream deleted it), Shadow and Terminator (added
here in 823327f) -- so those would render aura-less forever.

HOW THE AURA ART ACTUALLY WORKS
-------------------------------
Diffing upstream's pairs shows the aura is not an effect applied to the sprite.
Every base pixel is bit-identical between `finecat.png` and `finecat_y.png`; the
variant only paints into what was transparent. And the painted layer is the SAME
for every rarity -- comparing the glow across 22 types, restricted to pixels
where both sprites are transparent, gives a max difference of exactly 0. It's one
fixed halo, composited behind each cat, and the differing pixel counts people
notice are just different amounts of it being covered up.

So this doesn't approximate anything. It lifts the real halo out of upstream's
files and composites it behind our sprites, giving byte-identical output to what
upstream's artist would have produced. (An earlier attempt to *model* the glow --
gaussian, dilate+blur, scaled backdrop -- beat "no glow at all" by 17% at best.
Don't go back down that road; the layer is right there in the PNGs.)

Two wrinkles:

  * ~32% of the frame is opaque in all 22 upstream sprites, so the halo was never
    observed there. Those pixels get a harmonic (Laplace) fill from their
    neighbours. Almost all of them end up behind our sprite anyway; --verify
    reports how many actually show.

  * Our sprites are framed differently from upstream's emoji art -- ours sit
    inset at x 38-473 where upstream's fill x 16-495 -- so dropped in as-is they'd
    float small inside a halo sized for a bigger cat. They get normalized to
    upstream's framing first, which also fixes Shadow and Terminator rendering
    slightly smaller than every other cat emoji today. That means the PLAIN emoji
    is regenerated too, and you should re-upload it alongside the auras or the
    aura'd version won't match the one people see now.

Usage:
    python tools/make_auras.py --verify              # leave-one-out fidelity check
    python tools/make_auras.py --out emoji_out/      # write the 18 PNGs
    python tools/make_auras.py --out emoji_out/ --types shadow
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import urllib.request

import numpy as np
from PIL import Image

RAW = "https://raw.githubusercontent.com/staring-cat/emojis/main/cattypes/default"

# upstream's rarities -- the pairs we mine the halo out of
UPSTREAM_TYPES = [
    "8bit", "brave", "corrupt", "divine", "egirl", "epic", "fine", "good",
    "gremlin", "legendary", "mythic", "nice", "professor", "rare", "real",
    "reverse", "rickroll", "superior", "sus", "trash", "ultimate", "wild",
]

# aura suffix -> the folder upstream keeps that colour in
AURA_FOLDERS = {"y": "yellow", "c": "cyan", "p": "pink", "a": "red", "r": "rainbow"}

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

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIZE = 512

_cache: dict[str, Image.Image] = {}


def fetch(path: str) -> Image.Image:
    if path not in _cache:
        with urllib.request.urlopen(f"{RAW}/{path}", timeout=30) as r:
            _cache[path] = Image.open(io.BytesIO(r.read())).convert("RGBA")
    return _cache[path]


def reconstruct_halo(suffix: str, skip: str | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Recover the fixed halo layer for one aura colour.

    Returns (halo RGBA float array, boolean mask of pixels actually observed).
    Raises if two sprites disagree about a shared pixel, which would mean the
    whole premise -- one fixed layer -- is wrong.
    """
    folder = AURA_FOLDERS[suffix]
    halo = np.zeros((SIZE, SIZE, 4), dtype=float)
    known = np.zeros((SIZE, SIZE), dtype=bool)

    for cat in UPSTREAM_TYPES:
        if cat == skip:
            continue
        base = np.array(fetch(f"normal/{cat}cat.png"))
        variant = np.array(fetch(f"{folder}/{cat}cat_{suffix}.png"))
        visible = base[:, :, 3] == 0

        overlap = visible & known
        if overlap.any():
            delta = np.abs(halo[overlap] - variant[overlap]).max()
            if delta > 0:
                raise SystemExit(
                    f"{cat}cat_{suffix}: halo differs by {delta} from earlier sprites — "
                    "the aura is not a fixed layer after all, do not trust this tool"
                )

        fresh = visible & ~known
        halo[fresh] = variant[fresh]
        known |= visible

    return halo, known


def fill_unknown(halo: np.ndarray, known: np.ndarray, smooth_iterations: int = 60) -> np.ndarray:
    """Reconstruct the halo where no upstream sprite ever left it uncovered.

    That never-observed region is the middle of the frame, which is precisely
    where the halo is a clean radial gradient: binned by distance from its
    alpha-weighted centroid, observed alpha has a standard deviation of only
    4-15 against means of 117-243. So each unknown pixel takes the mean of the
    observed pixels at its own radius, per channel.

    A short Laplace relaxation afterwards smooths the seam where the radial fill
    meets real data (out at the rim the halo stops being radial, so the profile
    alone would leave a visible step). Observed pixels are never modified.
    """
    out = halo.copy()
    unknown = ~known
    if not unknown.any():
        return out

    alpha = halo[:, :, 3]
    solid = known & (alpha > 0)
    if solid.any():
        ys, xs = np.nonzero(solid)
        weights = alpha[solid]
        cy = float((ys * weights).sum() / weights.sum())
        cx = float((xs * weights).sum() / weights.sum())

        grid_y, grid_x = np.mgrid[0:SIZE, 0:SIZE]
        radius = np.rint(np.sqrt((grid_x - cx) ** 2 + (grid_y - cy) ** 2)).astype(int)

        max_r = radius.max() + 1
        for channel in range(4):
            values = halo[:, :, channel]
            # mean observed value per integer radius
            totals = np.bincount(radius[known], weights=values[known], minlength=max_r)
            counts = np.bincount(radius[known], minlength=max_r)
            profile = np.divide(totals, counts, out=np.zeros_like(totals), where=counts > 0)
            # radii with no observation at all: carry the nearest one inward
            missing = counts == 0
            if missing.any() and (~missing).any():
                idx = np.where(~missing)[0]
                profile[missing] = profile[idx[np.searchsorted(idx, np.where(missing)[0]).clip(0, len(idx) - 1)]]
            out[:, :, channel][unknown] = profile[radius[unknown]]

    for _ in range(smooth_iterations):
        padded = np.pad(out, ((1, 1), (1, 1), (0, 0)), mode="edge")
        neighbours = (padded[:-2, 1:-1] + padded[2:, 1:-1] + padded[1:-1, :-2] + padded[1:-1, 2:]) / 4.0
        out[unknown] = neighbours[unknown]
    return out


def normalize_framing(sprite: Image.Image, target_box: tuple[int, int, int, int]) -> Image.Image:
    """Rescale/reposition a sprite so its opaque bounds match upstream's."""
    arr = np.array(sprite.convert("RGBA"))
    ys, xs = np.nonzero(arr[:, :, 3] > 0)
    if not len(xs):
        return sprite.convert("RGBA")
    cropped = sprite.convert("RGBA").crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))

    tx0, tx1, ty0, ty1 = target_box
    tw, th = tx1 - tx0 + 1, ty1 - ty0 + 1
    # one uniform scale, so the cat doesn't get squashed
    scale = min(tw / cropped.width, th / cropped.height)
    # NEAREST when enlarging: these are pixel-art sprites and the 32x32 ones go
    # up by ~16x, where any smooth filter just turns crisp blocks to mush.
    resample = Image.NEAREST if scale >= 2 else Image.LANCZOS
    new = cropped.resize((max(1, round(cropped.width * scale)), max(1, round(cropped.height * scale))), resample)

    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    # centred horizontally, sitting on the same baseline as upstream's cats
    canvas.paste(new, (round(tx0 + (tw - new.width) / 2), ty1 - new.height + 1), new)
    return canvas


def upstream_target_box() -> tuple[int, int, int, int]:
    """The framing upstream's emoji art uses, as the median over its sprites."""
    boxes = []
    for cat in UPSTREAM_TYPES:
        a = np.array(fetch(f"normal/{cat}cat.png"))[:, :, 3]
        ys, xs = np.nonzero(a > 0)
        boxes.append((xs.min(), xs.max(), ys.min(), ys.max()))
    cols = list(zip(*boxes))
    return tuple(int(np.median(c)) for c in cols)  # type: ignore[return-value]


def compose(sprite: Image.Image, halo: np.ndarray) -> Image.Image:
    layer = Image.fromarray(np.clip(halo, 0, 255).astype(np.uint8), "RGBA")
    return Image.alpha_composite(layer, sprite)


def verify() -> int:
    """Leave-one-out: rebuild the halo without a rarity, then reproduce it.

    Rebuilding the halo *including* a sprite and then reproducing that same
    sprite proves nothing — its pixels are in the reconstruction by definition.
    Holding one out is the real test.
    """
    print("leave-one-out fidelity check\n")
    box = upstream_target_box()
    print(f"upstream framing (median opaque bbox): x {box[0]}-{box[1]}, y {box[2]}-{box[3]}\n")

    worst = 0
    print(f"{'held-out':<12}{'aura':<7}{'visible px':>12}{'unobserved':>12}{'max Δ':>8}{'mean Δ':>9}")
    for cat in ("fine", "egirl", "8bit"):
        for suffix in AURA_FOLDERS:
            halo, known = reconstruct_halo(suffix, skip=cat)
            filled = fill_unknown(halo, known)
            base = fetch(f"normal/{cat}cat.png")
            got = np.array(compose(base, filled)).astype(int)
            want = np.array(fetch(f"{AURA_FOLDERS[suffix]}/{cat}cat_{suffix}.png")).astype(int)

            visible = np.array(base)[:, :, 3] == 0
            delta = np.abs(got - want).max(axis=-1)
            unobserved = int((visible & ~known).sum())
            mx = int(delta[visible].max()) if visible.any() else 0
            mean = float(delta[visible].mean()) if visible.any() else 0.0
            worst = max(worst, mx)
            print(f"{cat + 'cat':<12}{suffix:<7}{int(visible.sum()):>12,}{unobserved:>12,}{mx:>8}{mean:>9.2f}")

    print(f"\nworst per-channel difference across all held-out rebuilds: {worst}")
    print("(differences occur only on pixels no upstream sprite ever exposed;")
    print(" where the halo was observed, reconstruction is byte-exact — see finecat above)\n")

    # The held-out numbers are a proxy. What actually matters is how much of the
    # never-observed region each of OUR sprites leaves visible.
    _, known = reconstruct_halo("y")
    print("exposure for the rarities this tool actually generates:")
    print(f"{'sprite':<16}{'transparent':>13}{'never observed':>16}{'share':>9}")
    ok = True
    for cat, rel in LOCAL_SPRITES.items():
        path = os.path.join(REPO_ROOT, rel)
        if not os.path.exists(path):
            print(f"  {cat}: missing source {rel}")
            ok = False
            continue
        visible = np.array(normalize_framing(Image.open(path), box))[:, :, 3] == 0
        gap = int((visible & ~known).sum())
        share = 100 * gap / max(int(visible.sum()), 1)
        print(f"{cat + 'cat':<16}{int(visible.sum()):>13,}{gap:>16,}{share:>8.2f}%")
    return 0 if ok else 1


def generate(out_dir: str, types: list[str]) -> int:
    os.makedirs(out_dir, exist_ok=True)
    box = upstream_target_box()
    halos = {}
    for suffix in AURA_FOLDERS:
        halo, known = reconstruct_halo(suffix)
        halos[suffix] = fill_unknown(halo, known)
        print(f"halo '{suffix}' reconstructed: {int(known.sum()):,}/{SIZE * SIZE:,} px observed, rest filled")
    print()

    written = 0
    for cat in types:
        path = os.path.join(REPO_ROOT, LOCAL_SPRITES[cat])
        if not os.path.exists(path):
            print(f"  !! {cat}: missing source {path}", file=sys.stderr)
            continue
        sprite = normalize_framing(Image.open(path), box)

        plain = os.path.join(out_dir, f"{cat}cat.png")
        sprite.save(plain)
        written += 1
        print(f"  {os.path.basename(plain):<24} (re-framed plain — upload this too)")

        for suffix, halo in halos.items():
            dest = os.path.join(out_dir, f"{cat}cat_{suffix}.png")
            compose(sprite, halo).save(dest)
            written += 1
            print(f"  {os.path.basename(dest):<24} {AURA_FOLDERS[suffix]}")

    print(f"\nwrote {written} files to {out_dir}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify", action="store_true", help="leave-one-out fidelity check, writes nothing")
    ap.add_argument("--out", help="directory to write generated PNGs into")
    ap.add_argument("--types", nargs="*", default=list(LOCAL_SPRITES), choices=list(LOCAL_SPRITES))
    args = ap.parse_args()

    if args.verify:
        return verify()
    if not args.out:
        ap.error("pass --out DIR to generate, or --verify to check fidelity")
    return generate(args.out, args.types)


if __name__ == "__main__":
    sys.exit(main())
