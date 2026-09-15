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


def test_long_text_stays_on_one_line_and_still_fits(monkeypatch):
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
    assert calls == [text]


def test_line_breaks_are_rendered_as_spaces(monkeypatch):
    draw_text = render.ImageDraw.ImageDraw.text
    calls = []

    def record_text(self, position, text, *args, **kwargs):
        calls.append(text)
        return draw_text(self, position, text, *args, **kwargs)

    monkeypatch.setattr(render.ImageDraw.ImageDraw, "text", record_text)
    render.render_text_raster("Best before\nFriday", 240)
    assert calls == ["Best before Friday"]


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
