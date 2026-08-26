"""
Read-only loader for DataHacks judging data.

READ-ONLY BY CONSTRUCTION. This module never calls .set(), .update(), .add(),
.delete() or any other Firestore write API. It only calls .stream() on
collections and Firebase Auth list_users(). Do not add writes here.

Produces one tidy DataFrame, one row per (judge, project) evaluation:

    judge_id     Firebase Auth UID of the judge
    judge_name   human-readable name (resolved via Auth email -> judges collection)
    project_id   Firestore document id of the project
    project_name human-readable project name
    track        judging track (e.g. "AI/ML"); None for the fallback-rubric rows
    total_score  sum of the 5 criterion scores, so 0..50 in principle, 5..50 in practice
    n_criteria   how many criteria were filled in (should be 5)

Why a cache: the Firestore read costs a few seconds and needs network + the
service account. `load_evaluations(cache=...)` writes a plain CSV to a local
path so the model, the report and the tests can be re-run offline. The cache is
a local file only -- nothing is ever written back to Firestore.
"""

from __future__ import annotations

import os
from typing import Optional

import pandas as pd

# Path to the service account, relative to the repo root.
DEFAULT_SERVICE_ACCOUNT = "src/assets/serviceAccount.json"
DEFAULT_CACHE = "pipeline/normalization/_cache_evaluations.csv"


def _repo_root() -> str:
    """pipeline/normalization/data.py -> repo root is two directories up."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def fetch_from_firestore(service_account: Optional[str] = None) -> pd.DataFrame:
    """
    Pull evaluations + projects + judges out of Firestore. Read-only.

    Judge identity is awkward in this dataset: `evaluations.judgeId` is a
    Firebase *Auth UID*, but documents in the `judges` collection are keyed by a
    squashed name ("AarushiBajaj"), not by UID. So we bridge them through email:

        Auth UID --(Auth)--> email --(judges collection)--> display name

    A handful of UIDs no longer exist in Auth (accounts cleaned up after the
    event). Those keep a truncated-UID label; they are still perfectly usable as
    a grouping key for the model, we just cannot print a nice name.
    """
    import firebase_admin
    from firebase_admin import auth, credentials, firestore

    root = _repo_root()
    sa_path = service_account or os.path.join(root, DEFAULT_SERVICE_ACCOUNT)

    # initialize_app() throws if called twice in one process (e.g. under pytest).
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(sa_path))
    db = firestore.client()

    projects = {d.id: d.to_dict() for d in db.collection("projects").stream()}
    judges = {d.id: d.to_dict() for d in db.collection("judges").stream()}

    # email -> display name, skipping blank emails so they cannot swallow
    # unresolved lookups (a judge doc with email "" would otherwise match every
    # UID that Auth does not know about).
    email_to_name = {}
    for j in judges.values():
        email = (j.get("email") or "").strip().lower()
        if email:
            email_to_name[email] = j.get("name") or email

    uid_to_email = {}
    page = auth.list_users()
    while page:
        for user in page.users:
            uid_to_email[user.uid] = (user.email or "").strip().lower()
        page = page.get_next_page()

    rows = []
    for doc in db.collection("evaluations").stream():
        e = doc.to_dict() or {}
        scores = e.get("scores") or {}
        # Only count criteria that actually carry a number. A missing criterion
        # would otherwise silently deflate the total.
        values = [v for v in scores.values() if isinstance(v, (int, float))]
        if not values:
            continue

        uid = e.get("judgeId")
        email = uid_to_email.get(uid, "")
        name = email_to_name.get(email) or (f"unknown-{str(uid)[:8]}" if uid else "unknown")

        project_id = e.get("projectId")
        project = projects.get(project_id) or {}

        rows.append(
            {
                "eval_id": doc.id,
                "judge_id": uid,
                "judge_name": name,
                "project_id": project_id,
                "project_name": project.get("name") or project_id,
                "track": e.get("track"),
                "total_score": float(sum(values)),
                "n_criteria": len(values),
                "timestamp": e.get("timestamp"),
            }
        )

    return pd.DataFrame(rows)


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Apply the two documented data-quality fixes and report exactly what was dropped.

    1. Rows with track = None used a generic fallback rubric whose criteria are
       not comparable to any real track's criteria, so they are excluded.
    2. A judge could re-open and re-save an evaluation. The platform updates in
       place, but at least one (judge, project) pair exists twice. We keep the
       most recent row per pair -- that is the judge's final answer.

    Returns (clean_df, notes) where notes is a plain dict of counts so the report
    can state the exclusions out loud rather than hiding them.
    """
    notes = {"rows_in": len(df)}

    # Count duplicates BEFORE the track filter, purely so the report can say how
    # many there were in the source data. Order matters below: in the 2026 data
    # the duplicated pair is a null-track fallback-rubric row paired with a
    # valid AI/ML row, and the null-track row has the LATER timestamp. Deduping
    # first with keep="last" would therefore keep the junk row and throw away
    # the good one. Filtering tracks first avoids that trap.
    notes["duplicate_pairs_in_source"] = int(
        df.duplicated(subset=["judge_id", "project_id"], keep="first").sum()
    )

    null_track = df["track"].isna() | (df["track"].astype(str).str.strip() == "")
    notes["dropped_null_track"] = int(null_track.sum())
    out = df[~null_track].copy()

    # Sort so the newest row per (judge, project) lands last, then keep last.
    if "timestamp" in out.columns:
        out = out.sort_values("timestamp", na_position="first")
    dupe_mask = out.duplicated(subset=["judge_id", "project_id"], keep="last")
    notes["dropped_duplicate_pairs"] = int(dupe_mask.sum())
    out = out[~dupe_mask].copy()

    notes["rows_out"] = len(out)
    notes["n_judges"] = out["judge_id"].nunique()
    notes["n_projects"] = out["project_id"].nunique()
    notes["n_tracks"] = out["track"].nunique()
    return out.reset_index(drop=True), notes


def load_evaluations(
    cache: Optional[str] = DEFAULT_CACHE,
    refresh: bool = False,
    service_account: Optional[str] = None,
) -> tuple[pd.DataFrame, dict]:
    """
    Main entry point: return (clean evaluations DataFrame, cleaning notes).

    Uses the local CSV cache when present unless refresh=True.
    """
    root = _repo_root()
    cache_path = os.path.join(root, cache) if cache and not os.path.isabs(cache) else cache

    if cache_path and os.path.exists(cache_path) and not refresh:
        raw = pd.read_csv(cache_path)
    else:
        raw = fetch_from_firestore(service_account)
        if cache_path:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            raw.to_csv(cache_path, index=False)

    return clean(raw)


if __name__ == "__main__":
    df, notes = load_evaluations(refresh=True)
    print(notes)
    print(df.head())
