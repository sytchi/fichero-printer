"""Dependency-light label renderer, kept separate for unit testing."""

import json
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .artwork import to_printable

PRINTHEAD_PX = 96
DOTS_PER_MM = 8
DEFAULT_MARGIN_DOTS = DOTS_PER_MM
DEFAULT_MAX_LINES = 3
# The date is a footnote under the name, and the name is printed bold so it
# stays readable on a shelf.
DATE_SIZE_RATIO = 0.55
# A name long enough to wrap onto three lines used to squeeze the date down to
# a few unreadable dots, so give it a floor and let the name shrink instead.
MIN_DATE_DOTS = 12
# Labels are tiny, so the on-screen preview is scaled up.
PREVIEW_SCALE = 2
ICON_SIDES = ("left", "right")
DEFAULT_ICON_SIDE = "left"
ARTWORK_MODES = ("icon", "full")
DEFAULT_ARTWORK_MODE = "icon"

# Pillow's built-in font only covers basic Latin: every accented character came
# out as a box on the tape. The bundled DejaVu faces carry the accents and give
# a real bold instead of an outlined fake one.
FONT_DIR = Path(__file__).parent / "fonts"
FONTS = {False: FONT_DIR / "DejaVuSans.ttf", True: FONT_DIR / "DejaVuSans-Bold.ttf"}
# Home Assistant keeps its Material Design Icons in the frontend, so the
# integration carries the webfont to be able to draw one on the tape. Some file
# transports refuse to create the subdirectory, so the assets are also accepted
# next to the module itself.
ICON_DIRS = (Path(__file__).parent / "icons", Path(__file__).parent)
ICON_FONT_NAME = "materialdesignicons-webfont.ttf"
ICON_CODEPOINTS_NAME = "mdi-codepoints.json"
_ICON_CODEPOINTS: dict[str, int] = {}


def _icon_file(name: str) -> Path:
    for directory in ICON_DIRS:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return ICON_DIRS[0] / name


@lru_cache(maxsize=128)
def _font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype(str(FONTS[bold]), size)
    except OSError:
        # Without the bundled files there is still something to print with,
        # even though accented characters will render as boxes.
        return ImageFont.load_default(size=size)


def _icon_codepoints() -> dict[str, int]:
    # Never cache an empty result: the assets may simply not have been copied
    # yet, and a restart should not be needed once they are.
    if not _ICON_CODEPOINTS:
        try:
            _ICON_CODEPOINTS.update(
                json.loads(_icon_file(ICON_CODEPOINTS_NAME).read_text(encoding="utf-8"))
            )
        except OSError:
            return {}
    return _ICON_CODEPOINTS


@lru_cache(maxsize=32)
def _icon_font(size: int):
    return ImageFont.truetype(str(_icon_file(ICON_FONT_NAME)), size)


def icon_character(icon: str) -> str:
    """Translate an icon name such as mdi:pasta into its glyph."""
    name = icon.strip().removeprefix("mdi:").strip()
    codepoint = _icon_codepoints().get(name)
    if codepoint is None:
        raise ValueError(f"Unknown icon: {icon}")
    return chr(codepoint)


def _date_size(title_size: int) -> int:
    """Keep the date readable without ever printing it larger than the name."""
    return min(title_size, max(MIN_DATE_DOTS, round(title_size * DATE_SIZE_RATIO)))


def _line_width(draw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _wrap(draw, words: list[str], font, span: int, max_lines: int) -> list[str] | None:
    """Greedily wrap words into at most max_lines lines no wider than span."""
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or _line_width(draw, candidate, font) <= span:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) == max_lines:
            return None
    lines.append(current)
    if len(lines) > max_lines:
        return None
    if any(_line_width(draw, line, font) > span for line in lines):
        return None
    return lines


