#!/usr/bin/env python3
"""Regenerate the xmlstarlet raster assets from the authoritative logo vector.

img/xmlstarlet.svg is the source of truth for the logo; this script renders the
derived PNGs that GitHub and package registries want as bitmaps:

    social-preview.png  1280x640  repository social card
    icon-wordmark.png    512x512  square icon, full "xml*" wordmark
    icon-star.png        512x512  square icon, star only
    icon-stacked.png     512x512  square icon, star above "xml"

The PNGs are build products and are not tracked in git; regenerate them with:

    pip install cairosvg pillow
    python img/generate-assets.py [output-dir]   # default: the img/ directory

Only cairosvg and Pillow are required - the letters and the star are both read
out of the single committed SVG, so there is no font or reference-image
dependency.
"""
import io
import os
import re
import sys

import cairosvg
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SVG = os.path.join(HERE, "xmlstarlet.svg")
OUT = sys.argv[1] if len(sys.argv) > 1 else HERE
WHITE = (255, 255, 255, 255)


def render(svg, width):
    png = cairosvg.svg2png(bytestring=svg.encode(), output_width=width)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def autocrop(im):
    bbox = im.getbbox()
    return im.crop(bbox) if bbox else im


def place(logo, cw, ch, frac, bg=WHITE):
    """Centre logo on a cw x ch canvas, scaled to frac of the shorter fit."""
    base = Image.new("RGBA", (cw, ch), bg)
    lw = int(cw * frac)
    lh = int(lw * logo.height / logo.width)
    if lh > ch * frac:
        lh = int(ch * frac)
        lw = int(lh * logo.width / logo.height)
    base.alpha_composite(logo.resize((lw, lh)), ((cw - lw) // 2, (ch - lh) // 2))
    return base


def main():
    src = open(SVG, encoding="utf-8").read()

    # The deliverable holds the blue letter <path>s and one gold star <polygon>.
    # Derive a star-only and a letters-only variant from that single SVG so every
    # asset traces back to img/xmlstarlet.svg.
    m = re.search(r'<polygon points="[^"]*" fill="#fdb313"/>', src)
    if not m:
        sys.exit("star polygon (fill #fdb313) not found in " + SVG)
    star_el = m.group(0)

    letters_svg = src.replace(star_el, "")  # full logo minus the star
    pts = re.search(r'points="([^"]*)"', star_el).group(1)
    xy = [tuple(map(float, p.split(","))) for p in pts.split()]
    xs = [p[0] for p in xy]
    ys = [p[1] for p in xy]
    vb = "%.2f %.2f %.2f %.2f" % (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
    star_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="%s">%s</svg>' % (vb, star_el)
    )

    logo = render(src, 1600)
    star = autocrop(render(star_svg, 1000))
    letters = autocrop(render(letters_svg, 1000))

    os.makedirs(OUT, exist_ok=True)

    # social preview card
    place(logo, 1280, 640, 0.66).convert("RGB").save(os.path.join(OUT, "social-preview.png"))
    # square icon: star only
    place(star, 512, 512, 0.72).convert("RGB").save(os.path.join(OUT, "icon-star.png"))
    # square icon: full wordmark
    place(logo, 512, 512, 0.80).convert("RGB").save(os.path.join(OUT, "icon-wordmark.png"))

    # square icon: star stacked above the letters, the pair vertically centred
    s = 512
    star_h = int(s * 0.42)
    star_w = int(star_h * star.width / star.height)
    let_w = int(s * 0.62)
    let_h = int(let_w * letters.height / letters.width)
    gap = int(s * 0.06)
    top = (s - (star_h + gap + let_h)) // 2
    stk = Image.new("RGBA", (s, s), WHITE)
    stk.alpha_composite(star.resize((star_w, star_h)), ((s - star_w) // 2, top))
    stk.alpha_composite(letters.resize((let_w, let_h)), ((s - let_w) // 2, top + star_h + gap))
    stk.convert("RGB").save(os.path.join(OUT, "icon-stacked.png"))

    print("wrote to %s: social-preview.png, icon-star.png, icon-wordmark.png, icon-stacked.png" % OUT)


if __name__ == "__main__":
    main()
