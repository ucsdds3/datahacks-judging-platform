"""
Readers for every file that feeds the judging platform.

This is the ONLY module in ``pipeline.etl`` that touches a filesystem. Everything
downstream (transform.py) works on the plain ``SourceRow`` objects produced here,
so the same transforms run unchanged whether the rows came from a local CSV, a
Databricks table, or a list you typed out in a test.

To add a Databricks/Spark source later you do NOT touch transform.py: you write
something that yields ``list[list[str]]`` and hand it to ``read_rows()``. The
``read_*_csv()`` helpers below are just "open the file, then call read_rows()".

Four inputs
-----------
1. judge roster        Mentors_Judges Calendar & Sign-Up - 4_19 Judge.csv
2. judge credentials   password_judges - Checked-In.csv
3. project list        Final_project_info - Sheet1.csv
4. DevPost export      the real one does not exist yet; schema is taken from
                       datahacks_2026_synthetic_submissions...csv

Two known landmines, both handled here rather than by the caller:

* The credentials sheet has judge names in it. The old Node scripts split those
  lines on "," by hand, so any name containing a comma ("Chen, Jr.") silently
  shifted every column after it -- that judge got someone else's password. We
  use the stdlib csv module, which is quote-aware.

* Line 21 of the project list ("AI Plant Companion") lost the quotes around its
  tracks cell, so that row has 6 fields where the header has 5 and every column
  after ``tracks`` is shifted one to the right. ``_repair_overflow`` folds the
  extra field back into the tracks cell and marks the row ``repaired=True`` so
  the validation report can show a human what was touched.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------


class ExtractError(RuntimeError):
    """Base class for anything that goes wrong while reading a source."""


class MissingColumnsError(ExtractError):
    """A source is missing a column the pipeline cannot work without."""


class MalformedRowError(ExtractError):
    """A row's shape could not be reconciled with the header."""


# --------------------------------------------------------------------------
# the row type everything downstream speaks
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceRow:
    """One row of a tabular source, plus enough context to write a good error.

    ``values`` maps column name -> raw (untrimmed) string. Nothing is coerced,
    parsed, or dropped here; that is transform.py's job.
    """

    source: str
    line_number: int
    values: Dict[str, str]
    repaired: bool = False
    notes: Tuple[str, ...] = ()

    def get(self, column: str, default: str = "") -> str:
        """Raw value for a column, or ``default`` if the column is absent."""
        value = self.values.get(column)
        return default if value is None else value

    def text(self, column: str, default: str = "") -> str:
        """Trimmed value for a column. Most callers want this one."""
        return self.get(column, default).strip()

    def where(self) -> str:
        """Human-readable location, for error messages."""
        return f"{self.source} line {self.line_number}"


# --------------------------------------------------------------------------
# generic, storage-agnostic table reader
# --------------------------------------------------------------------------

RepairFn = Callable[[Sequence[str], Sequence[str]], Tuple[List[str], str]]


def dedupe_header(header: Sequence[str]) -> List[str]:
    """Make column names unique, preserving order.

    The DevPost export really does ship two columns both called "Video Demo
    Link". csv.DictReader would keep only the last one -- which in the 2026
    export is the empty one. We rename the second occurrence to
    "Video Demo Link (2)" so both survive and the caller can coalesce them.
    """
    seen: Dict[str, int] = {}
    out: List[str] = []
    for raw in header:
        name = raw.strip()
        if name in seen:
            seen[name] += 1
            out.append(f"{name} ({seen[name]})")
        else:
            seen[name] = 1
            out.append(name)
    return out


