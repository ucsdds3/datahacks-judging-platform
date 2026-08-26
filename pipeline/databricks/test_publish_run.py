"""Tests for publish_run.

Nothing here touches Firestore or Databricks. The solver is real -- it is fast
and deterministic, and a fake would not tell us whether the run document
matches what it produces.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import os

import pytest

from pipeline.assignment.solver import (
    DisconnectedAssignmentError,
    Judge,
    Project,
    SolverConfig,
)
from pipeline.databricks import ingest_2026 as ingest
from pipeline.databricks import publish_run as pr

NOW = dt.datetime(2026, 4, 19, 9, 32, 11, tzinfo=dt.timezone.utc)


@pytest.fixture
def index():
    """A tiny title index, so these tests do not depend on the CSVs."""
    idx = ingest.TitleIndex()
    for is_synthetic, evidence, title, slug in (
        (False, ingest.REAL, "Tidal Wave", "tidal-wave"),
        (False, ingest.REAL, "House M.D.", "house-m-d"),
        (True, ingest.SYNTHETIC, "SmartVault", "smartvault"),
    ):
        idx.by_slug[slug] = (is_synthetic, evidence, title)
        idx.by_title[title.lower()] = (is_synthetic, evidence, title)
    return idx


JUDGE_DOCS = [
    {"_id": "AarushiBajaj", "name": "Aarushi Bajaj", "track": "AI/ML",
     "email": "a@datahacks2026.ucsd"},
    {"_id": "BobBuilder", "name": "Bob Builder", "track": "AI/ML",
     "email": "b@datahacks2026.ucsd"},
    {"_id": "NoAccount", "name": "No Account", "track": "AI/ML",
     "email": "n@datahacks2026.ucsd"},
    {"_id": "ShyamMani", "name": "Shyam Mani", "track": "Marimo Challenge",
     "email": "s@datahacks2026.ucsd"},
]

PROJECT_DOCS = [
    {"_id": "tidal-wave", "name": "Tidal Wave", "tracks": ["AI/ML"], "tableNumber": 1},
    {"_id": "house-m-d-", "name": "House M.D.", "tracks": ["AI/ML"], "tableNumber": 2},
    {"_id": "smartvault", "name": "SmartVault", "tracks": ["AI/ML"],
     "tableNumber": 3, "builtWith": "yolov8"},
]

UID_TO_EMAIL = {"uid-a": "a@datahacks2026.ucsd", "uid-b": "b@datahacks2026.ucsd"}


# --------------------------------------------------------------------------
# the write surface
# --------------------------------------------------------------------------


def test_the_only_firestore_write_is_the_runs_document():
    """Exactly one write-shaped call may exist in this module, and it must be
    the runs/{runId} set. Anything else touching production is a bug."""
    source = open(pr.__file__, "r", encoding="utf-8").read()
    for token in (".update(", ".delete(", ".add(", ".batch("):
        assert token not in source, f"{token} appeared in publish_run.py"
    sets = [line.strip() for line in source.splitlines() if ".set(" in line]
    assert sets == ['db.collection("runs").document(run_id).set(payload)']


def test_the_firestore_write_lives_only_in_write_firestore_run():
    tree = ast.parse(open(pr.__file__, "r", encoding="utf-8").read())
    owners = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "set"
            ):
                owners.append(node.name)
    assert owners == ["write_firestore_run"]


def test_the_cli_is_a_dry_run_unless_you_ask_otherwise():
    args = pr.build_parser().parse_args([])
    assert args.commit is False
    assert args.checkins == "firestore"


# --------------------------------------------------------------------------
# check-in sources
# --------------------------------------------------------------------------


def test_the_checkin_csv_reader_only_takes_the_true_rows(tmp_path):
    path = tmp_path / "checkin.csv"
    path.write_text(
        "Tracks,Name,Checked-In,Email,Username,,Password\n"
        "AI/ML,Manoj,TRUE,m@x.com,ManojKrishnaMohan,,9007\n"
        'Analytics,"Chen, Jr.",TRUE,c@x.com,ChenJr,,1234\n'
        "Analytics,Nobody,FALSE,n@x.com,Nobody,,5555\n",
        encoding="utf-8",
    )
    # The quoted name must not shift the Username column -- hand-splitting on
    # "," here is how a judge got someone else's login in 2026.
    assert pr.read_checkins_from_csv(str(path)) == ["ManojKrishnaMohan", "ChenJr"]


# --------------------------------------------------------------------------
# solver inputs
# --------------------------------------------------------------------------


def test_judges_are_keyed_by_auth_uid_so_scores_can_be_attributed(index):
    inputs = pr.build_solver_inputs(
        JUDGE_DOCS, PROJECT_DOCS, ["AarushiBajaj", "BobBuilder"], UID_TO_EMAIL, index
    )
    assert sorted(j.id for j in inputs.judges) == ["uid-a", "uid-b"]
    assert inputs.judge_doc_ids["uid-a"] == "AarushiBajaj"


def test_a_judge_with_no_auth_account_keeps_their_doc_id_and_is_warned_about(index):
    inputs = pr.build_solver_inputs(
        JUDGE_DOCS, PROJECT_DOCS, ["NoAccount"], UID_TO_EMAIL, index
    )
    assert [j.id for j in inputs.judges] == ["NoAccount"]
    assert any("no Firebase Auth account" in w for w in inputs.warnings)


def test_synthetic_projects_are_not_assignable(index):
    """212 of the 372 project documents are dry-run leftovers. Assigning them
    would spend a third of the room's judging capacity on nothing."""
    inputs = pr.build_solver_inputs(
        JUDGE_DOCS, PROJECT_DOCS, ["AarushiBajaj"], UID_TO_EMAIL, index
    )
    assert sorted(p.id for p in inputs.projects) == ["house-m-d-", "tidal-wave"]


