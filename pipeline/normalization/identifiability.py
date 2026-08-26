"""
Identifiability diagnostics for judge-score normalization.

READ THIS FIRST. This module answers a question that must be settled *before*
anyone fits a normalization model:

    Can we actually tell "harsh judge" apart from "weak project"?

The intuition, with no statistics
---------------------------------
Suppose judge A scores only projects 1-4, and judge B scores only projects 5-8.
A's scores average 30/50; B's average 40/50.

Is B a more generous judge, or did B simply get better projects?

There is no way to know. Nothing in the data connects A's world to B's world.
Any answer you produce is an assumption you smuggled in, not a fact you measured.

Now suppose A and B *both* scored project 9. A gave it 25, B gave it 35. Now the
10-point gap is attached to a fixed thing, and it becomes evidence that B runs
10 points generous. That shared project is called an **anchor**, and anchors are
the only thing that makes judge leniency measurable.

The graph view
--------------
Build a graph with judges on one side and projects on the other, and an edge
wherever a judge scored a project (a "bipartite" graph). Then:

  * If the graph is **connected** -- you can walk from any judge to any other
    judge by hopping judge -> project -> judge -> ... -- then every judge's
    leniency is measurable relative to every other judge. Leniency is
    IDENTIFIABLE.

  * If the graph splits into separate **components** (islands with no path
    between them), then leniency is only comparable *within* an island. Across
    islands, judge harshness and project quality are perfectly confounded: you
    can add 5 points to every judge in island 1 and subtract 5 from every
    project in island 1, and the data looks exactly the same. The model has no
    way to prefer one story over the other. Leniency is NOT IDENTIFIABLE.

This is not a small-sample problem that more careful modelling can fix. It is a
structural hole in the experiment design. A mixed-effects model will still
happily return numbers -- it will shrink each island toward the grand mean --
but those numbers encode the assumption "all islands are equally good on
average", which is exactly the thing we wanted to measure.

Hence: this module runs first and warns loudly.
"""

from __future__ import annotations

from typing import Optional

import networkx as nx
import pandas as pd

# A component holding only one judge tells us nothing about that judge relative
# to anyone else, so we count those separately -- they are the clearest symptom
# of a broken assignment design.
JUDGE_PREFIX = "J:"
PROJ_PREFIX = "P:"


def build_graph(df: pd.DataFrame, same_rubric_only: bool = True) -> nx.Graph:
    """
    Bipartite judge-project graph. One node per judge, one per project, one edge
    per evaluation.

    `same_rubric_only` matters enormously on the 2026 data. Most projects are
    entered in TWO tracks, so the same project can be scored by an AI/ML judge
    (on AI/ML criteria) and a Cloud judge (on Cloud criteria). Those two scores
    share a project but not a measuring stick -- 32/50 on "Effective Use of
    Managed Services" says nothing about how the AI/ML judge would have scored.
    Treating that as an anchor would silently claim the two rubrics are
    interchangeable.

    With same_rubric_only=True (the default and the honest setting) the project
    node is keyed by (project, track), so a link only forms when two judges
    scored the same project *under the same rubric*. Set it to False only to
    demonstrate how much of the apparent connectivity is this artifact.
    """
    g = nx.Graph()
    for _, row in df.iterrows():
        j = JUDGE_PREFIX + str(row["judge_id"])
        key = (
            f"{row['project_id']}|{row['track']}" if same_rubric_only else str(row["project_id"])
        )
        p = PROJ_PREFIX + key
        g.add_node(j, bipartite="judge")
        g.add_node(p, bipartite="project")
        g.add_edge(j, p)
    return g


