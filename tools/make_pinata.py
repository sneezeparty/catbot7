"""Generates images/items/pinata.png (python tools/make_pinata.py images/items/pinata.png).
Piñata emoji: side-view cat piñata in a sombrero, 32x32 -> 512x512.
Same idiom as the upstream item emojis: flat colours, thick black outline."""
import sys

import numpy as np
from PIL import Image

OUT = sys.argv[1]
H = W = 32
BLACK = (0, 0, 0, 255)
CLEAR = (0, 0, 0, 0)

g = np.zeros((H, W, 4), np.uint8)
fill = np.zeros((H, W), bool)      # piñata body (gets stripes)
hat = np.zeros((H, W), bool)


def rect(m, y0, y1, x0, x1):
    m[max(0, y0):y1 + 1, max(0, x0):x1 + 1] = True


# ---- cat-shaped body -----------------------------------------------------
rect(fill, 15, 22, 5, 21)                      # torso
for y, x in [(15, 5), (22, 5), (15, 21), (22, 21)]:
    fill[y, x] = False                         # rounded corners
rect(fill, 10, 19, 17, 28)                     # head
for y, x in [(19, 28), (19, 17)]:
    fill[y, x] = False
for x0 in (6, 9, 16, 19):                      # four stubby legs
    rect(fill, 23, 27, x0, x0 + 1)
# tail curling up off the back
for y, x in [(14, 4), (13, 4), (13, 3), (12, 3), (11, 3), (11, 2), (10, 2), (9, 2), (9, 3), (8, 3), (8, 4)]:
    fill[y, x] = True
# ears poking up either side of the sombrero crown
for y, x in [(7, 16), (7, 17), (7, 18), (6, 16), (6, 17), (5, 16)]:
    fill[y, x] = True
for y, x in [(7, 27), (7, 28), (7, 29), (6, 28), (6, 29), (5, 29)]:
    fill[y, x] = True

# ---- sombrero ------------------------------------------------------------
rect(hat, 9, 9, 14, 31)                        # brim
rect(hat, 8, 8, 13, 14); rect(hat, 8, 8, 30, 31)   # brim curls up
rect(hat, 3, 8, 20, 25)                        # crown
rect(hat, 2, 2, 21, 24)
fill &= ~hat

# ---- stripes: 2-row bands, fringe zig-zags every other column --------------
BANDS = [
    ((255, 92, 170), (214, 52, 132)),   # pink
    ((255, 214, 64), (226, 170, 30)),   # yellow
    ((64, 200, 230), (30, 150, 190)),   # cyan
    ((255, 140, 40), (214, 100, 20)),   # orange
    ((120, 214, 90), (70, 170, 60)),    # green
    ((176, 110, 236), (130, 70, 200)),  # purple
]
for y in range(H):
    for x in range(W):
        if fill[y, x]:
            k = y + (1 if x % 4 < 2 else 0)
            base, shade = BANDS[(k // 2) % len(BANDS)]
            g[y, x] = (*(base if k % 2 else shade), 255)

STRAW = (240, 200, 110, 255)
STRAW_SH = (204, 156, 72, 255)
RED = (222, 40, 52, 255)
GREEN = (40, 160, 80, 255)
for y in range(H):
    for x in range(W):
        if hat[y, x]:
            g[y, x] = STRAW
g[9, 14:32] = STRAW_SH                        # brim underside
g[8, 20:26] = STRAW_SH
g[6, 20:26] = RED                             # hat band
for x in (21, 24):
    g[6, x] = GREEN
for x in (16, 19, 28):                        # brim trim dots
    g[9, x] = RED

# ---- auto outline ----------------------------------------------------------
solid = fill | hat
edge = np.zeros_like(solid)
for y in range(H):
    for x in range(W):
        if solid[y, x]:
            continue
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            yy, xx = y + dy, x + dx
            if 0 <= yy < H and 0 <= xx < W and solid[yy, xx]:
                edge[y, x] = True
                break
g[edge] = BLACK
# line between brim and head so the hat sits ON the cat
for x in range(15, 31):
    if fill[10, x]:
        g[10, x] = BLACK

# ---- hanging string ----------------------------------------------------------
for y in range(0, 14):
    if not solid[y, 11] and not edge[y, 11]:
        g[y, 11] = (90, 70, 50, 255)

# ---- face ----------------------------------------------------------------------
for y in (12, 13, 14):                        # two big eyes
    g[y, 19:21] = BLACK; g[y, 25:27] = BLACK
g[12, 20] = (255, 255, 255, 255); g[12, 26] = (255, 255, 255, 255)
g[15, 22:24] = (255, 110, 150, 255)           # nose
g[16, 22] = BLACK; g[16, 23] = BLACK          # :3 mouth
g[17, 21] = BLACK; g[17, 24] = BLACK
for x in (29, 30, 31):                        # whiskers
    g[15, x] = BLACK; g[17, x] = BLACK

# feet: black soles
for x0 in (6, 9, 16, 19):
    g[28, x0:x0 + 2] = BLACK

Image.fromarray(g).resize((512, 512), Image.NEAREST).save(OUT)
