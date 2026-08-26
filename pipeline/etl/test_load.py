"""Tests for the writers: diffing, idempotency, and the dry-run guarantees."""

from __future__ import annotations

import os

import pytest

from pipeline.etl import load
from pipeline.etl.transform import JudgeRecord, ProjectRecord


def make_projects():
    return [
        ProjectRecord(
            id="ago", name="Ago", tracks=("Analytics", "AI/ML"), table_number=1,
        ),
        ProjectRecord(
            id="404", name="404", tracks=("Cloud",), table_number=100,
        ),
        ProjectRecord(
            id="ai-plant-companion",
            name="AI Plant Companion",
            tracks=("Entrepreneurship & Product", "Hardware & IoT"),
            table_number=20,
            provenance="unverified",
            needs_review=True,
            review_reason="not in the DevPost export",
        ),
    ]


def make_judges():
    return [
        JudgeRecord(
            id="AdaLovelace", name="Ada Lovelace", email="ada@example.com",
            track="AI/ML", sponsor_challenge=None, username="AdaLovelace",
            checked_in=True, company="Analytical Engines", role="Principal",
        ),
        JudgeRecord(
            id="SponsorJudge", name="Sponsor Judge", email="s@example.com",
            track=None, sponsor_challenge="DataBricks Challenge",
            username="SponsorJudge", checked_in=True, needs_review=True,
            review_reason="sponsor challenge",
        ),
    ]


# --------------------------------------------------------------------------
# diffing
# --------------------------------------------------------------------------


def test_missing_document_is_a_create():
    action, changed = load.diff_document(None, {"name": "Ago"})
    assert action == load.CREATE
    assert changed == ("name",)


def test_identical_document_is_unchanged():
    document = {"name": "Ago", "tracks": ["Analytics"], "tableNumber": 1}
    assert load.diff_document(dict(document), document) == (load.UNCHANGED, ())


def test_only_the_fields_the_pipeline_owns_are_compared():
    """assignedJudges lives on the destination document. The ETL does not
    produce it, so its presence must not read as a difference -- and the merge
    write must leave it alone."""
    existing = {"name": "Ago", "assignedJudges": ["AdaLovelace"], "createdAt": 12345}
    action, changed = load.diff_document(existing, {"name": "Ago"})
    assert (action, changed) == (load.UNCHANGED, ())


def test_a_changed_field_is_named_in_the_plan():
    action, changed = load.diff_document(
        {"name": "Ago", "tableNumber": 1}, {"name": "Ago", "tableNumber": 2}
    )
    assert action == load.UPDATE
    assert changed == ("tableNumber",)


@pytest.mark.parametrize(
    "existing,incoming",
    [
        (None, ""),                     # absent vs empty
        ("101", 101),                   # CSV round-trip lost the int
        (404, "404"),                   # ...and the other way round
        (["a", "b"], ("a", "b")),       # list vs tuple
    ],
)
def test_backend_type_quirks_do_not_read_as_changes(existing, incoming):
    assert load.diff_document({"f": existing}, {"f": incoming})[0] == load.UNCHANGED


def test_genuinely_different_values_still_read_as_changes():
    assert load.diff_document({"f": "1"}, {"f": "2"})[0] == load.UPDATE
    assert load.diff_document({"f": ["a"]}, {"f": ["a", "b"]})[0] == load.UPDATE
    assert load.diff_document({"f": False}, {"f": True})[0] == load.UPDATE


def test_orphans_are_listed_and_never_turned_into_deletes():
    """The 212 synthetic projects are an orphan-list problem, not a DELETE."""
    plan = load.build_plan(
        "projects",
        make_projects(),
        {"synthetic-one": {"name": "Synthetic One"}},
        backend="test",
        dry_run=True,
    )
    assert plan.orphans == ["synthetic-one"]
    assert all(a.action != "delete" for a in plan.actions)
    assert "never deleted" in plan.summary_line()


def test_legacy_id_scheme_duplicates_are_detected():
    """The exact shape of the production mess: the same judge under the old
    name-slug ID and the current username ID."""
    plan = load.build_plan(
        "judges",
        make_judges(),
        {"ada-lovelace": {"name": "Ada Lovelace"}},
        backend="test",
        dry_run=True,
    )
    assert plan.legacy_duplicates == [("AdaLovelace", "ada-lovelace")]
    assert "ada-lovelace  ->  AdaLovelace" in plan.render()
    assert "Decide what happens to the old ones" in plan.render()


def test_legacy_duplicates_catch_trailing_punctuation_slugs():
    """Real case: production holds "3-sharks-1-blue---" for a project this
    pipeline calls "3-sharks-1-blue"."""
    records = [
        ProjectRecord(
            id="3-sharks-1-blue", name="3 Sharks 1 Blue", tracks=("Cloud",),
            table_number=1,
        )
    ]
    plan = load.build_plan(
        "projects", records, {"3-sharks-1-blue---": {}}, backend="test", dry_run=True
    )
    assert plan.legacy_duplicates == [("3-sharks-1-blue", "3-sharks-1-blue---")]


