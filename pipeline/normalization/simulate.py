"""
Does the model actually work, and how much judge overlap does it need?

The 2026 data cannot answer either question, because we never observe the truth:
we do not know how generous each judge really was, so we cannot check whether
the model recovered it. Simulation solves that. We *invent* judges with known
leniency and projects with known quality, generate scores from them, hide the
truth, fit the model, and measure how close it got.

This does two jobs:

  1. Proves the model is correct. If it cannot recover parameters we planted
     ourselves under a clean design, it is broken and nothing else matters.

  2. Answers the practical question for next year: how many **anchor projects**
     does the assignment need?

What is an anchor?
------------------
An anchor is a project deliberately assigned to many judges, purely so their
scores can be compared. If judges A and B both score the anchor, the gap between
their scores measures the gap in their generosity. Without anchors, judges score
disjoint project sets and leniency is unmeasurable (see identifiability.py).

The sweep below varies the anchor count from 0 upward and reports recovery
error at each level, so the team can pick a number rather than guess.

Metrics, in plain terms
-----------------------
  leniency_rmse    typical error, in points out of 50, in the estimated judge
                   leniency. Lower is better. Compare it to the true spread of
                   leniency (tau_judge): an RMSE as large as tau_judge means the
                   model learned nothing.
  leniency_corr    correlation between estimated and true leniency. 1.0 = perfect
                   ordering of judges from harsh to generous, 0 = noise.
  rank_spearman    correlation between the normalized project ranking and the
                   TRUE quality ranking -- the number the organizers actually
                   care about, since prizes follow the ranking.
  rank_vs_raw      the same, but for the naive raw-average ranking. If
                   rank_spearman is not clearly above rank_vs_raw, normalizing
                   bought you nothing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.normalization.model import fit_normalization

# Scales chosen to resemble the real DataHacks data: totals out of 50, typically
# in the high 30s / low 40s, with judges differing by several points.
DEFAULT_GRAND_MEAN = 38.0
DEFAULT_TAU_JUDGE = 5.0  # SD of true judge leniency, in points
DEFAULT_TAU_PROJECT = 6.0  # SD of true project quality, in points
DEFAULT_SIGMA = 3.0  # SD of per-score noise, in points


def simulate_design(
    n_judges: int = 20,
    n_projects: int = 60,
    judges_per_project: int = 3,
    n_anchors: int = 3,
    tau_judge: float = DEFAULT_TAU_JUDGE,
    tau_project: float = DEFAULT_TAU_PROJECT,
    sigma: float = DEFAULT_SIGMA,
    grand_mean: float = DEFAULT_GRAND_MEAN,
    seed: int = 0,
    clip: bool = True,
    round_scores: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Build a synthetic hackathon with known ground truth.

    Design: `n_projects` regular projects are dealt out so each gets
    `judges_per_project` judges, spreading the load evenly. On top of that,
    `n_anchors` extra projects are scored by EVERY judge -- these are the anchors
    that tie the judges onto a common scale.

    Returns (evaluations, true_judges, true_projects).
    """
    rng = np.random.default_rng(seed)

    judge_ids = [f"J{i:03d}" for i in range(n_judges)]
    project_ids = [f"P{i:03d}" for i in range(n_projects)]
    anchor_ids = [f"A{i:03d}" for i in range(n_anchors)]

    # Ground truth, centred at zero so the effects are deviations from the mean.
    true_leniency = dict(zip(judge_ids, rng.normal(0, tau_judge, n_judges)))
    all_projects = project_ids + anchor_ids
    true_quality = dict(zip(all_projects, rng.normal(0, tau_project, len(all_projects))))

    assignments = []

    # Regular projects: deal judges round-robin from a shuffled pool so the
    # workload stays balanced and no judge is starved.
    pool = []
    for pid in project_ids:
        if len(pool) < judges_per_project:
            order = judge_ids.copy()
            rng.shuffle(order)
            pool.extend(order)
        chosen, pool = pool[:judges_per_project], pool[judges_per_project:]
        # Guard against a judge being picked twice for one project.
        chosen = list(dict.fromkeys(chosen))
        while len(chosen) < judges_per_project:
            cand = rng.choice(judge_ids)
            if cand not in chosen:
                chosen.append(cand)
        for jid in chosen:
            assignments.append((jid, pid))

    # Anchors: everyone scores every anchor.
    for aid in anchor_ids:
        for jid in judge_ids:
            assignments.append((jid, aid))

    rows = []
    for jid, pid in assignments:
        score = (
            grand_mean + true_leniency[jid] + true_quality[pid] + rng.normal(0, sigma)
        )
        if clip:
            score = min(max(score, 5.0), 50.0)
        if round_scores:
            score = float(round(score))
        rows.append(
            {"judge_id": jid, "project_id": pid, "track": "SIM", "total_score": score}
        )

    evals = pd.DataFrame(rows)
    tj = pd.DataFrame(
        {"judge_id": judge_ids, "true_leniency": [true_leniency[j] for j in judge_ids]}
    )
    tp = pd.DataFrame(
        {
            "project_id": all_projects,
            "true_quality": [true_quality[p] for p in all_projects],
            "is_anchor": [p in set(anchor_ids) for p in all_projects],
        }
    )
    return evals, tj, tp


