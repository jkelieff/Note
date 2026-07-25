#!/usr/bin/env python3
"""
Generate the app icon (app.ico for Windows, app.icns for macOS) from a single
vector-ish drawing, so the desktop app has a proper taskbar/Dock icon.

Run:  python make_icon.py
The PyInstaller spec picks up app.ico / app.icns automatically if present.
"""

from PIL import Image, ImageDraw

SIZE = 1024
BG_TOP = (44, 49, 62)        # #2c313e  (matches the app panel)
BG_BOTTOM = (20, 23, 30)     # #14171e
NOTE = (233, 236, 244)       # near-white "paper"
LINE = (150, 158, 178)       # transcript lines
ACCENT = (224, 62, 62)       # #e03e3e record red


def rounded(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def make_base() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Vertical gradient background inside a rounded square.
    grad = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(SIZE):
        t = y / SIZE
        r = int(BG_TOP[0] * (1 - t) + BG_BOTTOM[0] * t)
        g = int(BG_TOP[1] * (1 - t) + BG_BOTTOM[1] * t)
        b = int(BG_TOP[2] * (1 - t) + BG_BOTTOM[2] * t)
        gd.line([(0, y), (SIZE, y)], fill=(r, g, b, 255))

    mask = Image.new("L", (SIZE, SIZE), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([40, 40, SIZE - 40, SIZE - 40], radius=210, fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)

    # A "note card" tilted slightly, with transcript lines.
    card = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    cx0, cy0, cx1, cy1 = 300, 250, 724, 774
    cd.rounded_rectangle([cx0, cy0, cx1, cy1], radius=48, fill=NOTE)
    # transcript lines
    for i, y in enumerate(range(cy0 + 90, cy1 - 70, 82)):
        w = (cx1 - 70) if i % 3 != 2 else (cx1 - 190)
        cd.rounded_rectangle([cx0 + 60, y, w, y + 26], radius=13, fill=LINE)
    card = card.rotate(-8, center=(SIZE // 2, SIZE // 2), resample=Image.BICUBIC)
    img.alpha_composite(card)

    # Record dot with a soft ring, bottom-right — the "record" cue.
    rc = (712, 712)
    d.ellipse([rc[0] - 150, rc[1] - 150, rc[0] + 150, rc[1] + 150],
              fill=(20, 23, 30, 255))
    d.ellipse([rc[0] - 118, rc[1] - 118, rc[0] + 118, rc[1] + 118],
              outline=ACCENT, width=22)
    d.ellipse([rc[0] - 66, rc[1] - 66, rc[0] + 66, rc[1] + 66], fill=ACCENT)
    return img


def main():
    base = make_base()

    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48),
                 (64, 64), (128, 128), (256, 256)]
    base.save("app.ico", sizes=ico_sizes)
    print("wrote app.ico")

    # ICNS needs square power-of-two sizes; Pillow builds the iconset from one.
    try:
        icns = base.resize((1024, 1024), Image.LANCZOS)
        icns.save("app.icns")
        print("wrote app.icns")
    except Exception as exc:
        print(f"skipped app.icns ({exc})")

    base.resize((256, 256), Image.LANCZOS).save("icon_preview.png")
    print("wrote icon_preview.png")


if __name__ == "__main__":
    main()
