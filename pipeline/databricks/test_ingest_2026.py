"""Tests for the 2026 ingest.

No network: the Firestore documents are fixtures shaped like the real ones.
The classification tests use the two CSVs that actually ship in src/assets,
because the whole point is that those two files resolve the 372 project docs.
"""

from __future__ import annotations

import ast
import datetime as dt
import os

import pytest

from pipeline.databricks import ingest_2026 as ingest

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
SYNTHETIC_CSV = os.path.join(REPO_ROOT, ingest.DEFAULT_SYNTHETIC_CSV)
PROJECT_LIST = os.path.join(REPO_ROOT, ingest.DEFAULT_PROJECT_LIST)
HAVE_CSVS = os.path.exists(SYNTHETIC_CSV) and os.path.exists(PROJECT_LIST)

needs_csvs = pytest.mark.skipif(
    not HAVE_CSVS, reason="src/assets CSVs are not present in this checkout"
)

NOW = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)


@pytest.fixture(scope="module")
def index():
    if not HAVE_CSVS:
        pytest.skip("src/assets CSVs are not present")
    return ingest.TitleIndex.build(
        synthetic_csv=SYNTHETIC_CSV, project_list_csv=PROJECT_LIST
    )


# --------------------------------------------------------------------------
# the read-only guarantee
# --------------------------------------------------------------------------

WRITE_SHAPED = (".set(", ".update(", ".delete(", ".add(", ".create(", ".batch(")


def _source(module):
    return open(module.__file__, "r", encoding="utf-8").read()


def test_ingest_never_calls_anything_write_shaped():
    """Production Firestore holds real scores. This module reads and nothing
    else -- if a write ever lands here, this test is the tripwire."""
    source = _source(ingest)
    for token in WRITE_SHAPED:
        assert token not in source, f"{token} appeared in ingest_2026.py"


def test_ingest_touches_firestore_only_through_stream_and_list_users():
    source = _source(ingest)
    assert ".stream()" in source
    tree = ast.parse(source)
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not called & {"set", "delete", "add", "commit", "batch"}


# --------------------------------------------------------------------------
# project classification
# --------------------------------------------------------------------------


@needs_csvs
def test_a_plain_project_matches_on_its_document_id(index):
    result = ingest.classify_project({"_id": "tidal-wave", "name": "Tidal Wave"}, index)
    assert result["is_synthetic"] is False
    assert result["classification_source"] == "docid_exact"
    assert result["classification_evidence"] == ingest.REAL


@needs_csvs
@pytest.mark.parametrize(
    "doc_id,title",
    [
        ("house-m-d-", "House M.D."),
        ("panic--at-the-dataset", "Panic! At the Dataset"),
        ("o-anxiety-2-", "O(anxiety^2)"),
        ("ctrl---c", "CTRL + C"),
        ("3-sharks-1-blue---", "3 Sharks 1 Blue 🧿"),
        ("-", "ㄖ"),
        ("nah--i-d-lose", "nah, I'd lose"),
    ],
)
def test_the_punctuation_damaged_ids_still_resolve(index, doc_id, title):
    """The old slugger left repeated and trailing separators behind. These 14
    documents only match after collapsing them -- and collapsing them is the
    whole reason they are not sitting in the 'unresolved' bucket."""
    result = ingest.classify_project({"_id": doc_id, "name": title}, index)
    assert result["is_synthetic"] is False
    assert result["classification_source"] == "docid_normalized"


@needs_csvs
def test_a_synthetic_project_is_flagged_synthetic(index):
    result = ingest.classify_project({"_id": "smartvault", "name": "SmartVault"}, index)
    assert result["is_synthetic"] is True
    assert result["classification_evidence"] == ingest.SYNTHETIC


@needs_csvs
def test_an_unknown_project_is_kept_and_treated_as_real(index):
    """Never drop a row you cannot explain. Unknown provenance is a question
    for an organizer, not a reason to delete evidence."""
    result = ingest.classify_project(
        {"_id": "brand-new-thing", "name": "Brand New Thing"}, index
    )
    assert result["classification_source"] == "unresolved"
    assert result["is_synthetic"] is False


