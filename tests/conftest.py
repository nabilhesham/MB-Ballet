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


BACKENDS = ["sqlite", "mongo"]

# A URI kept deliberately separate from MB_MONGO_URI, so a misconfigured run
# cannot point the suite at production.
TEST_MONGO_URI = "MB_TEST_MONGO_URI"

# Every test database is named like this, and nothing else is ever dropped.
# See the guard in mongo_database() below and in MongoRepo.drop_all().
TEST_DB_PREFIX = "mbtest_"


@pytest.fixture(params=BACKENDS)
def backend(request):
    if request.param == "mongo" and not os.environ.get(TEST_MONGO_URI):
        # Skipped, never failed. The reception laptop and a CI run with no
        # secrets must still get a green SQLite result.
        pytest.skip(f"{TEST_MONGO_URI} is not set")
    return request.param


@pytest.fixture(scope="session")
def mongo_database():
    """
    One database for the whole session, cleared between tests.

    Not one per test: creating and dropping a database on Atlas costs a
    round trip each way and there are hundreds of tests.
    """
    uri = os.environ.get(TEST_MONGO_URI)
    if not uri:
        # A generator fixture must yield even when it has nothing to give.
        yield None
        return
    import uuid
    name = f"{TEST_DB_PREFIX}{uuid.uuid4().hex[:12]}"
    yield name
    from pymongo import MongoClient
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    try:
        client.drop_database(name)
    finally:
        client.close()


@pytest.fixture
def repo(backend, tmp_path, monkeypatch, mongo_database):
    """
    A repository over a throwaway database, chosen through config.

    Config is what answers "which database?", rather than a path passed from
    hand to hand. That is what makes the api/ layer testable at all: route
    handlers call a bare `data.connect()`, so without it any test touching
    one would reach for the real academy.db.
    """
    monkeypatch.setenv("MB_DB_BACKEND", backend)
    if backend == "sqlite":
        monkeypatch.setenv("MB_SQLITE_PATH", str(tmp_path / "academy.db"))
        db.init()
    else:
        assert mongo_database.startswith(TEST_DB_PREFIX), mongo_database
        monkeypatch.setenv("MB_MONGO_URI", os.environ[TEST_MONGO_URI])
        monkeypatch.setenv("MB_MONGO_DB", mongo_database)
        monkeypatch.setenv("MB_MONGO_ALLOW_DROP", "1")

    r = data.connect()
    if backend == "mongo":
        # Each test starts from nothing, and ids start from 1 again --
        # which is what lets the parity tests compare documents directly.
        r.drop_all()
    r.init_schema()
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
