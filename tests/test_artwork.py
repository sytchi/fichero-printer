"""Tests for turning generated artwork into printable black and white."""

import importlib.util
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "fichero_printer" / "artwork.py"
SPEC = importlib.util.spec_from_file_location("fichero_artwork", MODULE_PATH)
artwork = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(artwork)


def _outlined_png(size=(600, 600)):
    image = Image.new("L", size, 255)
    ImageDraw.Draw(image).rectangle((100, 100, 500, 500), outline=0, width=30)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _png(size=(600, 600), box=None, fill=0, background=255, band=None):
    image = Image.new("L", size, background)
    draw = ImageDraw.Draw(image)
    if box:
        draw.rectangle(box, fill=fill)
    if band:
        draw.rectangle(band, fill=fill)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_conversion_is_pure_black_and_white_at_the_requested_size():
    # An outline, not a filled block: a solid shape would be 100% ink after the
    # crop and would not show that white survives the conversion.
    result = artwork.to_printable(_outlined_png(), (80, 80))
    assert result.mode == "1"
    assert result.size == (80, 80)
    assert 0 < artwork.ink_ratio(result) < 1
    assert set(result.convert("L").getdata()) <= {0, 255}


def test_drawing_is_cropped_to_its_ink_and_centred():
    # A small square parked in the corner has to end up in the middle, filling
    # the canvas, instead of being a speck with a lot of white around it.
    corner = artwork.to_printable(_png(box=(20, 20, 140, 140)), (80, 80))
    assert artwork.ink_ratio(corner) > 0.9
    columns = [x for x in range(80) if any(corner.getpixel((x, y)) == 0 for y in range(80))]
    assert columns[0] <= 2 and columns[-1] >= 77


def _ink_groups(image):
    """How many separate horizontal bands of ink the picture has."""
    rows = [
        any(image.getpixel((x, y)) == 0 for x in range(image.width))
        for y in range(image.height)
    ]
    return sum(1 for index, inked in enumerate(rows) if inked and not (index and rows[index - 1]))


def test_band_crop_keeps_the_middle_for_a_wide_strip():
    # A bar across the top and another through the middle: taking the central
    # band for a wide label has to drop the top one.
    data = _png(size=(600, 600), box=(0, 0, 600, 60), band=(100, 280, 500, 320))
    whole = artwork.to_printable(data, (480, 80))
    strip = artwork.to_printable(data, (480, 80), crop_to_band=True)
    assert strip.size == (480, 80)
    assert _ink_groups(whole) == 2, "without the crop both bars are printed"
    assert _ink_groups(strip) == 1, "with the crop only the middle bar survives"


def test_threshold_decides_what_counts_as_ink():
    grey = _png(box=(100, 100, 500, 500), fill=150)
    assert artwork.ink_ratio(artwork.to_printable(grey, (40, 40), threshold=200)) > 0.5
    assert artwork.ink_ratio(artwork.to_printable(grey, (40, 40), threshold=100)) == 0


def test_silly_arguments_are_rejected():
    with pytest.raises(ValueError, match="Threshold"):
        artwork.to_printable(_png(box=(10, 10, 20, 20)), (40, 40), threshold=0)
    with pytest.raises(ValueError, match="positive"):
        artwork.to_printable(_png(box=(10, 10, 20, 20)), (0, 40))


def test_prompts_carry_the_constraints_that_survive_thresholding():
    for template in (artwork.ICON_PROMPT, artwork.ARTWORK_PROMPT):
        lowered = template.lower()
        assert "linocut" in lowered
        assert "no text" in lowered
        assert "no gray" in lowered and "halftone" in lowered
        assert "{subject}" in template or "{description}" in template
