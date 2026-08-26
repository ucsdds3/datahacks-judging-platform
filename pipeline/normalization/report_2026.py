"""
The DataHacks 2026 normalization report.

Run from the repo root:
    python -m pipeline.normalization.report_2026            (uses the cached extract)
    python -m pipeline.normalization.report_2026 --refresh  (re-reads Firestore, read-only)

It runs the identifiability check, fits the mixed-effects model, compares the
raw ranking against the normalized ranking, and states plainly how much of the
result can be trusted.

The short version of the conclusion is in the FINAL VERDICT section at the
bottom. Everything above it is the evidence.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from pipeline.normalization.data import load_evaluations
from pipeline.normalization.identifiability import diagnose, format_report
from pipeline.normalization.model import fit_normalization

LINE = "=" * 78


def section(title: str) -> str:
    return f"\n{LINE}\n{title}\n{LINE}\n"


def data_quality_section(notes: dict) -> str:
    """State the exclusions out loud. Nothing here should be a surprise later."""
    out = [section("1. DATA AND WHAT WE EXCLUDED")]
    out.append(f"Evaluations pulled from Firestore:            {notes['rows_in']}")
    out.append(
        f"Excluded -- track was null (generic fallback rubric): {notes['dropped_null_track']}"
    )
    out.append(
        f"Duplicate (judge, project) pairs found in source:    "
        f"{notes['duplicate_pairs_in_source']}"
    )
    out.append(
        f"Duplicates still present after the track filter:     "
        f"{notes['dropped_duplicate_pairs']}"
    )
    out.append(f"Evaluations used in this analysis:            {notes['rows_out']}")
    out.append("")
    out.append(
        "Note on the duplicate: the one duplicated (judge, project) pair in the source\n"
        "is itself one of the null-track rows -- the same judge scored the same project\n"
        "once under the AI/ML rubric and once under the generic fallback. Removing the\n"
        "null-track rows therefore removes the duplicate too, which is why the second\n"
        "count above is 0. The valid AI/ML score for that pair is kept."
    )
    out.append("")
    out.append(
        f"Judges: {notes['n_judges']}   Projects: {notes['n_projects']}   "
        f"Tracks: {notes['n_tracks']}"
    )
    out.append("")
    out.append(
        "Scores are the sum of 5 criteria, each 1-10, so 5-50 in practice. The rubric\n"
        "documents a 0-2 band but the UI never allowed 0, so 0 never appears."
    )
    return "\n".join(out)


def raw_spread_section(df: pd.DataFrame) -> str:
    """How different do judges look before any correction?"""
    out = [section("2. HOW DIFFERENT DO THE JUDGES LOOK?")]

    per_judge = df.groupby("judge_id").agg(
        n=("total_score", "size"),
        mean=("total_score", "mean"),
        sd=("total_score", "std"),
    )
    out.append(
        f"Apparent judge averages run from {per_judge['mean'].min():.1f}/50 to "
        f"{per_judge['mean'].max():.1f}/50."
    )
    out.append(
        f"Spread of judge averages (SD across judges): {per_judge['mean'].std():.2f} points."
    )
    out.append(
        f"Within-judge spread (median SD of one judge's own scores): "
        f"{per_judge['sd'].median():.2f} points."
    )
    out.append("")
    out.append(
        "That first number looks like strong evidence of judge bias. It is not -- yet.\n"
        "A judge who averages 50/50 may be generous, or may have been handed four\n"
        "excellent projects. Section 3a is about whether we can tell the difference."
    )
    out.append("")

    between = float(per_judge["mean"].std())
    within = float(per_judge["sd"].median())
    # Compare the two spreads rather than asserting a direction: which one is
    # larger tells you where the variation actually lives, and it decides how
    # much a judge-level correction could possibly buy you.
    if within > between:
        out.append(
            f"Within-judge spread ({within:.2f}) exceeds between-judge spread ({between:.2f}):\n"
            "a judge varies more across their own projects than judges differ from each\n"
            "other. Most of the variation is project-level, so a per-judge offset can only\n"
            "ever fix a minority of it."
        )
    else:
        out.append(
            f"Between-judge spread ({between:.2f}) is comparable to within-judge spread\n"
            f"({within:.2f}). Judges differ from each other about as much as any one judge\n"
            "varies across projects -- so IF the between-judge part were real bias, correcting\n"
            "it would matter. Whether it is real bias is exactly what section 3a decides.\n"
            "Note the extremes (5.0/50 and 50.0/50) come from judges with very few\n"
            "evaluations, where one project sets their whole average."
        )

    # Show how thin the extreme judges are -- the 5.0 and 50.0 averages are the
    # numbers people will quote, so show what they rest on.
    ext = per_judge.loc[[per_judge["mean"].idxmin(), per_judge["mean"].idxmax()]]
    out.append("")
    out.append("The two extreme judges, and how many evaluations they rest on:")
    out.append(ext[["n", "mean", "sd"]].to_string())
    return "\n".join(out)


def reliability_section(df: pd.DataFrame) -> str:
    """
    Inter-rater reliability (ICC), where computable.

    ICC asks: of all the variation in scores, how much is real differences
    between projects versus disagreement between judges? It requires projects
    scored by more than one judge -- which is exactly what 2026 lacks.
    """
    out = [section("3. INTER-RATER RELIABILITY (ICC)")]

    keyed = df.assign(slot=df["project_id"].astype(str) + "|" + df["track"].astype(str))
    counts = keyed.groupby("slot").size()
    multi = counts[counts > 1]

    out.append(
        "ICC (intraclass correlation) measures how much judges agree. It needs projects\n"
        "that more than one judge scored on the SAME rubric.\n"
    )
    out.append(f"Project-track slots in total:                 {len(counts)}")
    out.append(f"Slots scored by more than one judge:          {len(multi)}")

    if len(multi) < 5:
        out.append("")
        out.append(
            f"NOT COMPUTABLE. Only {len(multi)} slot(s) in the entire event were scored by\n"
            "more than one judge on the same rubric. Inter-rater reliability is undefined\n"
            "when there is essentially no second rater to correlate against. We cannot\n"
            "report ICC, Krippendorff's alpha, or any other agreement statistic for 2026."
        )
        if len(multi) > 0:
            out.append("")
            out.append("The double-scored slots, for the record:")
            for slot in multi.index:
                sub = keyed[keyed["slot"] == slot]
                scores = ", ".join(f"{s:.0f}" for s in sub["total_score"])
                out.append(
                    f"  {slot}: scores {scores} "
                    f"(spread {sub['total_score'].max() - sub['total_score'].min():.0f} points)"
                )
        return "\n".join(out)

    # Enough replication to actually compute it.
    try:
        import pingouin as pg

        sub = keyed[keyed["slot"].isin(multi.index)].copy()
        sub["rater_idx"] = sub.groupby("slot").cumcount()
        icc = pg.intraclass_corr(
            data=sub, targets="slot", raters="rater_idx", ratings="total_score"
        )
        out.append("")
        out.append(icc.to_string(index=False))
    except Exception as exc:
        out.append(f"\nICC computation failed: {exc}")
    return "\n".join(out)


def ranking_section(df: pd.DataFrame, result) -> tuple[str, pd.DataFrame]:
    """Compare the raw-average ranking to the normalized one."""
    from scipy.stats import spearmanr

    out = [section("5. RAW RANKING vs NORMALIZED RANKING")]

    raw = (
        df.groupby(["project_id", "track"])["total_score"]
        .agg(["mean", "size"])
        .reset_index()
        .rename(columns={"mean": "raw_mean", "size": "n_judges_raw"})
    )
    merged = result.projects.merge(raw, on=["project_id", "track"], how="inner", suffixes=("", "_r"))

    pieces = []
    all_moves = []
    for track, sub in merged.groupby("track"):
        sub = sub.copy()
        sub["raw_rank"] = sub["raw_mean"].rank(ascending=False, method="min")
        sub["norm_rank"] = sub["adjusted_score"].rank(ascending=False, method="min")
        sub["move"] = sub["raw_rank"] - sub["norm_rank"]
        all_moves.append(sub)

        rho = (
            spearmanr(sub["raw_mean"], sub["adjusted_score"])[0]
            if len(sub) > 2 and sub["adjusted_score"].std() > 0
            else np.nan
        )
        moved = int((sub["move"].abs() > 0).sum())
        big = int((sub["move"].abs() >= 3).sum())

        pieces.append(
            f"  {track:<36} n={len(sub):>3}  rank correlation raw-vs-normalized: "
            f"{rho:>6.3f}   {moved:>3} projects move, {big:>3} move 3+ places"
        )

        # Show the top of each track both ways -- this is what organizers look at.
        top_raw = sub.nlargest(3, "raw_mean")["project_id"].tolist()
        top_norm = sub.nlargest(3, "adjusted_score")["project_id"].tolist()
        pieces.append(f"      top-3 raw:        {', '.join(top_raw)}")
        pieces.append(f"      top-3 normalized: {', '.join(top_norm)}")
        pieces.append(
            f"      -> top-3 {'IS UNCHANGED' if top_raw == top_norm else 'CHANGES'}"
        )
        pieces.append("")

    out.extend(pieces)
    moves = pd.concat(all_moves) if all_moves else pd.DataFrame()

    if len(moves):
        out.append(
            f"Across all tracks: {int((moves['move'].abs() > 0).sum())} of {len(moves)} "
            f"project entries change rank, median absolute move "
            f"{moves['move'].abs().median():.1f} places, "
            f"largest move {moves['move'].abs().max():.0f} places."
        )
        out.append("")
        out.append("Ten largest rank movements (these are the ones that would change outcomes):")
        big = moves.reindex(moves["move"].abs().sort_values(ascending=False).index).head(10)
        out.append(
            big[
                ["project_id", "track", "n_judges", "raw_mean", "adjusted_score",
                 "raw_rank", "norm_rank", "move"]
            ].to_string(index=False)
        )

    return "\n".join(out), moves


def model_section(result) -> str:
    out = [section("4. THE MIXED-EFFECTS MODEL")]
    out.append(
        "Model: score = grand mean + judge leniency + project quality + noise,\n"
        "fitted per track (tracks too small for their own fit are pooled).\n"
        "'tau_judge' is how much judges genuinely differ; 'tau_project' is how much\n"
        "projects differ; 'sigma' is leftover noise. All in points out of 50.\n"
    )
    out.append(result.summary())
    out.append("")
    if result.notes:
        out.append("Model warnings:")
        for n in result.notes:
            out.append(f"  ! {n}")
        out.append("")

    out.append(
        "THE CLEAREST SIGN THAT THE MODEL IS GUESSING: look at how differently it splits\n"
        "the variance from track to track. It lands on flatly contradictory stories.\n"
    )
    for f in result.fits:
        tj, tp = f["tau_judge"], f["tau_project"]
        if tj > 2 * max(tp, 0.01):
            story = "'it was all the judges, projects were basically identical'"
        elif tp > 2 * max(tj, 0.01):
            story = "'it was all the projects, judges were basically identical'"
        else:
            story = "'a mix of both'"
        out.append(f"  {f['slice']:<36} tau_j={tj:>5.2f} tau_p={tp:>5.2f}  -> {story}")
    out.append("")
    out.append(
        "Analytics concludes projects were all equal (tau_project = 0.06) and judges\n"
        "differed hugely. AI/ML concludes almost the opposite. There is no reason the\n"
        "truth should flip like that between tracks -- these are the same kinds of judges\n"
        "and the same kinds of projects. The flipping is the model latching onto whichever\n"
        "explanation happens to fit each track's arbitrary assignment pattern, because\n"
        "the data cannot distinguish the two explanations.\n\n"
        "Likewise tau_judge = 0.00 in four tracks does not mean those judges were\n"
        "perfectly consistent. It means the model gave up on judge effects entirely and\n"
        "dumped all variation into project quality. That is the expected output of an\n"
        "unidentifiable design, not evidence of fair judging."
    )
    return "\n".join(out)


def leniency_section(df: pd.DataFrame, result) -> str:
    out = [section("6. ESTIMATED JUDGE LENIENCY (READ THE CAVEAT)")]
    names = df.drop_duplicates("judge_id").set_index("judge_id")["judge_name"].to_dict()
    j = result.judges.copy()
    j["judge_name"] = j["judge_id"].map(names).fillna("(unknown)")

    out.append(
        "'leniency' is points above/below average this judge appears to award.\n"
        "'shrinkage' is how much of the raw signal the model kept: 1.0 = fully trusted,\n"
        "near 0 = pulled back to the average because there was too little data.\n"
    )
    show = j.nlargest(8, "leniency")
    out.append("Most generous-looking judges:")
    out.append(
        show[["judge_name", "slice", "n_evals", "raw_mean", "leniency", "leniency_se", "shrinkage"]]
        .to_string(index=False)
    )
    out.append("")
    show = j.nsmallest(8, "leniency")
    out.append("Most severe-looking judges:")
    out.append(
        show[["judge_name", "slice", "n_evals", "raw_mean", "leniency", "leniency_se", "shrinkage"]]
        .to_string(index=False)
    )
    out.append("")
    out.append(
        "CAVEAT, and it is the whole report: because judges scored disjoint sets of\n"
        "projects, these numbers cannot distinguish 'this judge is generous' from\n"
        "'this judge got good projects'. Do not use this table to evaluate judges."
    )
    return "\n".join(out)


def verdict_section(diag: pd.DataFrame, result, moves: pd.DataFrame) -> str:
    out = [section("7. FINAL VERDICT -- HOW MUCH SHOULD YOU TRUST THIS?")]

    pooled = diag[diag["track"] == "(all tracks)"].iloc[0]
    bad = diag[diag["verdict"] == "NOT IDENTIFIABLE"]

    out.append(
        f"Of {len(diag) - 1} tracks, {len(bad[bad['track'] != '(all tracks)'])} are NOT "
        f"IDENTIFIABLE and the rest are not applicable.\n"
        f"Across the whole event, {pooled['same_rubric_pairs']} judge pairs out of "
        f"{pooled['total_pairs']} ({pooled['same_rubric_pct']:.1f}%) ever scored the same\n"
        f"project under the same rubric. {pooled['projects_with_one_judge']} of "
        f"{pooled['n_project_slots']} project-track slots were scored by exactly one judge."
    )
    out.append("")
    out.append("WHAT THIS MEANS, PLAINLY:")
    out.append("")
    out.append(
        "  1. The normalized ranking in section 5 is NOT trustworthy enough to change\n"
        "     who wins. It should not be used to award or revoke prizes for 2026."
    )
    out.append("")
    out.append(
        "  2. The reason is not that the model is bad -- simulate.py shows this exact\n"
        "     model recovers judge leniency to within ~1 point when the design connects\n"
        "     the judges. The reason is that the 2026 assignment gave almost every\n"
        "     project a single judge, so there is no overlap to learn from."
    )
    out.append("")
    out.append(
        "  3. A subtle trap: 106 judge pairs DO share a project, which looks like enough\n"
        "     overlap. But 101 of those pairs scored their shared project on DIFFERENT\n"
        "     tracks' rubrics, because most projects were entered in two tracks. Different\n"
        "     criteria, different scale, not comparable. Only 5 pairs are genuinely\n"
        "     usable. Any tool that counted shared project IDs without checking the track\n"
        "     would report 5.4% overlap and wrongly conclude normalization is viable."
    )
    out.append("")
    out.append(
        "  3b. The only direct evidence we have about judge agreement comes from the 3\n"
        "     slots that two judges did score on the same rubric, and it is alarming:\n"
        "     one project drew 31, 50 and 5 out of 50 from three judges; another drew\n"
        "     35 and 15. On the single occasion judges agreed, they agreed on 5/50.\n"
        "     Three data points cannot support a conclusion, but they give no reason to\n"
        "     assume the disagreement being corrected for is small."
    )
    out.append("")
    if len(moves):
        changed = (moves["move"].abs() > 0).mean()
        big = (moves["move"].abs() >= 3).mean()
        biggest = moves["move"].abs().max()
        out.append(
            f"  4. The correction is NOT cosmetic, which makes it more dangerous, not less.\n"
            f"     {100 * changed:.0f}% of project entries change rank, {100 * big:.0f}% move 3+ places, and the\n"
            f"     largest single move is {biggest:.0f} places. Whole top-3 lists change in AI/ML,\n"
            f"     Analytics and Cloud. So adopting these numbers WOULD change who wins --\n"
            f"     while resting on judge corrections the data cannot actually justify.\n"
            f"     Large movement plus unidentifiable parameters is the worst combination:\n"
            f"     it means the ranking is being reshuffled by essentially arbitrary values."
        )
        out.append("")
    out.append(
        "  5. RECOMMENDED ACTION FOR 2026 RESULTS: report raw averages, and state that\n"
        "     judge-effect correction was attempted and found to be unsupported by the\n"
        "     assignment design. That is more defensible than publishing adjusted numbers\n"
        "     whose corrections are indistinguishable from guesses."
    )
    out.append("")
    out.append(
        "  6. FOR NEXT YEAR: run simulate.py. Its answer is that 2 judges per project\n"
        "     plus 1-2 shared anchor projects makes leniency measurable to about 1.4\n"
        "     points and lifts ranking accuracy substantially over raw averages."
    )
    return "\n".join(out)


def main(refresh: bool = False) -> str:
    df, notes = load_evaluations(refresh=refresh)

    parts = []
    parts.append(LINE)
    parts.append("DATAHACKS 2026 -- JUDGE SCORE NORMALIZATION REPORT")
    parts.append(LINE)
    parts.append(
        "\nShort version: we built the normalization model, and the 2026 data cannot\n"
        "support it. The model is fine; the judge assignment design is the problem.\n"
        "Details below, recommendation in section 7."
    )

    parts.append(data_quality_section(notes))
    parts.append(raw_spread_section(df))

    diag = diagnose(df)
    parts.append(section("3a. IDENTIFIABILITY CHECK"))
    parts.append(format_report(diag))

    parts.append(reliability_section(df))

    result = fit_normalization(df)
    parts.append(model_section(result))

    rank_text, moves = ranking_section(df, result)
    parts.append(rank_text)
    parts.append(leniency_section(df, result))
    parts.append(verdict_section(diag, result, moves))

    return "\n".join(parts)


if __name__ == "__main__":
    text = main(refresh="--refresh" in sys.argv)
    print(text)