@needs_csvs
def test_a_project_whose_id_is_unrecognisable_still_matches_on_its_title(index):
    result = ingest.classify_project(
        {"_id": "project-abc123def", "name": "Tidal Wave"}, index
    )
    assert result["classification_source"] == "title_exact"
    assert result["is_synthetic"] is False


@needs_csvs
def test_a_title_in_both_csvs_is_treated_as_real():
    index = ingest.TitleIndex.build(
        synthetic_csv=SYNTHETIC_CSV, project_list_csv=PROJECT_LIST
    )
    # Real is loaded second and wins the key: calling a real project synthetic
    # is the more expensive mistake.
    for key, (is_synthetic, evidence, _title) in index.by_slug.items():
        if evidence == ingest.REAL:
            assert is_synthetic is False


# --------------------------------------------------------------------------
# judges
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "doc,expected",
    [
        ({"_id": "AarushiBajaj", "email": "a@datahacks2026.ucsd"}, "username_2026"),
        ({"_id": "anuj-jain", "email": "anuj@judge.datahacks"}, "legacy_judge_datahacks"),
        ({"_id": "anuj-jain", "email": ""}, "legacy_slug"),
        ({"_id": "NoEmail"}, "username_2026"),
    ],
)
def test_judge_generation(doc, expected):
    assert ingest.judge_generation(doc) == expected


def test_judge_rows_carry_the_auth_uid_when_the_email_resolves():
    rows = ingest.build_judge_rows(
        [
            {
                "_id": "AarushiBajaj",
                "name": "Aarushi Bajaj",
                "email": "A@datahacks2026.ucsd",
                "track": "UI/UX & Web Dev",
                "assignedProjects": ["tai", "team-snoopy"],
            },
            {"_id": "ghost", "name": "Ghost", "email": "ghost@judge.datahacks"},
        ],
        {"a@datahacks2026.ucsd": "uid-1"},
        NOW,
    )
    first, second = rows
    assert first["auth_uid"] == "uid-1"  # matched case-insensitively
    assert first["has_auth_account"] is True
    assert first["is_real"] is True
    assert first["assigned_project_count"] == 2
    assert second["auth_uid"] is None
    assert second["is_real"] is False


def test_a_judge_document_with_no_fields_does_not_crash_the_ingest():
    """One production doc really is just {"-": "-"}."""
    rows = ingest.build_judge_rows([{"_id": "SharadAgarwal", "-": "-"}], {}, NOW)
    assert rows[0]["judge_id"] == "SharadAgarwal"
    assert rows[0]["track"] is None


# --------------------------------------------------------------------------
# evaluations -- the track=null rows are the point
# --------------------------------------------------------------------------


def test_a_null_track_evaluation_is_kept_and_flagged_not_dropped():
    rows = ingest.build_evaluation_rows(
        [
            {
                "_id": "PHKqccXH3CF4bTquPFrL",
                "judgeId": "uid",
                "projectId": "tidal-wave",
                "track": None,
                "scores": {"execution": 10, "impact": 10, "innovation": 8,
                           "theme": 10, "technical": 9},
                "comment": "",
                "timestamp": "2026-04-19 22:18:51.660000+00:00",
            }
        ],
        NOW,
    )
    row = rows[0]
    assert len(rows) == 1, "the row must survive ingestion"
    assert row["include_in_analysis"] is False
    assert "track is null" in row["exclusion_reason"]
    # The scores are still there -- flagged, not discarded.
    assert row["total_score"] == 47.0
    assert row["n_criteria"] == 5


