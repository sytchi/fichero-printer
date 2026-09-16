"""Dependency-light label renderer, kept separate for unit testing."""

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

PRINTHEAD_PX = 96
DOTS_PER_MM = 8
DEFAULT_MARGIN_DOTS = DOTS_PER_MM
DEFAULT_MAX_LINES = 3
# The date is a footnote under the name, and the name is printed bold so it
# stays readable on a shelf.
DATE_SIZE_RATIO = 0.55
TITLE_STROKE = 1
# Labels are tiny, so the on-screen preview is scaled up.
PREVIEW_SCALE = 2


def _line_width(draw, text: str, font, stroke: int = 0) -> int:
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    return bbox[2] - bbox[0]


def _wrap(draw, words: list[str], font, span: int, max_lines: int, stroke: int) -> list[str] | None:
    """Greedily wrap words into at most max_lines lines no wider than span."""
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or _line_width(draw, candidate, font, stroke) <= span:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) == max_lines:
            return None
    lines.append(current)
    if len(lines) > max_lines:
        return None
    if any(_line_width(draw, line, font, stroke) > span for line in lines):
        return None
    return lines


def _height(draw, text: str, font, stroke: int = 0) -> int:
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    return bbox[3] - bbox[1]


def _block_height(draw, lines: list[str], font, gap: int, stroke: int) -> int:
    return sum(_height(draw, line, font, stroke) for line in lines) + gap * (len(lines) - 1)


def render_label_image(
    text: str,
    label_rows: int,
    margin_dots: int = DEFAULT_MARGIN_DOTS,
    offset_dots: int = 0,
    max_lines: int = DEFAULT_MAX_LINES,
    date: str | None = None,
) -> Image.Image:
    """Lay the label out as it will be printed and return it as an image.

    `margin_dots` is the blank border kept on every side. `offset_dots` shifts
    the text along the label to compensate for a printer whose paper stops
    short of the label edge; positive values move the text towards the end of
    the label. The shift is reserved before the text is sized, so text that
    fills the label can still move, and it is never printed past the margin.
    `date` is printed in a smaller font on its own line under the text, which
    is then printed bold.
    """
    # Pasted line breaks and repeated whitespace are ordinary word separators;
    # where the text breaks is decided by whatever gives the largest letters.
    words = text.split()
    date = (date or "").strip()
    if not words and not date:
        raise ValueError("Text cannot be empty")
    max_lines = max(1, int(max_lines))

    margin_dots = max(0, int(margin_dots))
    offset_dots = int(offset_dots)
    full_span = label_rows - 2 * margin_dots
    span = full_span - abs(offset_dots)
    usable_height = PRINTHEAD_PX - 2 * margin_dots
    if span <= 0 or usable_height <= 0:
        raise ValueError("Margins leave no room for text")

    canvas = Image.new("1", (label_rows, PRINTHEAD_PX), 1)
    draw = ImageDraw.Draw(canvas)
    if not words:
        # A date on its own is just an ordinary one-line label.
        words, date = date.split(), ""
    stroke = TITLE_STROKE if date else 0

    best = None
    low, high = 6, min(PRINTHEAD_PX, span)
    while low <= high:
        size = (low + high) // 2
        font = ImageFont.load_default(size=size)
        gap = max(1, size // 6)
        lines = _wrap(draw, words, font, span, max_lines, stroke)
        if lines is None:
            high = size - 1
            continue
        height = _block_height(draw, lines, font, gap, stroke)
        date_font = None
        if date:
            date_font = ImageFont.load_default(size=max(6, round(size * DATE_SIZE_RATIO)))
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
    boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=stroke) for line in lines]
    date_box = draw.textbbox((0, 0), date, font=date_font) if date else None
    width = max(box[2] - box[0] for box in boxes)
    if date_box:
        width = max(width, date_box[2] - date_box[0])
    start_row = margin_dots + (full_span - width) // 2 + offset_dots
    start_row = max(margin_dots, min(start_row, label_rows - margin_dots - width))
    # Rows leave the printer in the opposite order to the canvas x axis, so the
    # block is placed by mirroring its first printed row.
    block_x = label_rows - 1 - start_row - width
    y = (PRINTHEAD_PX - height) // 2
    for line, box in zip(lines, boxes):
        draw.text(
            (block_x + (width - (box[2] - box[0])) // 2 - box[0], y - box[1]),
            line,
            font=font,
            fill=0,
            stroke_width=stroke,
            stroke_fill=0,
        )
        y += box[3] - box[1] + gap
    if date_box:
        draw.text(
            (block_x + (width - (date_box[2] - date_box[0])) // 2 - date_box[0], y - date_box[1]),
            date,
            font=date_font,
            fill=0,
        )
    return canvas


def render_text_raster(
    text: str,
    label_rows: int,
    margin_dots: int = DEFAULT_MARGIN_DOTS,
    offset_dots: int = 0,
    max_lines: int = DEFAULT_MAX_LINES,
    date: str | None = None,
) -> bytes:
    """Render the label and pack it the way the printer expects it."""
    canvas = render_label_image(text, label_rows, margin_dots, offset_dots, max_lines, date)
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
    scale: int = PREVIEW_SCALE,
) -> bytes:
    """Render the same layout the printer gets, as a PNG for the dashboard."""
    canvas = render_label_image(text, label_rows, margin_dots, offset_dots, max_lines, date)
    scale = max(1, int(scale))
    if scale > 1:
        canvas = canvas.resize((canvas.width * scale, canvas.height * scale), Image.NEAREST)
    buffer = BytesIO()
    canvas.convert("L").save(buffer, format="PNG")
    return buffer.getvalue()
