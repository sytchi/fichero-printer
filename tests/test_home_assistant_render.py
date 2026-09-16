"""Tests for Home Assistant label auto-fitting."""

import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType

import pytest

# The renderer imports its sibling modules, so it has to be loaded as part of
# a package rather than as a loose file.
ROOT = Path(__file__).parents[1] / "custom_components" / "fichero_printer"
PACKAGE = ModuleType("fichero_render_pkg")
PACKAGE.__path__ = [str(ROOT)]
sys.modules.setdefault("fichero_render_pkg", PACKAGE)
SPEC = importlib.util.spec_from_file_location("fichero_render_pkg.render", ROOT / "render.py")
render = importlib.util.module_from_spec(SPEC)
sys.modules["fichero_render_pkg.render"] = render
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
    # Rows leave the printer right to left, so moving the print to the right
    # lowers the row numbers. Text that already fills the label cannot travel
    # the full distance - the reserved space narrows it instead - so assert the
    # direction and that the printed band really moved.
    assert (shifted[0] + shifted[-1]) / 2 < (centred[0] + centred[-1]) / 2 - 4
    assert shifted[-1] < centred[-1]


def test_offset_moves_short_text_both_ways():
    centred = _ink_rows(render.render_text_raster("A", 240, offset_dots=0))
    rightwards = _ink_rows(render.render_text_raster("A", 240, offset_dots=16))
    leftwards = _ink_rows(render.render_text_raster("A", 240, offset_dots=-16))
    assert rightwards[0] - centred[0] == -16
    assert leftwards[0] - centred[0] == 16


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
    faces = [font.getname()[1] for _, _, font in calls]
    assert all(face == "Bold" for face in faces)


def test_date_uses_a_smaller_font_than_the_text(monkeypatch):
    calls = _drawn_calls(monkeypatch, "Gnocchi chorizo", date="10-08-2026")
    title_font = calls[0][2]
    date_font = calls[-1][2]
    assert date_font.size < title_font.size


def test_text_is_bold_by_default(monkeypatch):
    calls = _drawn_calls(monkeypatch, "Gnocchi chorizo")
    assert all(font.getname()[1] == "Bold" for _, _, font in calls)


def test_bold_can_be_turned_off(monkeypatch):
    calls = _drawn_calls(monkeypatch, "Gnocchi chorizo", date="16-09-2026", bold=False)
    assert [value for value, _, _ in calls][-1] == "16-09-2026"
    assert all(font.getname()[1] == "Book" for _, _, font in calls)


def test_date_alone_prints_as_an_ordinary_label(monkeypatch):
    calls = _drawn_calls(monkeypatch, "", date="16-09-2026")
    assert [value for value, _, _ in calls] == ["16-09-2026"]
    assert all(font.getname()[1] == "Bold" for _, _, font in calls)


def test_empty_text_without_a_date_is_rejected():
    with pytest.raises(ValueError, match="cannot be empty"):
        render.render_text_raster("   ", 240)


def test_dated_label_respects_margins():
    raster = render.render_text_raster("Gnocchi chorizo", 240, margin_dots=8, date="10-08-2026")
    rows = _ink_rows(raster)
    columns = _ink_columns(raster)
    assert rows[0] >= 8 and rows[-1] <= 240 - 1 - 8
    assert columns[0] >= 8 and columns[-1] <= render.PRINTHEAD_PX - 1 - 8


def test_preview_is_a_png_of_the_whole_label():
    png = render.render_preview_png("Gnocchi chorizo", 240, date="16-09-2026")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    image = render.Image.open(io.BytesIO(png))
    assert image.size == (240 * render.PREVIEW_SCALE, render.PRINTHEAD_PX * render.PREVIEW_SCALE)


def test_preview_and_print_share_one_layout():
    image = render.render_label_image("Gnocchi chorizo", 240, date="16-09-2026")
    raster = render.render_text_raster("Gnocchi chorizo", 240, date="16-09-2026")
    rotated = image.rotate(90, expand=True)
    assert raster == bytes(byte ^ 0xFF for byte in rotated.tobytes())


def test_preview_keeps_the_label_size_at_scale_one():
    image = render.Image.open(io.BytesIO(render.render_preview_png("Kitchen", 240, scale=1)))
    assert image.size == (240, render.PRINTHEAD_PX)


def test_preview_refuses_an_empty_label():
    with pytest.raises(ValueError, match="cannot be empty"):
        render.render_preview_png("", 240)


def test_preview_refuses_text_that_cannot_fit():
    with pytest.raises(ValueError, match="cannot fit"):
        render.render_preview_png("Unbreakable" * 40, 240)


def test_bundled_fonts_are_used_and_carry_accents():
    font = render._font(30)
    assert font.getname() == ("DejaVu Sans", "Book")
    assert render._font(30, True).getname() == ("DejaVu Sans", "Bold")
    # A box glyph would be the same width for every accented character.
    widths = {ch: font.getbbox(ch)[2] - font.getbbox(ch)[0] for ch in "ąćęłńóśżź"}
    assert len(set(widths.values())) > 3


def test_accented_text_prints_differently_than_plain_text():
    with_accents = render.render_text_raster("ogórkowa", 240)
    without = render.render_text_raster("ogorkowa", 240)
    assert with_accents != without


def test_date_keeps_a_readable_size_when_the_name_wraps(monkeypatch):
    long_name = "Zupa ogorkowa ze smietana i ziemniakami oraz selerem i pietruszka"
    calls = _drawn_calls(monkeypatch, long_name, date="16-09-2026")
    title_font = calls[0][2]
    date_font = calls[-1][2]
    assert len(calls) >= 3  # the name really did wrap
    assert date_font.size >= render.MIN_DATE_DOTS
    assert date_font.size <= title_font.size


