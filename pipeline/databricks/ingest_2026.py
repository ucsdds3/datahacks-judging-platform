"""
Land the DataHacks 2026 event data in Delta.

    *** THIS SCRIPT NEVER WRITES TO FIRESTORE. ***
    Firestore access is .stream() and auth.list_users() only. There is no
    .set / .update / .delete / .add anywhere in this file, and test_ingest_2026
    greps the source to keep it that way. Production holds real scores from a
    real event.

What it does
------------
Reads judges / projects / evaluations out of Firestore, works out which
projects are synthetic dry-run leftovers and which are real submissions, and
upserts all three into the Delta tables from schema.py.

Project classification
----------------------
Resolved from the two CSVs already in the repo -- the DevPost export is not
needed for this:

    src/assets/synthetic_projects_generated.csv   the 212 synthetic ones
    src/assets/Final_project_info - Sheet1.csv    the real submission list

The document ids were produced by an old slugger that left repeated and
trailing separators behind ("house-m-d-" for "House M.D.", "-" for the project
literally titled "ㄖ"). So matching happens in three passes, and which pass hit
is recorded in ``projects.classification_source``:

    docid_exact       document id already equals slug(title)
    docid_normalized  equal only after collapsing repeated separators and
                      stripping the trailing ones
    title_exact       matched on the document's own name field instead
    unresolved        in neither CSV -- kept, marked real, and flagged loudly

Nothing is dropped. A project that matches nothing is still loaded, with
classification_source='unresolved', because deleting a row you cannot explain
is how evidence disappears.

The track=null evaluations
--------------------------
Four 2026 evaluations were written with track=null (the rubric lookup fell
through to a generic rubric). They are loaded into ``evaluations`` like
everything else, with ``include_in_analysis=false`` and an
``exclusion_reason``. Analysis queries filter on that flag. Filtering them out
here instead is exactly how they went unnoticed for months.

Run it
------
    python -m pipeline.databricks.ingest_2026                   # dry run
    python -m pipeline.databricks.ingest_2026 --commit          # write Delta
    python -m pipeline.databricks.ingest_2026 --cache /tmp/dh.json
    python -m pipeline.databricks.ingest_2026 --cache /tmp/dh.json --offline
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from pipeline.etl.transform import slugify

from . import schema as schema_module
from .connection import DatabricksClient, MissingCredentialsError, connect

DEFAULT_SERVICE_ACCOUNT = os.path.join("src", "assets", "serviceAccount.json")
DEFAULT_SYNTHETIC_CSV = os.path.join("src", "assets", "synthetic_projects_generated.csv")
DEFAULT_PROJECT_LIST = os.path.join("src", "assets", "Final_project_info - Sheet1.csv")

COLLECTIONS = ("judges", "projects", "evaluations")

SYNTHETIC = "synthetic_projects_generated.csv"
REAL = "Final_project_info - Sheet1.csv"


# --------------------------------------------------------------------------
# read-only Firestore access
# --------------------------------------------------------------------------


def fetch_firestore(
    service_account_path: str = DEFAULT_SERVICE_ACCOUNT,
    collections: Sequence[str] = COLLECTIONS,
) -> Dict[str, List[Dict[str, Any]]]:
    """Stream whole collections out of Firestore. READ ONLY."""
    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(service_account_path))
    db = firestore.client()

    out: Dict[str, List[Dict[str, Any]]] = {}
    for name in collections:
        out[name] = [
            {"_id": doc.id, **(doc.to_dict() or {})}
            for doc in db.collection(name).stream()  # READ ONLY
        ]
    return out


def fetch_auth_uids(
    service_account_path: str = DEFAULT_SERVICE_ACCOUNT,
) -> Dict[str, str]:
    """Firebase Auth UID -> lowercased email. READ ONLY.

    evaluations.judgeId is an Auth UID and judges/{id} is keyed by username;
    email is the only bridge between them.
    """
    import firebase_admin
    from firebase_admin import auth, credentials

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(service_account_path))

    mapping: Dict[str, str] = {}
    page = auth.list_users()  # READ ONLY
    while page:
        for user in page.users:
            mapping[user.uid] = (user.email or "").strip().lower()
        page = page.get_next_page()
    return mapping


# --------------------------------------------------------------------------
# the project-title index
# --------------------------------------------------------------------------


def _title_key(title: str) -> str:
    return " ".join((title or "").strip().lower().split())


def read_titles(path: str, column: str = "Project Title") -> List[str]:
    """Project titles from a CSV. utf-8-sig because Sheets exports carry a BOM."""
    with open(path, "r", newline="", encoding="utf-8-sig") as handle:
        return [
            (row.get(column) or "").strip()
            for row in csv.DictReader(handle)
            if (row.get(column) or "").strip()
        ]


@dataclass
class TitleIndex:
    """Slug and title lookups for both CSVs, so a doc id can be traced back."""

    by_slug: Dict[str, tuple] = field(default_factory=dict)
    by_title: Dict[str, tuple] = field(default_factory=dict)

    @classmethod
    def build(cls, *, synthetic_csv: str, project_list_csv: str) -> "TitleIndex":
        index = cls()
        # Real wins a tie: a title present in both lists is a real submission
        # that the synthetic generator happened to collide with, and calling a
        # real project synthetic is the more expensive mistake.
        for evidence, titles in (
            (SYNTHETIC, read_titles(synthetic_csv)),
            (REAL, read_titles(project_list_csv)),
        ):
            is_synthetic = evidence == SYNTHETIC
            for title in titles:
                index.by_slug[slugify(title)] = (is_synthetic, evidence, title)
                index.by_title[_title_key(title)] = (is_synthetic, evidence, title)
        return index


def classify_project(doc: Mapping[str, Any], index: TitleIndex) -> Dict[str, Any]:
    """Decide synthetic vs real for one Firestore project document.

    Returns is_synthetic / classification_source / classification_evidence.
    Never returns "drop this".
    """
    doc_id = doc.get("_id") or ""
    name = doc.get("name") or ""

    hit = index.by_slug.get(doc_id)
    if hit is not None and slugify(hit[2]) == doc_id:
        source = "docid_exact"
    else:
        hit = index.by_slug.get(slugify(doc_id))
        source = "docid_normalized"
        if hit is None:
            hit = index.by_title.get(_title_key(name))
            source = "title_exact"

    if hit is None:
        return {
            "is_synthetic": False,  # unknown provenance is treated as real
            "classification_source": "unresolved",
            "classification_evidence": (
                "in neither CSV; treated as real -- confirm before deleting"
            ),
        }
    return {
        "is_synthetic": hit[0],
        "classification_source": source,
        "classification_evidence": hit[1],
    }


# --------------------------------------------------------------------------
# judges
# --------------------------------------------------------------------------


def judge_generation(doc: Mapping[str, Any]) -> str:
    """Which generation of judge document this is.

    Three ID schemes were used over the life of this app and all three are
    still sitting in production. The email domain is the cleanest tell: the
    dry-run generation was seeded with @judge.datahacks addresses.
    """
    doc_id = doc.get("_id") or ""
    email = (doc.get("email") or "").strip().lower()
    if email.endswith("@judge.datahacks"):
        return "legacy_judge_datahacks"
    if "-" in doc_id and doc_id == doc_id.lower():
        return "legacy_slug"
    return "username_2026"


def build_judge_rows(
    judges: Sequence[Mapping[str, Any]],
    uid_by_email: Mapping[str, str],
    ingested_at: dt.datetime,
) -> List[Dict[str, Any]]:
    rows = []
    for doc in judges:
        email = (doc.get("email") or "").strip()
        uid = uid_by_email.get(email.lower()) or None
        generation = judge_generation(doc)
        assigned = doc.get("assignedProjects")
        rows.append(
            {
                "judge_id": doc.get("_id") or "",
                "name": (doc.get("name") or "").strip(),
                "email": email,
                "track": (doc.get("track") or "").strip() or None,
                "source_generation": generation,
                "is_real": generation == "username_2026",
                "auth_uid": uid,
                "has_auth_account": uid is not None,
                "assigned_project_count": len(assigned) if isinstance(assigned, list) else 0,
                "ingested_at": ingested_at,
            }
        )
    return rows


# --------------------------------------------------------------------------
# projects
# --------------------------------------------------------------------------


def build_project_rows(
    projects: Sequence[Mapping[str, Any]],
    index: TitleIndex,
    evaluation_counts: Mapping[str, int],
    ingested_at: dt.datetime,
) -> List[Dict[str, Any]]:
    rows = []
    for doc in projects:
        doc_id = doc.get("_id") or ""
        tracks = doc.get("tracks")
        table_number = doc.get("tableNumber")
        row = {
            "project_id": doc_id,
            "title": (doc.get("name") or "").strip(),
            "tracks": [str(t) for t in tracks] if isinstance(tracks, list) else [],
            "table_number": int(table_number) if isinstance(table_number, (int, float)) else None,
            # Corroboration, not the decision: only the synthetic docs were
            # seeded with a builtWith field.
            "has_built_with": "builtWith" in doc,
            "evaluation_count": evaluation_counts.get(doc_id, 0),
            "ingested_at": ingested_at,
        }
        rows.append({**row, **classify_project(doc, index)})
    return rows


# --------------------------------------------------------------------------
# evaluations
# --------------------------------------------------------------------------


def _as_datetime(value: Any) -> Optional[dt.datetime]:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def build_evaluation_rows(
    evaluations: Sequence[Mapping[str, Any]],
    ingested_at: dt.datetime,
) -> List[Dict[str, Any]]:
    """Every evaluation, including the broken ones.

    An evaluation is excluded from analysis (but still stored) when its track
    is null -- the app fell back to a generic rubric, so the scores are not
    comparable to anything -- or when no criterion carries a number.
    """
    rows = []
    for doc in evaluations:
        raw_scores = doc.get("scores")
        scores: Dict[str, int] = {}
        if isinstance(raw_scores, dict):
            for key, value in raw_scores.items():
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)):
                    scores[str(key)] = int(value)

        track = doc.get("track")
        track = track.strip() if isinstance(track, str) and track.strip() else None

        reason = ""
        if track is None:
            reason = (
                "track is null: the rubric lookup fell through to a generic "
                "rubric, so these scores are not comparable within a track"
            )
        elif not scores:
            reason = "no numeric criterion scores"

        rows.append(
            {
                "evaluation_id": doc.get("_id") or "",
                "judge_id": doc.get("judgeId") or None,
                "project_id": doc.get("projectId") or None,
                "track": track,
                "scores": scores,
                "n_criteria": len(scores),
                "total_score": float(sum(scores.values())) if scores else None,
                "comment": doc.get("comment") if isinstance(doc.get("comment"), str) else None,
                "submitted_at": _as_datetime(doc.get("timestamp")),
                "include_in_analysis": not reason,
                "exclusion_reason": reason or None,
                "ingested_at": ingested_at,
            }
        )
    return rows


# --------------------------------------------------------------------------
# putting it together
# --------------------------------------------------------------------------


@dataclass
class IngestResult:
    """Rows built, plus everything a human should look at before --commit."""

    judges: List[Dict[str, Any]] = field(default_factory=list)
    projects: List[Dict[str, Any]] = field(default_factory=list)
    evaluations: List[Dict[str, Any]] = field(default_factory=list)
    written: Dict[str, int] = field(default_factory=dict)

    def summary(self) -> Dict[str, Any]:
        synthetic = sum(1 for p in self.projects if p["is_synthetic"])
        return {
            "judges": len(self.judges),
            "judges_real": sum(1 for j in self.judges if j["is_real"]),
            "judges_with_auth": sum(1 for j in self.judges if j["has_auth_account"]),
            "judge_generations": dict(
                Counter(j["source_generation"] for j in self.judges)
            ),
            "projects": len(self.projects),
            "projects_synthetic": synthetic,
            "projects_real": len(self.projects) - synthetic,
            "classification_sources": dict(
                Counter(p["classification_source"] for p in self.projects)
            ),
            "builtwith_disagreements": [
                p["project_id"]
                for p in self.projects
                if p["has_built_with"] != p["is_synthetic"]
            ],
            "unresolved_projects": [
                p["project_id"]
                for p in self.projects
                if p["classification_source"] == "unresolved"
            ],
            "evaluations": len(self.evaluations),
            "evaluations_in_analysis": sum(
                1 for e in self.evaluations if e["include_in_analysis"]
            ),
            "evaluations_excluded": [
                {"id": e["evaluation_id"], "project": e["project_id"],
                 "reason": e["exclusion_reason"]}
                for e in self.evaluations
                if not e["include_in_analysis"]
            ],
        }

    def render(self) -> str:
        s = self.summary()
        lines = [
            "=" * 72,
            "DATAHACKS 2026 -> DELTA",
            "=" * 72,
            f"judges          : {s['judges']}  "
            f"({s['judges_real']} real, {s['judges_with_auth']} with an Auth account)",
            f"  generations   : {s['judge_generations']}",
            f"projects        : {s['projects']}  "
            f"({s['projects_synthetic']} synthetic, {s['projects_real']} real)",
            f"  matched by    : {s['classification_sources']}",
            f"evaluations     : {s['evaluations']}  "
            f"({s['evaluations_in_analysis']} usable for analysis)",
        ]
        if s["unresolved_projects"]:
            lines += [
                "",
                "UNRESOLVED PROJECTS (kept, marked real, need a human):",
                *(f"  {pid}" for pid in s["unresolved_projects"]),
            ]
        if s["builtwith_disagreements"]:
            lines += [
                "",
                "builtWith field disagrees with the CSV classification for "
                f"{len(s['builtwith_disagreements'])} project(s):",
                *(f"  {pid}" for pid in s["builtwith_disagreements"][:20]),
            ]
        if s["evaluations_excluded"]:
            lines += [
                "",
                f"EXCLUDED FROM ANALYSIS ({len(s['evaluations_excluded'])}) "
                "-- loaded into the raw table, flagged, not dropped:",
                *(
                    f"  {e['id']}  project={e['project']}  {e['reason'][:60]}"
                    for e in s["evaluations_excluded"]
                ),
            ]
        if self.written:
            lines += ["", "WRITTEN TO DELTA:"] + [
                f"  {name:<14} {count} rows" for name, count in self.written.items()
            ]
        lines.append("=" * 72)
        return "\n".join(lines)


def build_rows(
    raw: Mapping[str, Sequence[Mapping[str, Any]]],
    uid_to_email: Mapping[str, str],
    index: TitleIndex,
    *,
    ingested_at: Optional[dt.datetime] = None,
) -> IngestResult:
    """Pure: Firestore documents in, Delta rows out. No network, no clients."""
    ingested_at = ingested_at or dt.datetime.now(dt.timezone.utc)
    uid_by_email = {
        email: uid for uid, email in uid_to_email.items() if email
    }
    evaluation_counts = Counter(
        e.get("projectId") for e in raw.get("evaluations", []) if e.get("projectId")
    )
    return IngestResult(
        judges=build_judge_rows(raw.get("judges", []), uid_by_email, ingested_at),
        projects=build_project_rows(
            raw.get("projects", []), index, evaluation_counts, ingested_at
        ),
        evaluations=build_evaluation_rows(raw.get("evaluations", []), ingested_at),
    )


def write_to_delta(client: DatabricksClient, result: IngestResult) -> Dict[str, int]:
    """Upsert into Delta by primary key. Repeating a run does not duplicate."""
    written: Dict[str, int] = {}
    for table_name, rows in (
        ("judges", result.judges),
        ("projects", result.projects),
        ("evaluations", result.evaluations),
    ):
        table = schema_module.TABLES[table_name]
        written[table_name] = client.merge_rows(
            client.settings.table(table_name),
            table.columns,
            table.key_columns,
            rows,
        )
    return written


# --------------------------------------------------------------------------
# caching, so you can iterate without hammering production
# --------------------------------------------------------------------------


def load_cache(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_cache(path: str, payload: Mapping[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, default=str)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.databricks.ingest_2026",
        description=(
            "Load the 2026 event data from Firestore into Delta. "
            "Reads Firestore, never writes to it. Dry run unless --commit."
        ),
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="actually write to the Delta tables. Without it, nothing is written.",
    )
    parser.add_argument("--service-account", default=DEFAULT_SERVICE_ACCOUNT)
    parser.add_argument("--synthetic-csv", default=DEFAULT_SYNTHETIC_CSV)
    parser.add_argument("--project-list", default=DEFAULT_PROJECT_LIST)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--cache",
        default=None,
        help="save the Firestore snapshot here (and reuse it with --offline).",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="read the snapshot from --cache instead of Firestore.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the summary as JSON instead of a table.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.offline:
        if not args.cache:
            print("--offline needs --cache <path> to read from.")
            return 2
        snapshot = load_cache(args.cache)
        raw = {name: snapshot.get(name, []) for name in COLLECTIONS}
        uid_to_email = snapshot.get("_auth", {})
    else:
        print("Reading Firestore (read-only) ...")
        raw = fetch_firestore(args.service_account)
        uid_to_email = fetch_auth_uids(args.service_account)
        if args.cache:
            save_cache(args.cache, {**raw, "_auth": uid_to_email})
            print(f"  snapshot cached at {args.cache}")

    index = TitleIndex.build(
        synthetic_csv=args.synthetic_csv, project_list_csv=args.project_list
    )
    result = build_rows(raw, uid_to_email, index)

    if args.commit:
        try:
            client = connect(dotenv_path=args.env_file)
        except MissingCredentialsError as exc:
            print(exc)
            return 2
        print(f"Writing to {client.settings.full_schema} ...")
        result.written = write_to_delta(client, result)

    if args.json:
        print(json.dumps(result.summary(), indent=2, default=str))
    else:
        print(result.render())

    if not args.commit:
        print("\nDRY RUN -- nothing was written. Re-run with --commit to load Delta.")
        print("(Firestore is never written to by this script, --commit or not.)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
