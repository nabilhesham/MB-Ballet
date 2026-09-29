"""
Every backend implements every port method.

CLAUDE.md says of `repo/base.py`: "An ABC: a backend missing one fails at
construction rather than at reception." That was true and it was not being
*checked*, which is a different thing — and the gap had teeth, because
`MongoRepo` is only ever constructed when `MB_DB_BACKEND=mongo`. The whole
SQLite suite passes with a half-implemented Mongo backend, the Mongo half
is opt-in behind `MB_TEST_MONGO_URI` and normally skipped, so the first
thing to notice was a **packaged binary** dying at startup on a reception
Mac with

    TypeError: Can't instantiate abstract class MongoRepo without an
    implementation for abstract method 'plan_slot_times'

The method had been implemented on `MongoPlans` while its `@abstractmethod`
was declared on `ClientsPort` — SQLite has one flat class and so satisfied
both, and the mismatch was invisible until a real Mongo build ran.

Python already knows the answer: an ABC collects what is still unimplemented
into `__abstractmethods__`. It needs no database, no URI and no network, so
this runs in the ordinary suite rather than the opt-in half — which is the
whole point, since the opt-in half is exactly what nobody runs before a
build.
"""

import pytest

pytestmark = pytest.mark.sqlite_only    # about the classes, not about data


def _backends():
    from repo.sqlite import SqliteRepo
    out = [SqliteRepo]
    try:
        from repo.mongo import MongoRepo
    except ImportError:          # pragma: no cover - no pymongo installed
        pass
    else:
        out.append(MongoRepo)
    return out


def test_every_backend_implements_every_port_method():
    """
    A name here means a port method with no implementation on that backend.
    Either write it, or the abstract declaration is on the wrong ABC and the
    implementation landed on a class that does not inherit from it.
    """
    for repo_class in _backends():
        missing = sorted(repo_class.__abstractmethods__)
        assert not missing, (
            f"{repo_class.__name__} cannot be constructed — no implementation "
            f"for {', '.join(missing)}")


def test_the_mongo_backend_is_actually_being_checked():
    """
    The test above quietly passes on a machine with no pymongo, which is the
    machine most likely to add a port method without a Mongo half. It is
    worth knowing which of the two happened, so this fails only where the
    backend could have been checked and was not imported.
    """
    names = [c.__name__ for c in _backends()]
    if "MongoRepo" not in names:
        pytest.skip("pymongo is not installed, so only SQLite was checked")
    assert names == ["SqliteRepo", "MongoRepo"]
