"""Dependency-light label renderer, kept separate for unit testing."""

from PIL import Image, ImageDraw, ImageFont

PRINTHEAD_PX = 96
DOTS_PER_MM = 8
DEFAULT_MARGIN_DOTS = DOTS_PER_MM
DEFAULT_MAX_LINES = 3


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


def _block_height(draw, lines: list[str], font, gap: int) -> int:
    total = 0
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        total += bbox[3] - bbox[1]
    return total + gap * (len(lines) - 1)


def render_text_raster(
    text: str,
    label_rows: int,
    margin_dots: int = DEFAULT_MARGIN_DOTS,
    offset_dots: int = 0,
    max_lines: int = DEFAULT_MAX_LINES,
) -> bytes:
    """Fit text at the largest size that fits, wrapping onto up to max_lines.

    `margin_dots` is the blank border kept on every side. `offset_dots` shifts
    the text along the label to compensate for a printer whose paper stops
    short of the label edge; positive values move the text towards the end of
    the label. The shift is reserved before the text is sized, so text that
    fills the label can still move, and it is never printed past the margin.
    """
    # Pasted line breaks and repeated whitespace are ordinary word separators;
    # where the text breaks is decided by whatever gives the largest letters.
    words = text.split()
    if not words:
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
    best = None
    low, high = 6, min(PRINTHEAD_PX, span)
    while low <= high:
        size = (low + high) // 2
        font = ImageFont.load_default(size=size)
        gap = max(1, size // 6)
        lines = _wrap(draw, words, font, span, max_lines)
        if lines is not None and _block_height(draw, lines, font, gap) <= usable_height:
            best = (font, lines, gap)
            low = size + 1
        else:
            high = size - 1
    if best is None:
        raise ValueError("Text cannot fit on this label")

    font, lines, gap = best
    boxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
    width = max(box[2] - box[0] for box in boxes)
    height = _block_height(draw, lines, font, gap)
    start_row = margin_dots + (full_span - width) // 2 + offset_dots
    start_row = max(margin_dots, min(start_row, label_rows - margin_dots - width))
    # Rows leave the printer in the opposite order to the canvas x axis, so the
    # block is placed by mirroring its first printed row.
    block_x = label_rows - 1 - start_row - width
    y = (PRINTHEAD_PX - height) // 2
    for line, box in zip(lines, boxes):
        line_width = box[2] - box[0]
        draw.text(
            (block_x + (width - line_width) // 2 - box[0], y - box[1]),
            line,
            font=font,
            fill=0,
        )
        y += box[3] - box[1] + gap
    # Printer raster is 96 pixels wide and one row per dot along label length.
    rotated = canvas.rotate(90, expand=True)
    return bytes(byte ^ 0xFF for byte in rotated.tobytes())
