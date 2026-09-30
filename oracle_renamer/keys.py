"""
Key normalisation: the single most important function in the system.

A hash map only matches keys that are *byte-for-byte identical*. Real data is
not: Excel turns 00123 into 123 or 123.0, people type trailing spaces, files
are named "123.JPG". If the two sides of the join are not normalised by the
SAME function, lookups miss and the system reports "no photo" for staff who
actually have one.

Rule: every key that enters the hash map, and every key used to probe it,
passes through `normalise_key()`. No exceptions.
"""

from __future__ import annotations

import re
from typing import Any

# Characters Windows/macOS/Linux forbid (or make awkward) in filenames.
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Matches float artefacts Excel produces for numeric cells: "12345.0", "12345.00".
_TRAILING_DECIMAL_ZERO = re.compile(r"^(\d+)\.0+$")


def cell_to_text(value: Any) -> str:
    """Convert any spreadsheet cell value to clean text.

    - None            -> ""
    - 12345 (int)     -> "12345"
    - 12345.0 (float) -> "12345"   (Excel stores numbers as floats)
    - "  A12 "        -> "A12"
    """
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        # 12345.0 -> "12345"; avoids "12345.0" becoming the key.
        return str(int(value))
    text = str(value).strip()
    # Text cells can still carry "12345.0" if the sheet was exported oddly.
    match = _TRAILING_DECIMAL_ZERO.match(text)
    return match.group(1) if match else text


def normalise_key(value: Any, strip_leading_zeros: bool = False) -> str:
    """Produce the canonical form of an Oracle number.

    Canonical form:
      1. converted to text (see `cell_to_text`)
      2. internal whitespace removed ("123 45" -> "12345")
      3. upper-cased ("ab12" -> "AB12") so letter-case never breaks a match
      4. optionally, leading zeros stripped ("00123" -> "123")

    Leading-zero stripping is OFF by default: if Oracle numbers are genuinely
    fixed-width strings, "00123" and "123" may be different people. Turn it on
    only when you know Excel has been dropping zeros (see ADR-004).
    """
    text = cell_to_text(value)
    text = re.sub(r"\s+", "", text).upper()
    if strip_leading_zeros and text.isdigit():
        # lstrip would turn "000" into "", so keep a single "0" in that case.
        text = text.lstrip("0") or "0"
    return text


def normalise_lassra(value: Any) -> str:
    """Canonical LASSRA number, used verbatim as the output file stem.

    We do NOT upper-case or strip zeros here: the output name should look
    exactly like the LASSRA number in the source data, minus stray spaces.
    """
    return re.sub(r"\s+", "", cell_to_text(value))


def is_safe_filename_stem(stem: str) -> bool:
    """True if `stem` can be used as a filename on every major OS."""
    if not stem or stem in {".", ".."}:
        return False
    return _UNSAFE_FILENAME_CHARS.search(stem) is None
