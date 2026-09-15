"""Tests for Home Assistant label auto-fitting."""

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "fichero_printer" / "render.py"
SPEC = importlib.util.spec_from_file_location("fichero_render", MODULE_PATH)
render = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render)


def test_short_text_fills_standard_label():
    raster = render.render_text_raster("Kitchen", 240)
    assert len(raster) == 240 * 12
    assert any(raster)


def test_long_text_keeps_every_word(monkeypatch):
    draw_text = render.ImageDraw.ImageDraw.text
    calls = []

    def record_text(self, position, text, *args, **kwargs):
        calls.append(text)
        return draw_text(self, position, text, *args, **kwargs)

    monkeypatch.setattr(render.ImageDraw.ImageDraw, "text", record_text)
    text = "A considerably longer label name"
    raster = render.render_text_raster(text, 240)
    assert len(raster) == 240 * 12
    assert any(raster)
    # The text may now be wrapped, but no word may be dropped or reordered.
    assert " ".join(calls) == text


def test_line_breaks_are_treated_as_word_separators(monkeypatch):
    draw_text = render.ImageDraw.ImageDraw.text
    calls = []

    def record_text(self, position, text, *args, **kwargs):
        calls.append(text)
        return draw_text(self, position, text, *args, **kwargs)

    monkeypatch.setattr(render.ImageDraw.ImageDraw, "text", record_text)
    render.render_text_raster("Best before\nFriday", 240)
    assert " ".join(calls) == "Best before Friday"


def test_date_label_fits():
    raster = render.render_text_raster("29-08-2026", 240)
    assert len(raster) == 240 * 12


def _ink_rows(raster, label_rows=240):
    """Row indices along the label that contain any ink."""
    row_bytes = len(raster) // label_rows
    return [
        row
        for row in range(label_rows)
        if any(raster[row * row_bytes:(row + 1) * row_bytes])
    ]


def _ink_columns(raster, label_rows=240):
    """Bit positions across the tape that contain any ink."""
    row_bytes = len(raster) // label_rows
    columns = set()
    for row in range(label_rows):
        chunk = raster[row * row_bytes:(row + 1) * row_bytes]
        for byte_index, byte in enumerate(chunk):
            for bit in range(8):
                if byte & (1 << (7 - bit)):
                    columns.add(byte_index * 8 + bit)
    return sorted(columns)


def test_margin_keeps_the_label_edges_blank():
    raster = render.render_text_raster("Kitchen", 240, margin_dots=8)
    rows = _ink_rows(raster)
    columns = _ink_columns(raster)
    assert rows[0] >= 8 and rows[-1] <= 240 - 1 - 8
    assert columns[0] >= 8 and columns[-1] <= render.PRINTHEAD_PX - 1 - 8


def test_offset_moves_text_that_fills_the_label():
    centred = _ink_rows(render.render_text_raster("Kitchen", 240, offset_dots=0))
    shifted = _ink_rows(render.render_text_raster("Kitchen", 240, offset_dots=16))
    # The font picked for the narrower span can differ by a pixel or two, so
    # assert the direction and that the shift lands close to the 16 requested.
    assert 14 <= shifted[0] - centred[0] <= 18


def test_offset_moves_short_text_both_ways():
    centred = _ink_rows(render.render_text_raster("A", 240, offset_dots=0))
    later = _ink_rows(render.render_text_raster("A", 240, offset_dots=16))
    earlier = _ink_rows(render.render_text_raster("A", 240, offset_dots=-16))
    assert later[0] - centred[0] == 16
    assert earlier[0] - centred[0] == -16


def test_offset_never_prints_past_the_margin():
    for offset in (-100, 100):
        rows = _ink_rows(render.render_text_raster("Kitchen", 240, margin_dots=8, offset_dots=offset))
        assert rows[0] >= 8
        assert rows[-1] <= 240 - 1 - 8


def test_margins_larger_than_the_label_are_rejected():
    with pytest.raises(ValueError, match="no room"):
        render.render_text_raster("Kitchen", 240, margin_dots=60)


def test_offset_larger_than_the_label_is_rejected():
    with pytest.raises(ValueError, match="no room"):
        render.render_text_raster("Kitchen", 240, offset_dots=500)


