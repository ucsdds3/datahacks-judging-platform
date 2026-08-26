"""
Tests for the assignment solver.

Run from the repo root:

    pytest pipeline/assignment/test_solver.py -v

The tests that matter most are the connectivity ones. Everything else is
comfort; a disconnected assignment is the bug that broke DataHacks 2026.
"""

from __future__ import annotations

import itertools

import networkx as nx
import pytest

from pipeline.assignment import validate
from pipeline.assignment.solver import (
    GROUP,
    INDIVIDUAL,
    DisconnectedAssignmentError,
    EmptyTrackError,
    Judge,
    Project,
    SolverConfig,
    solve,
)


# ---------------------------------------------------------------------------
# fixtures / builders
# ---------------------------------------------------------------------------


def make_judges(track: str, n: int, prefix: str = "j", **kwargs) -> list[dict]:
    return [
        {
            "id": f"{prefix}{i}",
            "name": f"Judge {prefix}{i}",
            "track": track,
            **kwargs,
        }
        for i in range(1, n + 1)
    ]


def make_projects(track: str, n: int, prefix: str = "p", start_table: int = 1) -> list[dict]:
    return [
        {
            "id": f"{prefix}{i}",
            "name": f"Project {prefix}{i}",
            "tracks": [track],
            "table_number": start_table + i - 1,
        }
        for i in range(1, n + 1)
    ]


@pytest.fixture
def simple():
    """One track, 8 individual judges, 24 projects."""
    return make_judges("AI/ML", 8), make_projects("AI/ML", 24)


# ---------------------------------------------------------------------------
# 1. the connectivity gate
# ---------------------------------------------------------------------------


def test_connectivity_gate_rejects_a_disconnected_assignment():
    """The validator must catch the exact 2026 failure: islanded judges.

    This mirrors the old `assignGroups`: judges sliced into disjoint teams,
    each team on its own block of projects, nothing shared.
    """
    judge_projects = {
        "a1": ["p1", "p2"],
        "a2": ["p1", "p2"],   # team 1
        "b1": ["p3", "p4"],
        "b2": ["p3", "p4"],   # team 2 -- shares nothing with team 1
    }
    judge_tracks = {j: "AI/ML" for j in judge_projects}

    report = validate.check_connectivity(judge_projects, judge_tracks)
    assert report["ok"] is False
    assert report["failures"] == ["AI/ML"]
    assert report["tracks"]["AI/ML"]["n_components"] == 2

    message = validate.connectivity_error_message(report)
    assert "AI/ML" in message
    assert "2 islands" in message

    with pytest.raises(DisconnectedAssignmentError):
        validate.raise_if_broken({"connectivity": report})


def test_connectivity_gate_flags_a_judge_with_no_projects():
    judge_projects = {"a1": ["p1"], "a2": ["p1"], "a3": []}
    judge_tracks = {j: "Cloud" for j in judge_projects}

    report = validate.check_connectivity(judge_projects, judge_tracks)
    assert report["ok"] is False
    assert report["tracks"]["Cloud"]["isolated_judges"] == ["a3"]
    assert "a3" in validate.connectivity_error_message(report)


def test_solver_output_is_always_connected(simple):
    judges, projects = simple
    result = solve(judges, projects, SolverConfig(min_judges_per_project=3, anchors_per_track=3))

    report = validate.check_connectivity(result.judge_projects, result.judge_tracks)
    assert report["ok"] is True
    assert report["tracks"]["AI/ML"]["n_components"] == 1

    graph = result.track_graph("AI/ML")
    assert nx.is_connected(graph)


def test_solver_stays_connected_across_many_shapes():
    """Fuzz-ish sweep: the gate must hold for every plausible event size."""
    for n_judges, n_projects, min_j, anchors in itertools.product(
        (1, 2, 3, 5, 11, 21), (1, 2, 7, 40, 86), (1, 2, 3, 4), (1, 2, 3)
    ):
        judges = make_judges("AI/ML", n_judges)
        projects = make_projects("AI/ML", n_projects)
        result = solve(
            judges,
            projects,
            SolverConfig(min_judges_per_project=min_j, anchors_per_track=anchors),
        )
        assert nx.is_connected(result.track_graph("AI/ML")), (
            n_judges, n_projects, min_j, anchors
        )


