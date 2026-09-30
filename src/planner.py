"""
PROBE phase of the hash join: decide, for every staff row, what should happen.

This module is PURE: it reads nothing from disk and writes nothing to disk.
It turns (rows, image index) into a Plan. Keeping it pure means:
  * it is trivially unit-testable with in-memory data,
  * dry-run and real runs share exactly the same decision logic,
  * the slow part (file I/O) is isolated in executor.py.

Complexity
----------
Pass 1 (count duplicates):  O(n) time, O(n) space (two Counters)
Pass 2 (probe each row):    O(n) time, each dict/set lookup O(1) average
Orphan detection:           O(m) set difference
Total:                      O(n + m)
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import List

from .keys import is_safe_filename_stem
from .models import CopyTask, ImageIndex, Plan, ReportEntry, StaffRow, Status

log = logging.getLogger(__name__)


def build_plan(rows: List[StaffRow], index: ImageIndex, output_dir: Path) -> Plan:
    """Join staff rows to images on the normalised Oracle key.

    Decision order per row (first failing check wins, so each row gets
    exactly ONE status and the report is unambiguous):

        1. Oracle blank?                 -> BLANK_ORACLE
        2. LASSRA blank?                 -> BLANK_LASSRA
        3. LASSRA unsafe as filename?    -> INVALID_LASSRA
        4. Oracle on 2+ rows?            -> DUPLICATE_ORACLE
        5. LASSRA on 2+ rows?            -> DUPLICATE_LASSRA
        6. No image for Oracle key?      -> NO_PHOTO
        7. 2+ images for Oracle key?     -> DUPLICATE_IMAGE
        8. Otherwise                     -> CopyTask (status set later by executor)
    """
    plan = Plan()

    # ---- Pass 1: frequency counts -------------------------------------
    # We must know the TOTAL count of each key before judging any single
    # row; a streaming "first one wins" approach would hand the photo to
    # whichever duplicate row happened to come first, which for identity
    # data is a silent, wrong assignment (ADR-005).
    oracle_counts = Counter(r.oracle_key for r in rows if r.oracle_key)

    # LASSRA duplicates are compared case-INSENSITIVELY (casefold) because
    # Windows and macOS filesystems are case-insensitive: "AB12.jpg" and
    # "ab12.jpg" would overwrite each other there.
    lassra_counts = Counter(r.lassra_key.casefold() for r in rows if r.lassra_key)

    # ---- Pass 2: probe ------------------------------------------------
    for row in rows:
        entry = ReportEntry(
            status=Status.PLANNED,  # provisional; overwritten below or by executor
            row_number=row.row_number,
            oracle_no=row.oracle_raw,
            lassra_no=row.lassra_raw,
            name=row.name,
        )
        plan.row_entries[row.row_number] = entry

        if not row.oracle_key:
            entry.status = Status.BLANK_ORACLE
            continue
        if not row.lassra_key:
            entry.status = Status.BLANK_LASSRA
            continue
        if not is_safe_filename_stem(row.lassra_key):
            entry.status = Status.INVALID_LASSRA
            entry.detail = "Contains characters not allowed in filenames"
            continue
        if oracle_counts[row.oracle_key] > 1:
            entry.status = Status.DUPLICATE_ORACLE
            entry.detail = f"Oracle no appears on {oracle_counts[row.oracle_key]} rows"
            continue
        if lassra_counts[row.lassra_key.casefold()] > 1:
            entry.status = Status.DUPLICATE_LASSRA
            entry.detail = f"LASSRA no appears on {lassra_counts[row.lassra_key.casefold()]} rows"
            continue

        # The O(1) hash lookup that replaces the O(m) folder scan.
        paths = index.get(row.oracle_key)
        if not paths:
            entry.status = Status.NO_PHOTO
            continue
        if len(paths) > 1:
            entry.status = Status.DUPLICATE_IMAGE
            entry.detail = "; ".join(p.name for p in paths)
            continue

        source = paths[0]
        # Keep the source extension (lower-cased). .jpg and .jpeg are the
        # same format, so renaming the extension would be cosmetic (ADR-006).
        destination = output_dir / f"{row.lassra_key}{source.suffix.lower()}"

        entry.source = str(source)
        entry.destination = str(destination)
        plan.tasks.append(CopyTask(row.row_number, source, destination))

    # ---- Orphans: images no row referenced ----------------------------
    # Set difference, O(m). A key referenced by ANY row (even one skipped
    # as a duplicate) is not an orphan; the row's own status explains it.
    referenced = {r.oracle_key for r in rows if r.oracle_key}
    for key in sorted(index.keys() - referenced):  # sorted = deterministic report
        for path in index[key]:
            plan.orphan_entries.append(
                ReportEntry(status=Status.ORPHAN_IMAGE, source=str(path),
                            detail="No staff row has this Oracle no")
            )

    log.info(
        "Plan: %d copy task(s), %d row(s) with issues, %d orphan image(s)",
        len(plan.tasks), len(plan.row_entries) - len(plan.tasks), len(plan.orphan_entries),
    )
    return plan
