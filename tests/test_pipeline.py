"""
Tests for the oracle_renamer pipeline.

The planner is pure, so most logic is tested with in-memory data. The
end-to-end tests build a real temporary folder + sheet and run the CLI.
Run with:  pytest -q
"""

from pathlib import Path

import pytest
from openpyxl import Workbook

from oracle_renamer.cli import main
from oracle_renamer.keys import cell_to_text, is_safe_filename_stem, normalise_key
from oracle_renamer.models import StaffRow, Status
from oracle_renamer.planner import build_plan


# ---------------------------------------------------------------- helpers
def row(n, oracle, lassra, name="X", strip=False):
    """Build a StaffRow the same way reader.py does."""
    from oracle_renamer.keys import normalise_lassra
    return StaffRow(n, cell_to_text(oracle), cell_to_text(lassra), name,
                    normalise_key(oracle, strip), normalise_lassra(lassra))


def make_images(folder: Path, *names: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"\xff\xd8fake-jpeg-" + name.encode())


def make_sheet(path: Path, rows) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["Oracle No", "LASSRA No", "Name"])
    for r in rows:
        ws.append(list(r))
    wb.save(path)


# ------------------------------------------------------ key normalisation
@pytest.mark.parametrize("raw,expected", [
    (12345, "12345"), (12345.0, "12345"), (" 12345 ", "12345"),
    ("12345.0", "12345"), ("ab 12", "AB12"), (None, ""),
])
def test_normalise_key(raw, expected):
    assert normalise_key(raw) == expected


def test_leading_zeros_only_stripped_when_asked():
    assert normalise_key("00123") == "00123"
    assert normalise_key("00123", strip_leading_zeros=True) == "123"
    assert normalise_key("000", strip_leading_zeros=True) == "0"


def test_unsafe_filenames_rejected():
    assert is_safe_filename_stem("LAS/123") is False
    assert is_safe_filename_stem("LAS-123") is True


# ---------------------------------------------------------------- planner
def test_plan_happy_path(tmp_path):
    index = {"100": [tmp_path / "100.jpg"]}
    plan = build_plan([row(2, "100", "L-1")], index, tmp_path / "out")
    assert len(plan.tasks) == 1
    assert plan.tasks[0].destination.name == "L-1.jpg"


def test_plan_flags_every_problem(tmp_path):
    index = {
        "100": [tmp_path / "100.jpg"],
        "200": [tmp_path / "200.jpg", tmp_path / "200.jpeg"],  # duplicate image
        "300": [tmp_path / "300.jpg"],
        "999": [tmp_path / "999.jpg"],                         # orphan
    }
    rows = [
        row(2, "", "L-A"),        # blank oracle
        row(3, "100", ""),        # blank lassra
        row(4, "100", "L/B"),     # invalid lassra (also dup oracle, but earlier check wins)
        row(5, "200", "L-C"),     # duplicate image
        row(6, "300", "L-D"),     # duplicate lassra (case-insensitive) with row 7
        row(7, "400", "l-d"),
        row(8, "500", "L-E"),     # no photo
    ]
    plan = build_plan(rows, index, tmp_path / "out")
    status = {n: e.status for n, e in plan.row_entries.items()}
    assert status == {
        2: Status.BLANK_ORACLE, 3: Status.BLANK_LASSRA, 4: Status.INVALID_LASSRA,
        5: Status.DUPLICATE_IMAGE, 6: Status.DUPLICATE_LASSRA,
        7: Status.DUPLICATE_LASSRA, 8: Status.NO_PHOTO,
    }
    assert plan.tasks == []
    assert [Path(e.source).name for e in plan.orphan_entries] == ["999.jpg"]


def test_duplicate_oracle_skips_all_rows(tmp_path):
    index = {"100": [tmp_path / "100.jpg"]}
    plan = build_plan([row(2, "100", "L-1"), row(3, "100", "L-2")], index, tmp_path)
    assert {e.status for e in plan.row_entries.values()} == {Status.DUPLICATE_ORACLE}
    assert plan.orphan_entries == []  # referenced, so not an orphan


# ------------------------------------------------------------ end to end
def test_end_to_end_run_and_rerun_is_idempotent(tmp_path):
    images, out = tmp_path / "images", tmp_path / "out"
    make_images(images, "100.jpg", "200.JPEG", "notes.txt", "777.jpg")
    sheet = tmp_path / "staff.xlsx"
    make_sheet(sheet, [(100, "LAS001", "Ada"), (200.0, "LAS002", "Bayo")])

    args = ["--images", str(images), "--sheet", str(sheet), "--out", str(out),
            "--report", str(tmp_path / "r.csv")]

    # First run: both rows copied. The orphan (777) is reported but is not a
    # row failure, so the exit code is still 0.
    assert main(args) == 0
    assert sorted(p.name for p in out.iterdir()) == ["LAS001.jpg", "LAS002.jpeg"]
    assert (images / "100.jpg").exists()          # source untouched
    report = (tmp_path / "r.csv").read_text(encoding="utf-8-sig")
    assert "ORPHAN_IMAGE" in report and "777.jpg" in report

    # Second run: nothing recopied
    assert main(args) == 0
    assert report.count("RENAMED") == 2
    assert "ALREADY_DONE" in (tmp_path / "r.csv").read_text(encoding="utf-8-sig")


def test_dry_run_writes_no_photos(tmp_path):
    images, out = tmp_path / "images", tmp_path / "out"
    make_images(images, "100.jpg")
    sheet = tmp_path / "staff.xlsx"
    make_sheet(sheet, [(100, "LAS001", "Ada")])
    assert main(["--images", str(images), "--sheet", str(sheet), "--out", str(out),
                 "--report", str(tmp_path / "r.csv"), "--dry-run"]) == 0
    assert not out.exists()


def test_conflict_not_overwritten(tmp_path):
    images, out = tmp_path / "images", tmp_path / "out"
    make_images(images, "100.jpg")
    out.mkdir()
    (out / "LAS001.jpg").write_bytes(b"someone else's photo, different size!!!")
    sheet = tmp_path / "staff.xlsx"
    make_sheet(sheet, [(100, "LAS001", "Ada")])
    code = main(["--images", str(images), "--sheet", str(sheet), "--out", str(out),
                 "--report", str(tmp_path / "r.csv")])
    assert code == 2
    assert "TARGET_CONFLICT" in (tmp_path / "r.csv").read_text(encoding="utf-8-sig")


def test_missing_column_is_fatal(tmp_path):
    images = tmp_path / "images"
    make_images(images, "100.jpg")
    sheet = tmp_path / "staff.csv"
    sheet.write_text("Oracle,Other\n100,x\n")
    assert main(["--images", str(images), "--sheet", str(sheet),
                 "--out", str(tmp_path / "out")]) == 1