def test_a_challenge_track_judge_is_excluded_and_explained(index):
    inputs = pr.build_solver_inputs(
        JUDGE_DOCS, PROJECT_DOCS, ["AarushiBajaj", "ShyamMani"], UID_TO_EMAIL, index
    )
    assert [j.id for j in inputs.judges] == ["uid-a"]
    assert any("Marimo Challenge" in w for w in inputs.warnings)
    # The count of people who checked in is still 2 -- the console compares
    # judgeCount against that on purpose.
    assert inputs.checkin_snapshot_count == 2


def test_a_checkin_with_no_judge_document_is_reported_not_ignored(index):
    inputs = pr.build_solver_inputs(
        JUDGE_DOCS, PROJECT_DOCS, ["WhoDis"], UID_TO_EMAIL, index
    )
    assert inputs.judges == []
    assert any("no judges/ document" in w for w in inputs.warnings)


def test_duplicate_checkins_do_not_duplicate_a_judge(index):
    inputs = pr.build_solver_inputs(
        JUDGE_DOCS, PROJECT_DOCS, ["AarushiBajaj", "AarushiBajaj"], UID_TO_EMAIL, index
    )
    assert len(inputs.judges) == 1


# --------------------------------------------------------------------------
# solving
# --------------------------------------------------------------------------


def _small_inputs(index, n_judges=3, n_projects=9):
    judges = [
        Judge(id=f"uid-{i}", name=f"Judge {i}", track="AI/ML") for i in range(n_judges)
    ]
    projects = [
        Project(id=f"p{i}", name=f"P{i}", tracks=("AI/ML",), table_number=i)
        for i in range(n_projects)
    ]
    inputs = pr.SolverInputs(
        judges=judges,
        projects=projects,
        judge_doc_ids={j.id: j.id.replace("uid-", "doc-") for j in judges},
        checkin_snapshot_count=n_judges,
    )
    return inputs


def test_a_good_solve_produces_a_document_the_console_can_render(index):
    inputs = _small_inputs(index)
    config = SolverConfig(min_judges_per_project=2, anchors_per_track=1)
    outcome = pr.solve_capturing(inputs.judges, inputs.projects, config)
    assert outcome.ok

    doc = pr.build_run_document(
        "2026-04-19T09-32-11Z", outcome, inputs, config,
        generated_by="map@ucsd.edu", generated_at=NOW.isoformat(),
    )

    # Required by the contract: without generatedAt the console cannot see it
    # at all, and connectivityOk is the gate.
    assert doc["generatedAt"]
    assert doc["connectivityOk"] is True
    assert doc["status"] == "ok"
    assert doc["judgeCount"] == 3
    assert doc["projectCount"] == 9
    assert doc["checkinSnapshotCount"] == 3
    assert doc["config"]["minJudgesPerProject"] == 2
    assert doc["config"]["anchorsPerTrack"] == 1
    # Firestore map keys must be strings.
    assert all(isinstance(k, str) for k in doc["coverage"]["histogram"])
    assert doc["tracks"][0]["track"] == "AI/ML"
    assert doc["tracks"][0]["connected"] is True
    # judgeIndex is what makes score attribution exact rather than inferred.
    assert doc["judgeIndex"]["uid-0"]["judgeDocId"] == "doc-0"
    assert doc["errors"] == []


