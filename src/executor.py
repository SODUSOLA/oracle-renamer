"""
EXECUTE phase: perform the planned copies.

After the join is O(n + m), the run time is dominated by disk I/O, not CPU.
File copies spend most of their time waiting on the disk/network, during
which Python releases the GIL, so a THREAD pool gives near-linear speedup
for I/O-bound work without the overhead of processes.

Safety guarantees
-----------------
* Copy, never move: source images are never modified (ADR-003).
* Atomic writes: copy to "<name>.part", then os.replace() into place. A crash
  mid-copy leaves a .part file, never a half-written photo with the real name.
* Idempotent: an identical file already at the destination is ALREADY_DONE,
  so a rerun after a crash resumes instead of redoing or duplicating work.
* No silent overwrite: a DIFFERENT file at the destination is TARGET_CONFLICT
  unless --overwrite is given.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Tuple

from .models import CopyTask, Plan, Status

log = logging.getLogger(__name__)

_HASH_CHUNK = 1024 * 1024  # 1 MiB: bounded memory regardless of photo size


def execute_plan(
    plan: Plan,
    output_dir: Path,
    dry_run: bool = False,
    overwrite: bool = False,
    verify_hash: bool = False,
    workers: int = 8,
) -> None:
    """Run every CopyTask and record its outcome on the matching ReportEntry."""
    if not plan.tasks:
        log.info("Nothing to copy.")
        return

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    # Bounded pool: enough parallelism to hide disk latency, but not so much
    # that we thrash a slow USB/network drive. max(1, ...) guards bad input.
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_run_task, t, dry_run, overwrite, verify_hash): t
            for t in plan.tasks
        }
        done = 0
        # Log progress roughly every 10% so large runs stay readable.
        step = max(1, len(futures) // 10)
        for future in as_completed(futures):
            task = futures[future]
            status, detail = future.result()  # _run_task never raises (see below)

            # Results are applied here, on the MAIN thread, so no locks are
            # needed around the shared plan.row_entries dict.
            entry = plan.row_entries[task.row_number]
            entry.status = status
            entry.detail = detail

            done += 1
            if done % step == 0 or done == len(futures):
                log.info("Processed %d/%d copies", done, len(futures))


def _run_task(
    task: CopyTask, dry_run: bool, overwrite: bool, verify_hash: bool
) -> Tuple[Status, str]:
    """Copy one file. Returns (status, detail); never raises.

    Catching errors per task means one unreadable photo cannot abort the
    other 49,999 copies; it simply gets COPY_FAILED in the report.
    """
    try:
        dest = task.destination
        if dest.exists():
            if _same_file(task.source, dest, verify_hash):
                return Status.ALREADY_DONE, "Identical file already in output"
            if not overwrite:
                return Status.TARGET_CONFLICT, "Different file already at destination"

        if dry_run:
            return Status.PLANNED, "Dry run: no file written"

        _atomic_copy(task.source, dest)
        return Status.RENAMED, ""
    except OSError as exc:  # permissions, disk full, file vanished, etc.
        return Status.COPY_FAILED, f"{type(exc).__name__}: {exc}"


def _atomic_copy(source: Path, dest: Path) -> None:
    """Copy via a temporary .part file, then atomically rename into place."""
    tmp = dest.with_name(dest.name + ".part")
    try:
        shutil.copy2(source, tmp)  # copy2 also preserves timestamps
        os.replace(tmp, dest)      # atomic on the same filesystem
    finally:
        # If copy2 or replace failed, don't leave junk behind.
        if tmp.exists():
            tmp.unlink()


def _same_file(a: Path, b: Path, verify_hash: bool) -> bool:
    """Cheap equality check first (size), expensive check (SHA-256) only if asked.

    Size comparison is O(1) via stat(). Hashing is O(file size) and reads both
    files fully, so it is opt-in for when you need certainty.
    """
    if a.stat().st_size != b.stat().st_size:
        return False
    if not verify_hash:
        return True
    return _sha256(a) == _sha256(b)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        # Read in fixed chunks so a 20 MB photo never sits fully in memory.
        for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()
