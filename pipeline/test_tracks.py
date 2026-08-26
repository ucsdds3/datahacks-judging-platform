import csv

import pytest

from pipeline.tracks import (
    CANONICAL,
    UnknownTrackError,
    normalize_track,
    normalize_tracks,
    split_concatenated,
)

REAL_PROJECTS = "src/assets/Final_project_info - Sheet1.csv"


@pytest.mark.parametrize(
    "devpost,expected",
    [
        ("Machine Learning & Bio-AI", "AI/ML"),
        ("Data Analytics", "Analytics"),
        ("Cloud Development", "Cloud"),
        ("Entrepreneurship & Product Management", "Entrepreneurship & Product"),
        ("UI/UX Design & Web Development", "UI/UX & Web Dev"),
        ("Hardware & IoT", "Hardware & IoT"),
        ("Mechanical Design & Biotechnology", "Mechanical Design & Biotechnology"),
        ("Economics", "Economics"),
    ],
)
def test_devpost_names_map_to_canonical(devpost, expected):
    assert normalize_track(devpost) == expected


@pytest.mark.parametrize("canon", CANONICAL)
def test_canonical_names_pass_through(canon):
    """Normalizing twice must be a no-op, so re-running an import is safe."""
    assert normalize_track(canon) == canon
    assert normalize_track(normalize_track(canon)) == canon


@pytest.mark.parametrize("messy", ["  ai/ml  ", "AI/ML", "ai/ML", "Data  Analytics"])
def test_whitespace_and_case_are_tolerated(messy):
    assert normalize_track(messy) in CANONICAL


def test_unknown_track_raises_rather_than_returning_none():
    """A silent None is what wrote track=null and lost four 2026 evaluations."""
    with pytest.raises(UnknownTrackError):
        normalize_track("Quantum Basketweaving")


def test_unknown_track_can_be_opted_out_of():
    assert normalize_track("Quantum Basketweaving", strict=False) is None


def test_row_21_concatenated_cell_is_recovered():
    """Final_project_info row 21 lost its quotes and ran two tracks together."""
    assert split_concatenated("Entrepreneurship & Product Management Hardware & IoT") == [
        "Entrepreneurship & Product",
        "Hardware & IoT",
    ]


def test_normalize_tracks_recovers_concatenation_end_to_end():
    got = normalize_tracks("Entrepreneurship & Product Management Hardware & IoT", "")
    assert got == ["Entrepreneurship & Product", "Hardware & IoT"]


def test_normalize_tracks_dedupes_and_drops_blanks():
    assert normalize_tracks("Data Analytics", "Analytics", "", None) == ["Analytics"]


def test_every_real_2026_project_resolves():
    """The whole point: no project may end up with zero tracks."""
    unresolved = []
    with open(REAL_PROJECTS, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    for row in rows:
        tracks = normalize_tracks(
            row.get("What's The First Track You'd Like To Submit To?"),
            row.get("What's The Second Track You'd Like To Submit To?"),
        )
        if not tracks:
            unresolved.append(row.get("Project Title"))
        assert all(t in CANONICAL for t in tracks)

    assert rows, "project list should not be empty"
    assert unresolved == []
