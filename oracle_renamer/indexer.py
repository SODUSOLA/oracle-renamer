"""
BUILD phase of the hash join: scan the image folder ONCE and index it.

Complexity
----------
Time:   O(m)  -- one pass over the m files in the folder
Space:  O(m)  -- one dict entry (short string -> list of paths) per image

Why not search the folder per staff row? That is a nested loop,
O(n * m): 50,000 rows x 50,000 images = 2.5 billion comparisons.
Indexing once makes every later lookup O(1) on average.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import FrozenSet

from .keys import normalise_key
from .models import ImageIndex

log = logging.getLogger(__name__)

# Only JPEGs are in scope (see README). Stored lower-case; we compare
# lower-cased extensions so ".JPG" and ".Jpeg" are accepted too.
DEFAULT_EXTENSIONS: FrozenSet[str] = frozenset({".jpg", ".jpeg"})


def build_image_index(
    image_dir: Path,
    extensions: FrozenSet[str] = DEFAULT_EXTENSIONS,
    strip_leading_zeros: bool = False,
    recursive: bool = False,
) -> ImageIndex:
    """Map every normalised Oracle number to the image path(s) carrying it.

    Returns a *multimap* (key -> list of paths). A plain dict would let
    "123.jpg" silently overwrite "123.jpeg"; with a list, the planner sees
    len(paths) > 1 and reports DUPLICATE_IMAGE instead of guessing.
    """
    if not image_dir.is_dir():
        raise NotADirectoryError(f"Image folder not found: {image_dir}")

    index: ImageIndex = defaultdict(list)
    scanned = skipped = 0

    # os.scandir is faster than Path.iterdir for large folders because it
    # returns file-type info from the directory listing without extra stat calls.
    for path in _iter_files(image_dir, recursive):
        scanned += 1
        if path.suffix.lower() not in extensions:
            skipped += 1
            continue
        key = normalise_key(path.stem, strip_leading_zeros=strip_leading_zeros)
        if not key:
            skipped += 1
            continue
        index[key].append(path)  # O(1) amortised append

    # Sort each bucket so output (and duplicate reports) are deterministic
    # regardless of the order the filesystem returned entries in.
    for paths in index.values():
        paths.sort()

    log.info(
        "Indexed %d image(s) under %d key(s); ignored %d non-JPEG/unnamed file(s)",
        scanned - skipped, len(index), skipped,
    )
    return dict(index)  # freeze into a plain dict: no accidental auto-inserts later


def _iter_files(root: Path, recursive: bool):
    """Yield file paths under `root`, optionally walking subfolders."""
    if recursive:
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                yield Path(dirpath) / filename
    else:
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.is_file():
                    yield Path(entry.path)
