"""
Replay DataHacks 2026: what the assignments WERE vs what they SHOULD have been.

Pulls the real 2026 judges / projects / evaluations out of Firestore, rebuilds
the roster of judges who actually showed up, and re-solves the event with the
new solver. Then prints a side-by-side comparison.

    *** THIS SCRIPT IS STRICTLY READ-ONLY. ***
    It only ever calls .stream() / .get() / auth.get_users(). There is no
    set / update / delete / add anywhere in this file, and there must never be:
    this is live production data.

Usage (from the repo root):

    python -m pipeline.assignment.replay_2026
    python -m pipeline.assignment.replay_2026 --min-judges 3 --anchors 3
    python -m pipeline.assignment.replay_2026 --cache /tmp/dh2026.json   # save+reuse
    python -m pipeline.assignment.replay_2026 --cache /tmp/dh2026.json --offline

Legacy data warning
-------------------
The 2026 Firestore is a layered mess and this script has to cope with it:

* ``judges`` holds TWO generations of docs. Username-style ids
  (``AarushiBajaj``) are the real 2026 judges; slug-style ids (``anuj-jain``)
  are leftovers from an earlier synthetic dry run.
* ``projects`` likewise holds 160 real submissions plus 212 synthetic ones. The
  synthetic ones are the only ones carrying ``builtWith`` / ``submissionUrl``,
  so that field is the tell.
* ``projects.assignedJudges`` is stale synthetic-era data (name-slugs). The
  assignment that actually shipped lives in ``judges.assignedProjects``.
* ``evaluations.judgeId`` is a Firebase Auth UID, which matches NOTHING in the
  ``judges`` collection. We resolve it through Auth: UID -> email -> judge doc.

The solver itself has none of this in it; it takes clean lists and emits
UID-keyed assignments.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from collections import Counter
from typing import Any, Iterable, Mapping

from pipeline.assignment import validate
from pipeline.assignment.solver import SolverConfig, solve

DEFAULT_SERVICE_ACCOUNT = "src/assets/serviceAccount.json"
# Organiser's check-in sheet. Gitignored, so it may not exist on every machine;
# without it we fall back to "judges who submitted at least one real score".
DEFAULT_CHECKIN_CSV = "src/assets/password_judges - Checked-In.csv"

# Award tracks with no submissions of their own; judges on these are scored
# separately and are not part of the main room assignment.
CHALLENGE_TRACKS = {
    "Marimo Challenge",
    "DataBricks Challenge",
    "ZenPower Challenge",
    "Best Use of Scripps Data",
}


# ---------------------------------------------------------------------------
# read-only Firestore / Auth access
# ---------------------------------------------------------------------------


def fetch_firestore(service_account_path: str) -> dict[str, list[dict[str, Any]]]:
    """Read judges / projects / evaluations. READ ONLY."""
    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(service_account_path))
    db = firestore.client()

    data: dict[str, list[dict[str, Any]]] = {}
    for name in ("judges", "projects", "evaluations"):
        data[name] = [
            {"_id": doc.id, **(doc.to_dict() or {})} for doc in db.collection(name).stream()
        ]
    return data


def resolve_eval_uids(uids: Iterable[str], service_account_path: str) -> dict[str, str]:
    """UID -> email, via Firebase Auth. READ ONLY.

    ``evaluations.judgeId`` is an Auth UID; the ``judges`` collection is keyed
    by username. Auth is the only bridge between them.
    """
    import firebase_admin
    from firebase_admin import auth, credentials

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(service_account_path))

    uid_list = sorted(set(uids))
    mapping: dict[str, str] = {}
    for i in range(0, len(uid_list), 100):  # get_users caps at 100 per call
        batch = uid_list[i : i + 100]
        result = auth.get_users([auth.UidIdentifier(u) for u in batch])
        for user in result.users:
            if user.email:
                mapping[user.uid] = user.email.lower()
    return mapping


# ---------------------------------------------------------------------------
# untangling the legacy data
# ---------------------------------------------------------------------------


def is_legacy_slug_id(doc_id: str) -> bool:
    """True for the synthetic dry-run docs (``anuj-jain``) vs real (``AnujJain``)."""
    return "-" in doc_id and doc_id == doc_id.lower()


def split_real_data(raw: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Pull the real 2026 judges + projects out of the layered Firestore state."""
    judges = [j for j in raw["judges"] if not is_legacy_slug_id(j["_id"])]
    # synthetic projects are the only ones with builtWith / submissionUrl
    projects = [p for p in raw["projects"] if "builtWith" not in p]
    project_ids = {p["_id"] for p in projects}

    return {
        "judges": judges,
        "projects": projects,
        "project_ids": project_ids,
        "evaluations": [e for e in raw["evaluations"] if e.get("projectId") in project_ids],
    }


