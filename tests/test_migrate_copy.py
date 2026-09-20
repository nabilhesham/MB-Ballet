"""
What copy() actually sends to MongoDB, without a MongoDB.

The write itself needs a replica set, which no test here has, but the thing
worth pinning down is the shape of the operations: every row exactly once,
under the id SQLite gave it, as a replace-if-present rather than an insert.
A fake collection that records what it was handed answers all of that.

Both bugs this covers reached the academy's live data before anything caught
them, because nothing exercised copy() at all -- the dry run returns before
it, and every other test stops at the pymongo import.
"""

import sys

import pytest

import migrate_to_mongo as mig

# Not pytest.importorskip: that catches ImportError, and a machine whose
# system `cryptography` is broken fails this import with a pyo3
# PanicException instead -- which derives from BaseException, escaped the
# skip, and took the whole collection down rather than this one file.
try:
    from pymongo import ReplaceOne
except BaseException as e:  # noqa: BLE001 - see above
    pytest.skip(f"pymongo does not import here: {type(e).__name__}",
                allow_module_level=True)


class FakeCollection:
    def __init__(self, existing=0):
        self.ops = []
        self.batches = []
        self._existing = existing

    def bulk_write(self, ops, ordered=True):
        self.batches.append(len(ops))
        self.ops.extend(ops)

    def count_documents(self, _flt):
        return self._existing + len(self.ops)


class FakeTarget:
    def __init__(self):
        self.db = {}

    def __getitem__(self, coll):
        return self.db.setdefault(coll, FakeCollection())


class FakeDb(dict):
    def __missing__(self, coll):
        self[coll] = FakeCollection()
        return self[coll]


class Target:
    def __init__(self):
        self.db = FakeDb()


def test_every_row_goes_once_under_its_own_id(academy, repo):
    target = Target()
    n = mig.copy(repo, target, "clients", dry_run=False)

    rows = repo.find("clients")
    assert n == len(rows)
    ops = target.db["clients"].ops
    assert len(ops) == len(rows)
    assert all(isinstance(op, ReplaceOne) for op in ops)

    # ReplaceOne exposes no public accessors; these are the driver's own
    # attribute names. A driver change breaks this loudly, which is the right
    # way round for a test whose whole job is to inspect what was sent.
    sent = {op._filter["_id"]: op._doc for op in ops}
    assert sent.keys() == {r["id"] for r in rows}
    for r in rows:
        doc = sent[r["id"]]
        assert doc["_id"] == r["id"], "the printed member number is this id"
        assert doc["name_en"] == r["name_en"]
        assert "id" not in doc, "the integer lives in _id, not beside it"


def test_it_replaces_rather_than_inserts(academy, repo):
    """
    A migration is something people run more than once while they get it
    right. insert_many() fails the second time with a duplicate key, half way
    through, leaving a database that is neither the old one nor the new one.
    """
    target = Target()
    mig.copy(repo, target, "clients", dry_run=False)
    assert all(op._upsert for op in target.db["clients"].ops)


def test_a_dry_run_sends_nothing(academy, repo):
    target = Target()
    n = mig.copy(repo, target, "clients", dry_run=True)
    assert n == repo.count("clients")
    assert not target.db["clients"].ops


def test_pictures_go_in_smaller_batches(academy, repo):
    """
    Base64 photos are two orders of magnitude bigger per row than anything
    else, so five hundred in one command is tens of megabytes against a limit
    measured in them.
    """
    import images

    blob = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    for owner in range(mig.IMAGE_BATCH * 2 + 3):
        images.store(repo, images.CARD, owner + 1, blob, "image/png",
                     variant=f"class-{owner}")

    target = Target()
    n = mig.copy(repo, target, "images", dry_run=False)
    assert n == mig.IMAGE_BATCH * 2 + 3
    assert max(target.db["images"].batches) <= mig.IMAGE_BATCH
    assert sum(target.db["images"].batches) == n


def test_settings_is_not_migrated(repo):
    """
    SQLite bookkeeping -- one row recording that a one-shot repair ran --
    and db.migrate() never runs against MongoDB, so the marker means nothing
    there. Both backends' is_empty() already exclude it. It is also the one
    table keyed by `key` rather than `id`, so find()'s `id` tiebreak made it
    "no such column: id" on the first collection copied.
    """
    assert "settings" not in mig.ORDER


# ---------------------------------------------------------------- main()

class Recorder:
    """Stands in for the whole MongoDB side of a run."""

    def __init__(self, existing=None):
        self.db = FakeDb()
        self.closed = False
        for coll, ids in (existing or {}).items():
            for i in ids:
                self.db[coll].ops.append(None)

    def is_empty(self):
        return not any(c.ops for c in self.db.values())

    def init_schema(self):
        self.schema_built = True

    def close(self):
        self.closed = True


@pytest.fixture
def run_main(academy, repo, monkeypatch, capsys):
    """main() with the Mongo half replaced and the source pinned to the fixture."""
    def go(argv, target):
        monkeypatch.setattr(sys, "argv", ["migrate_to_mongo.py"] + argv)
        monkeypatch.setenv("MB_DB_BACKEND", "mongo")
        monkeypatch.setenv("MB_MONGO_URI", "mongodb://fake/")
        monkeypatch.setattr(mig, "sqlite_repo", lambda: repo)
        monkeypatch.setattr(mig, "mongo_repo", lambda: target)
        monkeypatch.setattr(mig.mongo_ids, "sync_counters", lambda db, fields: None)
        monkeypatch.setattr(repo, "close", lambda: None)
        code = mig.main()
        return code, capsys.readouterr().out
    return go


def test_a_run_copies_every_collection(run_main, repo):
    target = Recorder()
    code, out = run_main([], target)
    assert code == 0, out
    for coll in mig.ORDER:
        assert len(target.db[coll].ops) == repo.count(coll), coll
    assert "every id preserved" in out
    assert target.closed


def test_a_target_holding_data_is_refused_without_force(run_main):
    target = Recorder(existing={"clients": [1]})
    code, out = run_main([], target)
    assert code == 1
    assert "--force" in out
    assert len(target.db["clients"].ops) == 1, "nothing was written"


def test_force_overwrites_by_id_and_says_what_it_left(run_main, repo):
    """
    Nothing is deleted. A document under an id this source does not have is
    left where it is and counted -- on a re-run that usually means rows
    deleted from SQLite since the last one.
    """
    target = Recorder()
    target.db["clients"]._existing = 3
    code, out = run_main(["--force"], target)
    assert code == 0, out
    assert "Left in place, not in this source" in out
    assert "'clients': 3" in out


def test_a_dry_run_never_reaches_the_target(run_main):
    target = Recorder()
    code, out = run_main(["--dry-run"], target)
    assert code == 0
    assert "nothing was written to MongoDB" in out
    assert not any(c.ops for c in target.db.values())
    assert not hasattr(target, "schema_built")
