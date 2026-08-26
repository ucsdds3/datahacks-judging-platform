"""
Solve an assignment and publish it: Firestore for the console, Delta for the record.

This is the loop-closer. Databricks holds the history and does the solving; the
organizer console (`/organizer` -> Assignment status) reads a single
``runs/{runId}`` document out of Firestore. There is no backend server, so this
job writing that document IS the integration.

    *** --dry-run IS THE DEFAULT. ***
    Nothing is written anywhere unless you pass --commit. Only --commit may
    write to Firestore. Every read this script makes -- judges, projects,
    checkins, Auth -- is .stream() / list_users().

The document shape is docs/runs-contract.md. Read that first; this file is its
implementation and the two must stay in step.

The failure path matters as much as the success path
----------------------------------------------------
If the solver's connectivity gate fires, we STILL write a run document, with
``connectivityOk: false``, the island count per track and the stranded judges
by name. The console renders that as a full-width red "do not judge against
this assignment" banner. A failure the organizer can see beats silence -- in
2026 the graph was disconnected and nothing said so until after the event, by
which point it could not be repaired.

Where the judges come from
--------------------------
The checked-in judges, never the roster. That distinction is the single biggest
thing that broke 2026: assignments were generated for 97 rostered judges, 63
showed up, and 44 projects ended up with one judge.

    --checkins firestore   (default) the `checkins` collection the organizer
                           console writes as judges arrive
    --checkins csv         the organizer's check-in sheet, for rehearsing on
                           2026 data (the `checkins` collection is empty -- the
                           console did not exist during the event)

Run it
------
    python -m pipeline.databricks.publish_run --checkins csv          # dry run
    python -m pipeline.databricks.publish_run --checkins csv --commit
    python -m pipeline.databricks.publish_run --min-judges 3 --anchors 2
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from pipeline.assignment import validate
from pipeline.assignment.solver import (
    DisconnectedAssignmentError,
    Judge,
    Project,
    SolverConfig,
    solve,
)
from pipeline.tracks import CANONICAL

from . import ingest_2026
from . import schema as schema_module
from .connection import MissingCredentialsError, connect

DEFAULT_SERVICE_ACCOUNT = os.path.join("src", "assets", "serviceAccount.json")
DEFAULT_CHECKIN_CSV = os.path.join("src", "assets", "password_judges - Checked-In.csv")

SOURCE = "pipeline.databricks.publish_run"

#: Tracks that exist as awards but have no submissions of their own. A judge on
#: one of these is not part of the room assignment; leaving them in would ask
#: the solver to cover a track with zero projects.
CHALLENGE_TRACKS = frozenset(
    {
        "Marimo Challenge",
        "DataBricks Challenge",
        "ZenPower Challenge",
        "Best Use of Scripps Data",
    }
)


# --------------------------------------------------------------------------
# inputs -- every one of these is a read
# --------------------------------------------------------------------------


def read_checkins_from_firestore(
    service_account_path: str = DEFAULT_SERVICE_ACCOUNT,
) -> List[str]:
    """Judge doc ids that are currently checked in. READ ONLY.

    ``checkins/{judgeId}`` is written by the organizer console. A document with
    ``checkedIn: false`` is someone who was checked in and then undone, so it
    does not count -- that is how the console counts them too.
    """
    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(service_account_path))
    db = firestore.client()
    return [
        doc.id
        for doc in db.collection("checkins").stream()  # READ ONLY
        if (doc.to_dict() or {}).get("checkedIn") is not False
    ]


def read_checkins_from_csv(path: str = DEFAULT_CHECKIN_CSV) -> List[str]:
    """Usernames from the organizer's check-in sheet where Checked-In is TRUE.

    Read with the csv module, not str.split(",") -- a judge named "Chen, Jr."
    is quoted in that file and hand-splitting shifts every later column.
    """
    with open(path, "r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    return [
        (row.get("Username") or "").strip()
        for row in rows
        if str(row.get("Checked-In", "")).strip().upper() == "TRUE"
        and (row.get("Username") or "").strip()
    ]


# --------------------------------------------------------------------------
# turning Firestore documents into solver inputs
# --------------------------------------------------------------------------


@dataclass
class SolverInputs:
    """What the solver gets, plus the identity map the console needs."""

    judges: List[Judge] = field(default_factory=list)
    projects: List[Project] = field(default_factory=list)
    #: solver judge id (an Auth UID where we have one) -> judges/{docId}
    judge_doc_ids: Dict[str, str] = field(default_factory=dict)
    #: how many checkin records we started from, before any exclusions
    checkin_snapshot_count: int = 0
    warnings: List[str] = field(default_factory=list)


def build_solver_inputs(
    judge_docs: Sequence[Mapping[str, Any]],
    project_docs: Sequence[Mapping[str, Any]],
    checked_in_ids: Sequence[str],
    uid_to_email: Mapping[str, str],
    title_index: ingest_2026.TitleIndex,
) -> SolverInputs:
    """Pure. Firestore documents in, ``Judge`` / ``Project`` lists out.

    Judges are keyed by Firebase Auth UID wherever the email resolves to an
    account, because ``evaluations.judgeId`` is an Auth UID -- keying the
    assignment the same way is what lets the console attribute scores exactly
    instead of inferring them. A judge with no Auth account keeps their doc id
    and is reported as a warning.

    Only real projects are assignable. The 212 synthetic dry-run leftovers are
    still sitting in the `projects` collection and would otherwise soak up a
    third of the room's judging capacity.
    """
    out = SolverInputs(checkin_snapshot_count=len(checked_in_ids))
    uid_by_email = {email: uid for uid, email in uid_to_email.items() if email}

    # Judge docs are keyed by Auth UID since the identity migration, but the
    # check-in sheet still supplies usernames ("AarushiBajaj"). Index each doc
    # under every identifier it could be referred to by, so either works:
    #   - its Firestore doc id (what the checkins collection stores)
    #   - the local part of its email, which is the username
    by_doc_id: Dict[str, Any] = {}
    for doc in judge_docs:
        keys = {doc.get("_id"), (doc.get("_id") or "").lower()}
        email = (doc.get("email") or "").strip().lower()
        if "@" in email:
            # Set union rather than the usual set-append method:
            # test_the_only_firestore_write_is_the_runs_document scans this
            # file for write-shaped tokens and that method name trips it.
            # Keeping the guard blunt is worth the small awkwardness here.
            keys |= {email.split("@", 1)[0]}
        keys -= {"", None}
        for k in keys:
            by_doc_id.setdefault(k, doc)

    missing_docs: List[str] = []
    no_auth: List[str] = []
    skipped_tracks: Dict[str, List[str]] = {}
    seen: set = set()

    for doc_id in checked_in_ids:
        doc = by_doc_id.get(doc_id) or by_doc_id.get(doc_id.lower())
        if doc is None:
            missing_docs.append(doc_id)
            continue
        if doc_id in seen:
            continue
        seen |= {doc_id}

        track = (doc.get("track") or "").strip()
        if track in CHALLENGE_TRACKS or track not in CANONICAL:
            skipped_tracks.setdefault(track or "(no track)", []).append(doc_id)
            continue

        email = (doc.get("email") or "").strip().lower()
        uid = uid_by_email.get(email)
        if uid is None:
            no_auth.append(doc_id)
        judge_key = uid or doc_id
        out.judge_doc_ids[judge_key] = doc_id
        out.judges.append(
            Judge(
                id=judge_key,
                name=(doc.get("name") or doc_id).strip(),
                track=track,
            )
        )

    for doc in project_docs:
        classification = ingest_2026.classify_project(doc, title_index)
        if classification["is_synthetic"]:
            continue
        tracks = doc.get("tracks")
        table_number = doc.get("tableNumber")
        out.projects.append(
            Project(
                id=doc.get("_id") or "",
                name=(doc.get("name") or doc.get("_id") or "").strip(),
                tracks=tuple(str(t) for t in tracks) if isinstance(tracks, list) else (),
                table_number=int(table_number)
                if isinstance(table_number, (int, float))
                else None,
            )
        )

    if missing_docs:
        out.warnings.append(
            f"{len(missing_docs)} checked-in judge(s) have no judges/ document "
            f"and were skipped: {', '.join(sorted(missing_docs)[:10])}"
        )
    if no_auth:
        out.warnings.append(
            f"{len(no_auth)} checked-in judge(s) have no Firebase Auth account, "
            "so their assignment is keyed by document id and their scores "
            "cannot be attributed automatically: "
            f"{', '.join(sorted(no_auth)[:10])}"
        )
    for track, ids in sorted(skipped_tracks.items()):
        out.warnings.append(
            f"{len(ids)} checked-in judge(s) on track {track!r} are not part of "
            "the room assignment (challenge/award track with no submissions of "
            f"its own): {', '.join(sorted(ids))}"
        )
    return out


# --------------------------------------------------------------------------
# running the solver so the failure path keeps its detail
# --------------------------------------------------------------------------


@dataclass
class SolveOutcome:
    """Either an assignment, or the assignment that failed the gate."""

    assignment: Any = None
    error: Optional[BaseException] = None
    connectivity: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None


def solve_capturing(judges, projects, config: SolverConfig) -> SolveOutcome:
    """Call ``solve()``, keeping the assignment even when the gate rejects it.

    The contract's failure document needs structure -- which track has how many
    islands, and which judges are stranded in each -- so the console can name
    them in its red banner. ``DisconnectedAssignmentError`` carries the report
    and the rejected assignment for exactly this purpose.
    """
    try:
        assignment = solve(judges, projects, config)
        return SolveOutcome(
            assignment=assignment,
            connectivity=validate.check_connectivity(
                assignment.judge_projects, assignment.judge_tracks
            ),
        )
    except DisconnectedAssignmentError as exc:
        partial = getattr(exc, "assignment", None)
        connectivity = getattr(exc, "report", None) or (
            validate.check_connectivity(partial.judge_projects, partial.judge_tracks)
            if partial is not None
            else {"ok": False, "tracks": {}, "failures": []}
        )
        return SolveOutcome(assignment=partial, error=exc, connectivity=connectivity)


# --------------------------------------------------------------------------
# the run document
# --------------------------------------------------------------------------


def new_run_id(now: Optional[dt.datetime] = None) -> str:
    """Sortable, unique, and readable: ``2026-04-19T09-32-11Z``."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.strftime("%Y-%m-%dT%H-%M-%SZ")


