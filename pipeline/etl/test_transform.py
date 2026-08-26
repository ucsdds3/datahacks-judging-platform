"""Tests for the pure transforms: IDs, validation, provenance."""

from __future__ import annotations

import os

import pytest

from pipeline.etl import extract, transform
from pipeline.tracks import UnknownTrackError

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name: str) -> str:
    return os.path.join(FIXTURES, name)


def row(source: str, line: int, **values) -> extract.SourceRow:
    """Build a SourceRow by hand -- transforms never need a file."""
    return extract.SourceRow(source=source, line_number=line, values=dict(values))


def project_row(line: int, title: str, table: str, *, tracks="", first="", second=""):
    return row(
        "project_list",
        line,
        **{
            extract.PROJECT_TITLE_COLUMN: title,
            extract.PROJECT_TABLE_COLUMN: table,
            extract.PROJECT_TRACKS_COLUMN: tracks,
            extract.PROJECT_FIRST_TRACK_COLUMN: first,
            extract.PROJECT_SECOND_TRACK_COLUMN: second,
        },
    )


# --------------------------------------------------------------------------
# ID scheme
# --------------------------------------------------------------------------


def test_project_id_is_a_slug():
    assert transform.project_id("AI Plant Companion") == "ai-plant-companion"
    assert transform.project_id("CTRL + C") == "ctrl-c"
    assert transform.project_id("  Ago  ") == "ago"


def test_project_id_is_stable_across_calls():
    assert transform.project_id("Data Miners") == transform.project_id("Data Miners")


def test_project_id_falls_back_to_a_hash_for_unsluggable_titles():
    """The 2026 event had a project literally titled "ㄖ", which slugs to ""."""
    doc_id = transform.project_id("ㄖ")
    assert doc_id.startswith("project-")
    assert doc_id == transform.project_id("ㄖ")  # deterministic
    assert doc_id != transform.project_id("〇")  # and distinct


def test_project_id_refuses_a_blank_title():
    with pytest.raises(ValueError):
        transform.project_id("   ")


def test_judge_id_prefers_the_username():
    assert transform.judge_id("AarushiBajaj", "Aarushi Bajaj") == "AarushiBajaj"


def test_judge_id_falls_back_to_a_flagged_hash_without_a_username():
    doc_id = transform.judge_id("", "Radia Perlman")
    assert doc_id.startswith("judge-")
    # Case and spacing must not change the ID.
    assert doc_id == transform.judge_id("", "  radia   perlman ")


def test_judge_id_is_never_a_legacy_slug():
    """Neither of the two old schemes must be reachable from here: those are
    what left 210 judge docs in three formats in production."""
    doc_id = transform.judge_id("", "Anuj Jain")
    assert doc_id != "anuj-jain"
    assert doc_id != "anuj-jain-gmail-com"


def test_judge_id_refuses_a_blank_everything():
    with pytest.raises(ValueError):
        transform.judge_id("", "  ")


# --------------------------------------------------------------------------
# ID collisions
# --------------------------------------------------------------------------


def test_registry_raises_on_collision_between_different_records():
    registry = transform.IdRegistry("project")
    registry.claim("ctrl-c", "project_list line 2 'CTRL + C'")
    with pytest.raises(transform.IdCollisionError) as exc:
        registry.claim("ctrl-c", "project_list line 9 'ctrl/c'")

    message = str(exc.value)
    assert "ctrl-c" in message
    assert "line 2" in message and "line 9" in message
    assert "overwrite" in message


def test_registry_allows_the_same_owner_to_reclaim():
    registry = transform.IdRegistry("project")
    registry.claim("ago", "project_list line 2 'Ago'")
    assert registry.claim("ago", "project_list line 2 'Ago'") == "ago"


def test_two_titles_that_slug_the_same_raise_rather_than_overwrite():
    """"CTRL + C" and "ctrl/c" are different projects with the same slug.
    Writing both would silently lose one -- exactly the 2026 failure."""
    rows = [
        project_row(2, "CTRL + C", "1", first="Economics"),
        project_row(3, "ctrl/c", "2", first="Economics"),
    ]
    with pytest.raises(transform.IdCollisionError):
        transform.build_projects(rows)


# --------------------------------------------------------------------------
# track handling
# --------------------------------------------------------------------------


def test_devpost_track_names_are_normalized_via_pipeline_tracks():
    result = transform.build_projects(
        [project_row(2, "Ago", "1", first="Data Analytics", second="Cloud Development")]
    )
    assert result.records[0].tracks == ("Analytics", "Cloud")