def test_multi_track_event_is_connected_per_track():
    judges = (
        make_judges("AI/ML", 21, "ml")
        + make_judges("Analytics", 7, "an")
        + make_judges("Cloud", 3, "cl")
        + make_judges("Economics", 1, "ec")
    )
    projects = []
    for i in range(1, 61):
        # every project is dual-track, as in the real 2026 data
        pair = [
            ("AI/ML", "Analytics"),
            ("Analytics", "Cloud"),
            ("Cloud", "Economics"),
            ("Economics", "AI/ML"),
        ][i % 4]
        projects.append(
            {"id": f"p{i}", "name": f"P{i}", "tracks": list(pair), "table_number": i}
        )

    result = solve(judges, projects)
    for track in {j["track"] for j in judges}:
        assert nx.is_connected(result.track_graph(track)), track
    assert result.preflight()["connectivity"]["ok"]


# ---------------------------------------------------------------------------
# 2. coverage target
# ---------------------------------------------------------------------------


def test_min_judges_target_is_met(simple):
    judges, projects = simple
    result = solve(judges, projects, SolverConfig(min_judges_per_project=3))

    counts = {pid: len(js) for pid, js in result.project_judges.items()}
    assert min(counts.values()) >= 3
    assert validate.projects_below_target(result.judge_projects, 3) == []


@pytest.mark.parametrize("target", [1, 2, 3, 4, 5])
def test_min_judges_target_is_configurable(target):
    judges = make_judges("AI/ML", 10)
    projects = make_projects("AI/ML", 30)
    result = solve(judges, projects, SolverConfig(min_judges_per_project=target))
    assert min(len(js) for js in result.project_judges.values()) >= target


def test_undersized_track_warns_instead_of_lying():
    """2 judges cannot give any project 3 judges. Say so, do not pretend."""
    judges = make_judges("Hardware & IoT", 2)
    projects = make_projects("Hardware & IoT", 6)
    result = solve(judges, projects, SolverConfig(min_judges_per_project=3))

    assert min(len(js) for js in result.project_judges.values()) == 2
    assert any("only 2 judge(s)" in w for w in result.warnings)
    assert sum("target 3" in w for w in result.warnings) == 6  # one per project
    # still connected, which is what actually matters
    assert nx.is_connected(result.track_graph("Hardware & IoT"))


def test_lone_judge_guests_on_projects_owned_by_a_bigger_track():
    """2026's Economics track had exactly ONE judge.

    Left alone she would solo-score a handful of projects: unfair to those
    teams and useless for normalisation. She should instead be added as an
    extra judge on dual-listed projects owned by a track that can carry them.
    """
    judges = make_judges("Analytics", 8, "an") + make_judges("Economics", 1, "ec")
    projects = [
        {"id": f"p{i}", "name": f"P{i}", "tracks": ["Analytics", "Economics"],
         "table_number": i}
        for i in range(1, 41)
    ]
    result = solve(judges, projects, SolverConfig(min_judges_per_project=3))

    lone = result.judge_projects["ec1"]
    assert len(lone) >= 5, "the lone judge should get a normal-ish workload"
    # nothing she touches is below target, because Analytics owns all of it
    for pid in lone:
        assert len(result.project_judges[pid]) >= 4
    assert min(len(js) for js in result.project_judges.values()) >= 3
    assert all(t == "Analytics" for t in result.project_owner_track.values())
    assert any("Economics" in w and "extra judges" in w for w in result.warnings)
    assert nx.is_connected(result.track_graph("Economics"))


def test_top_up_can_be_disabled():
    judges = make_judges("Analytics", 8, "an") + make_judges("Economics", 1, "ec")
    projects = [
        {"id": f"p{i}", "name": f"P{i}", "tracks": ["Economics", "Analytics"],
         "table_number": i}
        for i in range(1, 41)
    ]
    off = solve(judges, projects, SolverConfig(top_up_undersized_tracks=False))
    # with the top-up off the lone judge only gets her track's anchors
    assert len(off.judge_projects["ec1"]) < len(
        solve(judges, projects).judge_projects["ec1"]
    )