def test_a_normal_evaluation_is_included():
    rows = ingest.build_evaluation_rows(
        [
            {
                "_id": "e1",
                "judgeId": "uid",
                "projectId": "ia",
                "track": "Hardware & IoT",
                "scores": {"a": 3, "b": 10},
                "comment": "good",
                "timestamp": "2026-04-19 22:18:51.660000+00:00",
            }
        ],
        NOW,
    )
    assert rows[0]["include_in_analysis"] is True
    assert rows[0]["exclusion_reason"] is None
    assert rows[0]["total_score"] == 13.0
    assert isinstance(rows[0]["submitted_at"], dt.datetime)


def test_an_evaluation_with_no_numeric_scores_is_kept_but_excluded():
    rows = ingest.build_evaluation_rows(
        [{"_id": "e2", "track": "AI/ML", "scores": {"a": "oops"}}], NOW
    )
    assert rows[0]["include_in_analysis"] is False
    assert rows[0]["total_score"] is None


def test_booleans_are_not_counted_as_scores():
    # True == 1 in Python; silently summing it would deflate nothing but would
    # invent a criterion that was never scored.
    rows = ingest.build_evaluation_rows(
        [{"_id": "e3", "track": "AI/ML", "scores": {"a": 5, "b": True}}], NOW
    )
    assert rows[0]["n_criteria"] == 1


def test_an_unparseable_timestamp_becomes_null_rather_than_raising():
    rows = ingest.build_evaluation_rows(
        [{"_id": "e4", "track": "AI/ML", "scores": {"a": 5}, "timestamp": "nonsense"}],
        NOW,
    )
    assert rows[0]["submitted_at"] is None


# --------------------------------------------------------------------------
# the whole build
# --------------------------------------------------------------------------


@needs_csvs
def test_build_rows_summarises_everything_a_human_needs(index):
    raw = {
        "judges": [
            {"_id": "AarushiBajaj", "email": "a@datahacks2026.ucsd", "track": "AI/ML"},
            {"_id": "old-judge", "email": "o@judge.datahacks"},
        ],
        "projects": [
            {"_id": "tidal-wave", "name": "Tidal Wave", "tracks": ["Analytics"],
             "tableNumber": 3},
            {"_id": "smartvault", "name": "SmartVault", "tracks": ["AI/ML"],
             "tableNumber": 1, "builtWith": "yolov8"},
            {"_id": "mystery", "name": "Mystery", "tracks": [], "tableNumber": 9},
        ],
        "evaluations": [
            {"_id": "e1", "projectId": "tidal-wave", "track": None,
             "scores": {"a": 1}},
            {"_id": "e2", "projectId": "tidal-wave", "track": "Analytics",
             "scores": {"a": 1}},
        ],
    }
    result = ingest.build_rows(raw, {"a@datahacks2026.ucsd": "uid-1"}, index)
    summary = result.summary()

    assert summary["judges"] == 2
    assert summary["judges_real"] == 1
    assert summary["projects_synthetic"] == 1
    assert summary["projects_real"] == 2
    assert summary["unresolved_projects"] == ["mystery"]
    assert summary["evaluations"] == 2
    assert summary["evaluations_in_analysis"] == 1
    # per-project evaluation counts are joined in
    projects = {p["project_id"]: p for p in result.projects}
    assert projects["tidal-wave"]["evaluation_count"] == 2
    assert projects["smartvault"]["has_built_with"] is True
    # and the report names the excluded row rather than hiding it
    assert "e1" in result.render()
    assert "mystery" in result.render()


@needs_csvs
def test_project_rows_survive_a_missing_table_number(index):
    rows = ingest.build_project_rows(
        [{"_id": "tidal-wave", "name": "Tidal Wave", "tracks": None,
          "tableNumber": None}],
        index,
        {},
        NOW,
    )
    assert rows[0]["table_number"] is None
    assert rows[0]["tracks"] == []


def test_offline_mode_requires_a_cache_path(capsys):
    assert ingest.main(["--offline"]) == 2
    assert "--cache" in capsys.readouterr().out


@needs_csvs
def test_the_cli_defaults_to_a_dry_run():
    assert ingest.build_parser().parse_args([]).commit is False