def read_rows(
    rows: Iterable[Sequence[str]],
    *,
    source: str,
    required: Sequence[str] = (),
    repair: Optional[RepairFn] = None,
    skip_blank: bool = True,
) -> List[SourceRow]:
    """Turn raw ``list[list[str]]`` into ``SourceRow``s. No filesystem access.

    ``rows`` is header-first. ``required`` columns must exist or we raise --
    a missing column is a broken export, not a per-record problem, and carrying
    on would write hundreds of half-empty documents.

    ``repair`` is called for rows whose field count does not match the header;
    it returns ``(fixed_row, note)``. Without a repair function, a ragged row
    raises rather than being silently truncated.
    """
    iterator = iter(rows)
    try:
        raw_header = next(iterator)
    except StopIteration:
        raise ExtractError(f"{source}: file is empty (no header row).") from None

    header = dedupe_header(raw_header)
    missing = [c for c in required if c not in header]
    if missing:
        raise MissingColumnsError(
            f"{source}: missing required column(s) {missing}. "
            f"Found columns: {header}. "
            f"Fix the export or update the column list in pipeline/etl/extract.py."
        )

    out: List[SourceRow] = []
    for offset, raw_row in enumerate(iterator):
        line_number = offset + 2  # header is line 1

        if skip_blank and not any((cell or "").strip() for cell in raw_row):
            continue

        row = list(raw_row)
        notes: Tuple[str, ...] = ()
        repaired = False

        if len(row) != len(header):
            if repair is None:
                raise MalformedRowError(
                    f"{source} line {line_number}: row has {len(row)} fields but "
                    f"the header has {len(header)}. Row: {row!r}"
                )
            row, note = repair(header, row)
            repaired = True
            notes = (note,)
            if len(row) != len(header):
                raise MalformedRowError(
                    f"{source} line {line_number}: repair produced {len(row)} "
                    f"fields, expected {len(header)}. Row: {raw_row!r}"
                )

        out.append(
            SourceRow(
                source=source,
                line_number=line_number,
                values=dict(zip(header, row)),
                repaired=repaired,
                notes=notes,
            )
        )
    return out


def read_csv_file(
    path: str,
    *,
    source: str,
    required: Sequence[str] = (),
    repair: Optional[RepairFn] = None,
) -> List[SourceRow]:
    """Quote-aware CSV read. The only place a path is opened."""
    try:
        # utf-8-sig: Google Sheets exports carry a BOM, which would otherwise
        # become part of the first column's name.
        with open(path, "r", newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.reader(handle))
    except FileNotFoundError:
        raise ExtractError(
            f"{source}: no such file {path!r}. "
            f"Check the path, or pass a different one on the command line."
        ) from None
    return read_rows(rows, source=source, required=required, repair=repair)


# --------------------------------------------------------------------------
# 1. judge roster
# --------------------------------------------------------------------------

JUDGE_ROSTER_SOURCE = "judge_roster"
JUDGE_ROSTER_REQUIRED = ("Name", "Tracks")


def read_judge_roster(path: str) -> List[SourceRow]:
    """Mentors_Judges Calendar & Sign-Up - 4_19 Judge.csv

    Columns: Name, Company, Role, Tracks, Format (+ per-event checkbox columns
    that we ignore). Supplies the affiliation fields; the credentials sheet is
    authoritative for track / email / username.
    """
    return read_csv_file(
        path,
        source=JUDGE_ROSTER_SOURCE,
        required=JUDGE_ROSTER_REQUIRED,
    )


# --------------------------------------------------------------------------
# 2. judge credentials / check-in
# --------------------------------------------------------------------------

JUDGE_CREDENTIALS_SOURCE = "judge_credentials"
JUDGE_CREDENTIALS_REQUIRED = ("Name", "Username", "Email", "Tracks")


def read_judge_credentials(path: str) -> List[SourceRow]:
    """password_judges - Checked-In.csv

    Columns: Tracks, Name, Checked-In, Email, Username, <blank>, Password.

    The blank-named column is a leftover note column ("{Firstname}+{Lastname}")
    and is preserved but unused. The Password column is deliberately NOT
    propagated into any loaded document -- see transform.build_judges.

    Read with the csv module, not str.split(","). A name like ``Chen, Jr.`` is
    quoted in the file; hand-splitting it shifts Email/Username/Password by one
    and hands that judge someone else's login.
    """
    return read_csv_file(
        path,
        source=JUDGE_CREDENTIALS_SOURCE,
        required=JUDGE_CREDENTIALS_REQUIRED,
    )


# --------------------------------------------------------------------------
# 3. project list
# --------------------------------------------------------------------------

PROJECT_LIST_SOURCE = "project_list"
PROJECT_TITLE_COLUMN = "Project Title"
PROJECT_TABLE_COLUMN = "Table Number"
PROJECT_TRACKS_COLUMN = "tracks"
PROJECT_FIRST_TRACK_COLUMN = "What's The First Track You'd Like To Submit To?"
PROJECT_SECOND_TRACK_COLUMN = "What's The Second Track You'd Like To Submit To?"
PROJECT_LIST_REQUIRED = (PROJECT_TITLE_COLUMN, PROJECT_TABLE_COLUMN)


