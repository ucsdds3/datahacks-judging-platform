"""
Tests for the normalization pipeline.

Run from the repo root:  python -m pytest pipeline/normalization/test_model.py -v

These tests deliberately do NOT touch Firestore. They run on synthetic data
with known ground truth, so they are fast, offline, and can actually assert
correctness -- which is impossible against real data where the truth is unknown.

The two headline tests are:
  * test_recovers_planted_leniency_connected_design -- the model works
  * test_flags_disconnected_design                  -- the diagnostic works
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline.normalization.identifiability import diagnose_track, judge_pair_overlap
from pipeline.normalization.model import _shrinkage_factor, fit_normalization
from pipeline.normalization.simulate import evaluate_recovery, simulate_design, sweep_anchors

# --------------------------------------------------------------------------
# the model recovers what we plant
# --------------------------------------------------------------------------


def test_recovers_planted_leniency_connected_design():
    """
    THE headline test. Plant known judge leniency in a well-connected design
    (3 judges per project + 3 anchors) and check the model finds it.

    Tolerance: true leniency has SD 5 points, so we require the typical error to
    be under 2 points -- comfortably better than the ~5 you would get from
    guessing zero for everyone, but loose enough not to be flaky across seeds.
    """
    evals, tj, tp = simulate_design(
        n_judges=20, n_projects=60, judges_per_project=3, n_anchors=3, seed=42
    )
    m = evaluate_recovery(evals, tj, tp)

    assert m["leniency_rmse"] < 2.0, f"leniency RMSE too high: {m['leniency_rmse']:.2f}"
    assert m["leniency_corr"] > 0.90, f"leniency correlation too low: {m['leniency_corr']:.3f}"
    # And the recovered project ranking should track true quality closely.
    assert m["rank_spearman"] > 0.85, f"ranking correlation too low: {m['rank_spearman']:.3f}"


def test_normalized_ranking_beats_raw_under_good_design():
    """
    Normalization has to actually buy something. Under a connected design the
    normalized ranking should match true quality better than raw averages do.
    Averaged over several seeds so one unlucky draw cannot fail the suite.
    """
    gains = []
    for seed in range(5):
        evals, tj, tp = simulate_design(
            n_judges=20, n_projects=60, judges_per_project=3, n_anchors=3, seed=seed
        )
        gains.append(evaluate_recovery(evals, tj, tp)["rank_gain"])
    assert np.mean(gains) > 0.0, f"normalization did not improve ranking: mean gain {np.mean(gains)}"


def test_recovers_project_quality():
    """Project effects should track planted quality, not just judge effects."""
    evals, tj, tp = simulate_design(
        n_judges=20, n_projects=60, judges_per_project=3, n_anchors=3, seed=7
    )
    m = evaluate_recovery(evals, tj, tp)
    # True quality SD is 6 points; recovery error should be well under that.
    assert m["quality_rmse"] < 4.0, f"quality RMSE too high: {m['quality_rmse']:.2f}"


# --------------------------------------------------------------------------
# identifiability diagnostics
# --------------------------------------------------------------------------


def _disconnected_frame() -> pd.DataFrame:
    """
    The 2026 failure mode in miniature: every judge scores their own private set
    of projects, no project is ever seen twice. Should be flagged immediately.
    """
    rows = []
    for j in range(6):
        for p in range(4):
            rows.append(
                {
                    "judge_id": f"J{j}",
                    "project_id": f"P{j}_{p}",  # unique per judge -> no sharing
                    "track": "T",
                    "total_score": 30.0 + j * 2 + p,
                }
            )
    return pd.DataFrame(rows)


def test_flags_disconnected_design():
    """THE other headline test: a fully disconnected design must be caught."""
    diag = diagnose_track(_disconnected_frame(), "T")

    assert diag["verdict"] == "NOT IDENTIFIABLE"
    assert diag["connected"] is False
    assert diag["n_components"] == 6, "each judge should form their own island"
    assert diag["isolated_judges"] == 6
    assert diag["same_rubric_pairs"] == 0
    assert "confounded" in diag["reason"]


def test_flags_connected_design_as_identifiable():
    """The mirror case: a connected design must NOT be flagged."""
    evals, _, _ = simulate_design(
        n_judges=8, n_projects=20, judges_per_project=3, n_anchors=2, seed=3
    )
    diag = diagnose_track(evals, "SIM")

    assert diag["verdict"] == "IDENTIFIABLE"
    assert diag["connected"] is True
    assert diag["n_components"] == 1


def test_anchors_repair_a_disconnected_design():
    """
    One anchor project scored by every judge should collapse N islands into 1.
    This is the mechanism the whole recommendation rests on, so assert it directly.
    """
    before = diagnose_track(
        simulate_design(n_judges=10, n_projects=30, judges_per_project=1, n_anchors=0, seed=5)[0],
        "no-anchor",
    )
    after = diagnose_track(
        simulate_design(n_judges=10, n_projects=30, judges_per_project=1, n_anchors=1, seed=5)[0],
        "one-anchor",
    )

    assert before["n_components"] > 1 and before["verdict"] == "NOT IDENTIFIABLE"
    assert after["n_components"] == 1 and after["verdict"] == "IDENTIFIABLE"


def test_cross_rubric_overlap_is_not_counted_as_usable():
    """
    Regression test for the subtlest bug in this pipeline. Two judges scoring the
    same project under DIFFERENT tracks share a project id but not a measuring
    stick. That must count as raw overlap but NOT as usable same-rubric overlap.
    """
    df = pd.DataFrame(
        [
            {"judge_id": "A", "project_id": "P1", "track": "AI/ML", "total_score": 40.0},
            {"judge_id": "B", "project_id": "P1", "track": "Cloud", "total_score": 30.0},
        ]
    )
    ov = judge_pair_overlap(df)

    assert ov["overlapping_pairs"] == 1, "they do share a project id"
    assert ov["same_rubric_pairs"] == 0, "but not on the same rubric -- unusable"
    assert ov["cross_rubric_pairs"] == 1


# --------------------------------------------------------------------------
# shrinkage behaviour
# --------------------------------------------------------------------------


def test_shrinkage_increases_with_evidence():
    """A judge with more evaluations should be trusted more."""
    factors = [_shrinkage_factor(tau=5.0, sigma=4.0, n=n) for n in (1, 2, 5, 20)]
    assert factors == sorted(factors), "shrinkage must increase monotonically with n"
    assert factors[0] < 0.7, "a single evaluation should be heavily shrunk"
    assert factors[-1] > 0.9, "20 evaluations should be largely trusted"


def test_low_evidence_judge_is_pulled_toward_mean():
    """
    End-to-end shrinkage check: a judge who scores ONE project very generously
    should have their estimated leniency pulled well below the raw gap, because
    one score is not enough evidence to justify the full correction.
    """
    evals, tj, tp = simulate_design(
        n_judges=12, n_projects=40, judges_per_project=3, n_anchors=2, seed=11
    )
    # Add a brand-new judge with a single, wildly generous score.
    evals = pd.concat(
        [
            evals,
            pd.DataFrame(
                [{"judge_id": "NEWBIE", "project_id": "P000", "track": "SIM", "total_score": 50.0}]
            ),
        ],
        ignore_index=True,
    )
    res = fit_normalization(evals, by_track=False)
    row = res.judges[res.judges["judge_id"] == "NEWBIE"].iloc[0]

    raw_gap = 50.0 - evals["total_score"].mean()
    assert row["n_evals"] == 1

    # The substantive property is RELATIVE: the 1-evaluation judge must be
    # trusted strictly less than the well-observed judges. An absolute threshold
    # would be arbitrary, because how much a single score is worth legitimately
    # depends on the fitted tau/sigma ratio.
    established = res.judges[res.judges["n_evals"] >= 5]
    assert len(established) > 0
    assert row["shrinkage"] < established["shrinkage"].min(), (
        "the 1-evaluation judge must be shrunk harder than every well-observed judge"
    )
    assert row["leniency_se"] > established["leniency_se"].max(), (
        "the 1-evaluation judge must carry the largest uncertainty"
    )
    assert abs(row["leniency"]) < abs(raw_gap), "estimate must be pulled toward the mean"


# --------------------------------------------------------------------------
# API / robustness
# --------------------------------------------------------------------------


def test_fit_returns_expected_columns():
    evals, _, _ = simulate_design(n_judges=8, n_projects=20, seed=1)
    res = fit_normalization(evals, by_track=False)

    for col in ("project_id", "adjusted_score", "n_judges", "adjusted_se"):
        assert col in res.projects.columns
    for col in ("judge_id", "leniency", "n_evals", "shrinkage", "leniency_se"):
        assert col in res.judges.columns
    assert len(res.fits) == 1


def test_missing_columns_raise():
    bad = pd.DataFrame({"judge_id": ["A"], "project_id": ["P"]})
    with pytest.raises(ValueError, match="missing required columns"):
        fit_normalization(bad)


def test_tiny_slice_does_not_crash():
    """
    Degenerate input must fall back rather than throw -- the pipeline should
    always produce output, with warnings, instead of failing the whole run.
    """
    tiny = pd.DataFrame(
        [
            {"judge_id": "A", "project_id": "P1", "track": "T", "total_score": 40.0},
            {"judge_id": "A", "project_id": "P2", "track": "T", "total_score": 35.0},
            {"judge_id": "B", "project_id": "P3", "track": "T", "total_score": 30.0},
        ]
    )
    res = fit_normalization(tiny, by_track=False)
    assert len(res.projects) > 0
    assert np.isfinite(res.projects["adjusted_score"]).all()


def test_alternating_fallback_also_recovers_leniency():
    """
    The closed-form fallback is not just a crash guard -- it should give
    substantively similar answers to MixedLM. Verify it independently recovers
    planted leniency, so falling back does not silently degrade results.
    """
    from pipeline.normalization.model import _fit_alternating

    evals, tj, _ = simulate_design(
        n_judges=15, n_projects=45, judges_per_project=3, n_anchors=3, seed=21
    )
    fit = _fit_alternating(evals)

    est = pd.Series(fit["judge_effects"])
    truth = tj.set_index("judge_id")["true_leniency"]
    common = est.index.intersection(truth.index)
    est_c = est[common] - est[common].mean()
    true_c = truth[common] - truth[common].mean()

    rmse = float(np.sqrt(np.mean((est_c - true_c) ** 2)))
    assert rmse < 2.5, f"fallback estimator RMSE too high: {rmse:.2f}"


# --------------------------------------------------------------------------
# the sweep that drives the recommendation
# --------------------------------------------------------------------------


def test_more_anchors_help_when_coverage_is_one():
    """
    The recommendation claims anchors rescue a 1-judge-per-project design.
    Verify that claim holds: leniency error at 3 anchors must be clearly better
    than at 0 anchors when each project otherwise gets a single judge.
    """
    agg = sweep_anchors(
        anchor_levels=(0, 3),
        judges_per_project=1,
        n_reps=3,
        n_judges=12,
        n_projects=36,
        seed0=500,
    )
    err0 = float(agg[agg["n_anchors"] == 0]["leniency_rmse"].iloc[0])
    err3 = float(agg[agg["n_anchors"] == 3]["leniency_rmse"].iloc[0])

    assert err3 < err0, f"anchors did not reduce leniency error: {err0:.2f} -> {err3:.2f}"
    # And connectivity should be repaired outright.
    assert float(agg[agg["n_anchors"] == 3]["n_components"].iloc[0]) == 1.0