def test_project_stuck_below_target_is_named_in_the_warnings():
    """A single-track project in a one-judge track cannot be fixed. Say which."""
    judges = make_judges("Analytics", 8, "an") + make_judges("Economics", 1, "ec")
    projects = [
        {"id": "solo_econ", "name": "Buterin", "tracks": ["Economics"], "table_number": 1}
    ] + [
        {"id": f"p{i}", "name": f"P{i}", "tracks": ["Analytics", "Economics"],
         "table_number": i}
        for i in range(2, 30)
    ]
    result = solve(judges, projects, SolverConfig(min_judges_per_project=3))

    assert len(result.project_judges["solo_econ"]) == 1
    assert any("solo_econ" in w and "target 3" in w for w in result.warnings)


def test_every_project_is_judged_by_someone():
    judges = make_judges("AI/ML", 6)
    projects = make_projects("AI/ML", 50)
    result = solve(judges, projects)
    assert all(js for js in result.project_judges.values())


# ---------------------------------------------------------------------------
# 3. group mode
# ---------------------------------------------------------------------------


def test_group_members_get_identical_sets():
    judges = [
        {"id": "g1a", "name": "A", "track": "AI/ML", "mode": GROUP, "group_id": "G1"},
        {"id": "g1b", "name": "B", "track": "AI/ML", "mode": GROUP, "group_id": "G1"},
        {"id": "g1c", "name": "C", "track": "AI/ML", "mode": GROUP, "group_id": "G1"},
        {"id": "g2a", "name": "D", "track": "AI/ML", "mode": GROUP, "group_id": "G2"},
        {"id": "g2b", "name": "E", "track": "AI/ML", "mode": GROUP, "group_id": "G2"},
        {"id": "g2c", "name": "F", "track": "AI/ML", "mode": GROUP, "group_id": "G2"},
    ]
    projects = make_projects("AI/ML", 20)
    result = solve(judges, projects, SolverConfig(min_judges_per_project=3))

    assert result.judge_projects["g1a"] == result.judge_projects["g1b"]
    assert result.judge_projects["g1b"] == result.judge_projects["g1c"]
    assert result.judge_projects["g2a"] == result.judge_projects["g2b"] == result.judge_projects["g2c"]
    # different groups must NOT be identical, otherwise there is no coverage gain
    assert set(result.judge_projects["g1a"]) != set(result.judge_projects["g2a"])
    assert nx.is_connected(result.track_graph("AI/ML"))


def test_a_group_of_three_satisfies_the_target_alone():
    """A group of 3 delivers 3 judges in one visit -- no second unit needed."""
    judges = [
        {"id": f"g{i}", "name": f"G{i}", "track": "Cloud", "mode": GROUP, "group_id": "G1"}
        for i in range(3)
    ] + [
        {"id": f"h{i}", "name": f"H{i}", "track": "Cloud", "mode": GROUP, "group_id": "G2"}
        for i in range(3)
    ]
    projects = make_projects("Cloud", 12)
    result = solve(judges, projects, SolverConfig(min_judges_per_project=3, anchors_per_track=2))

    non_anchor = [p for p in result.project_judges if p not in result.anchors["Cloud"]]
    for pid in non_anchor:
        assert len(result.project_judges[pid]) == 3  # exactly one group, not two


def test_mixed_group_and_individual_judges():
    judges = (
        [
            {"id": "g1a", "name": "A", "track": "AI/ML", "mode": GROUP, "group_id": "G1"},
            {"id": "g1b", "name": "B", "track": "AI/ML", "mode": GROUP, "group_id": "G1"},
        ]
        + make_judges("AI/ML", 5, "solo")
    )
    projects = make_projects("AI/ML", 25)
    result = solve(judges, projects)

    assert result.judge_projects["g1a"] == result.judge_projects["g1b"]
    assert nx.is_connected(result.track_graph("AI/ML"))
    assert min(len(js) for js in result.project_judges.values()) >= 3


def test_group_spanning_two_tracks_is_rejected():
    judges = [
        {"id": "a", "name": "A", "track": "AI/ML", "mode": GROUP, "group_id": "G1"},
        {"id": "b", "name": "B", "track": "Cloud", "mode": GROUP, "group_id": "G1"},
    ]
    with pytest.raises(ValueError, match="spans multiple tracks"):
        solve(judges, make_projects("AI/ML", 5) + make_projects("Cloud", 5, "c"))


# ---------------------------------------------------------------------------
# 4. individual mode
# ---------------------------------------------------------------------------


