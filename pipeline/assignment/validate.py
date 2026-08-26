"""
Standalone validators for judge -> project assignments.

Nothing in here imports the solver, so you can point these functions at ANY
assignment: one the solver just produced, one loaded from Firestore, or one
someone typed into a spreadsheet. That makes this module usable as a preflight
check the morning of the event.

Everything takes two plain mappings:

    judge_projects : {judge_id: [project_id, ...]}
    judge_tracks   : {judge_id: "AI/ML"}

Both are ordinary Python dicts. No dataclasses, no Firestore, no pandas
required (pandas is only used by the optional ``*_frame`` helpers).

Quick start:

    from pipeline.assignment import validate
    report = validate.preflight(judge_projects, judge_tracks)
    print(validate.format_report(report))
    validate.raise_if_broken(report)
"""

from __future__ import annotations

import itertools
import statistics
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping, Sequence

import networkx as nx

JUDGE_PREFIX = "J::"
PROJECT_PREFIX = "P::"


class DisconnectedAssignmentError(Exception):
    """Raised when a track's judge-project graph splits into islands.

    This is the failure the 2026 run shipped with. If the graph is not
    connected you cannot put two judges on a common scale, so any leniency
    correction / normalisation downstream is guesswork.

    Carries the offending ``report`` and ``assignment`` so callers can render
    *which* judges are stranded rather than only that something is wrong. The
    organizer console's red blocking banner is built from these.
    """

    def __init__(self, message, *, report=None, assignment=None):
        super().__init__(message)
        self.report = report
        self.assignment = assignment


# ---------------------------------------------------------------------------
# graph construction
# ---------------------------------------------------------------------------


def build_bipartite_graph(
    judge_projects: Mapping[str, Iterable[str]],
    judge_ids: Iterable[str] | None = None,
) -> nx.Graph:
    """Build the judge <-> project bipartite graph.

    Judge nodes are prefixed ``J::`` and project nodes ``P::`` so a judge and a
    project can never collide on the same id string (they do collide in the
    2026 data: judge ``ia`` vs project ``ia``).

    ``judge_ids`` lets you force judges into the graph even when they have zero
    projects. That is deliberate: a judge with no projects SHOULD show up as an
    isolated node and fail the connectivity gate.
    """
    graph = nx.Graph()
    ids = list(judge_ids) if judge_ids is not None else list(judge_projects)
    for judge_id in ids:
        graph.add_node(JUDGE_PREFIX + judge_id, bipartite=0)
    for judge_id in ids:
        for project_id in judge_projects.get(judge_id, ()) or ():
            graph.add_node(PROJECT_PREFIX + project_id, bipartite=1)
            graph.add_edge(JUDGE_PREFIX + judge_id, PROJECT_PREFIX + project_id)
    return graph


