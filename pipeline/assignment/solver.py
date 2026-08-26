"""
Judge -> project assignment solver for the DataHacks judging platform.

WHY THIS EXISTS
---------------
The 2026 run used a hardcoded roster of 97 judges split into fixed team sizes,
then sliced each track's projects across ``teams.length``. 63 judges actually
showed up. Because teams were filled in roster order, the late teams were empty
but the projects were still divided across the FULL team count. Result: 44
projects scored by a single judge, only 5.6% of judge pairs ever shared a
project, and the judge-project graph shattered into roughly one island per
judge. With islands you cannot estimate judge leniency, so cross-judge score
normalisation is mathematically impossible.

WHAT THIS DOES DIFFERENTLY
--------------------------
1. It is driven by who CHECKED IN, never by a hardcoded roster.
2. Every project targets ``min_judges_per_project`` judges (default 3).
3. Every judge in a track also scores the same handful of ANCHOR projects.
   Anchors are the connective tissue: with one shared anchor the whole track is
   one connected component, and leniency becomes estimable.
4. It supports both judging modes: ``group`` (everyone sharing a ``group_id``
   gets a byte-identical project list) and ``individual`` (overlapping but
   distinct lists, optionally biased toward friends via ``affinity``).
5. It refuses to return a disconnected assignment. The connectivity gate runs
   before ``solve()`` returns and raises if any track is islanded.

USAGE
-----
    from pipeline.assignment.solver import solve, SolverConfig

    result = solve(
        judges=[{"id": "u1", "name": "Ada", "track": "AI/ML"}, ...],
        projects=[{"id": "p1", "name": "NeuralScan", "tracks": ["AI/ML"],
                   "table_number": 1}, ...],
        config=SolverConfig(min_judges_per_project=3, anchors_per_track=3),
    )
    result.judge_projects   # {judge_id: [project_id, ...]}
    result.project_judges   # {project_id: [judge_id, ...]}

Inputs may be lists of dicts, lists of dataclasses, or pandas DataFrames.
Nothing here knows about Firestore.

Runs on plain CPython 3.9+ and on Databricks (no Databricks-only APIs).
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import networkx as nx

from . import validate
from .validate import DisconnectedAssignmentError  # re-exported for convenience

INDIVIDUAL = "individual"
GROUP = "group"

__all__ = [
    "Judge",
    "Project",
    "SolverConfig",
    "Assignment",
    "solve",
    "INDIVIDUAL",
    "GROUP",
    "DisconnectedAssignmentError",
    "EmptyTrackError",
]


class EmptyTrackError(ValueError):
    """A track has judges but zero eligible projects (or vice versa)."""


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Judge:
    """One checked-in judge.

    id        : stable key. Use the Firebase Auth UID -- that is what
                ``evaluations.judgeId`` holds, so UID-keyed assignments join
                cleanly to scores later.
    track     : the single track this judge scores.
    mode      : "individual" or "group".
    group_id  : required when mode == "group". Everyone sharing a group_id gets
                an identical project list -- that is what makes it a group.
    affinity  : other judge ids this judge wants to walk around with. Only
                meaningful for individuals: it biases their lists toward
                overlap WITHOUT making them identical.
    """

    id: str
    name: str = ""
    track: str = ""
    mode: str = INDIVIDUAL
    group_id: str | None = None
    affinity: tuple[str, ...] = ()


@dataclass(frozen=True)
class Project:
    """One submission. ``tracks`` may hold one or two tracks."""

    id: str
    name: str = ""
    tracks: tuple[str, ...] = ()
    table_number: int | None = None


@dataclass
class SolverConfig:
    """Knobs. All defaults are the ones we would actually run.

    min_judges_per_project
        Coverage target. 3 is the floor at which a median-of-judges score stops
        being a coin flip.
    anchors_per_track
        How many projects EVERY judge in a track scores. 1 is enough to
        guarantee connectivity; 3 gives a much better leniency estimate.
        Anchors are spread across the room (by table number) so they are
        representative rather than three neighbouring tables.
    max_projects_per_judge
        Hard cap on workload. ``None`` = uncapped. Anchors always count toward
        it but are never dropped (connectivity wins over the cap).
    balance_multi_track
        A project with two tracks is OWNED by exactly one of them for coverage
        purposes. With this on, ownership goes to whichever of its tracks is
        currently least loaded (projects-owned per judge), preferring tracks
        that actually have enough judges to hit the target. That is how a
        3-judge Cloud track avoids being handed 31 projects, and how a 1-judge
        Economics track avoids owning projects it can never cover.
    top_up_undersized_tracks
        A track with fewer than ``min_judges_per_project`` judges cannot cover
        anything on its own. Rather than leave those judges idle (or let them
        solo-judge a project), send them in as EXTRA judges on projects that
        list their track but are owned by a bigger track. Nobody sits out and
        nothing drops below target.
    affinity_weight
        How strongly an affinity hint pulls a judge onto a project a friend is
        already on. 0 disables affinity.
    enforce_distinct_individual_sets
        Best-effort pass that hands an extra project to one of any two
        individuals who ended up with byte-identical lists. Impossible in a
        track with <= min_judges judges; that case is reported as a warning.
    """

    min_judges_per_project: int = 3
    anchors_per_track: int = 3
    max_projects_per_judge: int | None = None
    balance_multi_track: bool = True
    affinity_weight: float = 1.0
    enforce_distinct_individual_sets: bool = True
    allow_empty_tracks: bool = True
    top_up_undersized_tracks: bool = True


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


@dataclass
class Assignment:
    """What the solver produced, plus enough context to explain it."""

    judge_projects: dict[str, list[str]]
    project_judges: dict[str, list[str]]
    anchors: dict[str, list[str]]
    project_owner_track: dict[str, str]
    judges: list[Judge]
    projects: list[Project]
    config: SolverConfig
    warnings: list[str] = field(default_factory=list)

    # -- convenience -------------------------------------------------------
    @property
    def judge_tracks(self) -> dict[str, str]:
        return {j.id: j.track for j in self.judges}

    @property
    def judges_by_id(self) -> dict[str, Judge]:
        return {j.id: j for j in self.judges}

    def track_graph(self, track: str) -> nx.Graph:
        """Bipartite judge-project graph for one track."""
        ids = [j.id for j in self.judges if j.track == track]
        return validate.build_bipartite_graph(self.judge_projects, judge_ids=ids)

    def to_records(self) -> list[dict[str, Any]]:
        """Flat (judge_id, project_id, ...) rows -- easy to write anywhere."""
        projects_by_id = {p.id: p for p in self.projects}
        judges_by_id = self.judges_by_id
        rows: list[dict[str, Any]] = []
        for judge_id, project_ids in sorted(self.judge_projects.items()):
            judge = judges_by_id.get(judge_id)
            for project_id in project_ids:
                project = projects_by_id.get(project_id)
                rows.append(
                    {
                        "judge_id": judge_id,
                        "judge_name": judge.name if judge else "",
                        "track": judge.track if judge else "",
                        "mode": judge.mode if judge else "",
                        "group_id": judge.group_id if judge else None,
                        "project_id": project_id,
                        "project_name": project.name if project else "",
                        "table_number": project.table_number if project else None,
                        "is_anchor": project_id
                        in self.anchors.get(judge.track if judge else "", []),
                    }
                )
        return rows

    def to_frame(self):
        """``to_records`` as a pandas DataFrame."""
        import pandas as pd

        return pd.DataFrame(self.to_records())

    def preflight(self) -> dict[str, Any]:
        return validate.preflight(
            self.judge_projects,
            self.judge_tracks,
            all_project_ids=[p.id for p in self.projects],
            min_judges_per_project=self.config.min_judges_per_project,
        )

    def summary(self) -> str:
        return validate.format_report(self.preflight())


# ---------------------------------------------------------------------------
# input coercion -- accept dicts, dataclasses, or DataFrames
# ---------------------------------------------------------------------------


def _rows(data: Any) -> list[dict[str, Any]]:
    """Normalise a DataFrame / list of dicts / list of dataclasses to dicts."""
    if data is None:
        return []
    if hasattr(data, "to_dict") and hasattr(data, "columns"):  # pandas DataFrame
        return list(data.to_dict("records"))
    out: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, Mapping):
            out.append(dict(item))
        elif isinstance(item, (Judge, Project)):
            out.append({"__obj__": item})
        elif hasattr(item, "__dict__"):
            out.append(dict(vars(item)))
        else:
            raise TypeError(f"cannot interpret row of type {type(item).__name__}")
    return out


def _pick(row: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in row and row[name] is not None:
            value = row[name]
            # pandas turns missing values into float('nan')
            if isinstance(value, float) and math.isnan(value):
                continue
            return value
    return default


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return (str(value),)


def coerce_judges(data: Any) -> list[Judge]:
    judges: list[Judge] = []
    for row in _rows(data):
        if "__obj__" in row:
            judges.append(row["__obj__"])
            continue
        judge_id = _pick(row, "id", "judge_id", "judgeId", "uid", "_id")
        if judge_id is None:
            raise ValueError(f"judge row is missing an id: {row!r}")
        mode = str(_pick(row, "mode", "judging_mode", default=INDIVIDUAL)).lower()
        if mode not in (INDIVIDUAL, GROUP):
            raise ValueError(
                f"judge {judge_id!r}: mode must be {INDIVIDUAL!r} or {GROUP!r}, got {mode!r}"
            )
        judges.append(
            Judge(
                id=str(judge_id),
                name=str(_pick(row, "name", "judge_name", default="") or ""),
                track=str(_pick(row, "track", "Tracks", "tracks", default="") or "").strip(),
                mode=mode,
                group_id=(
                    str(_pick(row, "group_id", "groupId", "group"))
                    if _pick(row, "group_id", "groupId", "group") is not None
                    else None
                ),
                affinity=_as_tuple(_pick(row, "affinity", "affinities", "walk_with")),
            )
        )
    return judges


def coerce_projects(data: Any) -> list[Project]:
    projects: list[Project] = []
    for row in _rows(data):
        if "__obj__" in row:
            projects.append(row["__obj__"])
            continue
        project_id = _pick(row, "id", "project_id", "projectId", "_id")
        if project_id is None:
            raise ValueError(f"project row is missing an id: {row!r}")
        table = _pick(row, "table_number", "tableNumber", "table")
        try:
            table = int(table) if table is not None else None
        except (TypeError, ValueError):
            table = None
        projects.append(
            Project(
                id=str(project_id),
                name=str(_pick(row, "name", "title", "project_name", default="") or ""),
                tracks=_as_tuple(_pick(row, "tracks", "track")),
                table_number=table,
            )
        )
    return projects


# ---------------------------------------------------------------------------
# units: a group is one unit, an individual is a unit of one
# ---------------------------------------------------------------------------


@dataclass
class _Unit:
    """A thing that gets assigned projects as a block.

    A ``group`` of 4 judges is ONE unit of size 4: it satisfies a project's
    3-judge target in a single assignment. An individual is a unit of size 1.
    """

    key: str
    track: str
    judge_ids: list[str]
    is_group: bool
    projects: list[str] = field(default_factory=list)
    order: int = 0

    @property
    def size(self) -> int:
        return len(self.judge_ids)

    @property
    def load(self) -> int:
        return len(self.projects)


def _build_units(judges: Sequence[Judge], warnings: list[str]) -> list[_Unit]:
    by_key: dict[str, _Unit] = {}
    order = 0
    for judge in judges:
        if judge.mode == GROUP and not judge.group_id:
            warnings.append(
                f"judge {judge.id!r} has mode='group' but no group_id; "
                "treating as an individual"
            )
        if judge.mode == GROUP and judge.group_id:
            key = f"group::{judge.group_id}"
        else:
            key = f"solo::{judge.id}"
        unit = by_key.get(key)
        if unit is None:
            by_key[key] = _Unit(
                key=key,
                track=judge.track,
                judge_ids=[judge.id],
                is_group=judge.mode == GROUP and bool(judge.group_id),
                order=order,
            )
            order += 1
        else:
            if unit.track != judge.track:
                raise ValueError(
                    f"group {judge.group_id!r} spans multiple tracks "
                    f"({unit.track!r} and {judge.track!r}); a group must be one track"
                )
            unit.judge_ids.append(judge.id)
    return list(by_key.values())


# ---------------------------------------------------------------------------
# project ownership
# ---------------------------------------------------------------------------


def _assign_ownership(
    projects: Sequence[Project],
    tracks_with_judges: Mapping[str, int],
    viable_tracks: set[str],
    balance: bool,
    warnings: list[str],
) -> dict[str, str]:
    """Give every project exactly one owning track.

    A project may list two tracks. Exactly one of them is responsible for
    hitting the coverage target, otherwise a two-track project would silently
    need 2x the judges. With ``balance=True`` we hand it to whichever of its
    tracks currently has the most spare judging capacity, which is how a small
    track (3 Cloud judges) avoids drowning in dual-listed projects.

    ``viable_tracks`` are the tracks with enough judges to actually hit the
    coverage target. A project is only handed to a non-viable track when it has
    no other option.
    """
    owned_count: dict[str, int] = defaultdict(int)
    owner: dict[str, str] = {}

    ordered = sorted(
        projects,
        key=lambda p: (p.table_number if p.table_number is not None else 10**9, p.id),
    )
    for project in ordered:
        candidates = [t for t in project.tracks if tracks_with_judges.get(t)]
        if not candidates:
            warnings.append(
                f"project {project.id!r} has no checked-in judges for tracks "
                f"{list(project.tracks)!r}; it will go unjudged"
            )
            continue
        preferred = [t for t in candidates if t in viable_tracks] or candidates
        if balance and len(preferred) > 1:
            chosen = min(
                preferred,
                key=lambda t: (
                    owned_count[t] / tracks_with_judges[t],
                    owned_count[t],
                    project.tracks.index(t),
                ),
            )
        else:
            chosen = preferred[0]
        owner[project.id] = chosen
        owned_count[chosen] += 1
    return owner


# ---------------------------------------------------------------------------
# anchors
# ---------------------------------------------------------------------------


def _pick_anchors(track_projects: Sequence[Project], n: int) -> list[str]:
    """Evenly-spaced picks across the track's projects, sorted by table number.

    Even spacing matters: three anchors from adjacent tables tell you nothing
    about a judge's behaviour on the rest of the room, and they also put every
    judge in the same corner at the same time.
    """
    if n <= 0 or not track_projects:
        return []
    ordered = sorted(
        track_projects,
        key=lambda p: (p.table_number if p.table_number is not None else 10**9, p.id),
    )
    n = min(n, len(ordered))
    step = len(ordered) / n
    picked: list[str] = []
    for i in range(n):
        idx = min(len(ordered) - 1, int(round(i * step + step / 2 - 0.5)))
        pid = ordered[idx].id
        if pid in picked:  # nudge forward on collision in tiny tracks
            for candidate in ordered:
                if candidate.id not in picked:
                    pid = candidate.id
                    break
        picked.append(pid)
    return picked


# ---------------------------------------------------------------------------
# the solver
# ---------------------------------------------------------------------------


def solve(
    judges: Any,
    projects: Any,
    config: SolverConfig | None = None,
) -> Assignment:
    """Build an assignment from the judges who actually checked in.

    Raises :class:`DisconnectedAssignmentError` if the resulting judge-project
    graph is not connected within every track. That gate is the whole point of
    this module -- it must be impossible to ship a disconnected assignment.
    """
    config = config or SolverConfig()
    judge_list = coerce_judges(judges)
    project_list = coerce_projects(projects)
    warnings: list[str] = []

    if not judge_list:
        raise ValueError("no judges supplied -- nothing to solve")

    seen_ids: set[str] = set()
    for judge in judge_list:
        if judge.id in seen_ids:
            raise ValueError(f"duplicate judge id {judge.id!r}")
        seen_ids.add(judge.id)
        if not judge.track:
            raise ValueError(f"judge {judge.id!r} has no track")

    seen_projects: set[str] = set()
    for project in project_list:
        if project.id in seen_projects:
            raise ValueError(f"duplicate project id {project.id!r}")
        seen_projects.add(project.id)

    # --- 1. who is here, per track ---------------------------------------
    units = _build_units(judge_list, warnings)
    units_by_track: dict[str, list[_Unit]] = defaultdict(list)
    for unit in units:
        units_by_track[unit.track].append(unit)
    judges_per_track = {
        track: sum(u.size for u in us) for track, us in units_by_track.items()
    }

    # --- 2. which track owns which project -------------------------------
    # A track needs at least min_judges judges before it can be trusted to
    # cover a project on its own.
    viable_tracks = {
        track
        for track, n in judges_per_track.items()
        if n >= config.min_judges_per_project
    }
    owner = _assign_ownership(
        project_list,
        judges_per_track,
        viable_tracks,
        config.balance_multi_track,
        warnings,
    )
    projects_by_id = {p.id: p for p in project_list}
    projects_by_track: dict[str, list[Project]] = defaultdict(list)
    for project_id, track in owner.items():
        projects_by_track[track].append(projects_by_id[project_id])

    # --- 3. affinity graph (symmetric) -----------------------------------
    affinity: dict[str, set[str]] = defaultdict(set)
    for judge in judge_list:
        for other in judge.affinity:
            if other in seen_ids and other != judge.id:
                affinity[judge.id].add(other)
                affinity[other].add(judge.id)

    anchors: dict[str, list[str]] = {}
    coverage: dict[str, int] = defaultdict(int)
    project_units: dict[str, list[_Unit]] = defaultdict(list)

    # projects a track's judges are eligible for but that another track owns.
    # These are where an undersized track's judges go as extra judges.
    guest_pool: dict[str, list[Project]] = defaultdict(list)
    for project in project_list:
        home = owner.get(project.id)
        for track in project.tracks:
            if track in units_by_track and track != home:
                guest_pool[track].append(project)

    for track, track_units in sorted(units_by_track.items()):
        track_projects = projects_by_track.get(track, [])
        if not track_projects:
            can_guest = config.top_up_undersized_tracks and bool(guest_pool.get(track))
            if can_guest:
                anchors[track] = []  # filled by the top-up pass below
                continue
            message = (
                f"track {track!r} has {judges_per_track[track]} checked-in judge(s) "
                "but zero projects to judge"
            )
            if config.allow_empty_tracks:
                anchors[track] = []  # the idle-judge warning is raised below
                continue
            raise EmptyTrackError(message)

        # 3a. anchors -- every unit gets all of them. This alone guarantees the
        #     track graph is a single connected component.
        track_anchor_ids = _pick_anchors(track_projects, config.anchors_per_track)
        if not track_anchor_ids:
            # anchors_per_track == 0: fall back to one anchor, because without
            # a shared project the connectivity gate will reject the result.
            track_anchor_ids = _pick_anchors(track_projects, 1)
            warnings.append(
                f"track {track!r}: anchors_per_track=0 forced up to 1 -- "
                "zero anchors cannot pass the connectivity gate"
            )
        anchors[track] = track_anchor_ids

        for unit in track_units:
            for project_id in track_anchor_ids:
                unit.projects.append(project_id)
                project_units[project_id].append(unit)
                coverage[project_id] += unit.size

        # 3b. everything else -- least-loaded unit first, nudged by affinity
        anchor_set = set(track_anchor_ids)
        remaining = [p for p in track_projects if p.id not in anchor_set]
        remaining.sort(
            key=lambda p: (p.table_number if p.table_number is not None else 10**9, p.id)
        )

        for project in remaining:
            assigned: list[_Unit] = []
            while coverage[project.id] < config.min_judges_per_project:
                candidates = [
                    u
                    for u in track_units
                    if u not in assigned
                    and (
                        config.max_projects_per_judge is None
                        or u.load < config.max_projects_per_judge
                    )
                ]
                if not candidates:
                    # either every unit is already on it, or the cap bit.
                    fallback = [u for u in track_units if u not in assigned]
                    if not fallback:
                        break
                    if config.max_projects_per_judge is not None:
                        warnings.append(
                            f"project {project.id!r}: max_projects_per_judge "
                            f"({config.max_projects_per_judge}) blocked full coverage"
                        )
                        break
                    candidates = fallback

                already = {j for u in assigned for j in u.judge_ids}

                def cost(unit: _Unit) -> tuple[float, int, int]:
                    bonus = sum(
                        1
                        for jid in unit.judge_ids
                        for friend in affinity.get(jid, ())
                        if friend in already
                    )
                    return (
                        unit.load - config.affinity_weight * bonus,
                        -unit.size,
                        unit.order,
                    )

                chosen = min(candidates, key=cost)
                chosen.projects.append(project.id)
                project_units[project.id].append(chosen)
                coverage[project.id] += chosen.size
                assigned.append(chosen)

    # --- 4. undersized tracks join projects owned by bigger tracks --------
    if config.top_up_undersized_tracks:
        _top_up_undersized_tracks(
            units_by_track,
            viable_tracks,
            guest_pool,
            anchors,
            coverage,
            project_units,
            config,
            warnings,
        )

    for track, track_units in units_by_track.items():
        if not any(u.projects for u in track_units):
            warnings.append(
                f"track {track!r} has {judges_per_track[track]} checked-in judge(s) "
                "but zero projects to judge -- those judges will be idle"
            )

    # Anything still short of the target is short because the room is short of
    # judges, not because the solver gave up quietly. Say so, per project.
    for project_id, track in sorted(owner.items()):
        if coverage[project_id] < config.min_judges_per_project:
            warnings.append(
                f"project {project_id!r} got {coverage[project_id]} judges "
                f"(target {config.min_judges_per_project}): its only eligible track "
                f"{track!r} has only {judges_per_track[track]} judge(s)"
            )

    # --- 5. individuals must not end up with identical lists --------------
    if config.enforce_distinct_individual_sets:
        pool_by_track = {
            track: [p.id for p in projects_by_track.get(track, [])]
            + [p.id for p in guest_pool.get(track, [])]
            for track in units_by_track
        }
        _break_identical_individual_sets(
            units_by_track, pool_by_track, anchors, coverage, project_units, warnings
        )

    # --- 6. flatten units back to judges ---------------------------------
    judge_projects: dict[str, list[str]] = {j.id: [] for j in judge_list}
    for unit in units:
        ordered = _order_by_table(unit.projects, projects_by_id)
        for judge_id in unit.judge_ids:
            judge_projects[judge_id] = list(ordered)

    project_judges: dict[str, list[str]] = {p.id: [] for p in project_list}
    for judge_id, project_ids in judge_projects.items():
        for project_id in project_ids:
            project_judges[project_id].append(judge_id)
    for project_id in project_judges:
        project_judges[project_id].sort()

    result = Assignment(
        judge_projects=judge_projects,
        project_judges=project_judges,
        anchors=anchors,
        project_owner_track=owner,
        judges=judge_list,
        projects=project_list,
        config=config,
        warnings=warnings,
    )

    # --- 7. THE GATE ------------------------------------------------------
    enforce_connectivity(result)
    return result


def _order_by_table(
    project_ids: Iterable[str], projects_by_id: Mapping[str, Project]
) -> list[str]:
    """Sort a judge's route by table number so they walk the room in order."""
    unique = list(dict.fromkeys(project_ids))
    return sorted(
        unique,
        key=lambda pid: (
            projects_by_id[pid].table_number
            if pid in projects_by_id and projects_by_id[pid].table_number is not None
            else 10**9,
            pid,
        ),
    )


