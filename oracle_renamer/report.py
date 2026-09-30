"""
Write the run report (CSV) and compute a summary.

The report is the audit trail: every staff row appears exactly once with a
status, followed by every orphan image. HR can open it in Excel, filter by
status, and fix the source data without re-reading logs.
"""

from __future__ import annotations

import csv
import logging
from collections import Counter
from pathlib import Path
from typing import Dict

from .models import Plan

log = logging.getLogger(__name__)

COLUMNS = ["row_number", "oracle_no", "lassra_no", "name",
           "status", "source", "destination", "detail"]


def write_report(plan: Plan, report_path: Path) -> None:
    """Write rows (in spreadsheet order) then orphan images to CSV."""
    report_path.parent.mkdir(parents=True, exist_ok=True)

    # utf-8-sig adds a BOM so Excel opens names with accents/Yoruba
    # diacritics correctly instead of showing mojibake.
    with report_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        # Sorting by row_number is O(n log n) but n is small and it makes
        # the report line up with the original spreadsheet.
        for row_number in sorted(plan.row_entries):
            e = plan.row_entries[row_number]
            writer.writerow([e.row_number, e.oracle_no, e.lassra_no, e.name,
                             e.status.value, e.source, e.destination, e.detail])
        for e in plan.orphan_entries:
            writer.writerow(["", "", "", "", e.status.value, e.source, "", e.detail])

    log.info("Report written to %s", report_path)


def summarise(plan: Plan) -> Dict[str, int]:
    """Count outcomes per status, O(n + orphans), for the console summary."""
    counts = Counter(e.status.value for e in plan.row_entries.values())
    counts.update(e.status.value for e in plan.orphan_entries)
    return dict(sorted(counts.items()))
