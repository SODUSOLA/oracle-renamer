"""
Command-line entry point. Wires the pipeline stages together:

    build_image_index  ->  read_staff_rows  ->  build_plan
        ->  execute_plan  ->  write_report  ->  exit code

Exit codes
----------
0  every staff row ended in RENAMED / PLANNED / ALREADY_DONE
1  fatal error (missing folder, missing column, bad arguments)
2  run completed, but some rows need attention (see the report)
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import __version__
from .executor import execute_plan
from .indexer import DEFAULT_EXTENSIONS, build_image_index
from .models import SUCCESS_STATUSES
from .planner import build_plan
from .reader import ColumnNotFoundError, read_staff_rows
from .report import summarise, write_report

log = logging.getLogger("oracle_renamer")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="oracle-renamer",
        description="Rename Oracle-numbered staff photos to LASSRA numbers.",
    )
    p.add_argument("--images", required=True, type=Path,
                   help="Folder of Oracle-named .jpg/.jpeg photos")
    p.add_argument("--sheet", required=True, type=Path,
                   help="Staff data file (.xlsx or .csv)")
    p.add_argument("--out", required=True, type=Path,
                   help="Output folder for renamed photos (created if missing)")
    p.add_argument("--report", type=Path, default=None,
                   help="Report CSV path (default: <out>/rename_report_<timestamp>.csv)")
    p.add_argument("--oracle-col", default="Oracle No", help="Oracle number column header")
    p.add_argument("--lassra-col", default="LASSRA No", help="LASSRA number column header")
    p.add_argument("--name-col", default="Name", help="Name column header (optional)")
    p.add_argument("--sheet-name", default=None, help="Worksheet name (default: first sheet)")
    p.add_argument("--strip-leading-zeros", action="store_true",
                   help="Treat 00123 and 123 as the same Oracle number")
    p.add_argument("--recursive", action="store_true", help="Also scan subfolders of --images")
    p.add_argument("--dry-run", action="store_true", help="Plan and report, but copy nothing")
    p.add_argument("--overwrite", action="store_true",
                   help="Replace different files already in the output folder")
    p.add_argument("--verify-hash", action="store_true",
                   help="Use SHA-256 (not just size) to detect already-copied files")
    p.add_argument("--workers", type=int, default=8, help="Parallel copy threads (default 8)")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def _validate_paths(args: argparse.Namespace) -> None:
    """Refuse configurations that would corrupt the run."""
    images = args.images.resolve()
    out = args.out.resolve()
    if images == out:
        raise ValueError("--out must be different from --images (source is never modified)")
    # With --recursive, an output folder inside the image folder would be
    # re-indexed on the next run, feeding renamed files back in as sources.
    if args.recursive and images in out.parents:
        raise ValueError("--out cannot be inside --images when --recursive is used")


def run(args: argparse.Namespace) -> int:
    _validate_paths(args)
    started = datetime.now()

    # BUILD: O(m)
    index = build_image_index(
        args.images, DEFAULT_EXTENSIONS, args.strip_leading_zeros, args.recursive
    )
    # READ: O(n)
    rows = read_staff_rows(
        args.sheet, args.oracle_col, args.lassra_col, args.name_col,
        args.sheet_name, args.strip_leading_zeros,
    )
    # PROBE: O(n + m), pure, no I/O
    plan = build_plan(rows, index, args.out)
    # EXECUTE: I/O-bound, parallel
    execute_plan(plan, args.out, args.dry_run, args.overwrite, args.verify_hash, args.workers)

    report_path = args.report or (
        args.out / f"rename_report_{started:%Y%m%d_%H%M%S}.csv"
    )
    write_report(plan, report_path)

    summary = summarise(plan)
    elapsed = (datetime.now() - started).total_seconds()
    print(f"\n{'DRY RUN ' if args.dry_run else ''}Summary ({elapsed:.1f}s)")
    for status, count in summary.items():
        print(f"  {status:<18} {count}")
    print(f"  Report: {report_path}")

    all_ok = all(e.status in SUCCESS_STATUSES for e in plan.row_entries.values())
    return 0 if all_ok else 2


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        return run(args)
    except (FileNotFoundError, NotADirectoryError, ColumnNotFoundError, ValueError) as exc:
        # Expected, user-fixable problems: short message, no traceback.
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