def _top_up_undersized_tracks(
    units_by_track: Mapping[str, list[_Unit]],
    viable_tracks: set[str],
    guest_pool: Mapping[str, list[Project]],
    anchors: dict[str, list[str]],
    coverage: dict[str, int],
    project_units: dict[str, list[_Unit]],
    config: SolverConfig,
    warnings: list[str],
) -> None:
    """Send judges from too-small tracks onto projects owned by bigger tracks.

    A track with 1 judge (2026 had exactly that in Economics) can never give a
    project 3 judges. The old behaviour was to let that one judge solo-score a
    handful of projects, which is both unfair to those teams and useless for
    normalisation. Instead we treat those judges as extra coverage on projects
    that list their track but are owned elsewhere: nobody sits idle, nothing
    drops below target, and the track still gets its own anchors so its slice
    of the graph is connected.
    """
    reference_loads = [
        u.load for track in viable_tracks for u in units_by_track.get(track, [])
    ]
    target_load = int(statistics.median(reference_loads)) if reference_loads else 0

    for track, track_units in sorted(units_by_track.items()):
        if track in viable_tracks:
            continue
        pool = list(guest_pool.get(track, []))
        if not pool:
            continue

        # Anchors first: same shared-project trick, so this track's judges are
        # comparable to each other too.
        needed_anchors = max(0, config.anchors_per_track - len(anchors.get(track, [])))
        if needed_anchors:
            spare = [p for p in pool if p.id not in set(anchors.get(track, ()))]
            new_anchors = _pick_anchors(spare, needed_anchors)
            anchors.setdefault(track, [])
            for pid in new_anchors:
                anchors[track].append(pid)
                for unit in track_units:
                    if pid not in unit.projects:
                        unit.projects.append(pid)
                        project_units[pid].append(unit)
                        coverage[pid] += unit.size

        # Then top each unit up to a normal workload, least-covered projects
        # first so the extra visits land where they help most.
        by_id = {p.id: p for p in pool}
        for unit in sorted(track_units, key=lambda u: (u.load, u.order)):
            while unit.load < target_load:
                spare = [pid for pid in by_id if pid not in unit.projects]
                if not spare:
                    break
                pid = min(
                    spare,
                    key=lambda p: (
                        coverage[p],
                        by_id[p].table_number
                        if by_id[p].table_number is not None
                        else 10**9,
                        p,
                    ),
                )
                unit.projects.append(pid)
                project_units[pid].append(unit)
                coverage[pid] += unit.size

        warnings.append(
            f"track {track!r} has only {sum(u.size for u in track_units)} judge(s) "
            f"(target is {config.min_judges_per_project}); they were added as extra "
            "judges on projects owned by larger tracks instead of judging alone"
        )


