# Oracle → LASSRA Photo Renamer

A tool that takes a folder of staff passport photos named by **Oracle number**,
looks each one up in a staff Excel/CSV sheet, and saves a copy named by the
staff member's **LASSRA number** into a new folder. Every row and every image
is accounted for in a CSV report.

The original photos are **never modified or moved**.

Two ways to use it:

- **Web UI** (Streamlit) -- drag-and-drop the sheet and photos, click Run,
  download the renamed photos and the report. Best for one-off or moderate
  batches (tens to a few thousand photos) run by HR/IT without a terminal.
- **CLI** (`python -m oracle_renamer`) -- scriptable, no upload size limits,
  the right choice for very large batches (tens of thousands of photos) run
  locally or in a scheduled job.

Both share the exact same matching/copying engine (`oracle_renamer/`), so
results are identical either way.

---

## Quick start (CLI)

```bash
# 1. Set up
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Preview first (writes the report, copies nothing)
python -m oracle_renamer --images ./photos --sheet ./staff.xlsx --out ./renamed --dry-run

# 3. Real run
python -m oracle_renamer --images ./photos --sheet ./staff.xlsx --out ./renamed
```

Then open the report CSV in Excel and filter the `status` column for anything
that isn't `RENAMED`.

### Command-line options

| Option | Default | Purpose |
|---|---|---|
| `--images PATH` | required | Folder of Oracle-named `.jpg`/`.jpeg` photos |
| `--sheet PATH` | required | Staff data file (`.xlsx` or `.csv`) |
| `--out PATH` | required | Output folder (created if missing) |
| `--report PATH` | `<out>/rename_report_<timestamp>.csv` | Where to write the report |
| `--oracle-col` | `Oracle No` | Header of the Oracle number column |
| `--lassra-col` | `LASSRA No` | Header of the LASSRA number column |
| `--name-col` | `Name` | Header of the name column (optional; used in the report) |
| `--sheet-name` | first sheet | Worksheet to read in an `.xlsx` file |
| `--strip-leading-zeros` | off | Treat `00123` and `123` as the same Oracle number |
| `--recursive` | off | Also scan subfolders of `--images` |
| `--dry-run` | off | Plan and report without copying anything |
| `--overwrite` | off | Replace a *different* file already at the destination |
| `--verify-hash` | off | Use SHA-256 (not just file size) to detect already-copied files |
| `--workers N` | `8` | Parallel copy threads |
| `-v`, `--verbose` | off | Debug logging |

Column headers are matched ignoring case, spaces, and punctuation, so
`Oracle No`, `ORACLE NO.`, and `oracle_no` all work.

### Report statuses

Every staff row gets **exactly one** status. Images no row claimed are listed
at the end.

| Status | Meaning | What to do |
|---|---|---|
| `RENAMED` | Photo copied to `<LASSRA>.jpg` | Nothing |
| `PLANNED` | Dry run: would be copied | Run without `--dry-run` |
| `ALREADY_DONE` | Identical file already in output (rerun) | Nothing |
| `BLANK_ORACLE` | Row has no Oracle number | Fill it in the sheet |
| `BLANK_LASSRA` | Row has no LASSRA number | Fill it in the sheet |
| `INVALID_LASSRA` | LASSRA has characters illegal in filenames (`/ \ : * ? " < > \|`) | Correct the value |
| `DUPLICATE_ORACLE` | Same Oracle number on 2+ rows (all are skipped) | Find the wrong row |
| `DUPLICATE_LASSRA` | Same LASSRA number on 2+ rows (all are skipped) | Find the wrong row |
| `NO_PHOTO` | No image with this Oracle number | Locate or retake the photo |
| `DUPLICATE_IMAGE` | 2+ images share this Oracle number | Delete the wrong one |
| `TARGET_CONFLICT` | A *different* file already has the output name | Check it, or use `--overwrite` |
| `COPY_FAILED` | OS error while copying (details in report) | Check permissions/disk space |
| `ORPHAN_IMAGE` | Image that no staff row references | Check whether a row is missing |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Every row ended as `RENAMED`, `PLANNED`, or `ALREADY_DONE` |
| `1` | Fatal error (missing folder, missing column, bad arguments) |
| `2` | Run finished, but some rows need attention (see report) |

