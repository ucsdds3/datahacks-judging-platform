"""End-to-end tests for the CLI: extract -> transform -> validate -> load."""

from __future__ import annotations

import json
import os

import pytest

from pipeline.etl import run

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
ASSETS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
    "assets",
)


def fixture(name: str) -> str:
    return os.path.join(FIXTURES, name)


def base_args(tmp_path, *extra):
    return [
        "--judge-roster", fixture("judge_roster.csv"),
        "--judge-credentials", fixture("judge_credentials.csv"),
        "--project-list", fixture("project_list.csv"),
        "--out", str(tmp_path),
        *extra,
    ]


def pipeline_on_fixtures(**kwargs):
    return run.run_pipeline(
        judge_roster_path=fixture("judge_roster.csv"),
        judge_credentials_path=fixture("judge_credentials.csv"),
        project_list_path=fixture("project_list.csv"),
        **kwargs,
    )


# --------------------------------------------------------------------------
# the pipeline itself
# --------------------------------------------------------------------------


def test_pipeline_produces_records_and_a_report():
    output = pipeline_on_fixtures()
    assert [p.id for p in output.projects] == sorted(p.id for p in output.projects)
    assert output.report.source_counts["project_list"] == 9
    assert output.report.output_counts["projects"] == len(output.projects)
    assert output.report.output_counts["judges"] == len(output.judges)


def test_pipeline_is_deterministic():
    first, second = pipeline_on_fixtures(), pipeline_on_fixtures()
    assert [p.to_document() for p in first.projects] == [
        p.to_document() for p in second.projects
    ]
    assert [j.to_document() for j in first.judges] == [
        j.to_document() for j in second.judges
    ]
    assert first.report.to_dict() == second.report.to_dict()


def test_report_accounts_for_every_rejected_record():
    report = pipeline_on_fixtures().report
    assert not report.ok
    assert sum(report.rejection_counts().values()) == len(report.rejections)
    for rejection in report.rejections:
        assert rejection.reason
        assert rejection.line_number > 0


def test_devpost_export_switches_projects_from_unverified_to_verified():
    without = pipeline_on_fixtures().report
    with_export = pipeline_on_fixtures(devpost_path=fixture("devpost_export.csv")).report

    total = without.output_counts["projects"]
    assert without.output_counts["projects_unverified"] == total
    assert with_export.output_counts["projects_unverified"] < total


def test_pipeline_raises_on_a_missing_required_column(tmp_path):
    broken = tmp_path / "broken.csv"
    broken.write_text("Name,Company\nAda,Analytical Engines\n", encoding="utf-8")
    with pytest.raises(run.extract.MissingColumnsError):
        run.run_pipeline(
            judge_roster_path=str(broken),
            judge_credentials_path=fixture("judge_credentials.csv"),
            project_list_path=fixture("project_list.csv"),
        )


def test_pipeline_raises_on_an_id_collision(tmp_path):
    clashing = tmp_path / "clash.csv"
    clashing.write_text(
        "Project Title,Table Number,tracks,"
        "What's The First Track You'd Like To Submit To?,"
        "What's The Second Track You'd Like To Submit To?\n"
        "CTRL + C,1,Economics,Economics,\n"
        "ctrl/c,2,Economics,Economics,\n",
        encoding="utf-8",
    )
    with pytest.raises(run.transform.IdCollisionError):
        run.run_pipeline(
            judge_roster_path=fixture("judge_roster.csv"),
            judge_credentials_path=fixture("judge_credentials.csv"),
            project_list_path=str(clashing),
        )


# --------------------------------------------------------------------------
# CLI behaviour
# --------------------------------------------------------------------------


def test_cli_defaults_to_dry_run_and_writes_nothing(tmp_path, capsys):
    code = run.main(base_args(tmp_path))
    out = capsys.readouterr().out

    assert "DRY RUN -- nothing was written" in out
    assert os.listdir(tmp_path) == []
    assert code == 1  # the fixtures contain deliberately-rejected rows


def test_cli_dry_run_exits_zero_when_there_is_nothing_wrong(tmp_path, capsys):
    good = tmp_path / "good.csv"
    good.write_text(
        "Project Title,Table Number,tracks,"
        "What's The First Track You'd Like To Submit To?,"
        "What's The Second Track You'd Like To Submit To?\n"
        "Ago,1,Economics,Economics,\n",
        encoding="utf-8",
    )
    code = run.main([
        "--judge-roster", fixture("judge_roster.csv"),
        "--judge-credentials", fixture("judge_credentials.csv"),
        "--project-list", str(good),
        "--out", str(tmp_path / "out"),
    ])
    assert code == 0


def test_cli_refuses_to_commit_while_records_are_rejected(tmp_path, capsys):
    code = run.main(base_args(tmp_path, "--commit"))
    captured = capsys.readouterr()
    assert code == 1
    assert "REFUSING TO LOAD" in captured.err
    assert not os.path.exists(tmp_path / "projects.csv")