def judge_pair_overlap(df: pd.DataFrame) -> dict:
    """
    What fraction of judge pairs share at least one project?

    This is the blunt headline number. With J judges there are J*(J-1)/2 possible
    pairs; we count how many of them ever co-scored something. Every shared
    project is a measurement of the two judges' relative leniency, so this is
    literally "what fraction of the leniency comparisons we would like to make
    are supported by data".

    We count two ways, and the gap between them is the story of the 2026 data:

      * raw overlap        -- pairs sharing a project id, regardless of rubric
      * same-rubric overlap -- pairs sharing a (project, track), i.e. a real anchor

    Only the second kind is usable for estimating leniency.
    """
    raw_by_judge = df.groupby("judge_id")["project_id"].apply(set).to_dict()
    keyed = df.assign(_k=df["project_id"].astype(str) + "|" + df["track"].astype(str))
    rubric_by_judge = keyed.groupby("judge_id")["_k"].apply(set).to_dict()

    judges = sorted(raw_by_judge)
    n = len(judges)
    total_pairs = n * (n - 1) // 2

    overlapping = 0
    same_rubric = 0
    shared_counts = []
    for i in range(n):
        for k in range(i + 1, n):
            a, b = judges[i], judges[k]
            if raw_by_judge[a] & raw_by_judge[b]:
                overlapping += 1
            shared = rubric_by_judge[a] & rubric_by_judge[b]
            if shared:
                same_rubric += 1
                shared_counts.append(len(shared))

    return {
        "n_judges": n,
        "total_pairs": total_pairs,
        "overlapping_pairs": overlapping,
        "overlap_pct": (100.0 * overlapping / total_pairs) if total_pairs else 0.0,
        "same_rubric_pairs": same_rubric,
        "same_rubric_pct": (100.0 * same_rubric / total_pairs) if total_pairs else 0.0,
        "cross_rubric_pairs": overlapping - same_rubric,
        "mean_shared_when_overlapping": (
            sum(shared_counts) / len(shared_counts) if shared_counts else 0.0
        ),
    }


def diagnose_track(df: pd.DataFrame, track_name: str = "(all)") -> dict:
    """
    Full identifiability report for one slice of the data (usually one track).

    The verdict logic:
      * 1 component covering every judge          -> identifiable
      * a few components, most judges in the big one -> partially identifiable
      * ~1 component per judge                    -> not identifiable
    """
    n_evals = len(df)
    n_judges = df["judge_id"].nunique()
    n_projects = df["project_id"].nunique()

    g = build_graph(df)
    components = list(nx.connected_components(g))
    n_components = len(components)

    # How many judges live in each component, and how big is the biggest island?
    judges_per_component = [
        sum(1 for node in comp if node.startswith(JUDGE_PREFIX)) for comp in components
    ]
    judges_per_component.sort(reverse=True)
    largest_judge_block = judges_per_component[0] if judges_per_component else 0
    isolated_judges = sum(1 for c in judges_per_component if c == 1)

    overlap = judge_pair_overlap(df)

    evals_per_judge = df.groupby("judge_id").size()
    # Count judges per (project, track): two judges on the same project but in
    # different tracks are NOT two opinions on the same thing, so they must not
    # be counted as a double-scored project.
    evals_per_project = df.groupby(["project_id", "track"]).size()

    # --- verdict ---------------------------------------------------------
    # "Connected" is the formal requirement. We soften it into three tiers so
    # the report can say something more useful than pass/fail.
    connected = n_components == 1 and n_judges > 1
    frac_in_largest = (largest_judge_block / n_judges) if n_judges else 0.0

    if n_judges <= 1:
        verdict = "N/A"
        reason = (
            f"only {n_judges} judge in this track -- there is no second judge to "
            "compare against, so leniency has no meaning here"
        )
    elif connected:
        verdict = "IDENTIFIABLE"
        reason = (
            f"all {n_judges} judges sit in one connected component, so every judge is "
            f"linked to every other judge through shared projects; leniency can be "
            f"estimated on a common scale"
        )
    elif frac_in_largest >= 0.6:
        verdict = "PARTIALLY IDENTIFIABLE"
        reason = (
            f"the graph splits into {n_components} components, but {largest_judge_block} "
            f"of {n_judges} judges ({100 * frac_in_largest:.0f}%) are in the largest one; "
            f"leniency is comparable inside that block only, and the remaining judges "
            f"float free"
        )
    else:
        verdict = "NOT IDENTIFIABLE"
        reason = (
            f"the graph shatters into {n_components} components across {n_judges} judges "
            f"({isolated_judges} judges share no project with anyone). Judge harshness and "
            f"project quality are confounded: a low score cannot be attributed to a strict "
            f"judge rather than a weak project, because nothing links the islands together"
        )

    return {
        "track": track_name,
        "n_evals": n_evals,
        "n_judges": n_judges,
        "n_projects": n_projects,
        "n_components": n_components,
        "largest_component_judges": largest_judge_block,
        "isolated_judges": isolated_judges,
        "frac_judges_in_largest": frac_in_largest,
        "connected": connected,
        "overlap_pct": overlap["overlap_pct"],
        "overlapping_pairs": overlap["overlapping_pairs"],
        "same_rubric_pairs": overlap["same_rubric_pairs"],
        "same_rubric_pct": overlap["same_rubric_pct"],
        "cross_rubric_pairs": overlap["cross_rubric_pairs"],
        "total_pairs": overlap["total_pairs"],
        "mean_shared_when_overlapping": overlap["mean_shared_when_overlapping"],
        "median_evals_per_judge": float(evals_per_judge.median()) if n_judges else 0.0,
        "min_evals_per_judge": int(evals_per_judge.min()) if n_judges else 0,
        "n_project_slots": int(len(evals_per_project)),
        "median_evals_per_project": float(evals_per_project.median()) if n_projects else 0.0,
        "projects_with_one_judge": int((evals_per_project == 1).sum()),
        "verdict": verdict,
        "reason": reason,
    }