def _break_identical_individual_sets(
    units_by_track: Mapping[str, list[_Unit]],
    pool_by_track: Mapping[str, list[str]],
    anchors: Mapping[str, list[str]],
    coverage: dict[str, int],
    project_units: dict[str, list[_Unit]],
    warnings: list[str],
) -> None:
    """Give one extra project to individuals who ended up with identical lists.

    We only ever ADD a project. Moving one would drop some other project below
    its coverage target; adding one cannot break anything except perfect
    workload balance, and we pick the least-covered project so the extra visit
    is useful.
    """
    for track, units in units_by_track.items():
        solos = [u for u in units if not u.is_group]
        if len(solos) < 2:
            continue
        anchor_set = set(anchors.get(track, ()))
        pool = list(dict.fromkeys(pool_by_track.get(track, [])))

        seen: dict[frozenset[str], _Unit] = {}
        for unit in sorted(solos, key=lambda u: u.order):
            key = frozenset(unit.projects)
            if key not in seen:
                seen[key] = unit
                continue
            spare = [p for p in pool if p not in unit.projects and p not in anchor_set]
            if not spare:
                warnings.append(
                    f"track {track!r}: individuals {seen[key].judge_ids[0]!r} and "
                    f"{unit.judge_ids[0]!r} have identical project lists and the "
                    "track is too small to differentiate them"
                )
                continue
            extra = min(spare, key=lambda pid: (coverage[pid], pid))
            unit.projects.append(extra)
            project_units[extra].append(unit)
            coverage[extra] += unit.size
            seen[frozenset(unit.projects)] = unit


def enforce_connectivity(assignment: Assignment) -> dict[str, Any]:
    """Hard gate. Raises unless every track's graph is one component.

    Tracks whose judges had no projects at all (an empty track that
    ``allow_empty_tracks`` let through) are excluded from the gate but are
    already recorded in ``assignment.warnings``.
    """
    empty_tracks = [t for t, ids in assignment.anchors.items() if not ids]
    report = validate.check_connectivity(
        assignment.judge_projects,
        assignment.judge_tracks,
        ignore_tracks=empty_tracks,
    )
    if not report["ok"]:
        raise DisconnectedAssignmentError(
            validate.connectivity_error_message(report),
            report=report,
            assignment=assignment,
        )
    return report