def test_cli_commits_when_rejects_are_explicitly_allowed(tmp_path, capsys):
    code = run.main(base_args(tmp_path, "--commit", "--allow-rejects"))
    assert code == 0
    assert os.path.exists(tmp_path / "projects.csv")
    assert os.path.exists(tmp_path / "judges.csv")


def test_cli_is_idempotent_end_to_end(tmp_path, capsys):
    """Run the whole CLI twice. Same bytes out, and the second run's plan
    reports zero creates and zero updates.

    This is the property the four Node scripts lacked, and the reason
    production ended up with two generations of everything.
    """
    args = base_args(tmp_path, "--commit", "--allow-rejects")

    assert run.main(args) == 0
    first = {
        name: (tmp_path / name).read_bytes()
        for name in ("projects.csv", "judges.csv")
    }
    capsys.readouterr()

    assert run.main(args) == 0
    second_output = capsys.readouterr().out
    second = {
        name: (tmp_path / name).read_bytes()
        for name in ("projects.csv", "judges.csv")
    }

    assert first == second
    assert "projects: 0 create, 0 update" in second_output
    assert "judges: 0 create, 0 update" in second_output


def test_cli_writes_a_json_report(tmp_path):
    report_path = tmp_path / "reports" / "validation.json"
    run.main(base_args(tmp_path, "--report", str(report_path)))

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["sourceCounts"]["project_list"] == 9
    assert payload["rejectionCounts"]
    assert payload["plans"][0]["collection"] == "projects"
    assert payload["plans"][0]["dryRun"] is True

    # Deterministic: writing it again produces the same bytes.
    before = report_path.read_bytes()
    run.main(base_args(tmp_path, "--report", str(report_path)))
    assert report_path.read_bytes() == before


def test_cli_reports_a_missing_input_file_as_fatal(tmp_path, capsys):
    code = run.main([
        "--judge-roster", "/nope/missing.csv",
        "--judge-credentials", fixture("judge_credentials.csv"),
        "--project-list", fixture("project_list.csv"),
        "--out", str(tmp_path),
    ])
    assert code == 2
    assert "FATAL" in capsys.readouterr().err


def test_cli_reports_an_id_collision_as_fatal(tmp_path, capsys):
    clashing = tmp_path / "clash.csv"
    clashing.write_text(
        "Project Title,Table Number,tracks,"
        "What's The First Track You'd Like To Submit To?,"
        "What's The Second Track You'd Like To Submit To?\n"
        "CTRL + C,1,Economics,Economics,\n"
        "ctrl/c,2,Economics,Economics,\n",
        encoding="utf-8",
    )
    code = run.main([
        "--judge-roster", fixture("judge_roster.csv"),
        "--judge-credentials", fixture("judge_credentials.csv"),
        "--project-list", str(clashing),
        "--out", str(tmp_path),
    ])
    assert code == 2
    assert "claimed by two different records" in capsys.readouterr().err


def test_firestore_backend_is_dry_run_unless_commit_is_passed():
    parser = run.build_parser()
    assert parser.parse_args([]).commit is False
    assert parser.parse_args(["--dry-run"]).commit is False
    assert parser.parse_args(["--commit"]).commit is True


def test_dry_run_and_commit_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        run.build_parser().parse_args(["--dry-run", "--commit"])


# --------------------------------------------------------------------------
# the real CSVs, if they are present
# --------------------------------------------------------------------------

REAL_INPUTS = {
    "judge_roster_path": os.path.join(
        ASSETS, "Mentors_Judges Calendar & Sign-Up - 4_19 Judge.csv"
    ),
    "judge_credentials_path": os.path.join(ASSETS, "password_judges - Checked-In.csv"),
    "project_list_path": os.path.join(ASSETS, "Final_project_info - Sheet1.csv"),
}


@pytest.mark.skipif(
    not all(os.path.exists(p) for p in REAL_INPUTS.values()),
    reason="src/assets CSVs not present",
)
def test_real_csvs_load_without_rejections():
    """The production data must go through cleanly. If this ever fails, the
    export changed and someone needs to look at it before the event."""
    output = run.run_pipeline(**REAL_INPUTS)
    assert output.report.rejections == []
    assert len(output.projects) == 160
    assert len(output.judges) == 111
    # The row-21 repair actually fired on the real file.
    assert any(n.kind == "repaired_row" for n in output.report.notes)
    plant = [p for p in output.projects if p.id == "ai-plant-companion"][0]
    assert plant.tracks == ("Entrepreneurship & Product", "Hardware & IoT")


@pytest.mark.skipif(
    not all(os.path.exists(p) for p in REAL_INPUTS.values()),
    reason="src/assets CSVs not present",
)
def test_real_judge_ids_are_all_usernames_not_legacy_slugs():
    output = run.run_pipeline(**REAL_INPUTS)
    legacy = [j.id for j in output.judges if "-" in j.id and j.id == j.id.lower()]
    # Only the deliberate "judge-<hash>" fallback may look like this, and there
    # should be none of those in the real data -- every judge has a username.
    assert legacy == []