def test_individuals_get_distinct_sets(simple):
    judges, projects = simple
    result = solve(judges, projects, SolverConfig(min_judges_per_project=3))

    sets = [frozenset(result.judge_projects[j["id"]]) for j in judges]
    assert len(set(sets)) == len(sets), "individual judges must not share a project list"


def test_individuals_overlap_but_are_not_identical(simple):
    judges, projects = simple
    result = solve(judges, projects)

    ids = [j["id"] for j in judges]
    overlapping = 0
    for a, b in itertools.combinations(ids, 2):
        sa = set(result.judge_projects[a])
        sb = set(result.judge_projects[b])
        assert sa != sb
        if sa & sb:
            overlapping += 1
    # anchors alone guarantee 100% pairwise overlap inside a track
    assert overlapping == len(ids) * (len(ids) - 1) // 2


def test_affinity_biases_toward_overlap_without_merging_sets():
    """Two friends should walk together far more than strangers do."""
    judges = [
        {"id": "f1", "name": "F1", "track": "AI/ML", "mode": INDIVIDUAL, "affinity": ["f2"]},
        {"id": "f2", "name": "F2", "track": "AI/ML", "mode": INDIVIDUAL, "affinity": ["f1"]},
    ] + make_judges("AI/ML", 6, "s")
    projects = make_projects("AI/ML", 30)
    result = solve(judges, projects, SolverConfig(min_judges_per_project=3, affinity_weight=3.0))

    friends = set(result.judge_projects["f1"]) & set(result.judge_projects["f2"])
    strangers = set(result.judge_projects["s1"]) & set(result.judge_projects["s2"])
    assert len(friends) > len(strangers)
    # ...but still independent judges with their own lists
    assert set(result.judge_projects["f1"]) != set(result.judge_projects["f2"])


def test_affinity_off_by_weight_zero():
    judges = [
        {"id": "f1", "name": "F1", "track": "AI/ML", "affinity": ["f2"]},
        {"id": "f2", "name": "F2", "track": "AI/ML", "affinity": ["f1"]},
    ] + make_judges("AI/ML", 6, "s")
    projects = make_projects("AI/ML", 30)
    result = solve(judges, projects, SolverConfig(affinity_weight=0.0))
    assert nx.is_connected(result.track_graph("AI/ML"))


# ---------------------------------------------------------------------------
# 5. anchors
# ---------------------------------------------------------------------------


def test_anchors_are_shared_by_every_judge_in_the_track(simple):
    judges, projects = simple
    result = solve(judges, projects, SolverConfig(anchors_per_track=3))

    anchors = result.anchors["AI/ML"]
    assert len(anchors) == 3
    for judge in judges:
        assigned = set(result.judge_projects[judge["id"]])
        assert set(anchors) <= assigned, f"{judge['id']} is missing an anchor"

    for anchor in anchors:
        assert len(result.project_judges[anchor]) == len(judges)


def test_anchors_are_spread_across_the_room_not_clustered():
    judges = make_judges("AI/ML", 5)
    projects = make_projects("AI/ML", 60)
    result = solve(judges, projects, SolverConfig(anchors_per_track=3))

    tables = sorted(
        p["table_number"] for p in projects if p["id"] in result.anchors["AI/ML"]
    )
    assert len(set(tables)) == 3
    gaps = [b - a for a, b in zip(tables, tables[1:])]
    assert min(gaps) >= 10, f"anchors clustered at tables {tables}"


def test_anchor_count_is_configurable():
    judges = make_judges("AI/ML", 6)
    projects = make_projects("AI/ML", 40)
    for n in (1, 2, 5):
        result = solve(judges, projects, SolverConfig(anchors_per_track=n))
        assert len(result.anchors["AI/ML"]) == n


def test_zero_anchors_is_forced_up_to_one_to_keep_the_graph_connected():
    judges = make_judges("AI/ML", 6)
    projects = make_projects("AI/ML", 40)
    result = solve(judges, projects, SolverConfig(anchors_per_track=0))
    assert len(result.anchors["AI/ML"]) == 1
    assert any("connectivity gate" in w for w in result.warnings)
    assert nx.is_connected(result.track_graph("AI/ML"))


def test_anchors_capped_by_available_projects():
    judges = make_judges("Economics", 3)
    projects = make_projects("Economics", 2)
    result = solve(judges, projects, SolverConfig(anchors_per_track=5))
    assert len(result.anchors["Economics"]) == 2


