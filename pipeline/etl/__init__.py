"""
One idempotent ingestion pipeline for the DataHacks judging platform.

Replaces scripts/assign.js, scripts/generate-and-assign.js,
scripts/reset-and-upload.js and sync-assignments.js -- four scripts that each
hand-rolled a CSV parser and an ID scheme, and between them produced 210 judge
documents in three incompatible ID formats plus 212 synthetic projects living
alongside the 160 real ones in production.

Shape
-----
    extract.py    reads CSVs (or any list-of-lists) -> SourceRow. All I/O.
    transform.py  SourceRow -> validated records. Pure, deterministic, no I/O.
    load.py       records -> LocalWriter or FirestoreWriter, behind one protocol.
    run.py        the CLI that wires them together.

I/O lives at the edges, so the same transforms run over a local CSV today and
over a Databricks table tomorrow: point ``extract.read_rows`` at a different
source and add a writer next to LocalWriter. Nothing in transform.py changes.

Run it
------
    python -m pipeline.etl.run              # dry run, writes nothing
    python -m pipeline.etl.run --help

Everything defaults to dry-run, including the Firestore backend. Writing takes
an explicit --commit.
"""

from .extract import (  # noqa: F401
    ExtractError,
    MalformedRowError,
    MissingColumnsError,
    SourceRow,
    extract_all,
)
from .load import (  # noqa: F401
    DryRunViolation,
    FirestoreWriter,
    LoadError,
    LocalWriter,
    WritePlan,
)
from .transform import (  # noqa: F401
    IdCollisionError,
    IdRegistry,
    JudgeRecord,
    ProjectRecord,
    TransformError,
    build_judges,
    build_projects,
    classify_provenance,
    judge_id,
    project_id,
)

# run.py is imported lazily. Importing it eagerly here would make
# ``python -m pipeline.etl.run`` load the module twice and warn about it.
def __getattr__(name):  # PEP 562
    if name in ("ValidationReport", "run_pipeline", "main"):
        from . import run

        return getattr(run, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DryRunViolation",
    "ExtractError",
    "FirestoreWriter",
    "IdCollisionError",
    "IdRegistry",
    "JudgeRecord",
    "LoadError",
    "LocalWriter",
    "MalformedRowError",
    "MissingColumnsError",
    "ProjectRecord",
    "SourceRow",
    "TransformError",
    "ValidationReport",
    "WritePlan",
    "build_judges",
    "build_projects",
    "classify_provenance",
    "extract_all",
    "judge_id",
    "project_id",
    "run_pipeline",
]
