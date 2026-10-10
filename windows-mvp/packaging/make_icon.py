"""Draw Alfred's icon and installer art in code (Pillow), so the repo needs no binary images.

  python packaging/make_icon.py build      writes build/alfred.ico, alfred.png and the installer's
                                           wizard images (wizard-*.bmp)

The icon is a glossy sky-blue disc with a white "A" (the Frutiger Aero look of the app's window).
packaging/build.ps1 runs this before PyInstaller (exe icons) and Inno Setup (installer art).
"""

import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
SKY_TOP, SKY_BOTTOM = (156, 222, 255), (16, 108, 210)
RIM = (10, 72, 150)
WHITE = (255, 255, 255)
SCALE = 4  # draw big, then scale down: smooth edges without anti-aliasing support in ImageDraw


def _vertical(size, top, bottom) -> Image.Image:
    """An RGB image fading from `top` (first row) to `bottom` (last row)."""
    w, h = size
    fade = Image.linear_gradient("L").resize((w, h))
    return Image.composite(Image.new("RGB", (w, h), bottom), Image.new("RGB", (w, h), top), fade)


def _ellipse_mask(size, box, blur=0.0) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).ellipse(box, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(blur)) if blur else mask


def _overlay(canvas: Image.Image, color, mask: Image.Image) -> None:
    """Blend `color` (RGBA) over the canvas where the mask is set, keeping what is underneath."""
    layer = Image.new("RGBA", canvas.size, color[:3] + (0,))
    layer.putalpha(mask.point(lambda v: v * color[3] // 255))
    canvas.alpha_composite(layer)


def draw_icon(size: int = 256) -> Image.Image:
    """The app icon, RGBA, `size` x `size`."""
    s = size * SCALE
    canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    m = s * 0.06  # room for the soft shadow
    disc = (m, m * 0.7, s - m, s - m * 1.3)

    # A soft shadow under the disc.
    shadow = _ellipse_mask((s, s), (disc[0], disc[1] + s * 0.025, disc[2], disc[3] + s * 0.035), blur=s * 0.02)
    _overlay(canvas, (6, 40, 90, 110), shadow)

    # The sky-blue disc with a darker rim.
    body = _vertical((s, s), SKY_TOP, SKY_BOTTOM).convert("RGBA")
    rim_mask = _ellipse_mask((s, s), disc)
    canvas.paste(Image.new("RGBA", (s, s), RIM + (255,)), (0, 0), rim_mask)
    inset = s * 0.018
    inner = (disc[0] + inset, disc[1] + inset, disc[2] - inset, disc[3] - inset)
    inner_mask = _ellipse_mask((s, s), inner)
    canvas.paste(body, (0, 0), inner_mask)

    # Light coming up from the bottom edge (Aero's inner glow).
    glow = _ellipse_mask((s, s), (s * 0.2, s * 0.55, s * 0.8, s * 1.0), blur=s * 0.06)
    glow = ImageChops.multiply(glow, inner_mask)
    _overlay(canvas, (170, 245, 255, 120), glow)

    # The white "A", with a soft blue shadow so it reads on the light top of the disc.
    letter = Image.new("L", (s, s), 0)
    pen = ImageDraw.Draw(letter)
    width = round(s * 0.105)
    apex, left, right = (s * 0.5, s * 0.26), (s * 0.29, s * 0.76), (s * 0.71, s * 0.76)
    pen.line([left, apex, right], fill=255, width=width, joint="curve")
    bar_y = s * 0.585
    pen.line([(s * 0.375, bar_y), (s * 0.625, bar_y)], fill=255, width=round(width * 0.8))
    letter_shadow = letter.filter(ImageFilter.GaussianBlur(s * 0.012))
    _overlay(canvas, (4, 50, 120, 140), ImageChops.offset(letter_shadow, 0, round(s * 0.012)))
    _overlay(canvas, WHITE + (255,), letter)

    # The gloss: a bright cap over the top half that fades out towards the middle.
    gloss_box = (disc[0] + s * 0.07, disc[1] + s * 0.02, disc[2] - s * 0.07, s * 0.56)
    gloss_mask = _ellipse_mask((s, s), gloss_box)
    fade = Image.linear_gradient("L").resize((s, s)).point(lambda v: max(0, 175 - v * 1.6))
    gloss = ImageChops.multiply(ImageChops.multiply(gloss_mask, fade), inner_mask)
    _overlay(canvas, WHITE + (255,), gloss)

    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def write_ico(path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    big = draw_icon(256)
    sharp = [draw_icon(n) for n in ICO_SIZES if n < 256]  # each size drawn on its own, not shrunk from 256
    big.save(path, format="ICO", sizes=[(n, n) for n in ICO_SIZES], append_images=sharp)
    return path


def wizard_images(folder: Path) -> list[Path]:
    """Inno Setup's art: the welcome/finish side panel and the small header logo, at 100% and 200% DPI.
    24-bit BMPs on an opaque background, which every Inno Setup 6 version shows correctly."""
    folder = Path(folder)
    written = []
    for scale in (1, 2):
        w, h = 164 * scale, 314 * scale
        panel = _vertical((w, h), (214, 243, 255), (72, 170, 236)).convert("RGBA")
        bubbles = Image.new("L", (w, h), 0)
        pen = ImageDraw.Draw(bubbles)
        for cx, cy, r in ((0.15, 0.78, 0.10), (0.82, 0.68, 0.14), (0.55, 0.92, 0.07), (0.9, 0.12, 0.06)):
            pen.ellipse((w * (cx - r), h * cy - w * r, w * (cx + r), h * cy + w * r), fill=70)
        _overlay(panel, WHITE + (255,), bubbles.filter(ImageFilter.GaussianBlur(scale)))
        icon = draw_icon(round(w * 0.62))
        panel.alpha_composite(icon, ((w - icon.width) // 2, round(h * 0.16)))
        written.append(_bmp(panel, folder / f"wizard-large-{scale * 100}.bmp"))

        n = 55 * scale
        header = Image.new("RGBA", (n, n), WHITE + (255,))
        header.alpha_composite(draw_icon(n))
        written.append(_bmp(header, folder / f"wizard-small-{scale * 100}.bmp"))
    return written


def _bmp(image: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, format="BMP")
    return path


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    folder = Path(args[0] if args else "build")
    written = [write_ico(folder / "alfred.ico")]
    png = folder / "alfred.png"
    draw_icon(256).save(png)
    written += [png, *wizard_images(folder)]
    for path in written:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