def test_judge_count_is_the_checked_in_count_not_the_roster(index):
    """97 rostered vs 63 present is the specific gap that broke 2026, and the
    console draws a roster-drift warning off this field."""
    roster = JUDGE_DOCS  # four people on the roster
    inputs = pr.build_solver_inputs(
        roster, PROJECT_DOCS, ["AarushiBajaj", "BobBuilder"], UID_TO_EMAIL, index
    )
    config = SolverConfig(min_judges_per_project=1, anchors_per_track=1)
    outcome = pr.solve_capturing(inputs.judges, inputs.projects, config)
    doc = pr.build_run_document(
        "r", outcome, inputs, config, generated_by="x", generated_at=NOW.isoformat()
    )
    assert doc["judgeCount"] == 2
    assert doc["judgeCount"] < len(roster)


def test_disconnected_error_carries_the_report_and_assignment():
    """The red banner needs to name stranded judges, not just say 'failed'.

    solver.py attaches both the connectivity report and the rejected assignment
    to the exception so callers can render them.
    """
    import dataclasses

    from pipeline.assignment.solver import solve, enforce_connectivity

    judges = [{"id": f"j{i}", "track": "AI/ML", "mode": "individual"} for i in range(4)]
    projects = [
        {"id": f"p{i}", "tracks": ["AI/ML"], "table_number": i} for i in range(8)
    ]
    healthy = solve(
        judges, projects, SolverConfig(min_judges_per_project=3, anchors_per_track=3)
    )
    # Split it into two islands that share no project.
    islanded = dataclasses.replace(
        healthy,
        judge_projects={
            "j0": ["p0", "p1"], "j1": ["p0", "p1"],
            "j2": ["p6", "p7"], "j3": ["p6", "p7"],
        },
        project_judges={
            "p0": ["j0", "j1"], "p1": ["j0", "j1"],
            "p6": ["j2", "j3"], "p7": ["j2", "j3"],
        },
    )

    with pytest.raises(DisconnectedAssignmentError) as caught:
        enforce_connectivity(islanded)

    err = caught.value
    assert err.report is not None, "report must ride along for the red banner"
    assert err.report["ok"] is False
    assert err.report["failures"] == ["AI/ML"]
    assert err.assignment is not None, "the rejected assignment must survive"
    # Both islands must be nameable, or the console cannot say who is stranded.
    components = err.report["tracks"]["AI/ML"]["components"]
    assert sorted(len(c) for c in components) == [2, 2]


def test_solve_capturing_surfaces_the_failed_assignment(monkeypatch, index):
    """publish_run must build a failure document, not crash, when the gate fires."""
    from pipeline.assignment import solver as solver_module

    sentinel_report = {
        "ok": False,
        "failures": ["AI/ML"],
        "tracks": {
            "AI/ML": {
                "connected": False,
                "n_components": 2,
                "components": [["j1"], ["j2"]],
                "isolated_judges": [],
            }
        },
    }

    def always_reject(assignment):
        raise DisconnectedAssignmentError(
            "2 islands in AI/ML", report=sentinel_report, assignment=assignment
        )

    monkeypatch.setattr(solver_module, "enforce_connectivity", always_reject)

    inputs = _small_inputs(index)
    outcome = pr.solve_capturing(
        inputs.judges, inputs.projects, SolverConfig(min_judges_per_project=2)
    )

    assert outcome.ok is False
    assert outcome.assignment is not None, "the rejected assignment must survive"
    assert outcome.connectivity["failures"] == ["AI/ML"]

def test_solve_capturing_always_restores_the_solver(monkeypatch, index):
    from pipeline.assignment import solver as solver_module

    before = solver_module.enforce_connectivity
    inputs = _small_inputs(index)
    pr.solve_capturing(inputs.judges, inputs.projects, SolverConfig())
    assert solver_module.enforce_connectivity is before