def _tracks_block(
    outcome: SolveOutcome,
    report: Optional[Mapping[str, Any]],
    inputs: SolverInputs,
) -> List[Dict[str, Any]]:
    """``tracks[]`` for the run document -- the per-track preflight table."""
    names = {j.id: j.name for j in inputs.judges}
    overlap = (report or {}).get("pair_overlap", {}).get("per_track", {})
    anchors = getattr(outcome.assignment, "anchors", {}) or {}
    rows = []
    for track, info in (outcome.connectivity.get("tracks") or {}).items():
        rows.append(
            {
                "track": track,
                "judges": info["n_judges"],
                "projects": info["n_projects"],
                "connected": info["connected"],
                "components": info["n_components"],
                "isolatedJudges": list(info["isolated_judges"]),
                # Names as well as ids: the red banner names stranded judges,
                # and an Auth UID means nothing to an organizer at 9am.
                "isolatedJudgeNames": [
                    names.get(jid, jid) for jid in info["isolated_judges"]
                ],
                "overlapPct": overlap.get(track, {}).get("pct", 0.0),
                "anchors": list(anchors.get(track, [])),
            }
        )
    return rows


def build_run_document(
    run_id: str,
    outcome: SolveOutcome,
    inputs: SolverInputs,
    config: SolverConfig,
    *,
    generated_by: str,
    generated_at: Any,
) -> Dict[str, Any]:
    """The ``runs/{runId}`` document, exactly as docs/runs-contract.md defines it.

    Works for both outcomes. On failure the coverage/workload blocks come from
    the assignment that failed the gate, so the organizer can see how close it
    got, and ``connectivityOk`` is false so the console blocks on it.
    """
    assignment = outcome.assignment
    report = assignment.preflight() if assignment is not None else None
    projects_by_id = {p.id: p for p in inputs.projects}

    doc: Dict[str, Any] = {
        "runId": run_id,
        "generatedAt": generated_at,
        "generatedBy": generated_by,
        "source": SOURCE,
        "status": "ok" if outcome.ok else "failed",
        # The checked-in count, never the roster size. 97 vs 63 is what broke
        # 2026 and the console compares this against live check-ins.
        "judgeCount": len(inputs.judges),
        "projectCount": len(inputs.projects),
        "checkinSnapshotCount": inputs.checkin_snapshot_count,
        "config": {
            "minJudgesPerProject": config.min_judges_per_project,
            "anchorsPerTrack": config.anchors_per_track,
            "maxProjectsPerJudge": config.max_projects_per_judge,
            "balanceMultiTrack": config.balance_multi_track,
            "topUpUndersizedTracks": config.top_up_undersized_tracks,
        },
        "connectivityOk": bool(outcome.ok and outcome.connectivity.get("ok")),
        "tracks": _tracks_block(outcome, report, inputs),
        "warnings": list(inputs.warnings)
        + list(getattr(assignment, "warnings", []) or []),
        "errors": [] if outcome.ok else [str(outcome.error)],
        "judgeIndex": {
            judge.id: {
                "judgeDocId": inputs.judge_doc_ids.get(judge.id, judge.id),
                "name": judge.name,
                "track": judge.track,
            }
            for judge in inputs.judges
        },
    }

    if report is None:
        return doc

    cov = report["coverage"]
    doc["coverage"] = {
        # Firestore map keys must be strings.
        "histogram": {str(k): v for k, v in cov["histogram"].items()},
        "min": cov["min"],
        "median": cov["median"],
        "mean": cov["mean"],
        "max": cov["max"],
        "nProjects": cov["n_projects"],
        "unjudged": cov["unjudged"],
        "belowTarget": [
            {
                "projectId": pid,
                "name": getattr(projects_by_id.get(pid), "name", pid),
                "judges": n,
                "tableNumber": getattr(projects_by_id.get(pid), "table_number", None),
            }
            for pid, n in report["projects_below_target"]
        ],
    }
    doc["pairOverlapPct"] = report["pair_overlap"]["pct"]
    doc["pairOverlapWithinTrackPct"] = report["pair_overlap_within_track"]["pct"]
    doc["workload"] = {
        **{
            k: report["workload"]["overall"][k]
            for k in ("min", "median", "mean", "max", "stdev")
        },
        "idleJudges": report["workload"]["idle_judges"],
    }
    return doc


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------


