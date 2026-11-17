"""EAN-13 and EAN-8 symbols, encoded here to avoid an extra dependency.

Food packaging carries an EAN code, and copying it onto a home made label lets
a scanner or a pantry app recognise a product that has been decanted into
another container. The bar pattern is pure arithmetic, so a few lookup tables
are cheaper than pulling a barcode library into a Home Assistant integration.

Everything is measured in modules: the narrowest bar a symbol is built from.
The printer lays down 8 dots per millimetre, so the module width in dots is
what decides whether the result can actually be read.
"""

from typing import NamedTuple

# Digits are drawn with three different seven-module patterns. The left half of
# an EAN-13 mixes two of them, and which one is used for each digit spells out
# the first digit of the code, which is why that digit has no bars of its own.
LEFT_ODD = (
    "0001101", "0011001", "0010011", "0111101", "0100011",
    "0110001", "0101111", "0111011", "0110111", "0001011",
)
LEFT_EVEN = (
    "0100111", "0110011", "0011011", "0100001", "0011101",
    "0111001", "0000101", "0010001", "0001001", "0010111",
)
RIGHT = (
    "1110010", "1100110", "1101100", "1000010", "1011100",
    "1001110", "1010000", "1000100", "1001000", "1110100",
)
PARITY = (
    "OOOOOO", "OOEOEE", "OOEEOE", "OOEEEO", "OEOOEE",
    "OEEOOE", "OEEEOO", "OEOEOE", "OEOEEO", "OEEOEO",
)

START_END_GUARD = "101"
CENTER_GUARD = "01010"
# The blank margin is part of the symbol: without it a scanner cannot tell
# where the code begins. EAN-13 needs a wider one on the left for the first
# digit, which is printed in that margin.
QUIET_LEFT_EAN13 = 11
QUIET_RIGHT_EAN13 = 7
QUIET_EAN8 = 7

# At 8 dots per millimetre a module of 2 dots is 0.25 mm, which is roughly the
# smallest magnification the EAN standard allows; below that scanners start to
# miss. Four dots is as wide as a 60 mm label can carry.
MIN_MODULE_DOTS = 2
MAX_MODULE_DOTS = 4


class DigitGroup(NamedTuple):
    """Human readable digits and the module span they belong under."""

    text: str
    start: int
    end: int


class Symbol(NamedTuple):
    """An encoded barcode, ready to be painted module by module."""

    symbology: str
    code: str
    modules: str
    guards: tuple[tuple[int, int], ...]
    digits: tuple[DigitGroup, ...]

    @property
    def width(self) -> int:
        return len(self.modules)


def _digits_only(code: str) -> str:
    cleaned = "".join(character for character in str(code) if not character.isspace())
    cleaned = cleaned.replace("-", "")
    if not cleaned.isdigit():
        raise ValueError("A barcode may only contain digits")
    return cleaned


def checksum(digits: str) -> int:
    """The last digit of an EAN, computed from the ones before it."""
    # Weights alternate 1 and 3, ending on 3 for the digit next to the check
    # digit, which is why the walk starts from the right.
    total = 0
    for position, character in enumerate(reversed(digits)):
        total += int(character) * (3 if position % 2 == 0 else 1)
    return (10 - total % 10) % 10


def normalise(code: str) -> str:
    """Return a complete EAN-8 or EAN-13, adding or verifying the check digit.

    A code copied off a package has its check digit printed with it, so it is
    verified rather than recomputed: a typo caught here saves a label that no
    scanner would read.
    """
    digits = _digits_only(code)
    if len(digits) in (7, 11):
        # Eleven digits is a UPC-A without its check digit; padding it with a
        # leading zero makes the EAN-13 that the same product carries.
        digits = digits.zfill(12) if len(digits) == 11 else digits
        return digits + str(checksum(digits))
    if len(digits) == 12:
        return digits + str(checksum(digits))
    if len(digits) in (8, 13):
        if int(digits[-1]) != checksum(digits[:-1]):
            raise ValueError(
                f"Check digit does not match: {digits} should end in {checksum(digits[:-1])}"
            )
        return digits
    raise ValueError("A barcode must have 8 or 13 digits (12 without the check digit)")


def encode(code: str) -> Symbol:
    """Turn an EAN-8 or EAN-13 into the bars that make it up."""
    digits = normalise(code)
    return _encode_ean13(digits) if len(digits) == 13 else _encode_ean8(digits)


def _encode_ean13(digits: str) -> Symbol:
    parity = PARITY[int(digits[0])]
    left = "".join(
        (LEFT_ODD if parity[index] == "O" else LEFT_EVEN)[int(digit)]
        for index, digit in enumerate(digits[1:7])
    )
    right = "".join(RIGHT[int(digit)] for digit in digits[7:])

    quiet = "0" * QUIET_LEFT_EAN13
    modules = quiet + START_END_GUARD + left + CENTER_GUARD + right + START_END_GUARD
    modules += "0" * QUIET_RIGHT_EAN13

    bars_start = QUIET_LEFT_EAN13
    left_start = bars_start + len(START_END_GUARD)
    center_start = left_start + len(left)
    right_start = center_start + len(CENTER_GUARD)
    end_start = right_start + len(right)

    return Symbol(
        symbology="EAN-13",
        code=digits,
        modules=modules,
        guards=(
            (bars_start, left_start),
            (center_start, right_start),
            (end_start, end_start + len(START_END_GUARD)),
        ),
        digits=(
            # The first digit sits in the left margin, beside the bars.
            DigitGroup(digits[0], 0, bars_start),
            DigitGroup(digits[1:7], left_start, center_start),
            DigitGroup(digits[7:], right_start, end_start),
        ),
    )


def _encode_ean8(digits: str) -> Symbol:
    left = "".join(LEFT_ODD[int(digit)] for digit in digits[:4])
    right = "".join(RIGHT[int(digit)] for digit in digits[4:])

    quiet = "0" * QUIET_EAN8
    modules = quiet + START_END_GUARD + left + CENTER_GUARD + right + START_END_GUARD
    modules += "0" * QUIET_EAN8

    bars_start = QUIET_EAN8
    left_start = bars_start + len(START_END_GUARD)
    center_start = left_start + len(left)
    right_start = center_start + len(CENTER_GUARD)
    end_start = right_start + len(right)

    return Symbol(
        symbology="EAN-8",
        code=digits,
        modules=modules,
        guards=(
            (bars_start, left_start),
            (center_start, right_start),
            (end_start, end_start + len(START_END_GUARD)),
        ),
        digits=(
            DigitGroup(digits[:4], left_start, center_start),
            DigitGroup(digits[4:], right_start, end_start),
        ),
    )


def module_dots(symbol: Symbol, available_dots: int) -> int:
    """Widest module that still fits, so the symbol stays as readable as it can.

    Raises when even the narrowest allowed module needs more room than the
    label has, which is the honest answer: a shorter tape cannot carry this
    symbology.
    """
    widest = min(MAX_MODULE_DOTS, available_dots // symbol.width)
    if widest < MIN_MODULE_DOTS:
        needed = symbol.width * MIN_MODULE_DOTS
        raise ValueError(
            f"{symbol.symbology} needs at least {needed} dots of label "
            f"({needed / 8:.0f} mm), only {available_dots} available"
        )
    return widest
