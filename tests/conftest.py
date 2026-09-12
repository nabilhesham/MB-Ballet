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

# Read .env the same way the app does, so MB_TEST_MONGO_URI can live there
# rather than being exported by hand before every run. Done before the
# ENTRY_SECRET default below, so a real secret in the file still wins.
import config                                 # noqa: E402
config.load_env()

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


def pytest_collection_modifyitems(items):
    """
    Skip the SQLite-only tests on any other backend.

    The marker was registered and tagged but nothing acted on it, so
    `PRAGMA table_info` ran against MongoDB. A marker with no hook behind it
    is a comment.
    """
    for item in items:
        if item.get_closest_marker("sqlite_only") and "[mongo]" in item.name:
            item.add_marker(pytest.mark.skip(
                reason="describes the SQLite backend itself"))


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
    One database for the whole session, with its indexes built once.

    Not one per test, and emphatically not drop_all() per test: dropping
    twelve collections and rebuilding every index costs about thirteen
    seconds a test over the network, which is three quarters of an hour for
    this suite. The indexes are structure and do not change between tests;
    only the documents do. See mongo_clean().
    """
    uri = os.environ.get(TEST_MONGO_URI)
    if not uri:
        # A generator fixture must yield even when it has nothing to give.
        yield None
        return
    import uuid
    name = f"{TEST_DB_PREFIX}{uuid.uuid4().hex[:12]}"

    # The same process-wide client the app uses. Building one per test is
    # a TLS handshake and a round of topology discovery each time, which is
    # the anti-pattern repo/mongo/client.py exists to avoid -- and closing
    # it mid-suite cancels operations still in flight.
    from repo.mongo import schema
    from repo.mongo.client import get_client, reset
    client = get_client(uri)
    schema.ensure_indexes(client[name])
    yield name
    try:
        client.drop_database(name)
    finally:
        reset()


def mongo_clean(uri, name):
    """
    Empty every collection and put the id counters back to zero.

    Resetting the counters matters beyond tidiness: the parity tests compare
    documents field for field, which only works because both backends hand
    out 1, 2, 3… from an empty database.
    """
    from repo.mongo import ids, schema
    from repo.mongo.client import get_client
    database = get_client(uri)[name]
    for coll in list(schema.FIELDS) + [ids.COUNTERS]:
        database[coll].delete_many({})


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

    if backend == "mongo":
        # Documents only. The indexes were built once, for the session.
        mongo_clean(os.environ[TEST_MONGO_URI], mongo_database)
    r = data.connect()
    if backend == "sqlite":
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
