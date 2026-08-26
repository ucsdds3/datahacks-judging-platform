"""Tests for the Delta DDL.

The important property is safety: every statement is IF NOT EXISTS, nothing
drops or replaces, and re-running is a no-op. These tables hold the archive of
a real event.
"""

from __future__ import annotations

import pytest

from pipeline.databricks import schema
from pipeline.databricks.connection import Settings

SETTINGS = Settings(
    host="https://example", token="t", catalog="datahacks", schema="judging"
)


def test_all_five_tables_are_defined():
    assert set(schema.TABLES) == {
        "judges",
        "projects",
        "evaluations",
        "assignments",
        "runs",
    }
    assert set(schema.TABLE_ORDER) == set(schema.TABLES)


@pytest.mark.parametrize("name", sorted(schema.TABLES))
def test_every_key_column_is_a_real_column(name):
    table = schema.TABLES[name]
    assert table.key_columns
    for key in table.key_columns:
        assert key in table.column_names


@pytest.mark.parametrize("name", sorted(schema.TABLES))
def test_no_duplicate_column_names(name):
    names = schema.TABLES[name].column_names
    assert len(names) == len(set(names))


def test_ddl_is_entirely_if_not_exists():
    for statement in schema.ddl_statements(SETTINGS):
        assert "IF NOT EXISTS" in statement, statement


def test_ddl_contains_nothing_destructive():
    joined = "\n".join(schema.ddl_statements(SETTINGS)).upper()
    for word in ("DROP ", "TRUNCATE", "CREATE OR REPLACE", "OVERWRITE"):
        assert word not in joined


def test_ddl_creates_the_catalog_then_the_schema_then_the_tables():
    statements = schema.ddl_statements(SETTINGS)
    assert statements[0].startswith("CREATE CATALOG")
    assert statements[1].startswith("CREATE SCHEMA")
    assert len(statements) == 2 + len(schema.TABLE_ORDER)


def test_tables_are_fully_qualified():
    for statement in schema.ddl_statements(SETTINGS)[2:]:
        assert "datahacks.judging." in statement


def test_evaluations_can_hold_the_broken_2026_rows():
    """The four track=null evaluations live in this table with a flag. If
    include_in_analysis ever disappears, they get filtered at load time again
    and vanish the way they did in 2026."""
    columns = dict(schema.EVALUATIONS.columns)
    assert columns["include_in_analysis"] == "BOOLEAN"
    assert columns["exclusion_reason"] == "STRING"
    # criterion names differ per track, so a map rather than five columns
    assert columns["scores"] == "MAP<STRING, INT>"


def test_projects_records_how_each_classification_was_made():
    columns = dict(schema.PROJECTS.columns)
    assert columns["is_synthetic"] == "BOOLEAN"
    assert columns["classification_source"] == "STRING"


def test_runs_mirrors_the_gate_fields_from_the_contract():
    columns = dict(schema.RUNS.columns)
    for name in (
        "connectivity_ok",
        "judge_count",
        "checkin_snapshot_count",
        "coverage_json",
        "tracks_json",
    ):
        assert name in columns


def test_create_table_sql_shape():
    sql = schema.JUDGES.create_sql(SETTINGS)
    assert sql.startswith("CREATE TABLE IF NOT EXISTS datahacks.judging.judges")
    assert "USING DELTA" in sql
    assert "`judge_id` STRING" in sql


# --------------------------------------------------------------------------
# create_all
# --------------------------------------------------------------------------


class RecordingClient:
    def __init__(self, settings=SETTINGS, fail_on=None):
        self.settings = settings
        self.statements = []
        self._fail_on = fail_on

    def sql(self, statement, **_kwargs):
        if self._fail_on and statement.startswith(self._fail_on):
            raise RuntimeError("PERMISSION_DENIED: cannot create catalog")
        self.statements.append(statement)

    def count(self, table):
        return 0


def test_create_all_runs_every_statement():
    client = RecordingClient()
    executed = schema.create_all(client, on_progress=lambda _m: None)
    assert len(executed) == len(schema.ddl_statements(SETTINGS))


def test_create_all_explains_a_denied_catalog_and_names_the_fallback():
    """Some Databricks tiers refuse CREATE CATALOG. Free Edition happens to
    allow it, but the failure has to be self-explanatory anywhere else."""
    client = RecordingClient(fail_on="CREATE CATALOG")
    with pytest.raises(RuntimeError) as exc:
        schema.create_all(client, on_progress=lambda _m: None)
    assert "DATABRICKS_CATALOG=workspace" in str(exc.value)


def test_describe_reports_none_for_a_missing_table():
    class Broken(RecordingClient):
        def count(self, table):
            raise RuntimeError("TABLE_OR_VIEW_NOT_FOUND")

    assert schema.describe(Broken()) == {name: None for name in schema.TABLE_ORDER}


def test_render_status_lists_every_table():
    text = schema.render_status(SETTINGS, {name: 0 for name in schema.TABLE_ORDER})
    for name in schema.TABLE_ORDER:
        assert name in text