def test_a_disconnected_run_still_produces_a_red_banner_document(index):
    """The console renders connectivityOk=false as a full-width red banner
    naming each island and the stranded judges. Writing nothing at all -- what
    2026 effectively did -- is the failure mode this replaces."""
    inputs = _small_inputs(index)
    config = SolverConfig(min_judges_per_project=2, anchors_per_track=1)
    good = pr.solve_capturing(inputs.judges, inputs.projects, config)

    failed = pr.SolveOutcome(
        assignment=good.assignment,
        error=DisconnectedAssignmentError("Judge-project graph is DISCONNECTED."),
        connectivity={
            "ok": False,
            "failures": ["AI/ML"],
            "tracks": {
                "AI/ML": {
                    "connected": False,
                    "n_components": 2,
                    "n_judges": 3,
                    "n_projects": 9,
                    "isolated_judges": ["uid-2"],
                    "components": [["uid-0", "uid-1"], ["uid-2"]],
                }
            },
        },
    )
    doc = pr.build_run_document(
        "r", failed, inputs, config, generated_by="x", generated_at=NOW.isoformat()
    )

    assert doc["status"] == "failed"
    assert doc["connectivityOk"] is False
    assert doc["errors"] and "DISCONNECTED" in doc["errors"][0]
    track = doc["tracks"][0]
    assert track["connected"] is False
    assert track["components"] == 2
    assert track["isolatedJudges"] == ["uid-2"]
    # An Auth UID means nothing to an organizer at 9am, so names travel too.
    assert track["isolatedJudgeNames"] == ["Judge 2"]
    # It still reports how far it got, so the gap is visible.
    assert doc["coverage"]["nProjects"] == 9


def test_a_run_document_survives_json_serialisation(index):
    inputs = _small_inputs(index)
    config = SolverConfig(min_judges_per_project=2, anchors_per_track=1)
    outcome = pr.solve_capturing(inputs.judges, inputs.projects, config)
    doc = pr.build_run_document(
        "r", outcome, inputs, config, generated_by="x", generated_at=NOW.isoformat()
    )
    json.loads(json.dumps(doc))  # Firestore rejects anything that cannot


# --------------------------------------------------------------------------
# Delta rows
# --------------------------------------------------------------------------


def test_assignment_rows_carry_the_anchor_flag_and_the_doc_id(index):
    inputs = _small_inputs(index)
    config = SolverConfig(min_judges_per_project=2, anchors_per_track=1)
    outcome = pr.solve_capturing(inputs.judges, inputs.projects, config)
    rows = pr.assignment_rows("run-1", outcome, inputs, NOW)

    assert rows
    assert {r["run_id"] for r in rows} == {"run-1"}
    assert any(r["is_anchor"] for r in rows), "anchors are what keep it connected"
    assert rows[0]["judge_doc_id"] == "doc-0"
    assert rows[0]["table_number"] is not None


def test_assignment_rows_match_the_delta_schema(index):
    from pipeline.databricks import schema

    inputs = _small_inputs(index)
    outcome = pr.solve_capturing(inputs.judges, inputs.projects, SolverConfig())
    rows = pr.assignment_rows("run-1", outcome, inputs, NOW)
    allowed = set(schema.ASSIGNMENTS.column_names)
    for row in rows:
        assert set(row) <= allowed


def test_run_row_matches_the_delta_schema_and_flattens_the_nested_blocks(index):
    from pipeline.databricks import schema

    inputs = _small_inputs(index)
    config = SolverConfig(min_judges_per_project=2, anchors_per_track=1)
    outcome = pr.solve_capturing(inputs.judges, inputs.projects, config)
    doc = pr.build_run_document(
        "r", outcome, inputs, config, generated_by="x", generated_at=NOW.isoformat()
    )
    row = pr.run_row(
        doc, generated_at=NOW, assignment_count=12, published_to_firestore=False
    )
    assert set(row) <= set(schema.RUNS.column_names)
    assert row["connectivity_ok"] is True
    assert row["assignment_count"] == 12
    assert row["published_to_firestore"] is False
    # the JSON columns must be readable back out again
    assert json.loads(row["coverage_json"])["nProjects"] == 9
    assert json.loads(row["tracks_json"])[0]["track"] == "AI/ML"


def test_no_assignment_means_no_assignment_rows(index):
    outcome = pr.SolveOutcome(assignment=None, error=RuntimeError("boom"))
    assert pr.assignment_rows("r", outcome, pr.SolverInputs(), NOW) == []


def test_run_ids_sort_chronologically():
    early = pr.new_run_id(dt.datetime(2026, 4, 19, 9, 0, 0, tzinfo=dt.timezone.utc))
    later = pr.new_run_id(dt.datetime(2026, 4, 19, 17, 0, 0, tzinfo=dt.timezone.utc))
    assert early < later
    assert later.endswith("Z")