def map_docs_to_uids(
    real: Mapping[str, Any], uid_to_email: Mapping[str, str]
) -> dict[str, str]:
    """judge doc id -> Firebase Auth UID, bridged through email."""
    by_email = {
        (j.get("email") or "").lower(): j["_id"] for j in real["judges"] if j.get("email")
    }
    uid_by_doc: dict[str, str] = {}
    for uid, email in uid_to_email.items():
        doc_id = by_email.get((email or "").lower())
        if doc_id:
            uid_by_doc[doc_id] = uid
    return uid_by_doc


def read_checkin_csv(path: str) -> list[str]:
    """Usernames from the organiser's check-in sheet where Checked-In is TRUE."""
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    return [
        (r.get("Username") or "").strip()
        for r in rows
        if str(r.get("Checked-In", "")).strip().upper() == "TRUE"
        and (r.get("Username") or "").strip()
    ]


def find_checked_in_judges(
    real: Mapping[str, Any],
    uid_by_doc: Mapping[str, str],
    checkin_csv: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Who was actually in the room.

    Preferred source is the organiser's check-in sheet. If that file is not
    available (it is gitignored), we fall back to "submitted at least one score
    on a real project", which under-counts anyone who checked in and then
    scored nothing.
    """
    by_doc_id = {j["_id"]: j for j in real["judges"]}

    if checkin_csv and os.path.exists(checkin_csv):
        usernames = read_checkin_csv(checkin_csv)
        judges = [by_doc_id[u] for u in usernames if u in by_doc_id]
        if judges:
            return judges, f"check-in sheet ({os.path.basename(checkin_csv)})"

    doc_by_uid = {uid: doc for doc, uid in uid_by_doc.items()}
    scored = {
        doc_by_uid[e["judgeId"]]
        for e in real["evaluations"]
        if e.get("judgeId") in doc_by_uid
    }
    return [by_doc_id[d] for d in sorted(scored)], "judges who submitted >=1 real score"


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


def graph_metrics(
    judge_projects: Mapping[str, list[str]],
    judge_tracks: Mapping[str, str],
    all_project_ids: Iterable[str],
    min_judges: int,
) -> dict[str, Any]:
    report = validate.preflight(
        judge_projects,
        judge_tracks,
        all_project_ids=all_project_ids,
        min_judges_per_project=min_judges,
        ignore_tracks=CHALLENGE_TRACKS,
    )
    conn = report["connectivity"]
    judged = [c for c in report["coverage"]["per_project"].values() if c > 0]
    return {
        "report": report,
        "islands": sum(t["n_components"] for t in conn["tracks"].values()),
        "disconnected_tracks": conn["failures"],
        "min_judges": min(judged) if judged else 0,
        "median_judges": statistics.median(judged) if judged else 0,
        "mean_judges": round(statistics.mean(judged), 2) if judged else 0,
        "max_judges": max(judged) if judged else 0,
        "solo_judged": sum(1 for c in judged if c == 1),
        "below_target": len(report["projects_below_target"]),
        "projects_judged": len(judged),
        "pair_pct": report["pair_overlap"]["pct"],
        "pairs_shared": report["pair_overlap"]["shared_pairs"],
        "pairs_total": report["pair_overlap"]["pairs"],
        "pair_pct_track": report["pair_overlap_within_track"]["pct"],
        "pairs_shared_track": report["pair_overlap_within_track"]["shared_pairs"],
        "pairs_total_track": report["pair_overlap_within_track"]["pairs"],
        "load": report["workload"]["overall"],
        "idle": len(report["workload"]["idle_judges"]),
    }


def _row(label: str, *cells: Any, width: int = 26, cell: int = 20) -> str:
    return f"{label:<{width}}" + "".join(f"{str(c):>{cell}}" for c in cells)


def print_comparison(
    before: dict[str, Any],
    actual: dict[str, Any],
    after: dict[str, Any],
    n_judges: int,
    n_projects: int,
) -> None:
    line = "=" * 86
    print()
    print(line)
    print("DATAHACKS 2026 REPLAY".center(86))
    print(f"{n_judges} judges who actually checked in  |  {n_projects} real projects".center(86))
    print(line)
    print(_row("", "WAS (assigned)", "WAS (scored)", "SHOULD HAVE BEEN"))
    print("-" * 86)
    print(_row("judge-project islands", before["islands"], actual["islands"], after["islands"]))
    print(
        _row(
            "disconnected tracks",
            len(before["disconnected_tracks"]),
            len(actual["disconnected_tracks"]),
            len(after["disconnected_tracks"]),
        )
    )
    print(
        _row(
            "pair overlap (all pairs)",
            f"{before['pair_pct']}%",
            f"{actual['pair_pct']}%",
            f"{after['pair_pct']}%",
        )
    )
    print(
        _row(
            "  shared / total",
            f"{before['pairs_shared']}/{before['pairs_total']}",
            f"{actual['pairs_shared']}/{actual['pairs_total']}",
            f"{after['pairs_shared']}/{after['pairs_total']}",
        )
    )
    print(
        _row(
            "pair overlap (same track)",
            f"{before['pair_pct_track']}%",
            f"{actual['pair_pct_track']}%",
            f"{after['pair_pct_track']}%",
        )
    )
    print(
        _row(
            "  shared / total",
            f"{before['pairs_shared_track']}/{before['pairs_total_track']}",
            f"{actual['pairs_shared_track']}/{actual['pairs_total_track']}",
            f"{after['pairs_shared_track']}/{after['pairs_total_track']}",
        )
    )
    print("-" * 86)
    print(_row("projects judged", before["projects_judged"], actual["projects_judged"], after["projects_judged"]))
    print(_row("min judges / project", before["min_judges"], actual["min_judges"], after["min_judges"]))
    print(_row("median judges / project", before["median_judges"], actual["median_judges"], after["median_judges"]))
    print(_row("mean judges / project", before["mean_judges"], actual["mean_judges"], after["mean_judges"]))
    print(_row("max judges / project", before["max_judges"], actual["max_judges"], after["max_judges"]))
    print(_row("projects with 1 judge", before["solo_judged"], actual["solo_judged"], after["solo_judged"]))
    print(_row("projects below target", before["below_target"], actual["below_target"], after["below_target"]))
    print("-" * 86)
    for key, label in (
        ("min", "min projects / judge"),
        ("median", "median projects / judge"),
        ("max", "max projects / judge"),
        ("stdev", "stdev projects / judge"),
    ):
        print(_row(label, before["load"][key], actual["load"][key], after["load"][key]))
    print(_row("judges with nothing to do", before["idle"], actual["idle"], after["idle"]))
    print(line)


def print_track_table(title: str, metrics: Mapping[str, Any]) -> None:
    conn = metrics["report"]["connectivity"]
    overlap = metrics["report"]["pair_overlap"]["per_track"]
    print()
    print(title)
    print(f"{'track':<36}{'judges':>8}{'projects':>10}{'islands':>9}{'overlap':>9}   status")
    print("-" * 86)
    for track, info in conn["tracks"].items():
        pct = overlap.get(track, {}).get("pct", 0.0)
        status = "OK" if info["connected"] else "DISCONNECTED"
        print(
            f"{track:<36}{info['n_judges']:>8}{info['n_projects']:>10}"
            f"{info['n_components']:>9}{pct:>8.1f}%   {status}"
        )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--service-account", default=DEFAULT_SERVICE_ACCOUNT)
    parser.add_argument(
        "--checkin-csv",
        default=DEFAULT_CHECKIN_CSV,
        help="organiser check-in sheet; falls back to evaluation history if absent",
    )
    parser.add_argument("--min-judges", type=int, default=3)
    parser.add_argument("--anchors", type=int, default=3)
    parser.add_argument("--max-per-judge", type=int, default=None)
    parser.add_argument("--cache", default=None, help="path to save/reuse the Firestore dump")
    parser.add_argument("--offline", action="store_true", help="only read --cache, never hit Firestore")
    parser.add_argument("--out", default=None, help="write the new assignment to this JSON path")
    parser.add_argument("--verbose", action="store_true", help="print full preflight reports")
    args = parser.parse_args(argv)

    # ---- 1. load -------------------------------------------------------
    raw = None
    if args.cache and os.path.exists(args.cache):
        with open(args.cache) as fh:
            raw = json.load(fh)
        print(f"loaded cached Firestore dump from {args.cache}")
    if raw is None:
        if args.offline:
            print("--offline given but no usable --cache file", file=sys.stderr)
            return 2
        if not os.path.exists(args.service_account):
            print(f"service account not found at {args.service_account}", file=sys.stderr)
            return 2
        print("reading Firestore (read-only)...")
        raw = fetch_firestore(args.service_account)
        raw["_uid_emails"] = resolve_eval_uids(
            [e.get("judgeId") for e in raw["evaluations"] if e.get("judgeId")],
            args.service_account,
        )
        if args.cache:
            with open(args.cache, "w") as fh:
                json.dump(raw, fh, default=str)
            print(f"cached dump -> {args.cache}")

    print(
        f"  judges={len(raw['judges'])}  projects={len(raw['projects'])}  "
        f"evaluations={len(raw['evaluations'])}  (all generations)"
    )

    # ---- 2. untangle ---------------------------------------------------
    real = split_real_data(raw)
    print(
        f"  real 2026: judges={len(real['judges'])}  projects={len(real['projects'])}  "
        f"evaluations={len(real['evaluations'])}"
    )

    uid_by_doc = map_docs_to_uids(real, raw.get("_uid_emails", {}))
    checked_in, source = find_checked_in_judges(real, uid_by_doc, args.checkin_csv)
    scored_docs = {
        doc for doc, uid in uid_by_doc.items()
        if any(e.get("judgeId") == uid for e in real["evaluations"])
    }
    print(f"  checked-in judges: {len(checked_in)}  (source: {source})")
    print(f"  ...of whom {len(scored_docs & {j['_id'] for j in checked_in})} submitted >=1 real score")
    print(f"  track spread: {dict(Counter(j.get('track') for j in checked_in))}")

    judge_tracks = {j["_id"]: (j.get("track") or "(no track)") for j in checked_in}
    project_ids = sorted(real["project_ids"])

    # ---- 3. WAS: the assignment that shipped ---------------------------
    was_assigned = {
        j["_id"]: [p for p in (j.get("assignedProjects") or []) if p in real["project_ids"]]
        for j in checked_in
    }

    # ---- 4. WAS: what those judges actually scored ---------------------
    doc_by_uid = {uid: doc for doc, uid in uid_by_doc.items()}
    was_scored: dict[str, list[str]] = {j["_id"]: [] for j in checked_in}
    for ev in real["evaluations"]:
        doc_id = doc_by_uid.get(ev.get("judgeId"))
        if doc_id is not None and ev["projectId"] not in was_scored[doc_id]:
            was_scored[doc_id].append(ev["projectId"])

    # ---- 5. SHOULD HAVE BEEN: re-solve ---------------------------------
    solver_judges = [
        {
            # solver output is keyed on the Auth UID, because that is what
            # evaluations.judgeId holds -- clean joins downstream.
            "id": uid_by_doc.get(j["_id"], j["_id"]),
            "name": j.get("name", ""),
            "track": j.get("track") or "(no track)",
            "mode": "individual",
        }
        for j in checked_in
    ]
    solver_projects = [
        {
            "id": p["_id"],
            "name": p.get("name", ""),
            "tracks": p.get("tracks") or [],
            "table_number": p.get("tableNumber"),
        }
        for p in real["projects"]
    ]
    config = SolverConfig(
        min_judges_per_project=args.min_judges,
        anchors_per_track=args.anchors,
        max_projects_per_judge=args.max_per_judge,
    )
    print(
        f"\nsolving: min_judges={config.min_judges_per_project} "
        f"anchors={config.anchors_per_track} "
        f"max_per_judge={config.max_projects_per_judge}"
    )
    result = solve(solver_judges, solver_projects, config)
    print("connectivity gate: PASSED (solve() would have raised otherwise)")
    for warning in result.warnings:
        print(f"  warn: {warning}")

    # map the solved assignment back onto judge doc ids so the three
    # scenarios are comparable judge-for-judge
    solved = {
        j["_id"]: result.judge_projects.get(uid_by_doc.get(j["_id"], j["_id"]), [])
        for j in checked_in
    }

    # ---- 6. compare ----------------------------------------------------
    before = graph_metrics(was_assigned, judge_tracks, project_ids, args.min_judges)
    actual = graph_metrics(was_scored, judge_tracks, project_ids, args.min_judges)
    after = graph_metrics(solved, judge_tracks, project_ids, args.min_judges)

    print_comparison(before, actual, after, len(checked_in), len(project_ids))
    print_track_table("PER TRACK -- WAS (what judges actually scored)", actual)
    print_track_table("PER TRACK -- SHOULD HAVE BEEN (solver)", after)

    print()
    print("anchor projects chosen per track (every judge in the track scores these):")
    names = {p["_id"]: p.get("name", "") for p in real["projects"]}
    tables = {p["_id"]: p.get("tableNumber") for p in real["projects"]}
    for track, anchor_ids in sorted(result.anchors.items()):
        if not anchor_ids:
            continue
        pretty = ", ".join(f"{names.get(a, a)!r} (table {tables.get(a)})" for a in anchor_ids)
        print(f"  {track:<36} {pretty}")

    if args.verbose:
        print()
        print(validate.format_report(after["report"]))

    if args.out:
        payload = {
            "config": vars(config),
            "judge_projects": result.judge_projects,
            "project_judges": result.project_judges,
            "anchors": result.anchors,
            "warnings": result.warnings,
        }
        with open(args.out, "w") as fh:
            json.dump(payload, fh, indent=2)
        print(f"\nwrote proposed assignment -> {args.out}  (local file only, Firestore untouched)")

    print()
    print("NOTE: this script never wrote anything to Firestore.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