def write_firestore_run(
    doc: Mapping[str, Any],
    *,
    service_account_path: str = DEFAULT_SERVICE_ACCOUNT,
) -> str:
    """Write ``runs/{runId}``. THE ONLY FIRESTORE WRITE IN pipeline/databricks/.

    Reached only from ``main()`` under ``--commit``. ``runs`` is append-only in
    practice -- a new run id per solve -- so this never overwrites an earlier
    run, and it touches no other collection.
    """
    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(service_account_path))
    db = firestore.client()

    payload = dict(doc)
    payload["generatedAt"] = firestore.SERVER_TIMESTAMP
    run_id = payload["runId"]
    db.collection("runs").document(run_id).set(payload)
    return run_id


def assignment_rows(
    run_id: str,
    outcome: SolveOutcome,
    inputs: SolverInputs,
    generated_at: dt.datetime,
) -> List[Dict[str, Any]]:
    """Flat rows for the Delta ``assignments`` table."""
    if outcome.assignment is None:
        return []
    projects_by_id = {p.id: p for p in inputs.projects}
    rows = []
    for record in outcome.assignment.to_records():
        project = projects_by_id.get(record["project_id"])
        rows.append(
            {
                "run_id": run_id,
                "judge_id": record["judge_id"],
                "project_id": record["project_id"],
                "is_anchor": bool(record["is_anchor"]),
                "track": record["track"],
                "judge_name": record["judge_name"],
                "judge_doc_id": inputs.judge_doc_ids.get(record["judge_id"]),
                "project_name": record["project_name"],
                "table_number": getattr(project, "table_number", None),
                "generated_at": generated_at,
            }
        )
    return rows