def test_concatenated_tracks_from_the_repaired_row_are_recovered():
    """After extract repairs line 21, the first-track cell still holds two
    run-together names. pipeline.tracks.split_concatenated pulls them apart."""
    result = transform.build_projects(
        [
            project_row(
                21,
                "AI Plant Companion",
                "20",
                tracks="Entrepreneurship & Product Management, Hardware & IoT",
                first="Entrepreneurship & Product Management Hardware & IoT",
            )
        ]
    )
    assert result.records[0].tracks == (
        "Entrepreneurship & Product",
        "Hardware & IoT",
    )


def test_unknown_track_is_rejected_not_nulled():
    """A None track is what lost four evaluations in 2026."""
    result = transform.build_projects(
        [project_row(2, "Tidal Wave", "1", first="Quantum Vibes")]
    )
    assert result.records == []
    assert [r.reason for r in result.rejections] == ["unknown_track"]
    assert "Quantum Vibes" in result.rejections[0].detail


def test_classify_track_separates_sponsor_challenges_from_real_tracks():
    assert transform.classify_track("AI/ML") == ("track", "AI/ML")
    assert transform.classify_track("Data Analytics") == ("track", "Analytics")
    assert transform.classify_track("databricks challenge") == (
        "sponsor",
        "DataBricks Challenge",
    )
    assert transform.classify_track("  ") == ("empty", None)


def test_classify_track_raises_on_anything_unrecognised():
    with pytest.raises(UnknownTrackError):
        transform.classify_track("Blockchain Vibes")


# --------------------------------------------------------------------------
# table numbers and other validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "  ", "abc", "0", "-3", "1e3", "12.5", "99999"])
def test_bad_table_numbers_raise(bad):
    with pytest.raises(ValueError):
        transform.parse_table_number(bad)


def test_good_table_numbers_parse():
    assert transform.parse_table_number(" 101 ") == 101


def test_project_validation_rejects_everything_it_should():
    rows = extract.read_project_list(fixture("project_list.csv"))
    result = transform.build_projects(rows)

    reasons = sorted(r.reason for r in result.rejections)
    assert reasons == [
        "bad_table_number",
        "duplicate_table_number",
        "duplicate_title",
        "missing_title",
        "no_tracks",
    ]
    # Nothing was dropped without a trace: every input row is accounted for.
    assert result.rows_out + len(result.rejections) == result.rows_in


def test_valid_projects_survive_and_are_sorted_by_id():
    rows = extract.read_project_list(fixture("project_list.csv"))
    result = transform.build_projects(rows)
    ids = [p.id for p in result.records]
    assert ids == sorted(ids)
    assert "ai-plant-companion" in ids
    assert any(i.startswith("project-") for i in ids)  # the "ㄖ" project


def test_repaired_rows_are_reported_as_notes():
    rows = extract.read_project_list(fixture("project_list.csv"))
    result = transform.build_projects(rows)
    kinds = [n.kind for n in result.notes]
    assert kinds.count("repaired_row") == 1


def test_looks_like_email():
    assert transform.looks_like_email("ada@example.com")
    assert not transform.looks_like_email("Puligundla")
    assert not transform.looks_like_email("?")
    assert not transform.looks_like_email("a@b")
    assert not transform.looks_like_email("a@@b.com")
    assert not transform.looks_like_email("")


# --------------------------------------------------------------------------
# synthetic-vs-real classification
# --------------------------------------------------------------------------


def build_fixture_projects():
    rows = extract.read_project_list(fixture("project_list.csv"))
    return transform.build_projects(rows).records


def test_projects_missing_from_devpost_are_flagged_never_deleted():
    projects = build_fixture_projects()
    devpost = extract.read_devpost_export(fixture("devpost_export.csv"))

    classified, notes = transform.classify_provenance(projects, devpost)

    # Nothing was removed.
    assert len(classified) == len(projects)

    by_id = {p.id: p for p in classified}
    assert by_id["ago"].provenance == "devpost"
    assert by_id["ago"].needs_review is False

    assert by_id["solo-track"].provenance == "unverified"
    assert by_id["solo-track"].needs_review is True
    assert "synthetic" in by_id["solo-track"].review_reason

    flagged = {n.identity for n in notes if n.kind == "unverified_project"}
    assert "Solo Track" in flagged