def _judges_by_track(judge_tracks: Mapping[str, str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for judge_id, track in judge_tracks.items():
        grouped[track or "(no track)"].append(judge_id)
    return {track: sorted(ids) for track, ids in sorted(grouped.items())}


# ---------------------------------------------------------------------------
# 1. connectivity
# ---------------------------------------------------------------------------


def check_connectivity(
    judge_projects: Mapping[str, Iterable[str]],
    judge_tracks: Mapping[str, str],
    ignore_tracks: Iterable[str] = (),
) -> dict[str, Any]:
    """Per-track connectivity of the judge-project graph.

    Returns::

        {
          "ok": bool,
          "tracks": {track: {"connected": bool, "n_components": int,
                             "n_judges": int, "n_projects": int,
                             "isolated_judges": [...],
                             "components": [[judge_id, ...], ...]}},
          "failures": [track, ...],
        }

    A track with a single judge is trivially connected as long as that judge
    has at least one project.
    """
    ignore = set(ignore_tracks)
    tracks: dict[str, Any] = {}
    failures: list[str] = []

    for track, ids in _judges_by_track(judge_tracks).items():
        if track in ignore:
            continue
        graph = build_bipartite_graph(judge_projects, judge_ids=ids)
        components = list(nx.connected_components(graph))
        judge_components = [
            sorted(n[len(JUDGE_PREFIX):] for n in comp if n.startswith(JUDGE_PREFIX))
            for comp in components
        ]
        judge_components = [c for c in judge_components if c]
        judge_components.sort(key=lambda c: (-len(c), c[0] if c else ""))
        isolated = sorted(j for j in ids if not list(judge_projects.get(j, ()) or ()))
        n_projects = sum(1 for n in graph if n.startswith(PROJECT_PREFIX))
        connected = len(judge_components) <= 1 and not isolated

        tracks[track] = {
            "connected": connected,
            "n_components": len(judge_components),
            "n_judges": len(ids),
            "n_projects": n_projects,
            "isolated_judges": isolated,
            "components": judge_components,
        }
        if not connected:
            failures.append(track)

    return {"ok": not failures, "tracks": tracks, "failures": failures}


def connectivity_error_message(report: Mapping[str, Any]) -> str:
    """Human-readable explanation of why the connectivity gate failed."""
    lines = ["Judge-project graph is DISCONNECTED. Score normalisation is impossible."]
    for track in report["failures"]:
        info = report["tracks"][track]
        lines.append(
            f"  track {track!r}: {info['n_judges']} judges, "
            f"{info['n_projects']} projects, {info['n_components']} islands"
        )
        if info["isolated_judges"]:
            lines.append(
                "    judges with NO projects at all: "
                + ", ".join(info["isolated_judges"])
            )
        for i, comp in enumerate(info["components"], 1):
            preview = ", ".join(comp[:8]) + (" ..." if len(comp) > 8 else "")
            lines.append(f"    island {i} ({len(comp)} judges): {preview}")
    lines.append(
        "Fix: raise anchors_per_track (>=1 shared anchor project per track "
        "connects every judge) or make sure every judge has projects."
    )
    return "\n".join(lines)


def raise_if_broken(report: Mapping[str, Any]) -> None:
    """Raise :class:`DisconnectedAssignmentError` if the preflight found islands."""
    conn = report.get("connectivity", report)
    if not conn.get("ok", True):
        raise DisconnectedAssignmentError(connectivity_error_message(conn))


# ---------------------------------------------------------------------------
# 2. coverage
# ---------------------------------------------------------------------------


def coverage_histogram(
    judge_projects: Mapping[str, Iterable[str]],
    all_project_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """How many judges each project got.

    Pass ``all_project_ids`` to make unjudged projects show up as a ``0`` bucket
    instead of silently vanishing.
    """
    per_project: Counter[str] = Counter()
    for judge_id, projects in judge_projects.items():
        for project_id in set(projects or ()):
            per_project[project_id] += 1

    if all_project_ids is not None:
        for project_id in all_project_ids:
            per_project.setdefault(project_id, 0)

    counts = sorted(per_project.values())
    return {
        "per_project": dict(per_project),
        "histogram": dict(sorted(Counter(counts).items())),
        "n_projects": len(counts),
        "min": min(counts) if counts else 0,
        "median": statistics.median(counts) if counts else 0,
        "mean": round(statistics.mean(counts), 2) if counts else 0,
        "max": max(counts) if counts else 0,
        "unjudged": sorted(p for p, c in per_project.items() if c == 0),
    }


def projects_below_target(
    judge_projects: Mapping[str, Iterable[str]],
    min_judges: int,
    all_project_ids: Iterable[str] | None = None,
) -> list[tuple[str, int]]:
    """Projects that did not reach ``min_judges``, as ``(project_id, count)``."""
    cov = coverage_histogram(judge_projects, all_project_ids)["per_project"]
    return sorted((p, c) for p, c in cov.items() if c < min_judges)


# ---------------------------------------------------------------------------
# 3. pair overlap
# ---------------------------------------------------------------------------


def judge_pair_overlap(
    judge_projects: Mapping[str, Iterable[str]],
    judge_tracks: Mapping[str, str] | None = None,
    within_track_only: bool = False,
) -> dict[str, Any]:
    """Fraction of judge pairs that share at least one project.

    This is the headline number for "can we compare these judges to each
    other". 2026 shipped at 5.6%.

    ``within_track_only=True`` restricts the denominator to pairs inside the
    same track, which is the number that actually matters (cross-track pairs
    are never meant to overlap).
    """
    sets = {j: set(p or ()) for j, p in judge_projects.items()}
    if judge_tracks is not None:
        for judge_id in judge_tracks:
            sets.setdefault(judge_id, set())

    judge_ids = sorted(sets)
    total = 0
    shared = 0
    per_track: dict[str, dict[str, int]] = defaultdict(lambda: {"pairs": 0, "shared": 0})

    for a, b in itertools.combinations(judge_ids, 2):
        same_track = (
            judge_tracks is not None
            and judge_tracks.get(a) == judge_tracks.get(b)
        )
        if within_track_only and not same_track:
            continue
        total += 1
        overlaps = bool(sets[a] & sets[b])
        if overlaps:
            shared += 1
        if same_track:
            bucket = per_track[judge_tracks.get(a) or "(no track)"]
            bucket["pairs"] += 1
            bucket["shared"] += int(overlaps)

    return {
        "n_judges": len(judge_ids),
        "pairs": total,
        "shared_pairs": shared,
        "pct": round(100.0 * shared / total, 1) if total else 0.0,
        "per_track": {
            track: {
                **counts,
                "pct": round(100.0 * counts["shared"] / counts["pairs"], 1)
                if counts["pairs"]
                else 0.0,
            }
            for track, counts in sorted(per_track.items())
        },
    }


# ---------------------------------------------------------------------------
# 4. workload balance
# ---------------------------------------------------------------------------


def workload_balance(
    judge_projects: Mapping[str, Iterable[str]],
    judge_tracks: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """min / median / mean / max / stdev projects per judge, overall and per track."""

    def _stats(loads: Sequence[int]) -> dict[str, Any]:
        if not loads:
            return {"n": 0, "min": 0, "median": 0, "mean": 0.0, "max": 0, "stdev": 0.0}
        return {
            "n": len(loads),
            "min": min(loads),
            "median": statistics.median(loads),
            "mean": round(statistics.mean(loads), 2),
            "max": max(loads),
            "stdev": round(statistics.pstdev(loads), 2),
        }

    ids = list(judge_tracks) if judge_tracks is not None else list(judge_projects)
    loads = {j: len(set(judge_projects.get(j, ()) or ())) for j in ids}

    per_track: dict[str, Any] = {}
    if judge_tracks is not None:
        grouped: dict[str, list[int]] = defaultdict(list)
        for judge_id, track in judge_tracks.items():
            grouped[track or "(no track)"].append(loads.get(judge_id, 0))
        per_track = {t: _stats(sorted(v)) for t, v in sorted(grouped.items())}

    return {
        "overall": _stats(sorted(loads.values())),
        "per_track": per_track,
        "per_judge": loads,
        "idle_judges": sorted(j for j, n in loads.items() if n == 0),
    }


# ---------------------------------------------------------------------------
# 5. the whole preflight
# ---------------------------------------------------------------------------


def preflight(
    judge_projects: Mapping[str, Iterable[str]],
    judge_tracks: Mapping[str, str],
    all_project_ids: Iterable[str] | None = None,
    min_judges_per_project: int = 3,
    ignore_tracks: Iterable[str] = (),
) -> dict[str, Any]:
    """Run every validator and return one report dict.

    Does NOT raise. Call :func:`raise_if_broken` (or check ``report["ok"]``)
    yourself so you can print the report first.
    """
    connectivity = check_connectivity(judge_projects, judge_tracks, ignore_tracks)
    coverage = coverage_histogram(judge_projects, all_project_ids)
    overlap = judge_pair_overlap(judge_projects, judge_tracks)
    overlap_within = judge_pair_overlap(judge_projects, judge_tracks, within_track_only=True)
    balance = workload_balance(judge_projects, judge_tracks)
    under = projects_below_target(judge_projects, min_judges_per_project, all_project_ids)

    return {
        "ok": connectivity["ok"] and not under,
        "min_judges_per_project": min_judges_per_project,
        "connectivity": connectivity,
        "coverage": coverage,
        "pair_overlap": overlap,
        "pair_overlap_within_track": overlap_within,
        "workload": balance,
        "projects_below_target": under,
    }


def format_report(report: Mapping[str, Any]) -> str:
    """Pretty-print a :func:`preflight` report for a terminal."""
    cov = report["coverage"]
    ov = report["pair_overlap"]
    ovw = report["pair_overlap_within_track"]
    wl = report["workload"]["overall"]
    conn = report["connectivity"]
    target = report["min_judges_per_project"]

    lines = [
        "=" * 72,
        "ASSIGNMENT PREFLIGHT",
        "=" * 72,
        f"connectivity          : {'PASS' if conn['ok'] else 'FAIL'} "
        f"({len(conn['tracks'])} tracks checked)",
        f"projects              : {cov['n_projects']}",
        f"judges per project    : min {cov['min']}  median {cov['median']}  "
        f"mean {cov['mean']}  max {cov['max']}",
        f"  histogram           : {cov['histogram']}",
        f"  below target ({target})     : {len(report['projects_below_target'])}",
        f"projects per judge    : min {wl['min']}  median {wl['median']}  "
        f"mean {wl['mean']}  max {wl['max']}  stdev {wl['stdev']}",
        f"judge pair overlap    : {ov['shared_pairs']}/{ov['pairs']} = {ov['pct']}% (all pairs)",
        f"  within track        : {ovw['shared_pairs']}/{ovw['pairs']} = {ovw['pct']}%",
        "",
        f"{'track':<36} {'judges':>6} {'proj':>5} {'islands':>8} {'overlap%':>9}",
        "-" * 72,
    ]
    for track, info in conn["tracks"].items():
        pair = ov["per_track"].get(track, {})
        flag = "" if info["connected"] else "  <-- DISCONNECTED"
        lines.append(
            f"{track:<36} {info['n_judges']:>6} {info['n_projects']:>5} "
            f"{info['n_components']:>8} {pair.get('pct', 0.0):>8.1f}%{flag}"
        )
    if report["workload"]["idle_judges"]:
        lines += ["", "IDLE JUDGES (no projects): " + ", ".join(report["workload"]["idle_judges"])]
    lines.append("=" * 72)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# optional pandas views
# ---------------------------------------------------------------------------


def coverage_frame(
    judge_projects: Mapping[str, Iterable[str]],
    all_project_ids: Iterable[str] | None = None,
):
    """Coverage as a pandas DataFrame (project_id, n_judges). Requires pandas."""
    import pandas as pd

    cov = coverage_histogram(judge_projects, all_project_ids)["per_project"]
    return pd.DataFrame(
        sorted(cov.items()), columns=["project_id", "n_judges"]
    ).sort_values("n_judges").reset_index(drop=True)


def workload_frame(
    judge_projects: Mapping[str, Iterable[str]],
    judge_tracks: Mapping[str, str] | None = None,
):
    """Workload as a pandas DataFrame (judge_id, track, n_projects)."""
    import pandas as pd

    loads = workload_balance(judge_projects, judge_tracks)["per_judge"]
    rows = [
        {
            "judge_id": j,
            "track": (judge_tracks or {}).get(j),
            "n_projects": n,
        }
        for j, n in sorted(loads.items())
    ]
    return pd.DataFrame(rows)