# ---------------------------------------------------------------------------
# 6. tracks, balance, misc
# ---------------------------------------------------------------------------


def test_judges_only_get_projects_in_their_track():
    judges = make_judges("AI/ML", 4, "ml") + make_judges("Cloud", 4, "cl")
    projects = make_projects("AI/ML", 12, "m") + make_projects("Cloud", 12, "c", start_table=13)
    result = solve(judges, projects)

    by_id = {p["id"]: p for p in projects}
    for judge in judges:
        for pid in result.judge_projects[judge["id"]]:
            assert judge["track"] in by_id[pid]["tracks"]


def test_dual_track_project_goes_to_the_track_with_spare_capacity():
    """31 Cloud-eligible projects and 3 Cloud judges must not all land on Cloud."""
    judges = make_judges("AI/ML", 21, "ml") + make_judges("Cloud", 3, "cl")
    projects = [
        {"id": f"p{i}", "name": f"P{i}", "tracks": ["Cloud", "AI/ML"], "table_number": i}
        for i in range(1, 41)
    ]
    balanced = solve(judges, projects, SolverConfig(balance_multi_track=True))
    naive = solve(judges, projects, SolverConfig(balance_multi_track=False))

    cloud_balanced = sum(1 for t in balanced.project_owner_track.values() if t == "Cloud")
    cloud_naive = sum(1 for t in naive.project_owner_track.values() if t == "Cloud")
    assert cloud_naive == 40           # everything dumped on 3 judges
    assert cloud_balanced < cloud_naive
    # and the workload gap should shrink
    wl = validate.workload_balance(balanced.judge_projects, balanced.judge_tracks)
    assert wl["per_track"]["Cloud"]["max"] < 40


def test_workload_is_roughly_balanced(simple):
    judges, projects = simple
    result = solve(judges, projects, SolverConfig(min_judges_per_project=3))

    loads = [len(result.judge_projects[j["id"]]) for j in judges]
    assert max(loads) - min(loads) <= 2, loads


def test_max_projects_per_judge_is_respected():
    judges = make_judges("AI/ML", 5)
    projects = make_projects("AI/ML", 60)
    result = solve(judges, projects, SolverConfig(min_judges_per_project=3, max_projects_per_judge=20))
    assert max(len(v) for v in result.judge_projects.values()) <= 20
    assert any("max_projects_per_judge" in w for w in result.warnings)


def test_track_with_judges_but_no_projects_warns_and_stays_idle():
    judges = make_judges("AI/ML", 4, "ml") + make_judges("Marimo Challenge", 1, "mo")
    projects = make_projects("AI/ML", 12)
    result = solve(judges, projects)

    assert result.judge_projects["mo1"] == []
    assert any("Marimo Challenge" in w for w in result.warnings)
    assert nx.is_connected(result.track_graph("AI/ML"))


def test_empty_track_can_be_made_fatal():
    judges = make_judges("AI/ML", 4, "ml") + make_judges("Marimo Challenge", 1, "mo")
    projects = make_projects("AI/ML", 12)
    with pytest.raises(EmptyTrackError):
        solve(judges, projects, SolverConfig(allow_empty_tracks=False))


def test_solver_is_deterministic(simple):
    judges, projects = simple
    a = solve(judges, projects)
    b = solve(judges, projects)
    assert a.judge_projects == b.judge_projects


def test_single_judge_track_is_connected():
    judges = make_judges("Economics", 1)
    projects = make_projects("Economics", 8)
    result = solve(judges, projects, SolverConfig(min_judges_per_project=3))
    assert len(result.judge_projects["j1"]) == 8
    assert nx.is_connected(result.track_graph("Economics"))


def test_accepts_dataclasses_and_camelcase_dicts():
    judges = [Judge(id="j1", name="A", track="AI/ML"), Judge(id="j2", name="B", track="AI/ML")]
    projects = [
        Project(id="p1", name="P1", tracks=("AI/ML",), table_number=1),
        Project(id="p2", name="P2", tracks=("AI/ML",), table_number=2),
    ]
    from_objects = solve(judges, projects, SolverConfig(min_judges_per_project=2))

    camel = solve(
        [{"judgeId": "j1", "name": "A", "track": "AI/ML"},
         {"judgeId": "j2", "name": "B", "track": "AI/ML"}],
        [{"projectId": "p1", "name": "P1", "tracks": ["AI/ML"], "tableNumber": 1},
         {"projectId": "p2", "name": "P2", "tracks": ["AI/ML"], "tableNumber": 2}],
        SolverConfig(min_judges_per_project=2),
    )
    assert from_objects.judge_projects == camel.judge_projects