def test_devpost_enrichment_is_attached_to_matched_projects():
    projects = build_fixture_projects()
    devpost = extract.read_devpost_export(fixture("devpost_export.csv"))
    by_id = {p.id: p for p in transform.classify_provenance(projects, devpost)[0]}

    ago = by_id["ago"]
    assert ago.submission_url == "https://devpost.test/ago"
    assert ago.built_with == "python,pandas"
    assert ago.description == "A real submission."
    assert ago.github_url == "https://github.test/ago"
    # Coalesced out of the *second* "Video Demo Link" column.
    assert ago.video_url == "https://youtu.be/ago"
    assert ago.sponsor_challenges == ("Best Overall", "Best Design")


def test_devpost_placeholder_dash_is_not_a_sponsor_challenge():
    projects = build_fixture_projects()
    devpost = extract.read_devpost_export(fixture("devpost_export.csv"))
    by_id = {p.id: p for p in transform.classify_provenance(projects, devpost)[0]}
    assert by_id["ai-plant-companion"].sponsor_challenges == ()


def test_devpost_only_submissions_are_reported():
    projects = build_fixture_projects()
    devpost = extract.read_devpost_export(fixture("devpost_export.csv"))
    _, notes = transform.classify_provenance(projects, devpost)
    ghosts = [n for n in notes if n.kind == "devpost_only_submission"]
    assert [n.identity for n in ghosts] == ["Ghost Submission"]


def test_no_devpost_export_marks_everything_unverified():
    """Without the real export we must not pretend the projects are verified."""
    projects = build_fixture_projects()
    classified, notes = transform.classify_provenance(projects, None)

    assert all(p.provenance == "unverified_no_export" for p in classified)
    assert all(p.needs_review for p in classified)
    assert [n.kind for n in notes] == ["no_devpost_export"]


def test_provenance_matching_ignores_case_and_spacing():
    projects = build_fixture_projects()
    devpost = extract.read_rows(
        [["Project Title"], ["  aGO  "]], source="devpost_export",
    )
    by_id = {p.id: p for p in transform.classify_provenance(projects, devpost)[0]}
    assert by_id["ago"].provenance == "devpost"


# --------------------------------------------------------------------------
# judges
# --------------------------------------------------------------------------


def build_fixture_judges():
    creds = extract.read_judge_credentials(fixture("judge_credentials.csv"))
    roster = extract.read_judge_roster(fixture("judge_roster.csv"))
    return transform.build_judges(creds, roster)


def test_judges_merge_credentials_with_the_roster():
    result = build_fixture_judges()
    by_id = {j.id: j for j in result.records}

    ada = by_id["AdaLovelace"]
    assert ada.name == "Ada Lovelace"
    assert ada.email == "ada@example.com"
    assert ada.track == "AI/ML"
    assert ada.company == "Analytical Engines"  # from the roster
    assert ada.role == "Principal Engineer"
    assert ada.checked_in is True
    assert ada.needs_review is False


def test_the_comma_name_judge_keeps_their_own_credentials():
    by_id = {j.id: j for j in build_fixture_judges().records}
    wei = by_id["WeiChen"]
    assert wei.name == "Chen, Jr., Wei"
    assert wei.email == "wei.chen@example.com"
    assert wei.company == "Comma Corp"
    assert wei.track == "Analytics"


def test_passwords_never_reach_a_document():
    """judges/* is read wholesale by the leaderboard page. Passwords belong in
    Firebase Auth, not in a document."""
    for judge in build_fixture_judges().records:
        document = judge.to_document()
        assert "password" not in {k.lower() for k in document}
        assert "1111" not in str(document)


def test_malformed_and_missing_emails_are_flagged_not_dropped():
    result = build_fixture_judges()
    by_id = {j.id: j for j in result.records}

    turing = by_id["AlanTuring"]  # Email column holds "Bletchley"
    assert turing.needs_review is True
    assert "not a valid address" in turing.review_reason

    katherine = by_id["KatherineJohnson"]
    assert katherine.needs_review is True
    assert "no email" in katherine.review_reason

    kinds = [n.kind for n in result.notes]
    assert kinds.count("malformed_email") == 1
    assert kinds.count("no_email") == 1


def test_emails_are_lowercased_because_the_app_looks_judges_up_by_email():
    by_id = {j.id: j for j in build_fixture_judges().records}
    assert by_id["NotOnRoster"].email == "not.on.roster@example.com"