def evaluate_recovery(
    evals: pd.DataFrame, true_judges: pd.DataFrame, true_projects: pd.DataFrame
) -> dict:
    """
    Fit the model to simulated data and measure how well it recovered the truth.

    Both true and estimated effects are re-centred before comparison. Only
    *differences* between judges are identifiable -- adding 2 points to every
    judge and subtracting 2 from every project gives identical data -- so
    comparing raw levels would penalise the model for an arbitrary offset it
    was never able to determine.
    """
    from scipy.stats import pearsonr, spearmanr

    res = fit_normalization(evals, by_track=False)

    j = res.judges.merge(true_judges, on="judge_id", how="inner")
    j["est_c"] = j["leniency"] - j["leniency"].mean()
    j["true_c"] = j["true_leniency"] - j["true_leniency"].mean()

    leniency_rmse = float(np.sqrt(np.mean((j["est_c"] - j["true_c"]) ** 2)))
    leniency_corr = (
        float(pearsonr(j["est_c"], j["true_c"])[0]) if len(j) > 2 and j["est_c"].std() > 0 else np.nan
    )

    p = res.projects.merge(true_projects, on="project_id", how="inner")
    p["est_c"] = p["project_effect"] - p["project_effect"].mean()
    p["true_c"] = p["true_quality"] - p["true_quality"].mean()
    quality_rmse = float(np.sqrt(np.mean((p["est_c"] - p["true_c"]) ** 2)))

    raw = evals.groupby("project_id")["total_score"].mean().rename("raw_mean_score")
    p = p.merge(raw, on="project_id", how="left")

    rank_spearman = float(spearmanr(p["est_c"], p["true_c"])[0]) if len(p) > 2 else np.nan
    rank_vs_raw = (
        float(spearmanr(p["raw_mean_score"], p["true_c"])[0]) if len(p) > 2 else np.nan
    )

    # Record the connectivity of the design that produced these numbers, so the
    # sweep can show *why* recovery succeeds or fails rather than just that it did.
    from pipeline.normalization.identifiability import diagnose_track

    diag = diagnose_track(evals, "SIM")

    fit = res.fits[0] if res.fits else {}
    return {
        "n_evals": len(evals),
        "n_components": diag["n_components"],
        "connected": diag["connected"],
        "overlap_pct": diag["same_rubric_pct"],
        "leniency_rmse": leniency_rmse,
        "leniency_corr": leniency_corr,
        "quality_rmse": quality_rmse,
        "rank_spearman": rank_spearman,
        "rank_vs_raw": rank_vs_raw,
        "rank_gain": rank_spearman - rank_vs_raw,
        "method": fit.get("method", "?"),
        "converged": fit.get("converged", False),
        "tau_judge_hat": fit.get("tau_judge", np.nan),
        "tau_project_hat": fit.get("tau_project", np.nan),
        "sigma_hat": fit.get("sigma", np.nan),
    }


