"""
A very small Databricks client.

Why this and not ``databricks-sql-connector`` / PySpark
-------------------------------------------------------
The workspace is **Databricks Free Edition**. It is serverless-only: there are
zero classic clusters to attach a Spark session to, so ``pyspark`` code that
says ``spark.read...`` has nothing to run on. What Free Edition *does* give you
is one SQL warehouse, and the SQL Statement Execution API can drive it over
plain HTTPS.

So: this module posts SQL to the warehouse and hands you back rows (or a pandas
DataFrame). The whole dataset is ~372 projects and ~262 evaluations. Anything
clever happens in pandas locally; Databricks is the durable store, not the
compute.

The only dependency is ``requests``, which is already installed as part of
``firebase-admin``.

Cold starts
-----------
The warehouse auto-stops after 10 minutes of idling and auto-resumes when you
send it something. **The first statement after a nap can take ~30-60 seconds.**
That is normal, not a failure. ``DatabricksClient.sql()`` waits it out and
prints one "waking the warehouse up" line so nobody thinks the script hung.

Usage
-----
    from pipeline.databricks.connection import connect

    db = connect()                       # reads .env / os.environ
    df = db.frame("SELECT * FROM judges LIMIT 5")
    db.sql("CREATE SCHEMA IF NOT EXISTS judging")
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

# --------------------------------------------------------------------------
# errors -- plain language, because the team is new to this
# --------------------------------------------------------------------------


class DatabricksError(RuntimeError):
    """Anything that went wrong talking to Databricks."""


class MissingCredentialsError(DatabricksError):
    """A required environment variable is not set."""


class StatementError(DatabricksError):
    """The warehouse ran the statement and rejected it."""

    def __init__(self, message: str, *, statement: str = "", state: str = "") -> None:
        super().__init__(message)
        self.statement = statement
        self.state = state


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------

#: Where the tables live when nothing else is configured.
#:
#: Free Edition turned out to ALLOW ``CREATE CATALOG``, so we landed on a
#: dedicated ``datahacks`` catalog rather than sharing the workspace's built-in
#: one. If a future workspace refuses to create it, set
#: ``DATABRICKS_CATALOG=workspace`` in .env -- nothing else has to change, and
#: ``schema.py --create`` will say so plainly when the CREATE is denied.
DEFAULT_CATALOG = "datahacks"

#: Fallback catalog that exists in every Unity Catalog workspace.
FALLBACK_CATALOG = "workspace"

DEFAULT_SCHEMA = "judging"

_REQUIRED = ("DATABRICKS_HOST", "DATABRICKS_TOKEN")


@dataclass(frozen=True)
class Settings:
    """Everything needed to reach the warehouse. Never log ``token``."""

    host: str
    token: str = field(repr=False)
    catalog: str = DEFAULT_CATALOG
    schema: str = DEFAULT_SCHEMA
    warehouse_id: Optional[str] = None

    @property
    def full_schema(self) -> str:
        return f"{self.catalog}.{self.schema}"

    def table(self, name: str) -> str:
        """Fully-qualified table name, e.g. ``workspace.judging.judges``."""
        return f"{self.catalog}.{self.schema}.{name}"

    def redacted(self) -> Dict[str, Any]:
        """Safe to print. The token is never included."""
        return {
            "host": self.host,
            "catalog": self.catalog,
            "schema": self.schema,
            "warehouse_id": self.warehouse_id or "(auto-discover)",
        }


def read_dotenv(path: str = ".env") -> Dict[str, str]:
    """Parse a ``KEY=value`` file. Missing file -> empty dict, not an error.

    Deliberately tiny: no interpolation, no ``export``, no multi-line values.
    Anything fancier belongs in the real environment.
    """
    values: Dict[str, str] = {}
    if not os.path.exists(path):
        return values
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip()] = value
    return values


def load_settings(
    env: Optional[Mapping[str, str]] = None,
    *,
    dotenv_path: str = ".env",
) -> Settings:
    """Build :class:`Settings` from the environment, falling back to ``.env``.

    The real environment wins over the file, so
    ``DATABRICKS_CATALOG=scratch python -m ...`` does what you expect.

    Raises :class:`MissingCredentialsError` naming the variable and the file it
    should live in -- an unset token should not surface as a 403 ten seconds
    later.
    """
    env = os.environ if env is None else env
    file_values = read_dotenv(dotenv_path)

    def get(key: str, default: str = "") -> str:
        value = env.get(key) or file_values.get(key) or default
        return value.strip()

    missing = [k for k in _REQUIRED if not get(k)]
    if missing:
        raise MissingCredentialsError(
            "Databricks credentials are missing: " + ", ".join(missing) + ".\n"
            f"Add them to {dotenv_path} (which is gitignored) or export them:\n"
            "    DATABRICKS_HOST=https://dbc-xxxxxxxx-xxxx.cloud.databricks.com\n"
            "    DATABRICKS_TOKEN=dapi...\n"
            "Generate a token in the Databricks UI under\n"
            "    Settings -> Developer -> Access tokens -> Generate new token."
        )

    host = get("DATABRICKS_HOST").rstrip("/")
    if not host.startswith("http"):
        host = "https://" + host

    return Settings(
        host=host,
        token=get("DATABRICKS_TOKEN"),
        catalog=get("DATABRICKS_CATALOG", DEFAULT_CATALOG) or DEFAULT_CATALOG,
        schema=get("DATABRICKS_SCHEMA", DEFAULT_SCHEMA) or DEFAULT_SCHEMA,
        warehouse_id=get("DATABRICKS_WAREHOUSE_ID") or None,
    )


# --------------------------------------------------------------------------
# turning Python values into SQL text
# --------------------------------------------------------------------------


def sql_ident(name: str) -> str:
    """Quote an identifier. Rejects backticks rather than escaping them.

    Every identifier this pipeline uses is a hard-coded table or column name,
    so a backtick here means a bug upstream, not user input.
    """
    if "`" in name:
        raise ValueError(f"identifier {name!r} contains a backtick")
    return f"`{name}`"


def sql_string(value: str) -> str:
    """A single-quoted Databricks SQL string literal.

    Databricks treats backslash as an escape character inside string literals
    (``spark.sql.parser.escapedStringLiterals`` is false by default), so both
    the backslash and the quote have to be escaped -- doing only the quote is
    the classic way to build an injectable literal.
    """
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return "'" + escaped + "'"


def sql_literal(value: Any) -> str:
    """Render a Python value as SQL text.

    Handles the shapes this pipeline actually stores: None, bool, int, float,
    str, datetime/date, list/tuple (-> ``array(...)``) and dict
    (-> ``map(...)``). Anything else raises rather than being stringified by
    accident.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return "NULL"  # NaN / inf have no literal form; store as NULL
        return repr(value)
    if isinstance(value, str):
        return sql_string(value)
    if isinstance(value, _dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        return "TIMESTAMP " + sql_string(value.isoformat(sep=" ", timespec="microseconds"))
    if isinstance(value, _dt.date):
        return "DATE " + sql_string(value.isoformat())
    if isinstance(value, (list, tuple)):
        if not value:
            return "array()"
        return "array(" + ", ".join(sql_literal(v) for v in value) + ")"
    if isinstance(value, dict):
        if not value:
            return "map()"
        parts = []
        for key, item in value.items():
            parts.append(sql_literal(str(key)))
            parts.append(sql_literal(item))
        return "map(" + ", ".join(parts) + ")"
    raise TypeError(
        f"don't know how to put {type(value).__name__} into SQL: {value!r}. "
        "Convert it to a str/int/float/bool/list/dict/datetime first."
    )


def cast_literal(value: Any, sql_type: str) -> str:
    """``sql_literal`` wrapped in an explicit CAST.

    Needed because a ``VALUES`` clause infers its column types from the literals
    it sees. A column that is NULL in every row of one batch would otherwise
    come out as ``VOID`` and the MERGE would fail with a type error that says
    nothing useful.
    """
    return f"CAST({sql_literal(value)} AS {sql_type})"


# --------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------


@dataclass
class Result:
    """Rows back from the warehouse."""

    columns: List[str]
    rows: List[List[Any]]
    statement_id: str = ""
    elapsed_seconds: float = 0.0

    def __len__(self) -> int:
        return len(self.rows)

    def dicts(self) -> List[Dict[str, Any]]:
        return [dict(zip(self.columns, row)) for row in self.rows]

    def scalar(self) -> Any:
        """First column of the first row, or None if there were no rows."""
        if not self.rows or not self.rows[0]:
            return None
        return self.rows[0][0]

    def frame(self):
        """As a pandas DataFrame. pandas is already a pipeline dependency."""
        import pandas as pd

        return pd.DataFrame(self.rows, columns=self.columns or None)


# --------------------------------------------------------------------------
# the client
# --------------------------------------------------------------------------

#: Longest the API will hold a request open before it makes us poll.
_API_MAX_WAIT = 50

#: How long we are willing to wait overall, including a warehouse cold start.
DEFAULT_TIMEOUT_SECONDS = 300


class DatabricksClient:
    """Runs SQL on a serverless SQL warehouse over the REST API.

    ``session`` is anything with a ``request(method, url, headers=, json=,
    timeout=)`` returning an object with ``status_code``, ``text`` and
    ``json()`` -- i.e. a ``requests.Session``. Tests pass a fake, which is why
    the whole suite runs with no network.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        session: Any = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        poll_seconds: float = 2.0,
        on_notice: Any = print,
    ) -> None:
        self.settings = settings
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self._on_notice = on_notice or (lambda _msg: None)
        self._warehouse_id = settings.warehouse_id
        self._warned_cold = False
        if session is None:
            import requests  # imported lazily so `--help` works without it

            session = requests.Session()
        self._session = session

    # -- plumbing ----------------------------------------------------------

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        url = self.settings.host + path
        response = self._session.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {self.settings.token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=_API_MAX_WAIT + 30,
        )
        status = getattr(response, "status_code", 0)
        if status == 401 or status == 403:
            raise DatabricksError(
                f"Databricks rejected the token ({status}) for {method} {path}.\n"
                "The token may have expired or may belong to a different "
                "workspace. Regenerate it under Settings -> Developer -> "
                "Access tokens and update DATABRICKS_TOKEN in .env."
            )
        if status >= 400:
            raise DatabricksError(
                f"Databricks returned HTTP {status} for {method} {path}: "
                f"{getattr(response, 'text', '')[:500]}"
            )
        try:
            return response.json() or {}
        except Exception as exc:  # pragma: no cover - malformed server reply
            raise DatabricksError(
                f"Could not read the reply to {method} {path}: {exc}"
            ) from exc

    def warehouse_id(self) -> str:
        """The warehouse to run on: the configured one, or the only one there is.

        Free Edition ships exactly one ("Serverless Starter Warehouse"), so
        auto-discovery is unambiguous. Set ``DATABRICKS_WAREHOUSE_ID`` once a
        workspace has more than one.
        """
        if self._warehouse_id:
            return self._warehouse_id
        payload = self._call("GET", "/api/2.0/sql/warehouses")
        warehouses = payload.get("warehouses") or []
        if not warehouses:
            raise DatabricksError(
                "This workspace has no SQL warehouses, so there is nothing to "
                "run SQL on. Create one in the Databricks UI under SQL "
                "Warehouses, or set DATABRICKS_WAREHOUSE_ID."
            )
        running = [w for w in warehouses if w.get("state") == "RUNNING"]
        chosen = (running or warehouses)[0]
        self._warehouse_id = chosen["id"]
        return self._warehouse_id

    # -- the one method that matters --------------------------------------

    def sql(
        self,
        statement: str,
        *,
        catalog: Optional[str] = None,
        schema: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ) -> Result:
        """Run one SQL statement and wait for it to finish.

        Blocks through a warehouse cold start (~30-60s from STOPPED). Raises
        :class:`StatementError` with the warehouse's own message if the SQL is
        rejected.
        """
        deadline = time.monotonic() + (timeout_seconds or self.timeout_seconds)
        started = time.monotonic()

        body = {
            "statement": statement,
            "warehouse_id": self.warehouse_id(),
            "wait_timeout": f"{_API_MAX_WAIT}s",
            "on_wait_timeout": "CONTINUE",
            "format": "JSON_ARRAY",
            "disposition": "INLINE",
        }
        # Only send catalog/schema when we have them; a schema that does not
        # exist yet makes the API refuse the statement that would create it.
        if catalog or self.settings.catalog:
            body["catalog"] = catalog or self.settings.catalog
        if schema is not None:
            body["schema"] = schema

        payload = self._call("POST", "/api/2.0/sql/statements", body)
        statement_id = payload.get("statement_id", "")

        while True:
            state = ((payload.get("status") or {}).get("state") or "").upper()

            if state == "SUCCEEDED":
                return self._to_result(
                    payload, statement_id, time.monotonic() - started
                )
            if state in ("FAILED", "CANCELED", "CLOSED"):
                error = (payload.get("status") or {}).get("error") or {}
                message = error.get("message") or f"statement ended in state {state}"
                raise StatementError(
                    f"Databricks could not run this statement:\n"
                    f"  {message}\n"
                    f"SQL was:\n  {_abbreviate(statement)}",
                    statement=statement,
                    state=state,
                )

            # PENDING / RUNNING. A PENDING statement on a stopped warehouse is
            # a cold start, not a hang -- say so once, then keep waiting.
            if state == "PENDING" and not self._warned_cold:
                self._warned_cold = True
                self._on_notice(
                    "  ... waiting for the SQL warehouse to wake up "
                    "(auto-resume from STOPPED takes about 30-60s; this "
                    "happens once)"
                )

            if time.monotonic() > deadline:
                raise DatabricksError(
                    f"Gave up after {timeout_seconds or self.timeout_seconds}s "
                    f"waiting for statement {statement_id} (last state: "
                    f"{state or 'unknown'}).\n"
                    "If the warehouse was asleep this can just be a slow cold "
                    "start -- try again, or pass a larger timeout_seconds."
                )

            time.sleep(self.poll_seconds)
            payload = self._call(
                "GET", f"/api/2.0/sql/statements/{statement_id}"
            )

    def frame(self, statement: str, **kwargs: Any):
        """``sql()`` as a pandas DataFrame."""
        return self.sql(statement, **kwargs).frame()

    def _to_result(
        self, payload: dict, statement_id: str, elapsed: float
    ) -> Result:
        manifest = payload.get("manifest") or {}
        columns = [
            c.get("name", f"col{i}")
            for i, c in enumerate((manifest.get("schema") or {}).get("columns") or [])
        ]
        chunk = payload.get("result") or {}
        rows = chunk.get("data_array") or []
        return Result(
            columns=columns,
            rows=[list(r) for r in rows],
            statement_id=statement_id,
            elapsed_seconds=round(elapsed, 2),
        )

    # -- writing -----------------------------------------------------------

    def merge_rows(
        self,
        table: str,
        columns: Sequence[tuple],
        key_columns: Sequence[str],
        rows: Iterable[Mapping[str, Any]],
        *,
        batch_size: int = 200,
    ) -> int:
        """Upsert rows into a Delta table. Idempotent by ``key_columns``.

        ``columns`` is ``[(name, sql_type), ...]`` -- normally
        ``schema.TABLES[name].columns``. Re-running with the same data updates
        the same rows instead of appending a second copy, which is what makes
        ``ingest_2026 --commit`` safe to run twice.

        Uses Delta ``MERGE``: no DELETE, no OVERWRITE, nothing dropped.
        """
        rows = list(rows)
        if not rows:
            return 0
        names = [name for name, _ in columns]
        missing = [k for k in key_columns if k not in names]
        if missing:
            raise ValueError(
                f"key column(s) {missing} are not in the column list for {table}"
            )

        written = 0
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            values = ",\n    ".join(
                "("
                + ", ".join(cast_literal(row.get(name), sql_type) for name, sql_type in columns)
                + ")"
                for row in batch
            )
            column_list = ", ".join(sql_ident(n) for n in names)
            on_clause = " AND ".join(
                f"t.{sql_ident(k)} <=> s.{sql_ident(k)}" for k in key_columns
            )
            statement = (
                f"MERGE INTO {table} AS t\n"
                f"USING (SELECT * FROM VALUES\n    {values}\n"
                f"  AS s({column_list})) AS s\n"
                f"ON {on_clause}\n"
                "WHEN MATCHED THEN UPDATE SET *\n"
                "WHEN NOT MATCHED THEN INSERT *"
            )
            self.sql(statement)
            written += len(batch)
        return written

    def count(self, table: str) -> int:
        """``SELECT COUNT(*)``, as an int."""
        return int(self.sql(f"SELECT COUNT(*) FROM {table}").scalar() or 0)


def _abbreviate(statement: str, limit: int = 600) -> str:
    flat = " ".join(statement.split())
    return flat if len(flat) <= limit else flat[:limit] + " ...(truncated)"


def connect(
    env: Optional[Mapping[str, str]] = None,
    *,
    dotenv_path: str = ".env",
    **kwargs: Any,
) -> DatabricksClient:
    """Load settings and build a client. The normal entry point."""
    return DatabricksClient(load_settings(env, dotenv_path=dotenv_path), **kwargs)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``python -m pipeline.databricks.connection`` -- a connectivity check."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Check that we can reach the Databricks SQL warehouse."
    )
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args(argv)

    try:
        client = connect(dotenv_path=args.env_file)
    except MissingCredentialsError as exc:
        print(exc)
        return 2

    print("settings:", json.dumps(client.settings.redacted(), indent=2))
    print("warehouse:", client.warehouse_id())
    result = client.sql("SELECT current_catalog() AS catalog, current_user() AS user")
    for row in result.dicts():
        print("connected:", row)
    print(f"round trip: {result.elapsed_seconds}s")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
