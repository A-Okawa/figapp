# -*- coding: utf-8 -*-
"""Draw the figapp icon (bars with error bars, in the app's paper colours) and write icons/figapp.png, .ico and .icns.
Run again only when the design changes:  python desktop/make_icons.py"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'icons')
BLUE, ORANGE, GREEN, INK = (0, 114, 178), (230, 159, 0), (0, 158, 115), (49, 51, 63)


def draw(size=1024, margin=100):
    """Square icon. margin: empty border (the Mac icon grid leaves about 10 % on each side)."""
    k = 4                                                     # draw large, then shrink for smooth edges
    S = size * k
    im = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m = margin * k
    body = S - 2 * m
    d.rounded_rectangle((m, m + 6 * k, S - m, S - m + 6 * k), radius=int(body * 0.225), fill=(0, 0, 0, 40))   # shadow
    d.rounded_rectangle((m, m, S - m, S - m), radius=int(body * 0.225), fill=(255, 255, 255, 255),
                        outline=(214, 220, 228, 255), width=4 * k)
    # axes
    x0, y0 = m + body * 0.20, m + body * 0.80
    x1, y1 = m + body * 0.84, m + body * 0.16
    lw = int(body * 0.032)
    d.line((x0, y1, x0, y0), fill=INK, width=lw)
    d.line((x0 - lw / 2, y0, x1, y0), fill=INK, width=lw)
    # bars with error bars
    bw = body * 0.14
    for i, (h, err, col) in enumerate(((0.30, 0.06, BLUE), (0.46, 0.07, ORANGE), (0.40, 0.06, GREEN))):
        bx = x0 + body * 0.09 + i * body * 0.20
        top = y0 - body * h
        d.rectangle((bx, top, bx + bw, y0 - lw / 2), fill=col)
        cx = bx + bw / 2
        ew = int(body * 0.018)
        d.line((cx, top - body * err, cx, top), fill=INK, width=ew)
        d.line((cx - bw * 0.28, top - body * err, cx + bw * 0.28, top - body * err), fill=INK, width=ew)
    return im.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    mac = draw(1024, 100)
    mac.save(os.path.join(OUT, 'figapp.png'))
    mac.save(os.path.join(OUT, 'figapp.icns'))
    win = draw(256, 8)
    win.save(os.path.join(OUT, 'figapp.ico'), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print('icons written to', OUT)


if __name__ == '__main__':
    main()