### Safe to rerun

If a run is interrupted (power cut, closed laptop), just run the same command
again. Files already copied are detected as `ALREADY_DONE` and skipped, and
half-written files are never left under a real LASSRA name.

---

## Web UI (Streamlit)

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

This opens a browser tab at `http://localhost:8501`. In the sidebar:

1. Upload the staff sheet (`.xlsx`/`.xlsm`/`.csv`). If it has multiple
   worksheets, pick the one to use.
2. Upload photos either as individual files or as a single ZIP (ZIP is
   recommended for large batches and supports subfolders).
3. Adjust options if needed (same meanings as the CLI flags above) and click
   **Run**.
4. Review the summary and the filterable report table, then download the
   report CSV and/or a ZIP of the renamed photos.

The UI calls the exact same pipeline modules as the CLI -- it just replaces
the file-and-folder plumbing with uploads and downloads, so results are
identical to running the CLI on the same inputs. Uploaded files and copies
are written to a temporary directory on the server and deleted as soon as the
run finishes; only the report and the renamed photos are kept, in memory, for
you to download.

**Scale note:** the web UI holds uploads and copies in server memory/disk for
the duration of one run, and hosted platforms (see below) cap both. For a
very large one-off import (tens of thousands of photos), run the CLI locally
instead -- it streams from disk and has no such ceiling (see
[Scaling path](#scaling-path)).

### Deploying the web UI

**Streamlit Community Cloud** (free, simplest):

1. Push this repo to GitHub.
2. At [share.streamlit.io](https://share.streamlit.io), create a new app
   pointing at this repo, branch, and `streamlit_app.py` as the entry point.
3. Streamlit Cloud installs `requirements.txt` automatically. No other
   configuration is needed.

**Self-hosted / Docker** (any VM, Render, Fly.io, etc.):

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

Build and run:

```bash
docker build -t oracle-renamer .
docker run -p 8501:8501 oracle-renamer
```

Because the app keeps no persistent state between runs (everything lives in
a temp directory that's deleted after each run, or in browser-side session
state), it scales horizontally with no shared storage or database needed.

---

## Project structure

```
oracle-renamer/
├── oracle_renamer/
│   ├── __init__.py      # package metadata
│   ├── __main__.py      # enables `python -m oracle_renamer`
│   ├── models.py        # dataclasses + Status enum (stage contracts)
│   ├── keys.py          # key normalisation (the correctness core)
│   ├── indexer.py       # BUILD phase: folder -> hash multimap
│   ├── reader.py        # read + normalise the staff sheet
│   ├── planner.py       # PROBE phase: pure join logic, no I/O
│   ├── executor.py      # parallel, atomic, idempotent copies
│   ├── report.py        # CSV audit trail + summary
│   └── cli.py           # CLI argument parsing + orchestration
├── streamlit_app.py     # Web UI -- reuses the same pipeline as the CLI
├── tests/
│   └── test_pipeline.py
├── requirements.txt
└── requirements-dev.txt
```

## Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

The suite covers key normalisation, every planner status, dry run, idempotent
reruns, conflict protection, and fatal errors (15 tests).

## How it works

For the full design -- architecture diagram, Architecture Decision Records,
algorithm complexity, and the DSA rules behind each data structure choice --
see [`oracle-lassra-photo-renamer.md`](../oracle-lassra-photo-renamer.md) in
the parent folder. In short: a hash join (`indexer.py` builds a folder ->
path multimap once, `planner.py` probes it once per row), so matching is
O(n + m) instead of the O(n × m) a naive nested loop would need.

### Scaling path

| Scale | Approach | Change needed |
|---|---|---|
| Up to a few million images (CLI) | Current in-memory hash join | None |
| Index doesn't fit in RAM | SQLite table + index on `oracle_no`, join in SQL; or an external sort-merge join | Replace `indexer.py` + planner lookup |
| Continuous uploads, many departments | Service + job queue; hash/index each photo on arrival | New front end; reuse `keys.py` + planner rules |
