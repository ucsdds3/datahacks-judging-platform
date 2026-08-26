"""
Writers. One protocol, two backends: a local directory today, Firestore when a
human says so, Spark/Delta later without touching transform.py.

Every writer works in two steps:

    plan   = writer.plan("projects", records)   # read-only. Always safe.
    writer.commit(plan)                         # only if you meant it.

``plan`` reads the destination and diffs it against the incoming records, so the
summary tells you exactly what would change before anything changes. ``commit``
replays that plan and nothing else.

Three deliberate safety properties
----------------------------------
* **Dry-run is the default.** ``FirestoreWriter()`` with no arguments cannot
  write. You have to pass ``dry_run=False`` to get a real write, and the CLI
  makes you type ``--commit`` to do that.
* **Writes merge, they do not replace.** ``assignedProjects`` /
  ``assignedJudges`` are owned by the assignment solver, and evaluations
  reference them. Re-running the ETL updates the fields the ETL owns and leaves
  the rest alone. ``reset-and-upload.js`` deleted whole collections first; that
  is exactly how a live event loses its assignments.
* **Nothing is ever deleted.** Documents in the destination that the pipeline
  did not produce are reported as orphans, with their IDs, for a human to look
  at. The 212 synthetic projects are an orphan-list problem, not a DELETE.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------


class LoadError(RuntimeError):
    """Base class for load-stage failures."""


class DryRunViolation(LoadError):
    """Something tried to write while the writer was in dry-run mode."""


# --------------------------------------------------------------------------
# plan types
# --------------------------------------------------------------------------

CREATE = "create"
UPDATE = "update"
UNCHANGED = "unchanged"


@dataclass(frozen=True)
class WriteAction:
    """What would happen to one document."""

    collection: str
    doc_id: str
    action: str
    changed_fields: tuple = ()
    document: Dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        if self.action == UPDATE:
            return f"  {self.action:9} {self.doc_id}  ({', '.join(self.changed_fields)})"
        return f"  {self.action:9} {self.doc_id}"


@dataclass
class WritePlan:
    """Everything a commit would do to one collection, plus what it would leave."""

    collection: str
    actions: List[WriteAction] = field(default_factory=list)
    orphans: List[str] = field(default_factory=list)
    # (incoming_id, orphan_id) pairs that differ only in punctuation/case --
    # almost certainly the same thing under an older ID scheme.
    legacy_duplicates: List[tuple] = field(default_factory=list)
    backend: str = "?"
    dry_run: bool = True

    def by_action(self, action: str) -> List[WriteAction]:
        return [a for a in self.actions if a.action == action]

    @property
    def creates(self) -> int:
        return len(self.by_action(CREATE))

    @property
    def updates(self) -> int:
        return len(self.by_action(UPDATE))

    @property
    def unchanged(self) -> int:
        return len(self.by_action(UNCHANGED))

    def summary_line(self) -> str:
        return (
            f"{self.collection}: {self.creates} create, {self.updates} update, "
            f"{self.unchanged} unchanged, {len(self.orphans)} orphan "
            f"(never deleted)"
        )

    def render(self, *, max_rows: int = 20) -> str:
        lines = [self.summary_line()]
        interesting = self.by_action(CREATE) + self.by_action(UPDATE)
        for action in interesting[:max_rows]:
            lines.append(action.render())
        if len(interesting) > max_rows:
            lines.append(f"  ... and {len(interesting) - max_rows} more")
        if self.legacy_duplicates:
            lines.append(
                f"  !! {len(self.legacy_duplicates)} orphan(s) look like the SAME "
                f"record under an older ID scheme:"
            )
            for incoming, orphan in self.legacy_duplicates[:max_rows]:
                lines.append(f"       {orphan}  ->  {incoming}")
            if len(self.legacy_duplicates) > max_rows:
                lines.append(
                    f"       ... and {len(self.legacy_duplicates) - max_rows} more"
                )
            lines.append(
                "     Committing leaves both generations in place. Decide what "
                "happens to the old ones before you do."
            )
        if self.orphans:
            shown = ", ".join(sorted(self.orphans)[:10])
            more = "" if len(self.orphans) <= 10 else f" (+{len(self.orphans) - 10} more)"
            lines.append(f"  orphans in destination, left untouched: {shown}{more}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# shared diff logic
# --------------------------------------------------------------------------


def _canonical(value: Any) -> str:
    """The one canonical text form of a value, used both to write and to compare.

    Two documents are "the same" exactly when every field serialises to the same
    string. Going through text is what makes the diff stable across backends,
    which is what makes "run twice -> zero updates" true:

    * A CSV round-trip loses types -- ``404`` comes back as the int 404 even
      though it was the project *name* "404", and ``101`` comes back as a
      string even though it was the int ``tableNumber``. Comparing text sides
      steps around the guessing entirely.
    * An empty string, a missing CSV cell and a Firestore document that simply
      lacks the field all mean the same thing, so all three collapse to ``""``.
    * Tuples and lists are indistinguishable once written.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return json.dumps([_canonical(v) for v in value], ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(
            {str(k): _canonical(v) for k, v in sorted(value.items())},
            ensure_ascii=False,
        )
    return str(value)


def diff_document(
    existing: Optional[Dict[str, Any]], incoming: Dict[str, Any]
) -> "tuple[str, tuple]":
    """Compare one document. Returns ``(action, changed_field_names)``.

    Only fields the pipeline produces are compared. Extra fields already on the
    destination document (``assignedProjects``, ``createdAt``, ...) are ignored
    and preserved -- the pipeline does not own them.
    """
    if existing is None:
        return (CREATE, tuple(sorted(incoming)))

    changed = tuple(
        sorted(
            key
            for key, value in incoming.items()
            if _canonical(existing.get(key)) != _canonical(value)
        )
    )
    return (UPDATE if changed else UNCHANGED, changed)


def build_plan(
    collection: str,
    records: Sequence[Any],
    existing: Dict[str, Dict[str, Any]],
    *,
    backend: str,
    dry_run: bool,
) -> WritePlan:
    """Diff ``records`` against ``existing``. Pure -- no I/O, easy to test."""
    plan = WritePlan(collection=collection, backend=backend, dry_run=dry_run)
    incoming_ids = set()

    for record in sorted(records, key=lambda r: r.id):
        document = record.to_document()
        action, changed = diff_document(existing.get(record.id), document)
        incoming_ids.add(record.id)
        plan.actions.append(
            WriteAction(
                collection=collection,
                doc_id=record.id,
                action=action,
                changed_fields=changed,
                document=document,
            )
        )

    plan.orphans = sorted(set(existing) - incoming_ids)
    plan.legacy_duplicates = find_legacy_duplicates(incoming_ids, plan.orphans)
    return plan


def _shape_key(doc_id: str) -> str:
    """Everything but the letters and digits, thrown away."""
    return "".join(ch for ch in doc_id.lower() if ch.isalnum())


def find_legacy_duplicates(
    incoming_ids: "set", orphans: Sequence[str]
) -> List[tuple]:
    """Spot orphans that are the same record under an older ID scheme.

    ``aarushi-bajaj`` (the old name-slug scheme) and ``AarushiBajaj`` (the
    current username scheme) are the same judge; ``3-sharks-1-blue---`` and
    ``3-sharks-1-blue`` are the same project. Committing writes the new ID and
    leaves the old document sitting there -- which is precisely how production
    ended up holding two generations of everything.

    This cannot be auto-resolved: the old document may carry evaluations. So we
    surface the mapping, loudly, and let a human decide.
    """
    by_shape = {}
    for doc_id in incoming_ids:
        by_shape.setdefault(_shape_key(doc_id), []).append(doc_id)

    pairs: List[tuple] = []
    for orphan in orphans:
        for match in sorted(by_shape.get(_shape_key(orphan), [])):
            if match != orphan:
                pairs.append((match, orphan))
    return sorted(pairs)


# --------------------------------------------------------------------------
# local backend
# --------------------------------------------------------------------------


# Writing a cell and comparing a cell are the same operation: a document is
# unchanged exactly when it would be written identically.
_to_cell = _canonical


def _from_cell(text: str) -> Any:
    """Inverse of ``_to_cell``, best-effort, used only to diff a previous run."""
    if text == "":
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    if text[0] in "[{":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    try:
        return int(text)
    except ValueError:
        return text


class LocalWriter:
    """Writes each collection to ``<directory>/<collection>.csv`` (or .parquet).

    Idempotent by construction: records are sorted by ID, columns are the sorted
    union of every document's keys, and no timestamps go into the payload. Run
    it twice and the file is byte-identical, so ``git diff`` on the output
    directory is a real review artifact.
    """

    backend = "local"

    def __init__(
        self, directory: str, *, fmt: str = "csv", dry_run: bool = False
    ) -> None:
        if fmt not in ("csv", "parquet"):
            raise LoadError(f"LocalWriter: unsupported format {fmt!r}; use csv or parquet.")
        self.directory = directory
        self.fmt = fmt
        self.dry_run = dry_run

    def path_for(self, collection: str) -> str:
        return os.path.join(self.directory, f"{collection}.{self.fmt}")

    def read_existing(self, collection: str) -> Dict[str, Dict[str, Any]]:
        """Load a previous run's output so the plan can show a real diff."""
        path = self.path_for(collection)
        if not os.path.exists(path):
            return {}

        if self.fmt == "parquet":
            frame = _require_pandas().read_parquet(path)
            # Every parquet column is written as a string by _to_cell, so it
            # comes back through the same decoder the CSV backend uses. One
            # decoder, one set of diff results, whichever format you picked.
            return {
                str(row["id"]): {
                    k: _from_cell("" if v is None else str(v))
                    for k, v in row.items()
                    if k != "id"
                }
                for row in frame.to_dict(orient="records")
            }

        with open(path, "r", newline="", encoding="utf-8") as handle:
            return {
                row["id"]: {k: _from_cell(v) for k, v in row.items() if k != "id"}
                for row in csv.DictReader(handle)
            }

    def plan(self, collection: str, records: Sequence[Any]) -> WritePlan:
        return build_plan(
            collection,
            records,
            self.read_existing(collection),
            backend=self.backend,
            dry_run=self.dry_run,
        )

    def commit(self, plan: WritePlan) -> int:
        if self.dry_run:
            raise DryRunViolation(
                "LocalWriter is in dry-run mode; construct it with dry_run=False "
                "to write files."
            )
        os.makedirs(self.directory, exist_ok=True)

        documents = [
            dict(action.document, id=action.doc_id)
            for action in sorted(plan.actions, key=lambda a: a.doc_id)
        ]
        columns = ["id"] + sorted({k for d in documents for k in d if k != "id"})

        path = self.path_for(plan.collection)
        if self.fmt == "parquet":
            frame = _require_pandas().DataFrame(
                [{c: _to_cell(d.get(c)) for c in columns} for d in documents],
                columns=columns,
            )
            frame.to_parquet(path, index=False)
        else:
            # newline="" + \n keeps output identical on Windows and macOS, which
            # matters because "the file did not change" is our idempotency check.
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
                writer.writeheader()
                for document in documents:
                    writer.writerow({c: _to_cell(document.get(c)) for c in columns})
        return len(documents)


def _require_pandas():
    try:
        import pandas  # noqa: F401
    except ImportError:
        raise LoadError(
            "parquet output needs pandas + pyarrow:\n"
            "    pip install -r pipeline/requirements.txt\n"
            "Or use --format csv, which needs nothing extra."
        ) from None
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        raise LoadError(
            "parquet output needs pyarrow (pandas alone is not enough):\n"
            "    pip install pyarrow\n"
            "Or use --format csv."
        ) from None
    import pandas

    return pandas


# --------------------------------------------------------------------------
# Firestore backend
# --------------------------------------------------------------------------


class FirestoreWriter:
    """Firestore backend. **Dry-run by default -- this is not negotiable.**

    ``FirestoreWriter("serviceAccount.json")`` can only read. Getting a real
    write requires ``FirestoreWriter(path, dry_run=False)``, and ``commit``
    re-checks the flag immediately before touching the database.

    Writes use ``set(..., merge=True)``: fields the pipeline owns are updated,
    every other field on the document survives. There is no delete path in this
    class at all.
    """

    backend = "firestore"

    def __init__(
        self,
        service_account_path: str,
        *,
        dry_run: bool = True,  # <- default. Do not change this.
        client: Any = None,
    ) -> None:
        self.service_account_path = service_account_path
        self.dry_run = dry_run
        self._client = client

    def client(self) -> Any:
        """Lazily build the Firestore client. Imported here so that the rest of
        the pipeline runs on a machine without firebase-admin installed."""
        if self._client is not None:
            return self._client
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore
        except ImportError:
            raise LoadError(
                "the Firestore backend needs firebase-admin:\n"
                "    pip install -r pipeline/requirements.txt"
            ) from None

        if not os.path.exists(self.service_account_path):
            raise LoadError(
                f"no service account at {self.service_account_path!r}. "
                f"Pass --service-account, or use --backend local."
            )
        if not firebase_admin._apps:
            firebase_admin.initialize_app(
                credentials.Certificate(self.service_account_path)
            )
        self._client = firestore.client()
        return self._client

    def read_existing(self, collection: str) -> Dict[str, Dict[str, Any]]:
        """READ ONLY. ``.stream()`` and nothing else."""
        return {
            doc.id: (doc.to_dict() or {})
            for doc in self.client().collection(collection).stream()
        }

    def plan(self, collection: str, records: Sequence[Any]) -> WritePlan:
        return build_plan(
            collection,
            records,
            self.read_existing(collection),
            backend=self.backend,
            dry_run=self.dry_run,
        )

    def commit(self, plan: WritePlan) -> int:
        if self.dry_run:
            raise DryRunViolation(
                "FirestoreWriter is in dry-run mode and will not write. This is "
                "the default. Pass dry_run=False (CLI: --commit) if you really "
                "mean to write to live Firestore."
            )

        db = self.client()
        written = 0
        batch = db.batch()
        pending = 0

        for action in plan.actions:
            if action.action == UNCHANGED:
                continue
            # merge=True: never clobber assignedProjects / assignedJudges /
            # anything else the ETL does not own.
            batch.set(
                db.collection(plan.collection).document(action.doc_id),
                action.document,
                merge=True,
            )
            written += 1
            pending += 1
            if pending >= 400:  # Firestore caps a batch at 500 operations.
                batch.commit()
                batch = db.batch()
                pending = 0

        if pending:
            batch.commit()
        return written


__all__ = [
    "CREATE",
    "UNCHANGED",
    "UPDATE",
    "DryRunViolation",
    "FirestoreWriter",
    "LoadError",
    "LocalWriter",
    "WriteAction",
    "WritePlan",
    "build_plan",
    "diff_document",
]