def test_short_name_keeps_the_date_proportional(monkeypatch):
    calls = _drawn_calls(monkeypatch, "Kurczak", date="16-09-2026")
    title_font = calls[0][2]
    date_font = calls[-1][2]
    assert date_font.size == render._date_size(title_font.size)
    assert date_font.size < title_font.size


def test_icon_prints_at_the_end_of_the_label():
    plain = _ink_rows(render.render_text_raster("Kurczak", 240))
    with_icon = _ink_rows(render.render_text_raster("Kurczak", 240, icon="mdi:food"))
    # The pictogram adds ink closer to the end of the label than the text alone.
    assert with_icon[-1] > plain[-1]
    assert with_icon[-1] <= 240 - 1 - 8


def test_icon_leaves_the_text_its_own_space():
    icon_size = render.PRINTHEAD_PX - 2 * 8
    raster = render.render_text_raster("Kurczak", 240, margin_dots=8, icon="mdi:food")
    rows = _ink_rows(raster)
    icon_starts_at = 240 - 8 - icon_size
    text_rows = [row for row in rows if row < icon_starts_at - 1]
    assert text_rows, "text should still be printed"
    # Nothing of the text may stray into the pictogram's square.
    assert max(text_rows) < icon_starts_at


def test_icon_name_works_with_and_without_the_prefix():
    assert render.icon_character("mdi:pasta") == render.icon_character("pasta")
    assert render.render_text_raster("Obiad", 240, icon="pasta") == render.render_text_raster(
        "Obiad", 240, icon="mdi:pasta"
    )


def test_unknown_icon_is_rejected():
    with pytest.raises(ValueError, match="Unknown icon"):
        render.render_text_raster("Obiad", 240, icon="mdi:definitely-not-an-icon")


def test_icon_changes_the_printed_label():
    assert render.render_text_raster("Obiad", 240, icon="mdi:pasta") != render.render_text_raster(
        "Obiad", 240, icon="mdi:snowflake"
    )


def test_icon_fits_next_to_a_dated_label():
    raster = render.render_text_raster("Zupa ogórkowa", 240, date="16-09-2026", icon="mdi:bowl-mix")
    assert len(raster) == 240 * 12
    assert any(raster)


def test_icon_data_is_looked_up_next_to_the_module_too(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "ICON_DIRS", (tmp_path / "icons", tmp_path))
    (tmp_path / "mdi-codepoints.json").write_text('{"pasta": 987000}', encoding="utf-8")
    render._ICON_CODEPOINTS.clear()
    try:
        assert render.icon_character("mdi:pasta") == chr(987000)
    finally:
        render._ICON_CODEPOINTS.clear()


def test_missing_icon_data_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "ICON_DIRS", (tmp_path / "nope",))
    render._ICON_CODEPOINTS.clear()
    try:
        assert render._icon_codepoints() == {}
        (tmp_path / "nope").mkdir()
        (tmp_path / "nope" / "mdi-codepoints.json").write_text('{"pasta": 987000}', encoding="utf-8")
        # No restart should be needed once the files show up.
        assert render._icon_codepoints() == {"pasta": 987000}
    finally:
        render._ICON_CODEPOINTS.clear()


def test_icon_moves_with_the_offset():
    still = _ink_rows(render.render_text_raster("Kurczak", 240, icon="mdi:food", offset_dots=0))
    moved = _ink_rows(render.render_text_raster("Kurczak", 240, icon="mdi:food", offset_dots=16))
    # The pictogram sits at the far end of the printed block, so the extreme
    # row belongs to it; a positive offset has to carry it along with the text.
    assert still[-1] - moved[-1] == 16


def _ink_halves(raster, label_rows=240):
    """Ink in the first and second half of the printed rows."""
    row_bytes = len(raster) // label_rows
    half = label_rows // 2
    def count(start, end):
        return sum(
            bin(byte).count("1")
            for row in range(start, end)
            for byte in raster[row * row_bytes:(row + 1) * row_bytes]
        )
    return count(0, half), count(half, label_rows)


def test_icon_side_swaps_the_ends():
    left = _ink_halves(render.render_text_raster("Kurczak", 240, icon="mdi:food", icon_side="left"))
    right = _ink_halves(render.render_text_raster("Kurczak", 240, icon="mdi:food", icon_side="right"))
    # Both layouts span the whole label, so the ends say nothing; the filled
    # pictogram carries far more ink than the text, so it is the heavier half
    # and it has to swap halves with the option.
    assert left[1] > left[0] * 2
    assert right[0] > right[1] * 2


def test_icon_side_default_is_left():
    assert render.DEFAULT_ICON_SIDE == "left"
    assert _ink_rows(render.render_text_raster("Kurczak", 240, icon="mdi:food")) == _ink_rows(
        render.render_text_raster("Kurczak", 240, icon="mdi:food", icon_side="left")
    )


def test_unknown_icon_side_is_rejected():
    with pytest.raises(ValueError, match="Icon side"):
        render.render_text_raster("Kurczak", 240, icon="mdi:food", icon_side="middle")


def test_icon_and_text_never_overlap_with_an_offset():
    icon_size = render.PRINTHEAD_PX - 2 * 8
    for side in ("left", "right"):
        for offset in (-24, 0, 24):
            rows = _ink_rows(
                render.render_text_raster(
                    "Kurczak", 240, margin_dots=8, icon="mdi:food", icon_side=side, offset_dots=offset
                )
            )
            assert rows[0] >= 8
            assert rows[-1] <= 240 - 1 - 8
            # Enough ink for both blocks means neither was clipped away.
            assert rows[-1] - rows[0] > icon_size