def sweep_anchors(
    anchor_levels=(0, 1, 2, 3, 5),
    coverage_levels=(1, 2, 3),
    n_reps: int = 8,
    n_judges: int = 20,
    n_projects: int = 60,
    judges_per_project: int | None = None,
    seed0: int = 1000,
    **kwargs,
) -> pd.DataFrame:
    """
    Sweep BOTH levers that control connectivity and average recovery over reps.

    Two levers matter, and conflating them hides the real lesson:

      * judges_per_project ("coverage") -- how many judges see each project. At
        3 judges per project, random assignment already creates a densely
        connected graph and anchors add almost nothing. At 1 judge per project
        -- the 2026 design -- there is zero natural overlap and anchors are the
        ONLY source of connectivity.

      * n_anchors -- extra projects every judge scores.

    So we cross them. `judges_per_project`, if given, pins coverage to a single
    value (used by the tests); otherwise the full grid in `coverage_levels` runs.

    Multiple repetitions matter: any single simulated hackathon could get lucky.
    """
    coverages = [judges_per_project] if judges_per_project is not None else list(coverage_levels)

    records = []
    for cov in coverages:
        for n_anchors in anchor_levels:
            for rep in range(n_reps):
                evals, tj, tp = simulate_design(
                    n_judges=n_judges,
                    n_projects=n_projects,
                    judges_per_project=cov,
                    n_anchors=n_anchors,
                    seed=seed0 + 971 * cov + 97 * n_anchors + rep,
                    **kwargs,
                )
                m = evaluate_recovery(evals, tj, tp)
                m["n_anchors"] = n_anchors
                m["judges_per_project"] = cov
                m["rep"] = rep
                records.append(m)

    df = pd.DataFrame(records)
    agg = (
        df.groupby(["judges_per_project", "n_anchors"])
        .agg(
            n_evals=("n_evals", "mean"),
            n_components=("n_components", "mean"),
            connected=("connected", "mean"),
            leniency_rmse=("leniency_rmse", "mean"),
            leniency_corr=("leniency_corr", "mean"),
            quality_rmse=("quality_rmse", "mean"),
            rank_spearman=("rank_spearman", "mean"),
            rank_vs_raw=("rank_vs_raw", "mean"),
            rank_gain=("rank_gain", "mean"),
            converged=("converged", "mean"),
        )
        .reset_index()
    )
    return agg


def format_sweep(agg: pd.DataFrame, tau_judge: float = DEFAULT_TAU_JUDGE) -> str:
    lines = []
    lines.append("=" * 78)
    lines.append("SIMULATION: how many anchor projects does normalization need?")
    lines.append("=" * 78)
    lines.append("")
    lines.append(
        f"Setup: 20 judges, 60 projects, plus N anchor projects that EVERY judge scores.\n"
        f"True judge leniency varies with SD = {tau_judge:.0f} points out of 50. We plant known\n"
        f"values, fit the model, and see what it recovers. Each row averages 8 simulated\n"
        f"events. 'cov' = judges per project."
    )
    lines.append("")
    lines.append(
        f"{'cov':>4} {'anchors':>7} {'evals':>6} {'islands':>8} {'leniency err':>13} "
        f"{'lenien corr':>12} {'rank vs truth':>14} {'raw rank':>9} {'gain':>7}"
    )
    lines.append("-" * 90)
    last_cov = None
    for _, r in agg.iterrows():
        if last_cov is not None and r["judges_per_project"] != last_cov:
            lines.append("")
        last_cov = r["judges_per_project"]
        lines.append(
            f"{int(r['judges_per_project']):>4} {int(r['n_anchors']):>7} {r['n_evals']:>6.0f} "
            f"{r['n_components']:>8.1f} {r['leniency_rmse']:>10.2f} pts "
            f"{r['leniency_corr']:>12.3f} {r['rank_spearman']:>14.3f} "
            f"{r['rank_vs_raw']:>9.3f} {r['rank_gain']:>+7.3f}"
        )
    lines.append("")
    lines.append(
        f"Reading this: 'leniency err' is the typical mistake in a judge's estimated\n"
        f"generosity. Judges truly differ by about {tau_judge:.0f} points (SD), so an error near\n"
        f"{tau_judge:.0f} means the model learned nothing; an error near 1 means it nailed it.\n"
        f"'islands' is the number of disconnected components -- 1 means every judge is\n"
        f"linked to every other. 'gain' is how much better the normalized ranking matches\n"
        f"true quality than the raw-average ranking does."
    )
    return "\n".join(lines)