def test_unrelated_orphans_are_not_called_duplicates():
    plan = load.build_plan(
        "projects",
        make_projects(),
        {"apexbot": {}, "synthetic-two": {}},
        backend="test",
        dry_run=True,
    )
    assert plan.legacy_duplicates == []
    assert len(plan.orphans) == 2


def test_plan_actions_are_sorted_by_id():
    plan = load.build_plan(
        "projects", make_projects(), {}, backend="test", dry_run=True
    )
    ids = [a.doc_id for a in plan.actions]
    assert ids == sorted(ids)


# --------------------------------------------------------------------------
# LocalWriter
# --------------------------------------------------------------------------


def test_local_writer_first_run_creates_everything(tmp_path):
    writer = load.LocalWriter(str(tmp_path))
    plan = writer.plan("projects", make_projects())
    assert (plan.creates, plan.updates, plan.unchanged) == (3, 0, 0)
    assert writer.commit(plan) == 3
    assert os.path.exists(tmp_path / "projects.csv")


def test_local_writer_is_idempotent(tmp_path):
    """THE property. Two runs, byte-identical output, and the second run's plan
    says nothing changed."""
    writer = load.LocalWriter(str(tmp_path))
    projects, judges = make_projects(), make_judges()

    writer.commit(writer.plan("projects", projects))
    writer.commit(writer.plan("judges", judges))
    first = (tmp_path / "projects.csv").read_bytes(), (tmp_path / "judges.csv").read_bytes()

    second_plan = writer.plan("projects", projects)
    assert (second_plan.creates, second_plan.updates) == (0, 0)
    assert second_plan.unchanged == 3

    writer.commit(second_plan)
    writer.commit(writer.plan("judges", judges))
    second = (tmp_path / "projects.csv").read_bytes(), (tmp_path / "judges.csv").read_bytes()

    assert first == second


def test_local_writer_idempotent_across_five_runs(tmp_path):
    writer = load.LocalWriter(str(tmp_path))
    digests = set()
    for _ in range(5):
        writer.commit(writer.plan("projects", make_projects()))
        digests.add((tmp_path / "projects.csv").read_bytes())
    assert len(digests) == 1


def test_local_writer_detects_a_real_change(tmp_path):
    writer = load.LocalWriter(str(tmp_path))
    writer.commit(writer.plan("projects", make_projects()))

    changed = list(make_projects())
    changed[0] = ProjectRecord(
        id="ago", name="Ago", tracks=("Analytics", "AI/ML"), table_number=7,
    )
    plan = writer.plan("projects", changed)
    assert plan.updates == 1
    action = plan.by_action(load.UPDATE)[0]
    assert action.doc_id == "ago"
    assert action.changed_fields == ("tableNumber",)


def test_local_writer_in_dry_run_refuses_to_commit(tmp_path):
    writer = load.LocalWriter(str(tmp_path), dry_run=True)
    plan = writer.plan("projects", make_projects())
    with pytest.raises(load.DryRunViolation):
        writer.commit(plan)
    assert not os.path.exists(tmp_path / "projects.csv")


def test_local_writer_rejects_an_unknown_format(tmp_path):
    with pytest.raises(load.LoadError):
        load.LocalWriter(str(tmp_path), fmt="xlsx")


def test_local_writer_round_trips_lists_and_numbers(tmp_path):
    writer = load.LocalWriter(str(tmp_path))
    writer.commit(writer.plan("projects", make_projects()))
    existing = writer.read_existing("projects")
    assert existing["ago"]["tracks"] == ["Analytics", "AI/ML"]
    assert existing["ago"]["tableNumber"] == 1
    assert existing["ai-plant-companion"]["needsReview"] is True


def test_local_writer_columns_are_the_sorted_union(tmp_path):
    writer = load.LocalWriter(str(tmp_path))
    writer.commit(writer.plan("projects", make_projects()))
    header = (tmp_path / "projects.csv").read_text(encoding="utf-8").splitlines()[0]
    columns = header.split(",")
    assert columns[0] == "id"
    assert columns[1:] == sorted(columns[1:])


def _has_parquet() -> bool:
    try:
        import pandas  # noqa: F401
        import pyarrow  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(not _has_parquet(), reason="pandas/pyarrow not installed")
def test_parquet_backend_round_trips_and_is_idempotent(tmp_path):
    writer = load.LocalWriter(str(tmp_path), fmt="parquet")
    writer.commit(writer.plan("projects", make_projects()))
    assert os.path.exists(tmp_path / "projects.parquet")

    second = writer.plan("projects", make_projects())
    assert (second.creates, second.updates, second.unchanged) == (0, 0, 3)

    existing = writer.read_existing("projects")
    assert existing["ago"]["tracks"] == ["Analytics", "AI/ML"]
    assert existing["ago"]["tableNumber"] == 1