def test_accepts_pandas_dataframes():
    pd = pytest.importorskip("pandas")
    judges = pd.DataFrame(make_judges("AI/ML", 5))
    projects = pd.DataFrame(make_projects("AI/ML", 15))
    result = solve(judges, projects)
    assert len(result.judge_projects) == 5
    assert nx.is_connected(result.track_graph("AI/ML"))


def test_duplicate_ids_are_rejected():
    with pytest.raises(ValueError, match="duplicate judge id"):
        solve(
            [{"id": "j1", "track": "AI/ML"}, {"id": "j1", "track": "AI/ML"}],
            make_projects("AI/ML", 3),
        )


def test_judge_without_a_track_is_rejected():
    with pytest.raises(ValueError, match="no track"):
        solve([{"id": "j1", "track": ""}], make_projects("AI/ML", 3))


def test_to_records_marks_anchors(simple):
    judges, projects = simple
    result = solve(judges, projects, SolverConfig(anchors_per_track=2))
    rows = result.to_records()
    anchors = set(result.anchors["AI/ML"])
    for row in rows:
        assert row["is_anchor"] == (row["project_id"] in anchors)
    assert sum(1 for r in rows if r["is_anchor"]) == 2 * len(judges)


# ---------------------------------------------------------------------------
# 7. validators as a standalone preflight
# ---------------------------------------------------------------------------


def test_preflight_reports_the_2026_shape_as_broken():
    """The historical assignment must fail the preflight we now ship."""
    judge_projects = {f"j{i}": [f"p{i}"] for i in range(10)}  # one island per judge
    judge_tracks = {f"j{i}": "AI/ML" for i in range(10)}
    report = validate.preflight(judge_projects, judge_tracks, min_judges_per_project=3)

    assert report["ok"] is False
    assert report["connectivity"]["tracks"]["AI/ML"]["n_components"] == 10
    assert report["pair_overlap"]["pct"] == 0.0
    assert len(report["projects_below_target"]) == 10
    assert "DISCONNECTED" in validate.format_report(report)


def test_preflight_passes_on_solver_output(simple):
    judges, projects = simple
    result = solve(judges, projects)
    report = result.preflight()

    assert report["ok"] is True
    assert report["coverage"]["min"] >= 3
    assert report["pair_overlap_within_track"]["pct"] == 100.0
    validate.raise_if_broken(report)  # must not raise
    assert "PASS" in validate.format_report(report)


def test_coverage_histogram_counts_unjudged_projects():
    cov = validate.coverage_histogram({"j1": ["p1"]}, all_project_ids=["p1", "p2", "p3"])
    assert cov["histogram"] == {0: 2, 1: 1}
    assert cov["unjudged"] == ["p2", "p3"]


def test_workload_balance_flags_idle_judges():
    wl = validate.workload_balance({"j1": ["p1"], "j2": []}, {"j1": "AI/ML", "j2": "AI/ML"})
    assert wl["idle_judges"] == ["j2"]
    assert wl["overall"]["min"] == 0


def test_judge_and_project_ids_may_collide():
    """Real 2026 data has judge 'ia' and project 'ia'. Prefixes must save us."""
    judge_projects = {"ia": ["ia"], "b": ["ia"]}
    judge_tracks = {"ia": "AI/ML", "b": "AI/ML"}
    report = validate.check_connectivity(judge_projects, judge_tracks)
    assert report["ok"] is True
    assert report["tracks"]["AI/ML"]["n_projects"] == 1


def test_pair_overlap_matches_a_hand_computed_case():
    judge_projects = {"a": ["p1"], "b": ["p1"], "c": ["p2"]}
    tracks = {"a": "T", "b": "T", "c": "T"}
    ov = validate.judge_pair_overlap(judge_projects, tracks)
    assert ov["pairs"] == 3 and ov["shared_pairs"] == 1
    assert ov["pct"] == pytest.approx(33.3)
