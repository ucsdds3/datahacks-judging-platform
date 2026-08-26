"""
The Delta tables, and the DDL that creates them.

Everything here is idempotent: ``CREATE CATALOG / SCHEMA / TABLE IF NOT
EXISTS``. Re-running ``--create`` on a populated workspace is a no-op. There is
no DROP and no ``CREATE OR REPLACE`` anywhere in this file, and there must not
be -- these tables hold the archive of a real event.

Five tables:

    judges        one row per judge document in Firestore, including the stale
                  generations, tagged with which generation it belongs to
    projects      one row per project document, tagged synthetic vs real and
                  with the evidence for that call
    evaluations   one row per submitted score. The four track=null rows from
                  2026 are IN this table with include_in_analysis=false --
                  filtering them out at load time is how they went unnoticed
    assignments   one row per (run, judge, project) the solver produced
    runs          one row per assignment run; mirrors docs/runs-contract.md,
                  which is what the organizer console reads out of Firestore

Nested structures (coverage histograms, per-track preflight, solver config) are
stored as JSON strings rather than as structs. Reasons: the shape is defined by
the runs contract and evolves with it, a JSON string survives a contract change
without a schema migration, and ``from_json`` / ``:`` path access in Databricks
SQL reads it back fine. Where a field is worth querying directly it is also
promoted to a real typed column.

Run it
------
    python -m pipeline.databricks.schema            # show what exists
    python -m pipeline.databricks.schema --create   # create anything missing
    python -m pipeline.databricks.schema --sql      # print DDL, touch nothing
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .connection import DatabricksClient, MissingCredentialsError, Settings, connect

Column = Tuple[str, str]


@dataclass(frozen=True)
class Table:
    """One Delta table: its columns, its key, and why it exists."""

    name: str
    comment: str
    columns: Tuple[Column, ...]
    key_columns: Tuple[str, ...]

    @property
    def column_names(self) -> List[str]:
        return [name for name, _ in self.columns]

    def create_sql(self, settings: Settings) -> str:
        body = ",\n  ".join(f"`{name}` {sql_type}" for name, sql_type in self.columns)
        return (
            f"CREATE TABLE IF NOT EXISTS {settings.table(self.name)} (\n"
            f"  {body}\n"
            f") USING DELTA\n"
            f"COMMENT '{self.comment}'"
        )


# --------------------------------------------------------------------------
# 1. judges
# --------------------------------------------------------------------------

JUDGES = Table(
    name="judges",
    comment=(
        "One row per judges/{id} document in Firestore, all generations. "
        "210 docs exist for ~63 real 2026 judges; source_generation and "
        "is_real are how you tell them apart."
    ),
    columns=(
        # The Firestore document id. Real 2026 judges are keyed by username
        # ("AarushiBajaj"); the stale generation uses name slugs.
        ("judge_id", "STRING"),
        ("name", "STRING"),
        ("email", "STRING"),
        ("track", "STRING"),
        # "username_2026"  -- the real 2026 judge docs
        # "legacy_judge_datahacks" -- dry-run leftovers (@judge.datahacks)
        # "legacy_slug"    -- older name-slug ids
        ("source_generation", "STRING"),
        ("is_real", "BOOLEAN"),
        # Firebase Auth UID, when the email resolves to an account. This is the
        # key evaluations.judgeId uses, and the only join between the two.
        ("auth_uid", "STRING"),
        ("has_auth_account", "BOOLEAN"),
        ("assigned_project_count", "INT"),
        ("ingested_at", "TIMESTAMP"),
    ),
    key_columns=("judge_id",),
)


# --------------------------------------------------------------------------
# 2. projects
# --------------------------------------------------------------------------

PROJECTS = Table(
    name="projects",
    comment=(
        "One row per projects/{id} document. 212 of the 372 are synthetic "
        "dry-run leftovers; classification_source records how each call was "
        "made so nobody has to take it on faith."
    ),
    columns=(
        ("project_id", "STRING"),
        ("title", "STRING"),
        ("tracks", "ARRAY<STRING>"),
        ("table_number", "INT"),
        ("is_synthetic", "BOOLEAN"),
        # How is_synthetic was decided. One of:
        #   docid_exact       document id == slug(title) in one of the CSVs
        #   docid_normalized  matched only after collapsing repeated separators
        #                     and stripping trailing ones ("house-m-d-")
        #   title_exact       matched on the document's own name field
        #   unresolved        in neither CSV; treated as real, flagged here
        ("classification_source", "STRING"),
        # Which CSV it matched: synthetic_projects_generated.csv or
        # "Final_project_info - Sheet1.csv".
        ("classification_evidence", "STRING"),
        # Corroborating signal: only the synthetic docs carry builtWith.
        ("has_built_with", "BOOLEAN"),
        ("evaluation_count", "INT"),
        ("ingested_at", "TIMESTAMP"),
    ),
    key_columns=("project_id",),
)


# --------------------------------------------------------------------------
# 3. evaluations
# --------------------------------------------------------------------------

EVALUATIONS = Table(
    name="evaluations",
    comment=(
        "One row per evaluations/{id} document, INCLUDING the four 2026 rows "
        "with track=null. Those have include_in_analysis=false. Do not filter "
        "them out at load time -- dropping them silently is how they were "
        "lost the first time."
    ),
    columns=(
        ("evaluation_id", "STRING"),
        # Firebase Auth UID. Join to judges.auth_uid, not judges.judge_id.
        ("judge_id", "STRING"),
        ("project_id", "STRING"),
        ("track", "STRING"),
        # criterion name -> score. Criterion names differ per track, so a map
        # rather than five columns.
        ("scores", "MAP<STRING, INT>"),
        ("n_criteria", "INT"),
        ("total_score", "DOUBLE"),
        ("comment", "STRING"),
        ("submitted_at", "TIMESTAMP"),
        # false for the four track=null rows and anything with no numeric
        # scores. Analysis queries filter on this; the raw rows stay.
        ("include_in_analysis", "BOOLEAN"),
        ("exclusion_reason", "STRING"),
        ("ingested_at", "TIMESTAMP"),
    ),
    key_columns=("evaluation_id",),
)


# --------------------------------------------------------------------------
# 4. assignments
# --------------------------------------------------------------------------

ASSIGNMENTS = Table(
    name="assignments",
    comment=(
        "One row per (run_id, judge_id, project_id) the solver produced. "
        "Anchors are the projects every judge in a track scores -- they are "
        "what makes the judge-project graph connected on purpose rather than "
        "by luck."
    ),
    columns=(
        ("run_id", "STRING"),
        ("judge_id", "STRING"),
        ("project_id", "STRING"),
        ("is_anchor", "BOOLEAN"),
        ("track", "STRING"),
        ("judge_name", "STRING"),
        ("judge_doc_id", "STRING"),
        ("project_name", "STRING"),
        ("table_number", "INT"),
        ("generated_at", "TIMESTAMP"),
    ),
    key_columns=("run_id", "judge_id", "project_id"),
)


# --------------------------------------------------------------------------
# 5. runs
# --------------------------------------------------------------------------

RUNS = Table(
    name="runs",
    comment=(
        "One row per assignment run. Mirrors docs/runs-contract.md, i.e. the "
        "runs/{runId} document the organizer console reads. connectivity_ok "
        "false is the red blocking banner."
    ),
    columns=(
        ("run_id", "STRING"),
        ("generated_at", "TIMESTAMP"),
        ("generated_by", "STRING"),
        ("source", "STRING"),
        ("status", "STRING"),  # ok | failed
        # The checked-in count, never the roster size. Feeding the solver a
        # 97-name roster when 63 people showed up is what broke 2026.
        ("judge_count", "INT"),
        ("project_count", "INT"),
        ("checkin_snapshot_count", "INT"),
        ("connectivity_ok", "BOOLEAN"),
        ("min_judges_per_project", "INT"),
        ("anchors_per_track", "INT"),
        ("projects_below_target", "INT"),
        ("unjudged_count", "INT"),
        ("pair_overlap_pct", "DOUBLE"),
        ("pair_overlap_within_track_pct", "DOUBLE"),
        ("assignment_count", "INT"),
        # JSON blobs, shaped exactly like the Firestore document's maps.
        ("config_json", "STRING"),
        ("coverage_json", "STRING"),
        ("workload_json", "STRING"),
        ("tracks_json", "STRING"),
        ("warnings", "ARRAY<STRING>"),
        ("errors", "ARRAY<STRING>"),
        # true once the same run has been written to Firestore runs/{runId}.
        ("published_to_firestore", "BOOLEAN"),
        ("ingested_at", "TIMESTAMP"),
    ),
    key_columns=("run_id",),
)


TABLES: Dict[str, Table] = {
    t.name: t for t in (JUDGES, PROJECTS, EVALUATIONS, ASSIGNMENTS, RUNS)
}

TABLE_ORDER: Tuple[str, ...] = ("judges", "projects", "evaluations", "assignments", "runs")


# --------------------------------------------------------------------------
# DDL
# --------------------------------------------------------------------------


def ddl_statements(settings: Settings) -> List[str]:
    """Every statement needed to bring an empty workspace up, in order.

    All of them are ``IF NOT EXISTS``. Running the list twice changes nothing.
    """
    statements = [
        f"CREATE CATALOG IF NOT EXISTS {settings.catalog}",
        f"CREATE SCHEMA IF NOT EXISTS {settings.full_schema} "
        f"COMMENT 'DataHacks judging platform: event archive and assignment runs'",
    ]
    statements += [TABLES[name].create_sql(settings) for name in TABLE_ORDER]
    return statements


def create_all(
    client: DatabricksClient,
    *,
    on_progress: Any = print,
) -> List[str]:
    """Run the DDL. Returns the statements that were executed.

    If ``CREATE CATALOG`` is denied (some Databricks tiers do not allow new
    top-level catalogs) this raises with an explanation and the exact fix,
    rather than failing later with "schema not found".
    """
    settings = client.settings
    executed: List[str] = []
    for statement in ddl_statements(settings):
        label = " ".join(statement.split())[:78]
        on_progress(f"  {label}")
        try:
            client.sql(statement)
        except Exception as exc:
            if statement.startswith("CREATE CATALOG"):
                raise RuntimeError(
                    f"Could not create the catalog {settings.catalog!r}:\n"
                    f"  {exc}\n"
                    "Some Databricks tiers do not let you create top-level "
                    "catalogs. Set DATABRICKS_CATALOG=workspace in .env and "
                    "re-run -- the workspace catalog exists in every Unity "
                    "Catalog workspace and everything else works unchanged."
                ) from exc
            raise
        executed.append(statement)
    return executed


def describe(client: DatabricksClient) -> Dict[str, Optional[int]]:
    """Row count per table, or None where the table does not exist yet."""
    counts: Dict[str, Optional[int]] = {}
    for name in TABLE_ORDER:
        try:
            counts[name] = client.count(client.settings.table(name))
        except Exception:
            counts[name] = None
    return counts


def render_status(settings: Settings, counts: Dict[str, Optional[int]]) -> str:
    lines = [
        "=" * 62,
        f"DELTA TABLES IN {settings.full_schema}",
        "=" * 62,
    ]
    for name in TABLE_ORDER:
        count = counts.get(name)
        shown = "does not exist" if count is None else f"{count} rows"
        lines.append(f"  {name:<14} {shown}")
    lines.append("=" * 62)
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Create (idempotently) or inspect the Delta tables."
    )
    parser.add_argument(
        "--create",
        action="store_true",
        help="run the CREATE ... IF NOT EXISTS statements. Safe to repeat.",
    )
    parser.add_argument(
        "--sql",
        action="store_true",
        help="print the DDL and exit without connecting to anything.",
    )
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args(argv)

    try:
        client = connect(dotenv_path=args.env_file)
    except MissingCredentialsError as exc:
        print(exc)
        return 2

    if args.sql:
        for statement in ddl_statements(client.settings):
            print(statement + ";\n")
        return 0

    if args.create:
        print(f"Creating tables in {client.settings.full_schema} (idempotent):")
        try:
            create_all(client)
        except RuntimeError as exc:
            print(exc)
            return 1

    print(render_status(client.settings, describe(client)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
