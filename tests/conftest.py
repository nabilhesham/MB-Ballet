"""
Test harness for MB Ballet Academy.

Every test gets its own throwaway database. The three scripts this replaces
ran against the live `academy.db` and wrote to it — inserting sessions and
bookings, and in one case rewriting a subscription's `frozen_on` to fabricate
a ten-day-old freeze. That is a standing hazard on a machine where that file
is the business record.
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
import repo as data                          # noqa: E402
from fixtures import build_academy           # noqa: E402


# Named so that adding "mongo" is a change to this one list rather than to
# every fixture below.
BACKENDS = ["sqlite"]


@pytest.fixture(params=BACKENDS)
def backend(request):
    return request.param


@pytest.fixture
def repo(backend, tmp_path, monkeypatch):
    """
    A repository over a throwaway database, chosen through config.

    Config is what answers "which database?", rather than a path passed from
    hand to hand. That is what makes the api/ layer testable at all: route
    handlers call a bare `data.connect()`, so without it any test touching
    one would reach for the real academy.db.
    """
    if backend != "sqlite":                  # pragma: no cover - until phase 4
        pytest.skip(f"no {backend} backend yet")
    monkeypatch.setenv("MB_DB_BACKEND", backend)
    monkeypatch.setenv("MB_SQLITE_PATH", str(tmp_path / "academy.db"))
    db.init()
    r = data.connect()
    yield r
    r.close()


@pytest.fixture
def conn(repo):
    """
    The raw sqlite3 connection behind the repository.

    Only for tests that are *about* SQLite — the schema-shape checks, and the
    transaction tests that assert on `in_transaction`. Anything describing
    the app's behaviour should go through `repo`, or it cannot be run against
    a second backend.
    """
    return repo.conn


@pytest.fixture
def academy(repo):
    """A populated academy. See tests/fixtures.py for what is in it."""
    return build_academy(repo)
