"""
The CLI: extract -> transform -> validate -> load.

This one command replaces scripts/assign.js, scripts/generate-and-assign.js,
scripts/reset-and-upload.js and sync-assignments.js. Those four each rolled
their own CSV parser and their own ID scheme, which is how production ended up
with 210 judge documents across three incompatible ID formats.

Usage
-----
Dry run against the local CSVs (this is the default -- it writes nothing)::

    python -m pipeline.etl.run

Write the result to a directory you can diff::

    python -m pipeline.etl.run --out build/etl --commit

See what would change in Firestore, without changing it::

    python -m pipeline.etl.run --backend firestore

Actually write to Firestore (needs an explicit --commit; think first)::

    python -m pipeline.etl.run --backend firestore --commit

Exit codes
----------
0  clean
1  records were rejected (re-run with --allow-rejects to load the rest anyway)
2  fatal: unknown track, ID collision, missing column, unreadable file
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from . import extract, transform
from .load import FirestoreWriter, LoadError, LocalWriter, WritePlan

ASSETS = os.path.join("src", "assets")
DEFAULT_JUDGE_ROSTER = os.path.join(
    ASSETS, "Mentors_Judges Calendar & Sign-Up - 4_19 Judge.csv"
)
DEFAULT_JUDGE_CREDENTIALS = os.path.join(ASSETS, "password_judges - Checked-In.csv")
DEFAULT_PROJECT_LIST = os.path.join(ASSETS, "Final_project_info - Sheet1.csv")
DEFAULT_SERVICE_ACCOUNT = os.path.join(ASSETS, "serviceAccount.json")


# --------------------------------------------------------------------------
# validation report
# --------------------------------------------------------------------------


@dataclass
class ValidationReport:
    """Counts in, counts out, and every record that did not make it.

    Deterministic: the same inputs produce the same report, so it can be
    committed next to the output and diffed between runs.
    """

    source_counts: Dict[str, int] = field(default_factory=dict)
    output_counts: Dict[str, int] = field(default_factory=dict)
    rejections: List[transform.Rejection] = field(default_factory=list)
    notes: List[transform.Note] = field(default_factory=list)
    plans: List[WritePlan] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejections

    def note_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for note in self.notes:
            counts[note.kind] = counts.get(note.kind, 0) + 1
        return dict(sorted(counts.items()))

    def rejection_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for rejection in self.rejections:
            counts[rejection.reason] = counts.get(rejection.reason, 0) + 1
        return dict(sorted(counts.items()))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sourceCounts": self.source_counts,
            "outputCounts": self.output_counts,
            "rejectionCounts": self.rejection_counts(),
            "noteCounts": self.note_counts(),
            "rejections": [
                {
                    "source": r.source,
                    "line": r.line_number,
                    "identity": r.identity,
                    "reason": r.reason,
                    "detail": r.detail,
                }
                for r in self.rejections
            ],
            "notes": [
                {
                    "source": n.source,
                    "line": n.line_number,
                    "identity": n.identity,
                    "kind": n.kind,
                    "detail": n.detail,
                }
                for n in self.notes
            ],
            "plans": [
                {
                    "collection": p.collection,
                    "backend": p.backend,
                    "dryRun": p.dry_run,
                    "create": p.creates,
                    "update": p.updates,
                    "unchanged": p.unchanged,
                    "orphans": p.orphans,
                    "legacyDuplicates": [
                        {"incoming": incoming, "orphan": orphan}
                        for incoming, orphan in p.legacy_duplicates
                    ],
                }
                for p in self.plans
            ],
        }

    def render(self, *, verbose: bool = False) -> str:
        lines: List[str] = []
        rule = "=" * 68

        lines.append(rule)
        lines.append("DataHacks ingestion -- validation report")
        lines.append(rule)

        lines.append("")
        lines.append("rows read")
        for name, count in self.source_counts.items():
            shown = "not supplied" if count < 0 else str(count)
            lines.append(f"  {name:22} {shown}")

        lines.append("")
        lines.append("records built")
        for name, count in self.output_counts.items():
            lines.append(f"  {name:22} {count}")

        lines.append("")
        if self.rejections:
            lines.append(f"REJECTED  {len(self.rejections)} record(s) -- NOT loaded")
            for reason, count in self.rejection_counts().items():
                lines.append(f"  {reason:22} {count}")
            lines.append("")
            for rejection in self.rejections:
                lines.append(f"  ! {rejection.render()}")
        else:
            lines.append("rejected                 0")

        if self.notes:
            lines.append("")
            lines.append(f"needs a human ({len(self.notes)} note(s))")
            for kind, count in self.note_counts().items():
                lines.append(f"  {kind:26} {count}")
            if verbose:
                lines.append("")
                for note in self.notes:
                    lines.append(f"  - {note.render()}")
            else:
                lines.append("  (--verbose to list them individually)")

        if self.plans:
            lines.append("")
            lines.append("what a load would change")
            for plan in self.plans:
                lines.append(f"  [{plan.backend}] {plan.render()}")

            duplicates = sum(len(p.legacy_duplicates) for p in self.plans)
            if duplicates:
                lines.append("")
                lines.append(
                    f"WARNING: {duplicates} destination document(s) look like an "
                    f"older-ID copy of a record this run would write. Committing "
                    f"does not remove them -- decide what happens to the old "
                    f"generation first."
                )

        lines.append("")
        lines.append(rule)
        return "\n".join(lines)


# --------------------------------------------------------------------------
# pipeline
# --------------------------------------------------------------------------


@dataclass
class PipelineOutput:
    projects: List[transform.ProjectRecord]
    judges: List[transform.JudgeRecord]
    report: ValidationReport


def run_pipeline(
    *,
    judge_roster_path: str,
    judge_credentials_path: str,
    project_list_path: str,
    devpost_path: Optional[str] = None,
) -> PipelineOutput:
    """extract -> transform -> validate. No writing happens here at all.

    Raises on anything structural (missing column, unreadable file, ID
    collision). Per-record problems come back in ``report.rejections``.
    """
    inputs = extract.extract_all(
        judge_roster_path=judge_roster_path,
        judge_credentials_path=judge_credentials_path,
        project_list_path=project_list_path,
        devpost_path=devpost_path,
    )

    project_result = transform.build_projects(inputs.project_list)
    projects, provenance_notes = transform.classify_provenance(
        project_result.records, inputs.devpost
    )
    judge_result = transform.build_judges(
        inputs.judge_credentials, inputs.judge_roster
    )

    report = ValidationReport(
        source_counts=inputs.counts(),
        output_counts={
            "projects": len(projects),
            "judges": len(judge_result.records),
            "projects_unverified": sum(1 for p in projects if p.needs_review),
            "judges_needing_review": sum(
                1 for j in judge_result.records if j.needs_review
            ),
        },
        rejections=list(project_result.rejections) + list(judge_result.rejections),
        notes=(
            list(project_result.notes)
            + list(provenance_notes)
            + list(judge_result.notes)
        ),
    )
    return PipelineOutput(projects=projects, judges=judge_result.records, report=report)


def _build_writer(args: argparse.Namespace):
    if args.backend == "local":
        return LocalWriter(args.out, fmt=args.format, dry_run=not args.commit)
    return FirestoreWriter(args.service_account, dry_run=not args.commit)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.etl.run",
        description="Idempotent ingestion for the DataHacks judging platform.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--judge-roster", default=DEFAULT_JUDGE_ROSTER)
    parser.add_argument("--judge-credentials", default=DEFAULT_JUDGE_CREDENTIALS)
    parser.add_argument("--project-list", default=DEFAULT_PROJECT_LIST)
    parser.add_argument(
        "--devpost",
        default=None,
        help="DevPost submissions export. Without it, every project is marked "
        "provenance=unverified_no_export rather than assumed real.",
    )
    parser.add_argument(
        "--backend", choices=("local", "firestore"), default="local",
    )
    parser.add_argument(
        "--out", default=os.path.join("build", "etl"),
        help="output directory for --backend local (default: build/etl)",
    )
    parser.add_argument("--format", choices=("csv", "parquet"), default="csv")
    parser.add_argument("--service-account", default=DEFAULT_SERVICE_ACCOUNT)

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", dest="commit", action="store_false", default=False,
        help="plan only, write nothing (THE DEFAULT)",
    )
    mode.add_argument(
        "--commit", dest="commit", action="store_true",
        help="actually write. Required for any write, local or Firestore.",
    )

    parser.add_argument(
        "--allow-rejects", action="store_true",
        help="load the valid records even though some were rejected. Off by "
        "default: a rejection usually means the export is wrong.",
    )
    parser.add_argument(
        "--report", default=None, help="also write the report as JSON to this path",
    )
    parser.add_argument("--verbose", action="store_true", help="list every note")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        output = run_pipeline(
            judge_roster_path=args.judge_roster,
            judge_credentials_path=args.judge_credentials,
            project_list_path=args.project_list,
            devpost_path=args.devpost,
        )
    except (extract.ExtractError, transform.TransformError, ValueError) as exc:
        print(f"\nFATAL: {exc}\n", file=sys.stderr)
        return 2

    report = output.report

    try:
        writer = _build_writer(args)
        report.plans = [
            writer.plan("projects", output.projects),
            writer.plan("judges", output.judges),
        ]
    except LoadError as exc:
        print(report.render(verbose=args.verbose))
        print(f"\nFATAL: {exc}\n", file=sys.stderr)
        return 2

    print(report.render(verbose=args.verbose))

    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
        print(f"report written to {args.report}")

    if not args.commit:
        print(
            "\nDRY RUN -- nothing was written. Re-run with --commit to apply.\n"
        )
        return 0 if report.ok else 1

    if report.rejections and not args.allow_rejects:
        print(
            f"\nREFUSING TO LOAD: {len(report.rejections)} record(s) were "
            f"rejected.\nFix the source data, or re-run with --allow-rejects to "
            f"load the rest.\n",
            file=sys.stderr,
        )
        return 1

    try:
        for plan in report.plans:
            count = writer.commit(plan)
            print(f"wrote {count} document(s) to {plan.collection}")
    except LoadError as exc:
        print(f"\nFATAL: {exc}\n", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