def test_judge_without_a_username_gets_a_flagged_fallback_id():
    result = build_fixture_judges()
    radia = [j for j in result.records if j.name == "Radia Perlman"][0]
    assert radia.id.startswith("judge-")
    assert radia.needs_review is True
    assert "cannot log in" in radia.review_reason


def test_sponsor_challenge_judges_are_separated_from_track_judges():
    result = build_fixture_judges()
    sponsor = [j for j in result.records if j.name == "Sponsor Judge"][0]
    assert sponsor.track is None
    assert sponsor.sponsor_challenge == "DataBricks Challenge"
    assert sponsor.needs_review is True
    assert "room assignment" in sponsor.review_reason


def test_judges_only_on_one_sheet_are_reported():
    result = build_fixture_judges()
    kinds = {n.kind: n for n in result.notes}
    assert kinds["not_on_roster"].identity == "Not On Roster"
    assert kinds["roster_only_judge"].identity == "Never Checked In"


def test_filler_rows_are_noted_not_rejected():
    """The real check-in sheet ends with seven ",,FALSE,,,," rows. There is no
    record there to lose, so they must not block every future --commit."""
    result = build_fixture_judges()
    assert result.rejections == []
    assert [n.kind for n in result.notes].count("empty_row") == 1


def test_a_blank_name_next_to_a_real_email_IS_rejected():
    rows = [
        row(
            "judge_credentials", 5,
            Name="", Username="SomeoneReal", Email="real@example.com",
            Tracks="AI/ML", **{"Checked-In": "TRUE"},
        )
    ]
    result = transform.build_judges(rows, [])
    assert [r.reason for r in result.rejections] == ["missing_name"]
    assert result.records == []


def test_duplicate_judge_names_are_rejected():
    rows = [
        row("judge_credentials", 2, Name="Ada Lovelace", Username="A1",
            Email="a@x.com", Tracks="AI/ML", **{"Checked-In": "TRUE"}),
        row("judge_credentials", 3, Name="ada  lovelace", Username="A2",
            Email="b@x.com", Tracks="AI/ML", **{"Checked-In": "TRUE"}),
    ]
    result = transform.build_judges(rows, [])
    assert len(result.records) == 1
    assert [r.reason for r in result.rejections] == ["duplicate_judge"]


def test_judge_with_no_track_is_rejected():
    rows = [
        row("judge_credentials", 2, Name="Trackless", Username="T1",
            Email="t@x.com", Tracks="", **{"Checked-In": "TRUE"}),
    ]
    result = transform.build_judges(rows, [])
    assert [r.reason for r in result.rejections] == ["no_track"]


def test_track_disagreement_between_the_two_sheets_is_reported():
    creds = [
        row("judge_credentials", 2, Name="Ada Lovelace", Username="AdaLovelace",
            Email="a@x.com", Tracks="AI/ML", **{"Checked-In": "TRUE"}),
    ]
    roster = [row("judge_roster", 2, Name="Ada Lovelace", Tracks="Economics")]
    result = transform.build_judges(creds, roster)

    disagreements = [n for n in result.notes if n.kind == "track_disagreement"]
    assert len(disagreements) == 1
    # The credentials sheet wins.
    assert result.records[0].track == "AI/ML"


def test_judge_records_are_sorted_by_id():
    ids = [j.id for j in build_fixture_judges().records]
    assert ids == sorted(ids)


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------


def test_transforms_are_deterministic():
    """Same input, same output -- byte for byte. Everything else rests on this."""
    rows = extract.read_project_list(fixture("project_list.csv"))
    first = transform.build_projects(rows)
    second = transform.build_projects(rows)

    assert [p.to_document() for p in first.records] == [
        p.to_document() for p in second.records
    ]
    assert [r.render() for r in first.rejections] == [
        r.render() for r in second.rejections
    ]


def test_input_row_order_does_not_change_the_output_order():
    rows = extract.read_project_list(fixture("project_list.csv"))
    forward = transform.build_projects(rows).records
    backward = transform.build_projects(list(reversed(rows))).records
    assert {p.id for p in forward} == {p.id for p in backward}
    assert [p.id for p in backward] == sorted(p.id for p in backward)


def test_documents_never_carry_assignment_fields():
    """assignedProjects / assignedJudges belong to the solver. If the ETL wrote
    them, re-running it would wipe the assignment that already shipped."""
    for judge in build_fixture_judges().records:
        assert "assignedProjects" not in judge.to_document()
    for project in build_fixture_projects():
        assert "assignedJudges" not in project.to_document()
