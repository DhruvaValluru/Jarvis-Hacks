"""Make Alfred's icon and installer art (Pillow) from the logo the app itself shows.

  python packaging/make_icon.py build      writes build/alfred.ico, alfred.png and the installer's
                                           wizard images (wizard-*.bmp)

The icon is Alfred's logo, jarvis/app/ui/logo.png: a glossy blue sphere with a white bow tie (the Frutiger
Aero look of the app's window). The installer's side panel is drawn here, around it.
packaging/build.ps1 runs this before PyInstaller (exe icons) and Inno Setup (installer art).
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
LOGO = Path(__file__).resolve().parent.parent / "jarvis" / "app" / "ui" / "logo.png"
WHITE = (255, 255, 255)


def _vertical(size, top, bottom) -> Image.Image:
    """An RGB image fading from `top` (first row) to `bottom` (last row)."""
    w, h = size
    fade = Image.linear_gradient("L").resize((w, h))
    return Image.composite(Image.new("RGB", (w, h), bottom), Image.new("RGB", (w, h), top), fade)


def _overlay(canvas: Image.Image, color, mask: Image.Image) -> None:
    """Blend `color` (RGBA) over the canvas where the mask is set, keeping what is underneath."""
    layer = Image.new("RGBA", canvas.size, color[:3] + (0,))
    layer.putalpha(mask.point(lambda v: v * color[3] // 255))
    canvas.alpha_composite(layer)


def draw_icon(size: int = 256) -> Image.Image:
    """The app icon, RGBA, `size` x `size`: Alfred's logo (the app's own jarvis/app/ui/logo.png)."""
    with Image.open(LOGO) as logo:
        icon = logo.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    if size <= 48:  # the bow tie and dots stay crisp at taskbar and title-bar sizes
        icon = icon.filter(ImageFilter.UnsharpMask(radius=1, percent=60, threshold=2))
    return icon


def write_ico(path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    big = draw_icon(256)
    sharp = [draw_icon(n) for n in ICO_SIZES if n < 256]  # each size made from the full logo, not from 256
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
