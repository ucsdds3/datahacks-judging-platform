"""Tests for the Databricks client.

Everything here runs offline. ``FakeSession`` stands in for requests.Session,
so the suite never needs credentials or a network. The one live test is marked
and skips itself when DATABRICKS_HOST/TOKEN are not set.
"""

from __future__ import annotations

import datetime as dt
import os

import pytest

from pipeline.databricks import connection as conn


# --------------------------------------------------------------------------
# a fake HTTP session
# --------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or str(payload)

    def json(self):
        return self._payload


class FakeSession:
    """Replays a scripted list of responses and records what was sent."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json,
                           "headers": headers})
        if not self.responses:
            raise AssertionError(f"unexpected extra request: {method} {url}")
        return self.responses.pop(0)


SETTINGS = conn.Settings(
    host="https://example.cloud.databricks.com",
    token="dapi-not-a-real-token",
    catalog="datahacks",
    schema="judging",
    warehouse_id="wh123",
)


def succeeded(columns=("a",), rows=((1,),)):
    return FakeResponse(
        {
            "statement_id": "s1",
            "status": {"state": "SUCCEEDED"},
            "manifest": {"schema": {"columns": [{"name": c} for c in columns]}},
            "result": {"data_array": [list(r) for r in rows]},
        }
    )


def client(session, **kwargs):
    kwargs.setdefault("poll_seconds", 0)
    kwargs.setdefault("on_notice", lambda _m: None)
    return conn.DatabricksClient(SETTINGS, session=session, **kwargs)


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------


def test_missing_credentials_names_the_variables(tmp_path):
    with pytest.raises(conn.MissingCredentialsError) as exc:
        conn.load_settings({}, dotenv_path=str(tmp_path / "nope.env"))
    message = str(exc.value)
    assert "DATABRICKS_HOST" in message
    assert "DATABRICKS_TOKEN" in message
    # A newcomer should be told where to get one, not just that it is missing.
    assert "Access tokens" in message


def test_settings_come_from_dotenv_when_env_is_empty(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# comment\n"
        "DATABRICKS_HOST=https://h.example.com/\n"
        'DATABRICKS_TOKEN="dapi-xyz"\n'
        "DATABRICKS_CATALOG=datahacks\n"
        "DATABRICKS_SCHEMA=judging\n"
    )
    settings = conn.load_settings({}, dotenv_path=str(path))
    assert settings.host == "https://h.example.com"  # trailing slash stripped
    assert settings.token == "dapi-xyz"  # quotes stripped
    assert settings.table("judges") == "datahacks.judging.judges"


def test_real_environment_beats_the_dotenv_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text("DATABRICKS_HOST=https://file.example.com\n"
                    "DATABRICKS_TOKEN=file-token\n"
                    "DATABRICKS_CATALOG=from_file\n")
    settings = conn.load_settings(
        {"DATABRICKS_HOST": "https://env.example.com",
         "DATABRICKS_TOKEN": "env-token",
         "DATABRICKS_CATALOG": "from_env"},
        dotenv_path=str(path),
    )
    assert settings.catalog == "from_env"


def test_host_without_a_scheme_gets_https(tmp_path):
    settings = conn.load_settings(
        {"DATABRICKS_HOST": "dbc-1234.cloud.databricks.com",
         "DATABRICKS_TOKEN": "t"},
        dotenv_path=str(tmp_path / "none"),
    )
    assert settings.host.startswith("https://")


def test_the_token_never_appears_in_repr_or_redacted():
    assert "dapi-not-a-real-token" not in repr(SETTINGS)
    assert "dapi-not-a-real-token" not in str(SETTINGS.redacted())


def test_read_dotenv_of_a_missing_file_is_empty_not_an_error(tmp_path):
    assert conn.read_dotenv(str(tmp_path / "absent")) == {}


# --------------------------------------------------------------------------
# SQL literals -- the escaping is the security-relevant part
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "NULL"),
        (True, "TRUE"),
        (False, "FALSE"),
        (7, "7"),
        ("plain", "'plain'"),
        ([], "array()"),
        ({}, "map()"),
        (["a", "b"], "array('a', 'b')"),
        ({"k": 3}, "map('k', 3)"),
    ],
)
def test_sql_literal_basics(value, expected):
    assert conn.sql_literal(value) == expected


def test_sql_literal_escapes_quotes_and_backslashes():
    # Databricks treats backslash as an escape character inside a string
    # literal, so escaping only the quote leaves an injectable literal.
    assert conn.sql_literal("nah, I'd lose") == "'nah, I\\'d lose'"
    assert conn.sql_literal("back\\slash") == "'back\\\\slash'"
    assert conn.sql_literal("'; DROP TABLE judges; --") == (
        "'\\'; DROP TABLE judges; --'"
    )


def test_sql_literal_handles_the_projects_with_odd_titles():
    # These are real 2026 titles and they all have to survive the round trip.
    for title in ("ㄖ", "3 Sharks 1 Blue 🧿", "O(anxiety^2)", "Panic! At the Dataset"):
        assert conn.sql_literal(title).startswith("'")


def test_sql_literal_renders_a_utc_timestamp():
    stamp = dt.datetime(2026, 4, 19, 22, 18, 51, tzinfo=dt.timezone.utc)
    assert conn.sql_literal(stamp) == "TIMESTAMP '2026-04-19 22:18:51.000000'"


def test_sql_literal_converts_an_aware_timestamp_to_utc():
    tz = dt.timezone(dt.timedelta(hours=-7))
    stamp = dt.datetime(2026, 4, 19, 15, 18, 51, tzinfo=tz)
    assert conn.sql_literal(stamp) == "TIMESTAMP '2026-04-19 22:18:51.000000'"


def test_sql_literal_turns_nan_into_null_rather_than_broken_sql():
    assert conn.sql_literal(float("nan")) == "NULL"


def test_sql_literal_refuses_types_it_does_not_understand():
    with pytest.raises(TypeError):
        conn.sql_literal(object())


def test_cast_literal_wraps_the_type():
    assert conn.cast_literal(None, "INT") == "CAST(NULL AS INT)"


def test_sql_ident_rejects_a_backtick():
    with pytest.raises(ValueError):
        conn.sql_ident("bad`name")


# --------------------------------------------------------------------------
# running statements
# --------------------------------------------------------------------------


def test_sql_returns_columns_and_rows():
    session = FakeSession([succeeded(("n",), ((372,),))])
    result = client(session).sql("SELECT 1")
    assert result.columns == ["n"]
    assert result.scalar() == 372
    assert result.dicts() == [{"n": 372}]


def test_sql_sends_the_configured_catalog_and_warehouse():
    session = FakeSession([succeeded()])
    client(session).sql("SELECT 1")
    body = session.calls[0]["json"]
    assert body["warehouse_id"] == "wh123"
    assert body["catalog"] == "datahacks"
    assert body["on_wait_timeout"] == "CONTINUE"


def test_a_pending_statement_is_a_cold_start_not_a_failure():
    """The warehouse auto-stops after 10 minutes; the first statement back
    sits in PENDING for ~30-60s. That must not read as an error."""
    notices = []
    session = FakeSession(
        [
            FakeResponse({"statement_id": "s1", "status": {"state": "PENDING"}}),
            FakeResponse({"statement_id": "s1", "status": {"state": "RUNNING"}}),
            succeeded(("n",), ((1,),)),
        ]
    )
    result = client(session, on_notice=notices.append).sql("SELECT 1")
    assert result.scalar() == 1
    assert any("wake" in n for n in notices)
    # ... and it says so only once, however long the nap was.
    assert len(notices) == 1


def test_a_failed_statement_raises_with_the_warehouse_message():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "statement_id": "s1",
                    "status": {
                        "state": "FAILED",
                        "error": {"message": "TABLE_OR_VIEW_NOT_FOUND: judges"},
                    },
                }
            )
        ]
    )
    with pytest.raises(conn.StatementError) as exc:
        client(session).sql("SELECT * FROM judges")
    assert "TABLE_OR_VIEW_NOT_FOUND" in str(exc.value)
    assert "SELECT * FROM judges" in str(exc.value)


def test_a_bad_token_says_so_in_plain_language():
    session = FakeSession([FakeResponse({}, status_code=403, text="Forbidden")])
    with pytest.raises(conn.DatabricksError) as exc:
        client(session).sql("SELECT 1")
    assert "token" in str(exc.value).lower()


def test_giving_up_mentions_the_cold_start_possibility():
    session = FakeSession(
        [FakeResponse({"statement_id": "s1", "status": {"state": "RUNNING"}})] * 4
    )
    with pytest.raises(conn.DatabricksError) as exc:
        client(session, timeout_seconds=0).sql("SELECT 1")
    assert "cold start" in str(exc.value)


def test_warehouse_is_discovered_when_not_configured():
    settings = conn.Settings(host="https://x", token="t", warehouse_id=None)
    session = FakeSession(
        [
            FakeResponse(
                {
                    "warehouses": [
                        {"id": "stopped-one", "state": "STOPPED"},
                        {"id": "running-one", "state": "RUNNING"},
                    ]
                }
            )
        ]
    )
    c = conn.DatabricksClient(settings, session=session, poll_seconds=0)
    assert c.warehouse_id() == "running-one"
    # discovered once, then cached
    assert c.warehouse_id() == "running-one"


def test_a_workspace_with_no_warehouse_says_what_to_do():
    settings = conn.Settings(host="https://x", token="t", warehouse_id=None)
    session = FakeSession([FakeResponse({"warehouses": []})])
    c = conn.DatabricksClient(settings, session=session)
    with pytest.raises(conn.DatabricksError) as exc:
        c.warehouse_id()
    assert "no SQL warehouses" in str(exc.value)


# --------------------------------------------------------------------------
# merge_rows
# --------------------------------------------------------------------------


COLUMNS = (("id", "STRING"), ("tracks", "ARRAY<STRING>"), ("n", "INT"))


def test_merge_rows_builds_an_upsert_and_never_a_delete():
    session = FakeSession([succeeded()])
    written = client(session).merge_rows(
        "cat.sch.t",
        COLUMNS,
        ("id",),
        [{"id": "a", "tracks": ["AI/ML"], "n": 2}],
    )
    statement = session.calls[0]["json"]["statement"]
    assert written == 1
    assert statement.startswith("MERGE INTO cat.sch.t")
    assert "WHEN MATCHED THEN UPDATE SET *" in statement
    assert "WHEN NOT MATCHED THEN INSERT *" in statement
    assert "DELETE" not in statement.upper()
    assert "DROP" not in statement.upper()
    assert "CAST(array('AI/ML') AS ARRAY<STRING>)" in statement


def test_merge_rows_casts_every_column_so_an_all_null_batch_still_types():
    session = FakeSession([succeeded()])
    client(session).merge_rows(
        "cat.sch.t", COLUMNS, ("id",), [{"id": "a", "tracks": None, "n": None}]
    )
    statement = session.calls[0]["json"]["statement"]
    assert "CAST(NULL AS INT)" in statement
    assert "CAST(NULL AS ARRAY<STRING>)" in statement


def test_merge_rows_batches():
    session = FakeSession([succeeded(), succeeded(), succeeded()])
    rows = [{"id": str(i), "tracks": [], "n": i} for i in range(5)]
    assert client(session).merge_rows(
        "cat.sch.t", COLUMNS, ("id",), rows, batch_size=2
    ) == 5
    assert len(session.calls) == 3


def test_merge_rows_with_no_rows_sends_nothing():
    session = FakeSession([])
    assert client(session).merge_rows("cat.sch.t", COLUMNS, ("id",), []) == 0


def test_merge_rows_rejects_a_key_that_is_not_a_column():
    with pytest.raises(ValueError):
        client(FakeSession([])).merge_rows(
            "t", COLUMNS, ("nope",), [{"id": "a"}]
        )


def test_merge_rows_uses_null_safe_equality_on_the_key():
    # <=> rather than =, so a NULL key column matches itself instead of
    # inserting a duplicate row on every run.
    session = FakeSession([succeeded()])
    client(session).merge_rows("t", COLUMNS, ("id",), [{"id": None, "n": 1}])
    assert "<=>" in session.calls[0]["json"]["statement"]


# --------------------------------------------------------------------------
# live -- skipped unless credentials are present
# --------------------------------------------------------------------------


def _has_credentials() -> bool:
    try:
        conn.load_settings()
        return True
    except conn.MissingCredentialsError:
        return False


@pytest.mark.live
@pytest.mark.skipif(
    not _has_credentials(),
    reason="no Databricks credentials in the environment or .env",
)
def test_live_round_trip():
    client_ = conn.connect()
    assert client_.sql("SELECT 1 AS one").scalar() in (1, "1")
