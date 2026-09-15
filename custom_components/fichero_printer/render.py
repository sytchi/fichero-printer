"""Dependency-light label renderer, kept separate for unit testing."""

from PIL import Image, ImageDraw, ImageFont

PRINTHEAD_PX = 96
DOTS_PER_MM = 8
DEFAULT_MARGIN_DOTS = DOTS_PER_MM


def render_text_raster(
    text: str,
    label_rows: int,
    margin_dots: int = DEFAULT_MARGIN_DOTS,
    offset_dots: int = 0,
) -> bytes:
    """Fit text on one line at the largest possible size.

    `margin_dots` is the blank border kept on every side. `offset_dots` shifts
    the text along the label to compensate for a printer whose paper stops
    short of the label edge; positive values move the text towards the end of
    the label. The shift is reserved before the text is sized, so text that
    fills the label can still move, and it is never printed past the margin.
    """
    # Treat pasted line breaks and repeated whitespace as ordinary spaces. A
    # label should only become smaller horizontally, never wrap vertically.
    text = " ".join(text.split())
    if not text:
        raise ValueError("Text cannot be empty")

    margin_dots = max(0, int(margin_dots))
    offset_dots = int(offset_dots)
    full_span = label_rows - 2 * margin_dots
    span = full_span - abs(offset_dots)
    usable_height = PRINTHEAD_PX - 2 * margin_dots
    if span <= 0 or usable_height <= 0:
        raise ValueError("Margins leave no room for text")

    canvas = Image.new("1", (label_rows, PRINTHEAD_PX), 1)
    draw = ImageDraw.Draw(canvas)
    best = None
    low, high = 6, min(PRINTHEAD_PX, span)
    while low <= high:
        size = (low + high) // 2
        font = ImageFont.load_default(size=size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= span and bbox[3] - bbox[1] <= usable_height:
            best = (font, bbox)
            low = size + 1
        else:
            high = size - 1
    if best is None:
        raise ValueError("Text cannot fit on this label")
    font, bbox = best
    width = bbox[2] - bbox[0]
    start_row = margin_dots + (full_span - width) // 2 + offset_dots
    start_row = max(margin_dots, min(start_row, label_rows - margin_dots - width))
    # Rows leave the printer in the opposite order to the canvas x axis, so the
    # text is placed by mirroring its first printed row.
    x = label_rows - 1 - start_row - width - bbox[0]
    y = (PRINTHEAD_PX - (bbox[3] - bbox[1])) // 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=0)
    # Printer raster is 96 pixels wide and one row per dot along label length.
    rotated = canvas.rotate(90, expand=True)
    return bytes(byte ^ 0xFF for byte in rotated.tobytes())