def diagnose(df: pd.DataFrame, by_track: bool = True) -> pd.DataFrame:
    """
    Run diagnose_track over the whole dataset and each track. Returns a tidy
    DataFrame, one row per slice, with the pooled "(all tracks)" row first.
    """
    rows = [diagnose_track(df, "(all tracks)")]
    if by_track:
        for track, sub in df.groupby("track"):
            rows.append(diagnose_track(sub, str(track)))
    return pd.DataFrame(rows)


def format_report(diag: pd.DataFrame) -> str:
    """Plain-language rendering, written for readers who do not do statistics."""
    lines = []
    lines.append("=" * 78)
    lines.append("IDENTIFIABILITY CHECK -- can we measure judge leniency at all?")
    lines.append("=" * 78)
    lines.append("")
    lines.append(
        "A judge's leniency is only measurable if their projects overlap with other\n"
        "judges' projects. If two judges never scored the same project, there is no\n"
        "way to tell a harsh judge from a weak set of projects. The check below asks,\n"
        "per track, whether the judges are linked together by shared projects."
    )
    lines.append("")

    for _, r in diag.iterrows():
        lines.append("-" * 78)
        lines.append(f"TRACK: {r['track']}")
        lines.append(
            f"  {r['n_evals']} evaluations | {r['n_judges']} judges | {r['n_projects']} projects"
        )
        lines.append(
            f"  judge pairs sharing >=1 project: {r['overlapping_pairs']}/{r['total_pairs']}"
            f" ({r['overlap_pct']:.1f}%)"
        )
        lines.append(
            f"  ...of which USABLE (same project AND same rubric):"
            f" {r['same_rubric_pairs']} ({r['same_rubric_pct']:.1f}%)"
            + (
                f"   [{r['cross_rubric_pairs']} pairs share a project but scored it on"
                f" different tracks' criteria -- not comparable]"
                if r["cross_rubric_pairs"]
                else ""
            )
        )
        lines.append(
            f"  connected islands in judge-project graph: {r['n_components']}"
            f"  (largest holds {r['largest_component_judges']} of {r['n_judges']} judges;"
            f" {r['isolated_judges']} judges are alone)"
        )
        lines.append(
            f"  evaluations per judge: median {r['median_evals_per_judge']:.0f},"
            f" min {r['min_evals_per_judge']}"
        )
        lines.append(
            f"  project-track slots scored by only ONE judge:"
            f" {r['projects_with_one_judge']} of {r['n_project_slots']}"
        )
        lines.append("")
        lines.append(f"  >> VERDICT: normalization is {_verdict_phrase(r['verdict'])}"
                     f" for track '{r['track']}'")
        lines.append(f"     because {r['reason']}.")
        lines.append("")

    return "\n".join(lines)


def _verdict_phrase(verdict: str) -> str:
    return {
        "IDENTIFIABLE": "STATISTICALLY SUPPORTED",
        "PARTIALLY IDENTIFIABLE": "ONLY PARTIALLY SUPPORTED",
        "NOT IDENTIFIABLE": "NOT STATISTICALLY SUPPORTED",
        "N/A": "NOT APPLICABLE",
    }.get(verdict, verdict)


def assert_or_warn(diag: pd.DataFrame, raise_on_fail: bool = False) -> list[str]:
    """
    Emit warnings for every track that cannot support normalization.

    Call this before fitting. Set raise_on_fail=True in any automated pipeline
    that must not publish normalized rankings from an unidentifiable design.
    """
    import warnings

    problems = []
    for _, r in diag.iterrows():
        if r["verdict"] in ("NOT IDENTIFIABLE", "PARTIALLY IDENTIFIABLE"):
            msg = (
                f"[identifiability] track '{r['track']}': {r['verdict']} -- {r['reason']}"
            )
            problems.append(msg)
            warnings.warn(msg, stacklevel=2)
    if problems and raise_on_fail:
        raise ValueError(
            "Normalization is not statistically supported by this assignment design:\n"
            + "\n".join(problems)
        )
    return problems


if __name__ == "__main__":
    from pipeline.normalization.data import load_evaluations

    df, notes = load_evaluations()
    print(notes)
    print(format_report(diagnose(df)))
