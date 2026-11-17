"""EAN encoding, checked against codes whose bars are publicly known."""

import importlib.util
from pathlib import Path

import pytest


def _load():
    root = Path(__file__).parents[1] / "custom_components" / "fichero_printer"
    spec = importlib.util.spec_from_file_location("fichero_barcode", root / "barcode.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


barcode = _load()


def test_check_digit_matches_a_known_code():
    assert barcode.checksum("590123412345") == 7
    assert barcode.checksum("9638507") == 4


def test_twelve_digits_get_their_check_digit():
    assert barcode.normalise("590123412345") == "5901234123457"


def test_a_complete_code_is_kept():
    assert barcode.normalise("5901234123457") == "5901234123457"
    assert barcode.normalise("5901234 123457") == "5901234123457"


def test_a_typo_in_the_check_digit_is_rejected():
    with pytest.raises(ValueError, match="Check digit"):
        barcode.normalise("5901234123450")


def test_letters_and_odd_lengths_are_rejected():
    with pytest.raises(ValueError, match="only contain digits"):
        barcode.normalise("59012341234X")
    with pytest.raises(ValueError, match="8 or 13 digits"):
        barcode.normalise("12345")


def test_ean13_has_the_standard_shape():
    symbol = barcode.encode("5901234123457")
    assert symbol.symbology == "EAN-13"
    # 95 modules of bars plus the quiet zones on either side.
    assert symbol.width == 113
    assert symbol.modules.startswith("0" * 11 + "101")
    assert symbol.modules.endswith("101" + "0" * 7)
    assert [group.text for group in symbol.digits] == ["5", "901234", "123457"]


def test_ean13_left_half_encodes_the_first_digit():
    symbol = barcode.encode("5901234123457")
    left = symbol.modules[14:56]
    # First digit 5 means the parity pattern OEEOOE for the six digits after it.
    expected = "".join(
        (barcode.LEFT_ODD if parity == "O" else barcode.LEFT_EVEN)[int(digit)]
        for parity, digit in zip("OEEOOE", "901234")
    )
    assert left == expected


def test_ean8_is_shorter():
    symbol = barcode.encode("96385074")
    assert symbol.symbology == "EAN-8"
    assert symbol.width == 81
    assert [group.text for group in symbol.digits] == ["9638", "5074"]


def test_module_width_grows_with_the_label():
    symbol = barcode.encode("5901234123457")
    assert barcode.module_dots(symbol, 480) == barcode.MAX_MODULE_DOTS
    assert barcode.module_dots(symbol, 300) == 2


def test_a_label_too_short_for_readable_bars_is_rejected():
    symbol = barcode.encode("5901234123457")
    with pytest.raises(ValueError, match="needs at least"):
        barcode.module_dots(symbol, 200)