def _repair_project_overflow(
    header: Sequence[str], row: Sequence[str]
) -> Tuple[List[str], str]:
    """Fold an unquoted, comma-bearing ``tracks`` cell back into one field.

    Line 21 of the 2026 project list reads:

        AI Plant Companion,20,Entrepreneurship & Product Management, Hardware & IoT,Entrepreneurship & Product Management Hardware & IoT,

    The tracks cell lost its quotes, so its internal comma became a field
    separator: 6 fields for a 5-column header, with everything after ``tracks``
    shifted right by one.

    ``tracks`` is the only free-text, comma-bearing column in this sheet, so any
    overflow necessarily belongs to it. We re-join the surplus fields into the
    tracks position and leave the trailing columns aligned to the end of the row.
    The resulting first/second-track cells may still hold two run-together track
    names; ``pipeline.tracks.split_concatenated`` recovers those downstream.
    """
    extra = len(row) - len(header)
    if extra <= 0:
        raise MalformedRowError(
            f"project list row has {len(row)} fields, fewer than the "
            f"{len(header)}-column header; cannot repair. Row: {list(row)!r}"
        )
    if PROJECT_TRACKS_COLUMN not in header:
        raise MalformedRowError(
            f"project list row has {extra} surplus field(s) but there is no "
            f"{PROJECT_TRACKS_COLUMN!r} column to fold them into. "
            f"Row: {list(row)!r}"
        )

    index = list(header).index(PROJECT_TRACKS_COLUMN)
    merged = ",".join(row[index : index + extra + 1])
    fixed = list(row[:index]) + [merged] + list(row[index + extra + 1 :])
    return fixed, (
        f"unquoted {PROJECT_TRACKS_COLUMN!r} cell split across "
        f"{extra + 1} fields; re-joined as {merged!r}"
    )


def read_project_list(path: str) -> List[SourceRow]:
    """Final_project_info - Sheet1.csv

    Columns: Project Title, Table Number, tracks, first track, second track.
    Ragged rows are repaired (and flagged) rather than dropped.
    """
    return read_csv_file(
        path,
        source=PROJECT_LIST_SOURCE,
        required=PROJECT_LIST_REQUIRED,
        repair=_repair_project_overflow,
    )


# --------------------------------------------------------------------------
# 4. DevPost submission export
# --------------------------------------------------------------------------

DEVPOST_SOURCE = "devpost_export"

# Only the title is load-bearing; everything else enriches the project document
# and may be absent from a given export. DevPost's column set changes between
# events, so this reader must not require the optional ones.
DEVPOST_REQUIRED = ("Project Title",)
DEVPOST_OPTIONAL = (
    "Submission Url",
    "Project Status",
    "About The Project",
    "Video Demo Link",
    "Built With",
    "Table Number",
    "What's The First Track You'd Like To Submit To?",
    "What's The Second Track You'd Like To Submit To?",
    "Which Sponsor Challenges Are You Submitting To?",
    "Project Github Repository Url",
)


def read_devpost_export(path: str) -> List[SourceRow]:
    """The DevPost submissions CSV export.

    The real 2026 export is not in the repo yet. This reader is written against
    the column schema visible in the synthetic sample and is deliberately
    tolerant: only "Project Title" is required, so an export that drops
    "Built With" or renames the sponsor-challenge question still loads. Missing
    optional columns simply come back as "" via ``SourceRow.text``.

    Duplicate column names (the export ships two "Video Demo Link" columns) are
    disambiguated by ``dedupe_header``; ``transform`` coalesces them.
    """
    return read_csv_file(path, source=DEVPOST_SOURCE, required=DEVPOST_REQUIRED)


# --------------------------------------------------------------------------
# bundling
# --------------------------------------------------------------------------


@dataclass
class ExtractedInputs:
    """Everything the pipeline read, before any interpretation."""

    judge_roster: List[SourceRow] = field(default_factory=list)
    judge_credentials: List[SourceRow] = field(default_factory=list)
    project_list: List[SourceRow] = field(default_factory=list)
    devpost: Optional[List[SourceRow]] = None

    def counts(self) -> Dict[str, int]:
        counts = {
            "judge_roster": len(self.judge_roster),
            "judge_credentials": len(self.judge_credentials),
            "project_list": len(self.project_list),
        }
        counts["devpost"] = -1 if self.devpost is None else len(self.devpost)
        return counts


def extract_all(
    *,
    judge_roster_path: str,
    judge_credentials_path: str,
    project_list_path: str,
    devpost_path: Optional[str] = None,
) -> ExtractedInputs:
    """Read every input. ``devpost_path`` is optional -- the real export may not
    exist yet, in which case synthetic-vs-real classification is skipped and the
    validation report says so loudly."""
    return ExtractedInputs(
        judge_roster=read_judge_roster(judge_roster_path),
        judge_credentials=read_judge_credentials(judge_credentials_path),
        project_list=read_project_list(project_list_path),
        devpost=read_devpost_export(devpost_path) if devpost_path else None,
    )