@pytest.mark.skipif(_has_parquet(), reason="pyarrow IS installed")
def test_parquet_without_pyarrow_gives_an_actionable_error(tmp_path):
    writer = load.LocalWriter(str(tmp_path), fmt="parquet")
    with pytest.raises(load.LoadError) as exc:
        writer.commit(writer.plan("projects", make_projects()))
    assert "--format csv" in str(exc.value)


# --------------------------------------------------------------------------
# FirestoreWriter -- with a fake client, so no network and no live data
# --------------------------------------------------------------------------


class FakeDocument:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return dict(self._data)


class FakeCollection:
    def __init__(self, store):
        self._store = store

    def stream(self):
        return [FakeDocument(k, v) for k, v in sorted(self._store.items())]

    def document(self, doc_id):
        return ("doc", doc_id)


class FakeBatch:
    def __init__(self, client):
        self.client = client

    def set(self, ref, document, merge=False):
        self.client.writes.append((ref[1], document, merge))

    def commit(self):
        self.client.batch_commits += 1


class FakeFirestore:
    """Records every write. Has no delete method at all, on purpose: if the
    writer ever grew one, these tests would fail with AttributeError."""

    def __init__(self, collections=None):
        self.collections = collections or {}
        self.writes = []
        self.batch_commits = 0

    def collection(self, name):
        return FakeCollection(self.collections.setdefault(name, {}))

    def batch(self):
        return FakeBatch(self)


def test_firestore_writer_defaults_to_dry_run():
    """The single most important line in load.py."""
    writer = load.FirestoreWriter("does-not-matter.json")
    assert writer.dry_run is True


def test_firestore_writer_in_dry_run_will_not_write():
    client = FakeFirestore({"projects": {}})
    writer = load.FirestoreWriter("x.json", client=client)

    plan = writer.plan("projects", make_projects())
    assert plan.creates == 3
    assert plan.dry_run is True

    with pytest.raises(load.DryRunViolation) as exc:
        writer.commit(plan)
    assert "--commit" in str(exc.value)
    assert client.writes == []


def test_firestore_plan_only_reads():
    client = FakeFirestore({"projects": {"ago": {"name": "Ago"}}})
    writer = load.FirestoreWriter("x.json", client=client)
    writer.plan("projects", make_projects())
    assert client.writes == []
    assert client.batch_commits == 0


def test_firestore_writer_commits_with_merge_true():
    """merge=True is what stops a re-run wiping assignedProjects."""
    client = FakeFirestore({"projects": {}})
    writer = load.FirestoreWriter("x.json", dry_run=False, client=client)

    written = writer.commit(writer.plan("projects", make_projects()))
    assert written == 3
    assert all(merge is True for _, _, merge in client.writes)
    assert sorted(doc_id for doc_id, _, _ in client.writes) == [
        "404", "ago", "ai-plant-companion",
    ]


def test_firestore_writer_skips_unchanged_documents():
    existing = {p.id: p.to_document() for p in make_projects()}
    client = FakeFirestore({"projects": existing})
    writer = load.FirestoreWriter("x.json", dry_run=False, client=client)

    plan = writer.plan("projects", make_projects())
    assert plan.unchanged == 3
    assert writer.commit(plan) == 0
    assert client.writes == []


def test_firestore_writer_never_touches_orphans():
    client = FakeFirestore({"projects": {"synthetic-one": {"name": "Synthetic"}}})
    writer = load.FirestoreWriter("x.json", dry_run=False, client=client)

    plan = writer.plan("projects", make_projects())
    assert plan.orphans == ["synthetic-one"]
    writer.commit(plan)
    assert "synthetic-one" not in {doc_id for doc_id, _, _ in client.writes}


def test_firestore_writer_has_no_delete_path():
    source = open(load.__file__, encoding="utf-8").read()
    for forbidden in (".delete(", "delete_document", "batch.delete"):
        assert forbidden not in source, f"load.py must not contain {forbidden}"


def test_firestore_writer_reports_a_missing_service_account():
    writer = load.FirestoreWriter("/nope/serviceAccount.json")
    try:
        import firebase_admin  # noqa: F401
    except ImportError:
        pytest.skip("firebase-admin not installed")
    with pytest.raises(load.LoadError) as exc:
        writer.client()
    assert "serviceAccount.json" in str(exc.value)


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def test_plan_render_lists_changes_and_orphans():
    plan = load.build_plan(
        "projects", make_projects(), {"ghost": {}}, backend="local", dry_run=True
    )
    text = plan.render()
    assert "3 create" in text
    assert "orphans in destination, left untouched: ghost" in text


def test_plan_render_truncates_long_lists():
    records = [
        ProjectRecord(id=f"p{i:03}", name=f"P{i}", tracks=("Cloud",), table_number=i)
        for i in range(1, 60)
    ]
    text = load.build_plan(
        "projects", records, {}, backend="local", dry_run=True
    ).render(max_rows=5)
    assert "... and 54 more" in text