def _height(draw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[3] - bbox[1]


def _block_height(draw, lines: list[str], font, gap: int) -> int:
    return sum(_height(draw, line, font) for line in lines) + gap * (len(lines) - 1)


def render_label_image(
    text: str,
    label_rows: int,
    margin_dots: int = DEFAULT_MARGIN_DOTS,
    offset_dots: int = 0,
    max_lines: int = DEFAULT_MAX_LINES,
    date: str | None = None,
    icon: str | None = None,
    icon_side: str = DEFAULT_ICON_SIDE,
    artwork: bytes | None = None,
    artwork_mode: str = DEFAULT_ARTWORK_MODE,
) -> Image.Image:
    """Lay the label out as it will be printed and return it as an image.

    `margin_dots` is the blank border kept on every side. `offset_dots` shifts
    everything that is printed along the label to compensate for a printer
    whose paper stops short of the label edge; positive values move it to the
    right as the label is read. The shift is reserved before the text is sized,
    so text that fills the label can still move, and nothing is printed past
    the margin. `date` is printed in a smaller font on its own line under the
    text, which is then printed bold. `icon` is a Material Design Icons name
    printed as a square pictogram on the `icon_side` end of the label.
    `artwork` is a generated picture: in `icon` mode it takes the place of the
    pictogram, in `full` mode it is printed alone across the whole label.
    """
    # Pasted line breaks and repeated whitespace are ordinary word separators;
    # where the text breaks is decided by whatever gives the largest letters.
    if artwork_mode not in ARTWORK_MODES:
        raise ValueError(f"Artwork mode must be one of {', '.join(ARTWORK_MODES)}")
    margin_dots = max(0, int(margin_dots))
    offset_dots = int(offset_dots)
    if artwork is not None and artwork_mode == "full":
        return _render_full_artwork(artwork, label_rows, margin_dots, offset_dots)

    words = text.split()
    date = (date or "").strip()
    icon = (icon or "").strip()
    side = (icon_side or DEFAULT_ICON_SIDE).strip().lower()
    if side not in ICON_SIDES:
        raise ValueError(f"Icon side must be one of {', '.join(ICON_SIDES)}")
    if not words and not date:
        raise ValueError("Text cannot be empty")
    max_lines = max(1, int(max_lines))

    usable_height = PRINTHEAD_PX - 2 * margin_dots
    if usable_height <= 0:
        raise ValueError("Margins leave no room for text")

    # The pictogram is a square as tall as the printable strip; the text gets
    # whatever is left on the other side of it.
    icon_glyph = icon_character(icon) if icon and artwork is None else ""
    icon_bitmap = (
        to_printable(artwork, (usable_height, usable_height)) if artwork is not None else None
    )
    icon_size = usable_height if (icon_glyph or icon_bitmap) else 0
    icon_gap = margin_dots if icon_size else 0

    text_area = label_rows - 2 * margin_dots - icon_size - icon_gap
    span = text_area - abs(offset_dots)
    if span <= 0:
        raise ValueError("Margins leave no room for text")

    canvas = Image.new("1", (label_rows, PRINTHEAD_PX), 1)
    draw = ImageDraw.Draw(canvas)
    if not words:
        # A date on its own is just an ordinary one-line label.
        words, date = date.split(), ""
    bold = bool(date)

    best = None
    low, high = 6, min(PRINTHEAD_PX, span)
    while low <= high:
        size = (low + high) // 2
        font = _font(size, bold)
        gap = max(1, size // 6)
        lines = _wrap(draw, words, font, span, max_lines)
        if lines is None:
            high = size - 1
            continue
        height = _block_height(draw, lines, font, gap)
        date_font = None
        if date:
            date_font = _font(_date_size(size))
            if _line_width(draw, date, date_font) > span:
                high = size - 1
                continue
            height += gap + _height(draw, date, date_font)
        if height <= usable_height:
            best = (font, lines, gap, date_font, height)
            low = size + 1
        else:
            high = size - 1
    if best is None:
        raise ValueError("Text cannot fit on this label")

    font, lines, gap, date_font, height = best
    boxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
    date_box = draw.textbbox((0, 0), date, font=date_font) if date else None
    width = max(box[2] - box[0] for box in boxes)
    if date_box:
        width = max(width, date_box[2] - date_box[0])

    # Canvas x runs the way the label is read, so the offset is simply added to
    # every block and then clamped inside the printable strip.
    def place(block_width: int, left_edge: int) -> int:
        shifted = left_edge + offset_dots
        return max(margin_dots, min(shifted, label_rows - margin_dots - block_width))

    text_left = margin_dots + (icon_size + icon_gap if side == "left" else 0)
    block_x = place(width, text_left + (text_area - width) // 2)
    icon_left = margin_dots if side == "left" else label_rows - margin_dots - icon_size
    y = (PRINTHEAD_PX - height) // 2
    for line, box in zip(lines, boxes):
        draw.text(
            (block_x + (width - (box[2] - box[0])) // 2 - box[0], y - box[1]),
            line,
            font=font,
            fill=0,
        )
        y += box[3] - box[1] + gap
    if date_box:
        draw.text(
            (block_x + (width - (date_box[2] - date_box[0])) // 2 - date_box[0], y - date_box[1]),
            date,
            font=date_font,
            fill=0,
        )
    if icon_bitmap is not None:
        canvas.paste(icon_bitmap, (place(icon_size, icon_left), margin_dots))
    elif icon_glyph:
        icon_font = _icon_font(icon_size)
        icon_box = draw.textbbox((0, 0), icon_glyph, font=icon_font)
        icon_x = place(icon_size, icon_left) + (icon_size - (icon_box[2] - icon_box[0])) // 2 - icon_box[0]
        icon_y = margin_dots + (usable_height - (icon_box[3] - icon_box[1])) // 2 - icon_box[1]
        draw.text((icon_x, icon_y), icon_glyph, font=icon_font, fill=0)
    return canvas


def _render_full_artwork(
    artwork: bytes, label_rows: int, margin_dots: int, offset_dots: int
) -> Image.Image:
    """Print a generated picture alone, filling the label."""
    usable_height = PRINTHEAD_PX - 2 * margin_dots
    usable_width = label_rows - 2 * margin_dots - abs(offset_dots)
    if usable_height <= 0 or usable_width <= 0:
        raise ValueError("Margins leave no room for the picture")
    picture = to_printable(artwork, (usable_width, usable_height), crop_to_band=True)
    canvas = Image.new("1", (label_rows, PRINTHEAD_PX), 1)
    left = margin_dots + (usable_width - picture.width) // 2 + offset_dots
    left = max(margin_dots, min(left, label_rows - margin_dots - picture.width))
    canvas.paste(picture, (left, margin_dots + (usable_height - picture.height) // 2))
    return canvas


def render_text_raster(
    text: str,
    label_rows: int,
    margin_dots: int = DEFAULT_MARGIN_DOTS,
    offset_dots: int = 0,
    max_lines: int = DEFAULT_MAX_LINES,
    date: str | None = None,
    icon: str | None = None,
    icon_side: str = DEFAULT_ICON_SIDE,
    artwork: bytes | None = None,
    artwork_mode: str = DEFAULT_ARTWORK_MODE,
) -> bytes:
    """Render the label and pack it the way the printer expects it."""
    canvas = render_label_image(
        text, label_rows, margin_dots, offset_dots, max_lines, date, icon, icon_side,
        artwork, artwork_mode,
    )
    # Printer raster is 96 pixels wide and one row per dot along label length.
    rotated = canvas.rotate(90, expand=True)
    return bytes(byte ^ 0xFF for byte in rotated.tobytes())


def render_preview_png(
    text: str,
    label_rows: int,
    margin_dots: int = DEFAULT_MARGIN_DOTS,
    offset_dots: int = 0,
    max_lines: int = DEFAULT_MAX_LINES,
    date: str | None = None,
    icon: str | None = None,
    icon_side: str = DEFAULT_ICON_SIDE,
    artwork: bytes | None = None,
    artwork_mode: str = DEFAULT_ARTWORK_MODE,
    scale: int = PREVIEW_SCALE,
) -> bytes:
    """Render the same layout the printer gets, as a PNG for the dashboard."""
    canvas = render_label_image(
        text, label_rows, margin_dots, offset_dots, max_lines, date, icon, icon_side,
        artwork, artwork_mode,
    )
    scale = max(1, int(scale))
    if scale > 1:
        canvas = canvas.resize((canvas.width * scale, canvas.height * scale), Image.NEAREST)
    buffer = BytesIO()
    canvas.convert("L").save(buffer, format="PNG")
    return buffer.getvalue()