def recommend_anchors(agg: pd.DataFrame, tau_judge: float = DEFAULT_TAU_JUDGE) -> str:
    """
    Turn the sweep into a single recommendation: the smallest anchor count that
    gets leniency error down to a third of the true spread AND makes the
    normalized ranking beat the raw ranking.
    """
    target = tau_judge / 3.0
    lines = ["=" * 78, "RECOMMENDATION FOR NEXT YEAR'S ASSIGNMENT DESIGN", "=" * 78, ""]

    # For each coverage level, find the cheapest anchor count that hits target.
    for cov, sub in agg.groupby("judges_per_project"):
        sub = sub.sort_values("n_anchors")
        ok = sub[(sub["leniency_rmse"] <= target) & (sub["rank_gain"] > 0)]
        if len(ok):
            r = ok.iloc[0]
            lines.append(
                f"  With {int(cov)} judge(s) per project: {int(r['n_anchors'])} anchor(s) is enough "
                f"-- leniency error {r['leniency_rmse']:.2f} pts "
                f"(target <={target:.2f}), ranking rho {r['rank_spearman']:.3f} "
                f"vs {r['rank_vs_raw']:.3f} raw."
            )
        else:
            b = sub.loc[sub["leniency_rmse"].idxmin()]
            lines.append(
                f"  With {int(cov)} judge(s) per project: NO tested anchor count reached the "
                f"target. Best was {int(b['n_anchors'])} anchors at {b['leniency_rmse']:.2f} pts "
                f"error (target <={target:.2f})."
            )

    # Two headlines, because "cheapest" depends on what you are optimising.
    # Estimating leniency well is NOT the same as ranking projects well: with 1
    # judge per project you can nail every judge's bias and still rank projects
    # poorly, because each project's quality still rests on a single noisy score.
    # Prizes follow the ranking, so that gets its own threshold.
    lines.append("")
    ok_all = agg[(agg["leniency_rmse"] <= target) & (agg["rank_gain"] > 0)]
    if len(ok_all):
        best = ok_all.loc[ok_all["n_evals"].idxmin()]
        lines.append(
            f"  CHEAPEST design that measures JUDGE LENIENCY well:\n"
            f"    {int(best['judges_per_project'])} judge(s)/project + "
            f"{int(best['n_anchors'])} anchor(s) = {best['n_evals']:.0f} evaluations, "
            f"leniency error {best['leniency_rmse']:.2f} pts.\n"
            f"    But its project ranking only reaches rho={best['rank_spearman']:.3f} -- "
            f"each project still has one score."
        )

    rank_target = 0.90
    ok_rank = agg[(agg["leniency_rmse"] <= target) & (agg["rank_spearman"] >= rank_target)]
    lines.append("")
    if len(ok_rank):
        b = ok_rank.loc[ok_rank["n_evals"].idxmin()]
        lines.append(
            f"  CHEAPEST design that also RANKS PROJECTS well (rho >= {rank_target:.2f}) "
            f"-- USE THIS ONE:\n"
            f"    {int(b['judges_per_project'])} judges/project + {int(b['n_anchors'])} anchor(s) "
            f"= {b['n_evals']:.0f} evaluations,\n"
            f"    leniency error {b['leniency_rmse']:.2f} pts, ranking rho {b['rank_spearman']:.3f} "
            f"vs {b['rank_vs_raw']:.3f} for raw averages."
        )
    else:
        lines.append(
            f"  No tested design reached ranking rho >= {rank_target:.2f}. "
            f"Increase judges per project."
        )
    lines.append("")
    lines.append(
        "  The non-obvious result: raising coverage from 1 to 2 judges per project does\n"
        "  more than adding anchors, because it fixes connectivity AND gives every project\n"
        "  a second opinion. Anchors only become the deciding factor when coverage is 1."
    )
    lines.append("")
    lines.append(
        "  PRACTICAL ADVICE: still require 1-2 anchors even at 2 judges/project. The rows\n"
        "  above reach 1 island at 0 anchors only because random assignment happened to\n"
        "  overlap; that is luck, not a guarantee, and a bad shuffle can still fragment the\n"
        "  graph. Anchors make connectivity structural instead of accidental, and 2 anchors\n"
        "  costs only ~2 extra evaluations per judge. Verify with identifiability.py BEFORE\n"
        "  judging starts -- once the event is over, a broken design cannot be repaired."
    )
    return "\n".join(lines)


if __name__ == "__main__":
    agg = sweep_anchors()
    print(format_sweep(agg))
    print()
    print(recommend_anchors(agg))