def run_row(
    doc: Mapping[str, Any],
    *,
    generated_at: dt.datetime,
    assignment_count: int,
    published_to_firestore: bool,
) -> Dict[str, Any]:
    """The Firestore run document, flattened for the Delta ``runs`` table."""
    coverage = doc.get("coverage") or {}
    config = doc.get("config") or {}
    return {
        "run_id": doc["runId"],
        "generated_at": generated_at,
        "generated_by": doc.get("generatedBy"),
        "source": doc.get("source"),
        "status": doc.get("status"),
        "judge_count": doc.get("judgeCount"),
        "project_count": doc.get("projectCount"),
        "checkin_snapshot_count": doc.get("checkinSnapshotCount"),
        "connectivity_ok": doc.get("connectivityOk"),
        "min_judges_per_project": config.get("minJudgesPerProject"),
        "anchors_per_track": config.get("anchorsPerTrack"),
        "projects_below_target": len(coverage.get("belowTarget") or []),
        "unjudged_count": len(coverage.get("unjudged") or []),
        "pair_overlap_pct": doc.get("pairOverlapPct"),
        "pair_overlap_within_track_pct": doc.get("pairOverlapWithinTrackPct"),
        "assignment_count": assignment_count,
        "config_json": json.dumps(config, sort_keys=True),
        "coverage_json": json.dumps(coverage, sort_keys=True, default=str),
        "workload_json": json.dumps(doc.get("workload") or {}, sort_keys=True),
        "tracks_json": json.dumps(doc.get("tracks") or [], sort_keys=True),
        "warnings": list(doc.get("warnings") or []),
        "errors": list(doc.get("errors") or []),
        "published_to_firestore": published_to_firestore,
        "ingested_at": generated_at,
    }


