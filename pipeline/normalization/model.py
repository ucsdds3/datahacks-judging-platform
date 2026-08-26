"""
Mixed-effects normalization model:  score ~ 1 + (1|judge) + (1|project)

What this model says, in words
------------------------------
Every score is treated as three things added together:

    score  =  grand mean  +  how generous this judge is  +  how good this project is  +  noise

The two middle terms are "random effects". Calling them random rather than fixed
buys us **shrinkage**, which is the whole point of using this model instead of
just subtracting each judge's average:

    A judge who scored 12 projects and averaged 8 points above everyone else is
    probably genuinely generous -- we trust that estimate and apply most of it.

    A judge who scored 1 project and happened to give it 48/50 might be generous,
    or might have judged one great project. With a single data point we cannot
    tell, so the model pulls their estimated leniency most of the way back to
    zero. It only "believes" a judge in proportion to how much evidence exists.

The amount of pull is decided by the data itself: the model estimates how much
judges genuinely vary (tau_judge) versus how noisy individual scores are
(sigma). If judges barely differ, everyone gets pulled hard toward the mean.

A project's adjusted score is then:  grand mean + that project's estimated
quality -- which is the score the project would be expected to get from an
average-strictness judge.

Implementation note for the JS folks
------------------------------------
R and Python's lme4/statsmodels differ here. statsmodels' MixedLM is built for
*nested* grouping, but judges and projects are **crossed** (any judge could in
principle see any project). The standard statsmodels idiom for crossed effects
is to put every row in one artificial group and declare judge and project as
"variance components" inside it. That is what `_fit_mixedlm` does.

IMPORTANT: run identifiability.py first
(python -m pipeline.normalization.identifiability). This model will return
numbers on a disconnected design, but on a disconnected design those numbers
encode an assumption ("every island of judges saw equally good projects on
average") rather than a measurement.

This module also self-reports two failure modes in `NormalizationResult.notes`:
`saturated` (residual SD collapsed to ~0, so the standard errors are fiction)
and `no_replication` (nearly every project has a single score, so the
judge/project split is assumed rather than measured). Check those notes before
quoting any adjusted score.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

try:
    import statsmodels.formula.api as smf

    HAVE_STATSMODELS = True
except ImportError:  # pragma: no cover - dependency is in requirements.txt
    HAVE_STATSMODELS = False


@dataclass
class NormalizationResult:
    """Everything the fit produced, in three tidy tables plus diagnostics."""

    projects: pd.DataFrame  # project_id, track, raw_mean, adjusted_score, n_judges, se
    judges: pd.DataFrame  # judge_id, track, leniency, n_evals, shrinkage, raw_mean
    fits: list = field(default_factory=list)  # one dict per fitted slice
    notes: list = field(default_factory=list)

    def summary(self) -> str:
        out = []
        for f in self.fits:
            out.append(
                f"  {f['slice']:<36} n={f['n']:>4}  method={f['method']:<14} "
                f"tau_judge={f['tau_judge']:.2f}  tau_project={f['tau_project']:.2f}  "
                f"sigma={f['sigma']:.2f}  converged={f['converged']}"
            )
        return "\n".join(out)


# --------------------------------------------------------------------------
# core fitting
# --------------------------------------------------------------------------


def _bare_level(s: str) -> str:
    """'C(judge_id)[abc123]' -> 'abc123'; anything already bare passes through."""
    m = re.match(r"^C\([^)]*\)\[(?:T\.)?(.*)\]$", s)
    return m.group(1) if m else s


def _fit_mixedlm(df: pd.DataFrame) -> Optional[dict]:
    """
    Fit score ~ 1 + (1|judge) + (1|project) with statsmodels MixedLM.

    Returns None if statsmodels cannot fit this slice at all (too few rows, a
    singular design, or an outright numerical failure). Callers fall back to the
    closed-form estimator below, which always returns something sane.
    """
    if not HAVE_STATSMODELS or len(df) < 4:
        return None

    d = df.copy()
    d["_grp"] = 1  # single artificial group -> crossed variance components

    # "0 + C(x)" means: one random intercept per level of x, no extra intercept.
    vc = {"judge": "0 + C(judge_id)", "project": "0 + C(project_id)"}

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            md = smf.mixedlm(
                "total_score ~ 1",
                data=d,
                groups=d["_grp"],
                vc_formula=vc,
                re_formula="0",  # no group-level intercept beyond the components
            )
            fit = md.fit(reml=True, method=["lbfgs", "bfgs"], maxiter=2000)
    except Exception:
        return None

    # Variance components come back as variances scaled by sigma^2 in
    # statsmodels' internal parameterisation; cov_re is already on the data scale.
    sigma2 = float(fit.scale)
    vcomp = np.asarray(fit.vcomp, dtype=float)
    tau_judge = float(np.sqrt(max(vcomp[0], 0.0))) if len(vcomp) > 0 else 0.0
    tau_project = float(np.sqrt(max(vcomp[1], 0.0))) if len(vcomp) > 1 else 0.0

    # BLUPs: random_effects is keyed by group (we only have group "1"), and the
    # Series index carries names like "judge[abc]" / "project[xyz]".
    try:
        re = fit.random_effects[1]
    except Exception:
        return None

    # statsmodels names these like "judge[C(judge_id)[<uid>]]" -- peel off both
    # the variance-component prefix and the patsy C(...) wrapper to recover the
    # bare id, otherwise every downstream join silently misses.
    judge_eff, project_eff = {}, {}
    for name, val in re.items():
        s = str(name)
        if s.startswith("judge["):
            judge_eff[_bare_level(s[len("judge[") : -1])] = float(val)
        elif s.startswith("project["):
            project_eff[_bare_level(s[len("project[") : -1])] = float(val)

    if not judge_eff and not project_eff:
        return None

    return {
        "method": "MixedLM(REML)",
        "intercept": float(fit.fe_params.iloc[0]),
        "sigma": float(np.sqrt(sigma2)),
        "tau_judge": tau_judge,
        "tau_project": tau_project,
        "judge_effects": judge_eff,
        "project_effects": project_eff,
        "converged": bool(getattr(fit, "converged", False)),
    }


def _fit_alternating(df: pd.DataFrame, max_iter: int = 200, tol: float = 1e-8) -> dict:
    """
    Closed-form fallback: the same model fitted by alternating shrunk means.

    This is a simple EM-flavoured loop that always converges and never throws,
    so the pipeline still produces output when MixedLM gives up on a tiny or
    degenerate slice. It applies exactly the same shrinkage logic:

        judge effect = (sum of that judge's residuals) / (n_j + lambda_judge)

    The +lambda in the denominator is the shrinkage. lambda = sigma^2 / tau^2,
    so a judge with few evaluations (small n_j) gets pulled hard toward 0, and a
    judge with many gets pulled barely at all. Variance components are estimated
    by simple method-of-moments and re-estimated each pass.
    """
    y = df["total_score"].to_numpy(dtype=float)
    j_codes, j_levels = pd.factorize(df["judge_id"])
    p_codes, p_levels = pd.factorize(df["project_id"])
    n = len(y)

    mu = float(y.mean())
    a = np.zeros(len(j_levels))  # judge effects
    b = np.zeros(len(p_levels))  # project effects

    j_counts = np.bincount(j_codes, minlength=len(j_levels)).astype(float)
    p_counts = np.bincount(p_codes, minlength=len(p_levels)).astype(float)

    # Start with a rough variance split: assume judges and projects each explain
    # a share of the total spread, remainder is noise.
    total_var = float(y.var(ddof=1)) if n > 1 else 1.0
    tau_j2 = max(total_var / 3.0, 1e-6)
    tau_p2 = max(total_var / 3.0, 1e-6)
    sigma2 = max(total_var / 3.0, 1e-6)

    for _ in range(max_iter):
        prev = (mu, a.copy(), b.copy())

        resid = y - mu - b[p_codes]
        a = np.bincount(j_codes, weights=resid, minlength=len(j_levels)) / (
            j_counts + sigma2 / tau_j2
        )

        resid = y - mu - a[j_codes]
        b = np.bincount(p_codes, weights=resid, minlength=len(p_levels)) / (
            p_counts + sigma2 / tau_p2
        )

        mu = float(np.mean(y - a[j_codes] - b[p_codes]))

        e = y - mu - a[j_codes] - b[p_codes]
        sigma2 = max(float(np.sum(e**2) / max(n - 1, 1)), 1e-6)
        tau_j2 = max(float(np.sum(a**2) / max(len(a), 1)), 1e-6)
        tau_p2 = max(float(np.sum(b**2) / max(len(b), 1)), 1e-6)

        if (
            abs(prev[0] - mu) < tol
            and np.max(np.abs(prev[1] - a)) < tol
            and np.max(np.abs(prev[2] - b)) < tol
        ):
            break

    return {
        "method": "alternating-shrunk",
        "intercept": mu,
        "sigma": float(np.sqrt(sigma2)),
        "tau_judge": float(np.sqrt(tau_j2)),
        "tau_project": float(np.sqrt(tau_p2)),
        "judge_effects": {str(k): float(v) for k, v in zip(j_levels, a)},
        "project_effects": {str(k): float(v) for k, v in zip(p_levels, b)},
        "converged": True,
    }


def _posterior_sd(tau: float, sigma: float, n: int) -> float:
    """
    Approximate uncertainty on one random effect.

    Standard random-intercept result: after seeing n observations, the remaining
    uncertainty about a group's effect is

        sd = sqrt( tau^2 * sigma^2 / (n * tau^2 + sigma^2) )

    Exact for a one-way model, approximate here because judge and project
    effects are estimated jointly. Read it as an honest order-of-magnitude, not
    a publication-grade confidence interval. Note it never exceeds tau -- with
    zero information you fall back to the prior spread.
    """
    if tau <= 0:
        return 0.0
    if n <= 0:
        return float(tau)
    return float(np.sqrt((tau**2 * sigma**2) / (n * tau**2 + sigma**2)))


def _shrinkage_factor(tau: float, sigma: float, n: int) -> float:
    """
    How much of the raw observed deviation actually survives into the estimate.
    1.0 = fully trusted, 0.0 = entirely pulled back to the grand mean.
    """
    if tau <= 0 or n <= 0:
        return 0.0
    return float((n * tau**2) / (n * tau**2 + sigma**2))


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------


def fit_slice(df: pd.DataFrame, slice_name: str = "(all)") -> dict:
    """Fit one slice, preferring MixedLM and falling back if it fails."""
    res = _fit_mixedlm(df)
    fell_back = False
    if res is None or not res["converged"]:
        alt = _fit_alternating(df)
        if res is None:
            res, fell_back = alt, True
        else:
            # MixedLM ran but did not converge -- keep its estimates only if they
            # are finite and not degenerate, otherwise take the stable fallback.
            bad = (
                not np.isfinite(res["sigma"])
                or res["sigma"] <= 0
                or (res["tau_judge"] == 0 and res["tau_project"] == 0)
            )
            if bad:
                res, fell_back = alt, True
            else:
                res["method"] = "MixedLM(no-converge)"
    res["slice"] = slice_name
    res["n"] = len(df)
    res["fell_back"] = fell_back

    # Saturation check. On a 0-50 scale a residual SD below ~1.5 points means the
    # model has explained essentially every observation exactly -- which happens
    # when almost every project was scored by exactly one judge, so a project
    # effect can absorb its single score perfectly. The fit "succeeds" and reports
    # tiny standard errors, but it has learned nothing: there is no replication to
    # separate signal from noise. Flag it rather than let the false precision through.
    n_single = int((df.groupby("project_id").size() == 1).sum())
    n_proj = int(df["project_id"].nunique())
    res["frac_single_scored"] = (n_single / n_proj) if n_proj else 0.0

    # Two distinct pathologies, tracked separately because they need different
    # explanations to the reader:
    #   saturated   -- residual SD collapsed toward 0; the model reproduced each
    #                  observation exactly and its standard errors are fiction.
    #   no_replication -- almost every project has one score, so nothing pins
    #                  down the judge/project split regardless of what sigma is.
    res["saturated"] = bool(res["sigma"] < 1.5)
    res["no_replication"] = bool(res["frac_single_scored"] > 0.9)
    res["degenerate"] = bool(res["saturated"] or res["no_replication"])
    return res


def fit_normalization(
    df: pd.DataFrame,
    by_track: bool = True,
    min_rows_per_track: int = 12,
    min_judges_per_track: int = 3,
) -> NormalizationResult:
    """
    Main entry point.

    Input: DataFrame with columns judge_id, project_id, track, total_score.
    Output: NormalizationResult with per-project adjusted scores, per-judge
            leniency, and uncertainty on both.

    Fitting strategy: one model per track where the track has enough data
    (`min_rows_per_track` rows and `min_judges_per_track` judges), because each
    track has its own rubric and its own difficulty level, so their scores are
    not on a common scale. Thin tracks fall back to a single pooled model fitted
    on all tracks at once -- less appropriate, but better than fitting a model to
    9 rows from 1 judge.
    """
    required = {"judge_id", "project_id", "track", "total_score"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")

    df = df.dropna(subset=["judge_id", "project_id", "total_score"]).copy()

    notes = []
    fits = []
    project_rows = []
    judge_rows = []

    # Decide which tracks get their own model.
    if by_track:
        sizes = df.groupby("track").agg(n=("total_score", "size"), j=("judge_id", "nunique"))
        own_model = sizes[
            (sizes["n"] >= min_rows_per_track) & (sizes["j"] >= min_judges_per_track)
        ].index.tolist()
        pooled_tracks = [t for t in sizes.index if t not in own_model]
    else:
        own_model, pooled_tracks = [], list(df["track"].dropna().unique())

    slices = [(str(t), df[df["track"] == t], str(t)) for t in own_model]
    if pooled_tracks:
        pooled_df = df[df["track"].isin(pooled_tracks)]
        if len(pooled_df):
            slices.append(("POOLED:" + ",".join(map(str, pooled_tracks)), pooled_df, None))
            notes.append(
                f"tracks {pooled_tracks} were too small for their own model "
                f"(<{min_rows_per_track} evaluations or <{min_judges_per_track} judges); "
                f"they were fitted together in one pooled model"
            )

    for slice_name, sub, track_label in slices:
        res = fit_slice(sub, slice_name)
        fits.append(res)
        if res["fell_back"]:
            notes.append(
                f"slice '{slice_name}': statsmodels MixedLM failed or degenerated; "
                f"used the closed-form alternating-shrinkage fallback instead"
            )
        elif res["method"] == "MixedLM(no-converge)":
            notes.append(
                f"slice '{slice_name}': MixedLM did NOT report convergence -- "
                f"estimates are usable but should be treated as approximate"
            )
        if res.get("saturated"):
            notes.append(
                f"slice '{slice_name}': SATURATED FIT -- residual SD collapsed to "
                f"{res['sigma']:.2f} points out of 50. The model reproduced almost every "
                f"score exactly, which it can only do because there is no second opinion "
                f"to disagree with. Its standard errors are far too small to believe"
            )
        if res.get("no_replication"):
            notes.append(
                f"slice '{slice_name}': NO REPLICATION -- "
                f"{100 * res['frac_single_scored']:.0f}% of projects were scored by exactly "
                f"one judge, so the split between 'harsh judge' and 'weak project' is not "
                f"pinned down by the data. tau_judge={res['tau_judge']:.2f} vs "
                f"tau_project={res['tau_project']:.2f} here is an assumption, not a measurement"
            )

        mu = res["intercept"]
        tau_j, tau_p, sigma = res["tau_judge"], res["tau_project"], res["sigma"]

        n_by_judge = sub.groupby("judge_id").size().to_dict()
        n_by_project = sub.groupby("project_id").size().to_dict()
        raw_judge = sub.groupby("judge_id")["total_score"].mean().to_dict()

        for jid, eff in res["judge_effects"].items():
            n = int(n_by_judge.get(jid, 0))
            judge_rows.append(
                {
                    "judge_id": jid,
                    "slice": slice_name,
                    "n_evals": n,
                    "raw_mean": raw_judge.get(jid, np.nan),
                    "leniency": eff,
                    "leniency_se": _posterior_sd(tau_j, sigma, n),
                    "shrinkage": _shrinkage_factor(tau_j, sigma, n),
                }
            )

        # A project may appear in two tracks; keep track on the row so the two
        # entries stay distinct rather than colliding.
        for pid, eff in res["project_effects"].items():
            psub = sub[sub["project_id"] == pid]
            n = int(n_by_project.get(pid, 0))
            project_rows.append(
                {
                    "project_id": pid,
                    "track": track_label if track_label else _mode(psub["track"]),
                    "slice": slice_name,
                    "n_judges": n,
                    "raw_mean": float(psub["total_score"].mean()) if len(psub) else np.nan,
                    "adjusted_score": mu + eff,
                    "project_effect": eff,
                    "adjusted_se": _posterior_sd(tau_p, sigma, n),
                    "shrinkage": _shrinkage_factor(tau_p, sigma, n),
                }
            )

    projects = pd.DataFrame(project_rows)
    judges = pd.DataFrame(judge_rows)

    if len(projects):
        # An adjusted score outside 0-50 is impossible for a real submission --
        # the rubric caps at 50. When it happens it is a symptom of the model
        # over-extrapolating a lone score through a large judge correction, so
        # surface it rather than quietly clipping.
        projects["out_of_range"] = (projects["adjusted_score"] > 50.0) | (
            projects["adjusted_score"] < 0.0
        )
        n_oor = int(projects["out_of_range"].sum())
        if n_oor:
            notes.append(
                f"{n_oor} project(s) received an adjusted score outside the valid 0-50 "
                f"range (max {projects['adjusted_score'].max():.1f}). This is impossible "
                f"for a real score and indicates the model extrapolating beyond its data"
            )
        projects = projects.sort_values("adjusted_score", ascending=False).reset_index(drop=True)
    if len(judges):
        judges = judges.sort_values("leniency", ascending=False).reset_index(drop=True)

    return NormalizationResult(projects=projects, judges=judges, fits=fits, notes=notes)


def _mode(s: pd.Series):
    m = s.mode()
    return m.iloc[0] if len(m) else None


if __name__ == "__main__":
    from pipeline.normalization.data import load_evaluations

    df, notes = load_evaluations()
    print(notes)
    result = fit_normalization(df)
    print(result.summary())
    for n in result.notes:
        print("NOTE:", n)
    print(result.projects.head(10).to_string())
    print(result.judges.head(10).to_string())
