"""
Core data types shared by every stage of the pipeline.

Keeping these as small, immutable dataclasses means each stage has a clear
contract: the indexer produces an ImageIndex, the reader produces StaffRows,
the planner turns both into CopyTasks + ReportEntries, and the executor only
ever sees CopyTasks. No stage reaches into another stage's internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional


class Status(str, Enum):
    """Every possible outcome for a row or an image.

    Inheriting from `str` makes the value print cleanly in the CSV report.
    """

    # --- Success states -------------------------------------------------
    RENAMED = "RENAMED"                    # photo copied to LASSRA name
    PLANNED = "PLANNED"                    # dry-run: would be copied
    ALREADY_DONE = "ALREADY_DONE"          # identical file already in output (idempotent rerun)

    # --- Row-level data problems ----------------------------------------
    BLANK_ORACLE = "BLANK_ORACLE"          # row has no Oracle number
    BLANK_LASSRA = "BLANK_LASSRA"          # row has no LASSRA number
    INVALID_LASSRA = "INVALID_LASSRA"      # LASSRA contains characters unsafe for filenames
    DUPLICATE_ORACLE = "DUPLICATE_ORACLE"  # same Oracle number on 2+ rows
    DUPLICATE_LASSRA = "DUPLICATE_LASSRA"  # same LASSRA number on 2+ rows

    # --- Matching problems ----------------------------------------------
    NO_PHOTO = "NO_PHOTO"                  # no image found for this Oracle number
    DUPLICATE_IMAGE = "DUPLICATE_IMAGE"    # 2+ images share this Oracle number (e.g. .jpg and .jpeg)

    # --- Output problems ------------------------------------------------
    TARGET_CONFLICT = "TARGET_CONFLICT"    # a different file already sits at the destination
    COPY_FAILED = "COPY_FAILED"            # OS error while copying

    # --- Image-level (not tied to any row) --------------------------------
    ORPHAN_IMAGE = "ORPHAN_IMAGE"          # image that no staff row claimed


# Statuses that count as "the row ended up fine".
SUCCESS_STATUSES = {Status.RENAMED, Status.PLANNED, Status.ALREADY_DONE}


# Type alias: normalised Oracle key -> every image path carrying that key.
# It is a *multimap* (list of paths) rather than a plain map so that
# duplicates are detected instead of silently overwritten.
ImageIndex = Dict[str, List[Path]]


@dataclass(frozen=True)
class StaffRow:
    """One data row from the staff sheet, with raw and normalised keys."""

    row_number: int          # 1-based spreadsheet row (header is row 1), for traceability
    oracle_raw: str          # exactly what the cell contained, for the report
    lassra_raw: str
    name: str
    oracle_key: str          # canonical key used for hashing/matching
    lassra_key: str          # canonical LASSRA value used for the output filename


@dataclass(frozen=True)
class CopyTask:
    """A single planned file copy. The executor only ever sees these."""

    row_number: int
    source: Path
    destination: Path


@dataclass
class ReportEntry:
    """One line in the run report CSV."""

    status: Status
    row_number: Optional[int] = None
    oracle_no: str = ""
    lassra_no: str = ""
    name: str = ""
    source: str = ""
    destination: str = ""
    detail: str = ""


@dataclass
class Plan:
    """Output of the PROBE phase: what to copy, and what to report."""

    tasks: List[CopyTask] = field(default_factory=list)
    # Keyed by row_number so the executor can update a row's entry in O(1)
    # after its copy finishes.
    row_entries: Dict[int, ReportEntry] = field(default_factory=dict)
    orphan_entries: List[ReportEntry] = field(default_factory=list)
