"""
Databricks side of the DataHacks judging platform.

Firebase stays the serving tier during the event. Databricks owns everything
before it (assignment runs) and after it (the historical archive and analysis).

    connection.py    talk to the SQL warehouse, get pandas DataFrames back
    schema.py        create the catalog / schema / Delta tables (idempotent)
    ingest_2026.py   land the 2026 event data from Firestore  (READ-ONLY)
    publish_run.py   solve an assignment, publish it to Firestore + Delta

Read pipeline/databricks/README.md first. The short version:

    python -m pipeline.databricks.schema --create
    python -m pipeline.databricks.ingest_2026            # dry run
    python -m pipeline.databricks.ingest_2026 --commit   # writes Delta only
    python -m pipeline.databricks.publish_run            # dry run

Nothing here ever writes to Firestore unless you pass ``--commit`` to
``publish_run``. ``ingest_2026`` cannot write to Firestore at all -- it only
calls ``.stream()``.
"""

# Submodules are imported lazily. Importing connection.py eagerly here would
# make ``python -m pipeline.databricks.connection`` load the module twice and
# warn about it -- the same trick pipeline/etl/__init__.py uses.

_LAZY = {
    "DatabricksClient": "connection",
    "DatabricksError": "connection",
    "MissingCredentialsError": "connection",
    "Settings": "connection",
    "StatementError": "connection",
    "connect": "connection",
    "load_settings": "connection",
    "sql_literal": "connection",
}


def __getattr__(name):  # PEP 562
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(f"{__name__}.{module_name}")
    return getattr(module, name)


__all__ = sorted(_LAZY)
