"""pytest configuration for the Databricks tests.

Registers the ``live`` marker used by the handful of tests that actually talk
to a warehouse. Those tests skip themselves when no credentials are present, so
the suite runs offline by default. To skip them even when credentials ARE
present:

    pytest pipeline/ -m "not live"
"""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: touches a real Databricks workspace; skipped without credentials",
    )
