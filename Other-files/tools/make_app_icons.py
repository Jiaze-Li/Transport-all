"""Icons of the standalone applications, from the main program's icon: the
same picture turned to another colour, with the application's name on it.

    python make_app_icons.py <main's Other-files/assets> <output folder>

For each application it writes <Name>_icon.png (512 px, the window icon),
<Name>_icon_64.png and <Name>.ico (16 to 256 px, the .exe and taskbar icon).
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SOURCE = "Magneto_Electro_Optical_Microwave_Transport_NH_master.png"
# name, label, hue turn in degrees (the main icon is blue), label band colour
APPS = (
    ("Transport_PPMS", "PPMS", 55, (86, 36, 140)),      # violet
    ("Transport_RT", "RT", 150, (150, 30, 44)),          # red
    ("Transport_MOKE", "MOKE", -85, (22, 110, 60)),      # green
    ("Transport_FMR", "FMR", -170, (160, 96, 12)),       # amber
)
FONTS = ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def font(size):
    for path in FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def turned(image, degrees):
    alpha = image.getchannel("A")
    h, s, v = image.convert("RGB").convert("HSV").split()
    h = h.point(lambda value: (value + round(degrees / 360 * 256)) % 256)
    out = Image.merge("HSV", (h, s, v)).convert("RGB")
    out.putalpha(alpha)
    return out


def labelled(image, text, colour):
    size = image.width
    out = image.copy()
    band = Image.new("RGBA", out.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(band)
    top, bottom, side = round(size * 0.67), round(size * 0.93), round(size * 0.08)
    draw.rounded_rectangle((side, top, size - side, bottom), radius=round(size * 0.07), fill=(*colour, 235),
                           outline=(255, 255, 255, 235), width=max(2, round(size * 0.012)))
    face = font(round(size * (0.21 if len(text) <= 3 else 0.185)))
    box = draw.textbbox((0, 0), text, font=face)
    draw.text(((size - (box[2] - box[0])) / 2 - box[0], (top + bottom - (box[3] - box[1])) / 2 - box[1]), text,
              font=face, fill=(255, 255, 255, 255))
    # Only inside the icon's own rounded shape.
    band.putalpha(Image.composite(band.getchannel("A"), Image.new("L", out.size, 0), out.getchannel("A")))
    return Image.alpha_composite(out, band)


def main(source_folder, output_folder):
    master = Image.open(Path(source_folder) / SOURCE).convert("RGBA")
    output = Path(output_folder)
    output.mkdir(parents=True, exist_ok=True)
    for name, text, degrees, colour in APPS:
        icon = labelled(turned(master, degrees), text, colour)
        icon.resize((512, 512), Image.LANCZOS).save(output / f"{name}_icon.png")
        icon.resize((64, 64), Image.LANCZOS).save(output / f"{name}_icon_64.png")
        icon.resize((256, 256), Image.LANCZOS).save(output / f"{name}.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
        print(name)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
