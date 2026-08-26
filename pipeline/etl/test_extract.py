"""Tests for the readers. Fixtures live in pipeline/etl/fixtures/."""

from __future__ import annotations

import os

import pytest

from pipeline.etl import extract

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name: str) -> str:
    return os.path.join(FIXTURES, name)


# --------------------------------------------------------------------------
# the comma-in-name case
# --------------------------------------------------------------------------


def test_quoted_name_with_commas_does_not_shift_columns():
    """The bug the old Node scripts had.

    ``"Chen, Jr., Wei"`` is one quoted field containing two commas. Splitting
    the line on "," by hand yields 9 fields instead of 7 and hands this judge
    the next person's username and password.
    """
    rows = extract.read_judge_credentials(fixture("judge_credentials.csv"))
    wei = [r for r in rows if r.text("Username") == "WeiChen"]
    assert len(wei) == 1
    row = wei[0]

    assert row.text("Name") == "Chen, Jr., Wei"
    assert row.text("Email") == "wei.chen@example.com"
    assert row.text("Password") == "2222"
    assert row.text("Tracks") == "Analytics"


def test_naive_split_would_have_broken_that_row():
    """Pin the failure mode so nobody 'simplifies' the reader back to split(',')."""
    with open(fixture("judge_credentials.csv"), encoding="utf-8") as handle:
        line = [ln for ln in handle if "Chen" in ln][0]

    naive = line.rstrip("\n").split(",")
    assert len(naive) == 9  # header has 7 columns
    assert naive[3] != "TRUE"  # Checked-In lands in the wrong column


# --------------------------------------------------------------------------
# the row-21 / unquoted-tracks case
# --------------------------------------------------------------------------


def test_unquoted_tracks_cell_is_repaired_and_flagged():
    """Line 21 of the real project list ("AI Plant Companion") lost the quotes
    around its tracks cell, so that row has one field too many."""
    rows = extract.read_project_list(fixture("project_list.csv"))
    plant = [r for r in rows if r.text("Project Title") == "AI Plant Companion"]
    assert len(plant) == 1
    row = plant[0]

    assert row.repaired is True
    assert row.notes and "re-joined" in row.notes[0]
    assert (
        row.text(extract.PROJECT_TRACKS_COLUMN)
        == "Entrepreneurship & Product Management, Hardware & IoT"
    )
    # Table Number must not have been shifted by the surplus field.
    assert row.text("Table Number") == "2"


def test_well_formed_rows_are_not_marked_repaired():
    rows = extract.read_project_list(fixture("project_list.csv"))
    ago = [r for r in rows if r.text("Project Title") == "Ago"][0]
    assert ago.repaired is False
    assert ago.notes == ()


def test_ragged_row_raises_when_there_is_no_repair_function():
    with pytest.raises(extract.MalformedRowError) as exc:
        extract.read_rows(
            [["a", "b"], ["1", "2", "3"]], source="toy",
        )
    assert "3 fields" in str(exc.value)


def test_row_with_too_few_fields_cannot_be_repaired():
    with pytest.raises(extract.MalformedRowError):
        extract.read_rows(
            [["Project Title", "Table Number", "tracks"], ["Solo", "1"]],
            source="project_list",
            repair=extract._repair_project_overflow,
        )


# --------------------------------------------------------------------------
# required / optional columns
# --------------------------------------------------------------------------


def test_missing_required_column_raises_with_a_useful_message():
    with pytest.raises(extract.MissingColumnsError) as exc:
        extract.read_rows(
            [["Name", "Company"], ["Ada", "Analytical Engines"]],
            source="judge_roster",
            required=extract.JUDGE_ROSTER_REQUIRED,
        )
    message = str(exc.value)
    assert "Tracks" in message
    assert "judge_roster" in message


def test_empty_file_raises():
    with pytest.raises(extract.ExtractError):
        extract.read_rows([], source="toy")


def test_missing_file_raises_with_the_path():
    with pytest.raises(extract.ExtractError) as exc:
        extract.read_csv_file("/nope/does-not-exist.csv", source="toy")
    assert "does-not-exist.csv" in str(exc.value)


def test_devpost_tolerates_a_stripped_down_export():
    """Only Project Title is required; a thin export must still load."""
    rows = extract.read_devpost_export(fixture("devpost_minimal.csv"))
    assert [r.text("Project Title") for r in rows] == ["Ago", "Solo Track"]
    # Absent optional columns read as "" rather than blowing up.
    assert rows[0].text("Built With") == ""
    assert rows[0].text("Submission Url") == ""


def test_devpost_duplicate_column_names_are_both_kept():
    """The export ships two columns called "Video Demo Link"; DictReader would
    keep only the last, which in 2026 was the empty one."""
    rows = extract.read_devpost_export(fixture("devpost_export.csv"))
    ago = rows[0]
    assert ago.text("Video Demo Link") == ""
    assert ago.text("Video Demo Link (2)") == "https://youtu.be/ago"


def test_dedupe_header_numbers_repeats_in_order():
    assert extract.dedupe_header(["a", "b", "a", "a"]) == ["a", "b", "a (2)", "a (3)"]


# --------------------------------------------------------------------------
# storage-agnostic reading
# --------------------------------------------------------------------------


def test_read_rows_works_without_any_filesystem():
    """The Databricks path: hand read_rows a list of lists, get the same
    SourceRows a CSV would have produced."""
    rows = extract.read_rows(
        [["Name", "Tracks"], ["Ada Lovelace", "AI/ML"]],
        source="judge_roster",
        required=extract.JUDGE_ROSTER_REQUIRED,
    )
    assert len(rows) == 1
    assert rows[0].values == {"Name": "Ada Lovelace", "Tracks": "AI/ML"}
    assert rows[0].line_number == 2
    assert rows[0].where() == "judge_roster line 2"


def test_blank_rows_are_skipped_but_filler_rows_are_not():
    rows = extract.read_rows(
        [["a", "b"], ["", ""], ["   ", ""], ["", "FALSE"]], source="toy"
    )
    assert len(rows) == 1
    assert rows[0].get("b") == "FALSE"


def test_line_numbers_survive_skipped_blanks():
    rows = extract.read_rows([["a"], [""], ["x"]], source="toy")
    assert [r.line_number for r in rows] == [3]


def test_extract_all_without_a_devpost_export():
    inputs = extract.extract_all(
        judge_roster_path=fixture("judge_roster.csv"),
        judge_credentials_path=fixture("judge_credentials.csv"),
        project_list_path=fixture("project_list.csv"),
    )
    assert inputs.devpost is None
    assert inputs.counts()["devpost"] == -1
    assert inputs.counts()["judge_roster"] == 7