def _drawn_lines(monkeypatch, text, label_rows=240, **kwargs):
    draw_text = render.ImageDraw.ImageDraw.text
    calls = []

    def record_text(self, position, value, *args, **extra):
        calls.append(value)
        return draw_text(self, position, value, *args, **extra)

    monkeypatch.setattr(render.ImageDraw.ImageDraw, "text", record_text)
    raster = render.render_text_raster(text, label_rows, **kwargs)
    # Only the lines drawn for the final, largest fitting size are of interest;
    # the search draws nothing, so every recorded call belongs to the result.
    return calls, raster


def test_long_text_wraps_onto_several_lines(monkeypatch):
    lines, raster = _drawn_lines(monkeypatch, "A considerably longer label name")
    assert len(lines) > 1
    assert " ".join(lines) == "A considerably longer label name"
    assert len(raster) == 240 * 12


def test_wrapping_can_be_limited_to_one_line(monkeypatch):
    lines, _ = _drawn_lines(monkeypatch, "A considerably longer label name", max_lines=1)
    assert lines == ["A considerably longer label name"]


def test_wrapping_uses_a_larger_font_than_one_line():
    wrapped = render.render_text_raster("A considerably longer label name", 240)
    single = render.render_text_raster("A considerably longer label name", 240, max_lines=1)
    # More ink means bigger letters on the same label.
    assert sum(bin(byte).count("1") for byte in wrapped) > sum(bin(byte).count("1") for byte in single)


def test_short_text_stays_on_one_line(monkeypatch):
    lines, _ = _drawn_lines(monkeypatch, "Kitchen")
    assert lines == ["Kitchen"]


def test_wrapped_text_respects_margins():
    raster = render.render_text_raster("A considerably longer label name", 240, margin_dots=8)
    rows = _ink_rows(raster)
    columns = _ink_columns(raster)
    assert rows[0] >= 8 and rows[-1] <= 240 - 1 - 8
    assert columns[0] >= 8 and columns[-1] <= render.PRINTHEAD_PX - 1 - 8


def test_single_unbreakable_word_that_cannot_fit_is_rejected():
    with pytest.raises(ValueError, match="cannot fit"):
        render.render_text_raster("Unbreakable" * 40, 240)


def _drawn_calls(monkeypatch, text, label_rows=240, **kwargs):
    draw_text = render.ImageDraw.ImageDraw.text
    calls = []

    def record_text(self, position, value, *args, **extra):
        calls.append((value, extra.get("stroke_width", 0), extra.get("font") or (args[0] if args else None)))
        return draw_text(self, position, value, *args, **extra)

    monkeypatch.setattr(render.ImageDraw.ImageDraw, "text", record_text)
    render.render_text_raster(text, label_rows, **kwargs)
    return calls


def test_date_is_printed_on_its_own_line_under_bold_text(monkeypatch):
    calls = _drawn_calls(monkeypatch, "Gnocchi chorizo", date="10-08-2026")
    values = [value for value, _, _ in calls]
    assert values[-1] == "10-08-2026"
    assert " ".join(values[:-1]) == "Gnocchi chorizo"
    strokes = [stroke for _, stroke, _ in calls]
    assert all(stroke == render.TITLE_STROKE for stroke in strokes[:-1])
    assert strokes[-1] == 0


def test_date_uses_a_smaller_font_than_the_text(monkeypatch):
    calls = _drawn_calls(monkeypatch, "Gnocchi chorizo", date="10-08-2026")
    title_font = calls[0][2]
    date_font = calls[-1][2]
    assert date_font.size < title_font.size


def test_text_without_a_date_is_not_bold(monkeypatch):
    calls = _drawn_calls(monkeypatch, "Gnocchi chorizo")
    assert all(stroke == 0 for _, stroke, _ in calls)


def test_date_alone_prints_as_a_plain_label(monkeypatch):
    calls = _drawn_calls(monkeypatch, "", date="16-09-2026")
    assert [value for value, _, _ in calls] == ["16-09-2026"]
    assert all(stroke == 0 for _, stroke, _ in calls)


def test_empty_text_without_a_date_is_rejected():
    with pytest.raises(ValueError, match="cannot be empty"):
        render.render_text_raster("   ", 240)


def test_dated_label_respects_margins():
    raster = render.render_text_raster("Gnocchi chorizo", 240, margin_dots=8, date="10-08-2026")
    rows = _ink_rows(raster)
    columns = _ink_columns(raster)
    assert rows[0] >= 8 and rows[-1] <= 240 - 1 - 8
    assert columns[0] >= 8 and columns[-1] <= render.PRINTHEAD_PX - 1 - 8
