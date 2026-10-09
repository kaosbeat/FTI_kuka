#!/usr/bin/env python3
"""Generate the NextUI PAK launcher icon (icon.png).

NextUI shows PAKs in an icon grid; a PAK without an icon may render as a blank
tile or not at all. This renders a crisp 128x128 ``icon.png`` in the app's own
theme (navy background + yellow, matching launch.sh / config.json) using the
bundled DejaVuSansMono font, so no external image tooling is required.

Usage:
    python scripts/make_icon.py [out.png]     # default: pak/RTRREMOTE.pak/icon.png
    python scripts/make_icon.py /tmp/x.png 256  # optional size arg
"""

import os
import sys

import pygame

# App theme colors (mirror config.json "theme").
NAVY = (0, 0, 128)        # bar_bg
YELLOW = (255, 255, 0)    # bar / label
WHITE = (255, 255, 255)   # state_val


def _font_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "..", "pak", "RTRREMOTE.pak", "fonts", "DejaVuSansMono.ttf")


def make_icon(out_path, size=128):
    pygame.init()
    pygame.font.init()
    font = pygame.font.Font(_font_path(), int(size * 0.42))

    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    surf.fill(NAVY)
    # Yellow frame, inset so it stays inside the tile.
    pygame.draw.rect(surf, YELLOW, (0, 0, size, size), max(3, size // 32))

    # "RTR" (the app) centered; a small cadence tick beneath for a bit of life.
    label = font.render("RTR", True, YELLOW, NAVY)
    surf.blit(label, (size // 2 - label.get_width() // 2,
                      size // 2 - label.get_height() // 2 - size // 16))
    tick = font.render("CAD", True, WHITE, NAVY)
    surf.blit(tick, (size // 2 - tick.get_width() // 2,
                     size // 2 + label.get_height() // 2 + size // 24))

    pygame.image.save(surf, out_path)
    pygame.quit()
    print(f"wrote {out_path} ({size}x{size})")


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "..", "pak", "RTRREMOTE.pak", "icon.png")
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 128
    make_icon(out, size)