def write_delta(client, rows: Sequence[Mapping[str, Any]], table_name: str) -> int:
    table = schema_module.TABLES[table_name]
    return client.merge_rows(
        client.settings.table(table_name), table.columns, table.key_columns, rows
    )


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def render(doc: Mapping[str, Any], outcome: SolveOutcome) -> str:
    lines = ["=" * 72, f"ASSIGNMENT RUN {doc['runId']}", "=" * 72]
    if outcome.assignment is not None:
        lines.append(outcome.assignment.summary())
    lines += [
        "",
        f"status                : {doc['status']}",
        f"connectivityOk        : {doc['connectivityOk']}",
        f"judgeCount            : {doc['judgeCount']}  (checked in: "
        f"{doc['checkinSnapshotCount']})",
        f"projectCount          : {doc['projectCount']}",
        f"assignments           : "
        f"{sum(len(v) for v in (outcome.assignment.judge_projects.values() if outcome.assignment else []))}",
    ]
    if doc.get("errors"):
        lines += ["", "ERRORS (the console renders these as a red banner):"]
        for message in doc["errors"]:
            lines += ["  " + line for line in str(message).splitlines()]
    if doc.get("warnings"):
        lines += ["", f"WARNINGS ({len(doc['warnings'])}):"]
        lines += [f"  {w}" for w in doc["warnings"][:15]]
        if len(doc["warnings"]) > 15:
            lines.append(f"  ... and {len(doc['warnings']) - 15} more")
    lines.append("=" * 72)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.databricks.publish_run",
        description=(
            "Solve the judge->project assignment for the judges who checked "
            "in, then publish the run. Dry run unless --commit."
        ),
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="write runs/{runId} to Firestore and the assignment to Delta. "
        "Without it nothing is written anywhere.",
    )
    parser.add_argument(
        "--checkins",
        choices=("firestore", "csv"),
        default="firestore",
        help="where the checked-in judge list comes from (default: firestore).",
    )
    parser.add_argument("--checkin-csv", default=DEFAULT_CHECKIN_CSV)
    parser.add_argument("--service-account", default=DEFAULT_SERVICE_ACCOUNT)
    parser.add_argument("--synthetic-csv", default=ingest_2026.DEFAULT_SYNTHETIC_CSV)
    parser.add_argument("--project-list", default=ingest_2026.DEFAULT_PROJECT_LIST)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--min-judges", type=int, default=3)
    parser.add_argument("--anchors", type=int, default=3)
    parser.add_argument("--max-projects-per-judge", type=int, default=None)
    parser.add_argument(
        "--generated-by",
        default=os.environ.get("USER", "unknown"),
        help="who ran it; shown in the console.",
    )
    parser.add_argument("--run-id", default=None, help="override the generated run id.")
    parser.add_argument(
        "--no-firestore",
        action="store_true",
        help="with --commit, write Delta but not Firestore.",
    )
    parser.add_argument(
        "--no-delta",
        action="store_true",
        help="with --commit, write Firestore but not Delta.",
    )
    parser.add_argument(
        "--out", default=None, help="also save the run document as JSON here."
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    print("Reading Firestore (read-only) ...")
    raw = ingest_2026.fetch_firestore(args.service_account, ("judges", "projects"))
    uid_to_email = ingest_2026.fetch_auth_uids(args.service_account)

    if args.checkins == "csv":
        checked_in = read_checkins_from_csv(args.checkin_csv)
        print(f"  check-in source: {args.checkin_csv} ({len(checked_in)} checked in)")
    else:
        checked_in = read_checkins_from_firestore(args.service_account)
        print(f"  check-in source: checkins collection ({len(checked_in)} checked in)")

    if not checked_in:
        print(
            "\nNobody is checked in, so there is nothing to solve.\n"
            "The `checkins` collection is written by the organizer console as "
            "judges arrive. To rehearse against the 2026 data instead, run:\n"
            "    python -m pipeline.databricks.publish_run --checkins csv"
        )
        return 1

    title_index = ingest_2026.TitleIndex.build(
        synthetic_csv=args.synthetic_csv, project_list_csv=args.project_list
    )
    inputs = build_solver_inputs(
        raw["judges"], raw["projects"], checked_in, uid_to_email, title_index
    )
    print(
        f"  solving for {len(inputs.judges)} judges over "
        f"{len(inputs.projects)} real projects"
    )

    config = SolverConfig(
        min_judges_per_project=args.min_judges,
        anchors_per_track=args.anchors,
        max_projects_per_judge=args.max_projects_per_judge,
    )
    outcome = solve_capturing(inputs.judges, inputs.projects, config)

    generated_at = dt.datetime.now(dt.timezone.utc)
    run_id = args.run_id or new_run_id(generated_at)
    doc = build_run_document(
        run_id,
        outcome,
        inputs,
        config,
        generated_by=args.generated_by,
        generated_at=generated_at.isoformat(),
    )

    print()
    print(render(doc, outcome))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(doc, handle, indent=2, default=str)
        print(f"\nrun document written to {args.out}")

    if not args.commit:
        print(
            "\nDRY RUN -- nothing was written.\n"
            "  Firestore runs/{id}: not written (only --commit may write to "
            "Firestore)\n"
            "  Delta assignments/runs: not written\n"
            "Re-run with --commit to publish."
        )
        return 0 if outcome.ok else 1

    published = False
    if not args.no_firestore:
        write_firestore_run(doc, service_account_path=args.service_account)
        published = True
        print(f"\nwrote Firestore runs/{run_id}")

    if not args.no_delta:
        try:
            client = connect(dotenv_path=args.env_file)
        except MissingCredentialsError as exc:
            print(exc)
            return 2
        rows = assignment_rows(run_id, outcome, inputs, generated_at)
        n = write_delta(client, rows, "assignments")
        write_delta(
            client,
            [
                run_row(
                    doc,
                    generated_at=generated_at,
                    assignment_count=len(rows),
                    published_to_firestore=published,
                )
            ],
            "runs",
        )
        print(f"wrote {n} rows to {client.settings.table('assignments')}")
        print(f"wrote 1 row to {client.settings.table('runs')}")

    return 0 if outcome.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
