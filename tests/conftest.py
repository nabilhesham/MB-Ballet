"""
Test harness for MB Ballet Academy.

Every test gets its own throwaway database. The three scripts this replaces
ran against the live `academy.db` and wrote to it — inserting sessions and
bookings, and in one case rewriting a subscription's `frozen_on` to fabricate
a ten-day-old freeze. That is a standing hazard on a machine where that file
is the business record.

The `academy` fixture is deliberately the only way in, so a test cannot
quietly reach a database that outlives it.
"""

import os
import sys

import pytest

# The repo root, so `import db` works however pytest was invoked. Done before
# any project import below.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# tokens.py refuses to import a secret it cannot find, and the real one lives
# in a .env that is not in git. Set before `import tokens` anywhere.
os.environ.setdefault("ENTRY_SECRET", "test-secret-not-a-real-key")

import db                                    # noqa: E402
from fixtures import build_academy           # noqa: E402


# Named so the parametrisation added in a later phase — sqlite and mongo —
# is a change to this one list rather than to every fixture below.
BACKENDS = ["sqlite"]


@pytest.fixture(params=BACKENDS)
def backend(request):
    return request.param


@pytest.fixture
def conn(backend, tmp_path, monkeypatch):
    """
    A throwaway database, pointed at through config rather than by passing a
    path around.

    That matters: route handlers call a bare `db.connect()`, so the only way
    to test one without it reaching for the real academy.db is for config to
    be the thing that answers "which database?". Before config.py existed
    this fixture could reach access.py but not the api/ layer at all.
    """
    if backend != "sqlite":                  # pragma: no cover - until phase 4
        pytest.skip(f"no {backend} backend yet")
    monkeypatch.setenv("MB_DB_BACKEND", "sqlite")
    monkeypatch.setenv("MB_SQLITE_PATH", str(tmp_path / "academy.db"))
    db.init()
    c = db.connect()
    yield c
    c.close()


@pytest.fixture
def academy(conn):
    """A populated academy. See tests/fixtures.py for what is in it."""
    return build_academy(conn)
