"""
What a packaged binary carries out of `.env`, and what it must not.

`academy.spec` bakes this folder's `.env` into the binary because a frozen
build cannot read one -- that part is covered by the app working at all. What
is easy to undo silently is the *exceptions*: two EZVIZ credentials that open
the academy's front door, and a test-only Atlas URI. Anyone who can fetch a
built binary can read what is compiled into it, so each of those is a
credential handed out with the download, and nothing about the build would
look wrong if one came back.

The other half of this file is the duplicated parser. `academy.spec` cannot
import `config` -- it runs under PyInstaller's own interpreter before anything
of the app is importable -- so the few lines that read `.env` exist twice, and
CLAUDE.md says to keep the two in step. That is exactly the kind of
instruction that gets skipped, so it is asserted here instead.
"""

import os
import textwrap

import pytest

pytestmark = pytest.mark.sqlite_only   # about packaging, not about a backend

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _spec_functions():
    """
    `_read_env` and `_bake_env` out of academy.spec, without running the rest.

    The spec is not importable: it uses PyInstaller's injected globals
    (SPECPATH, Analysis, PYZ, EXE) and bakes at module level. Only the two
    functions above that are wanted, so the source is sliced at the line that
    calls them.
    """
    src = open(os.path.join(ROOT, "academy.spec"), encoding="utf-8").read()
    start = src.index("def _read_env")
    end = src.index("BAKED_DIR, BAKED_KEYS = _bake_env")
    ns = {"os": os}
    exec(compile(src[start:end], "academy.spec", "exec"), ns)   # noqa: S102
    return ns


def _write_env(tmp_path, text):
    (tmp_path / ".env").write_text(textwrap.dedent(text), encoding="utf-8")
    return str(tmp_path)


FULL = """\
    ENTRY_SECRET=signs-every-card
    EZVIZ_LOCK_SERIAL=BK5433560
    EZVIZ_REGION=apiieu.ezvizlife.com
    EZVIZ_TERMINAL=academy phone
    EZVIZ_EMAIL=reception@example.com
    EZVIZ_PASSWORD=opens-the-front-door
    MB_DB_BACKEND=sqlite
    MB_TEST_MONGO_URI=mongodb+srv://u:p@cluster.example.net/
"""


def _bake(tmp_path, text=FULL):
    fns = _spec_functions()
    spec_dir = _write_env(tmp_path, text)
    baked_dir, keys = fns["_bake_env"](spec_dir, os.path.join(spec_dir, "build"))
    body = open(os.path.join(baked_dir, "_baked_env.py"), encoding="utf-8").read()
    return keys, body


def test_the_door_credentials_never_travel_in_the_binary(tmp_path):
    """
    These two are the EZVIZ account that unlocks the academy's front door.

    The app does not need them: the warm path is the cached session copied in
    beside the binary by hand, and an interactive first sign-in wants an SMS
    code that no server can type. So there is nothing on the other side of the
    trade -- baking them only puts the front door inside a file that gets
    emailed around.
    """
    keys, body = _bake(tmp_path)
    assert "EZVIZ_EMAIL" not in keys
    assert "EZVIZ_PASSWORD" not in keys
    assert "reception@example.com" not in body
    assert "opens-the-front-door" not in body


def test_the_test_suites_atlas_uri_never_travels_either(tmp_path):
    """
    MB_TEST_MONGO_URI legitimately sits in `.env` -- conftest.py reads that
    file the way the app does -- but no running code reads it, and it carries
    an Atlas password. A setting the app never looks at cannot even be the
    reason a build behaves differently, so there is nothing bought by it.
    """
    keys, body = _bake(tmp_path)
    assert "MB_TEST_MONGO_URI" not in keys
    assert "cluster.example.net" not in body


def test_the_serial_and_the_region_do_travel(tmp_path):
    """
    The other side of the split: a frozen build reads no file, so a serial
    left out is a binary with no door -- silently, because `configured()`
    answering "no" is a legitimate state the kiosk expresses by showing
    nothing at all. A serial identifies a device and is useless on its own; a
    password that opens a door is not.
    """
    keys, body = _bake(tmp_path)
    assert "EZVIZ_LOCK_SERIAL" in keys
    assert "EZVIZ_REGION" in keys
    assert "EZVIZ_TERMINAL" in keys
    assert "BK5433560" in body
    assert "signs-every-card" in body       # ENTRY_SECRET, the whole point


def test_a_build_with_no_env_refuses(tmp_path):
    """
    A build that found no `.env` used to produce a binary that invented its
    own ENTRY_SECRET, rejecting every card already printed while looking
    perfectly healthy.
    """
    fns = _spec_functions()
    with pytest.raises(SystemExit):
        fns["_bake_env"](str(tmp_path), os.path.join(str(tmp_path), "build"))


def test_a_build_with_an_empty_secret_refuses(tmp_path):
    """Present but blank is the same failure one step along."""
    with pytest.raises(SystemExit):
        _bake(tmp_path, "ENTRY_SECRET=\nMB_DB_BACKEND=sqlite\n")


def test_the_two_env_parsers_agree(tmp_path, monkeypatch):
    """
    The spec's `_read_env` is a deliberate second copy of config.load_env()'s
    few lines, and CLAUDE.md says to keep them in step. A build that read the
    file differently from the app is the class of bug the baking exists to
    remove, so it is checked rather than remembered.

    Both sides are the real code: the spec's function is sliced out of the
    spec, and the app's is reached by pointing `app_dir()` at the same folder
    and calling `load_env()`, which is what an entry point does. Comparing
    against a hand-copy of the loop would only prove the copy.
    """
    import config

    awkward = """\
        # a comment
        ENTRY_SECRET = spaced out
        MB_SQLITE_PATH=academy.db
        a line with no equals sign
        MB_MONGO_URI=mongodb://h:27017/?a=1&b=2

        MB_DB_BACKEND=sqlite
    """
    spec_dir = _write_env(tmp_path, awkward)
    from_spec = _spec_functions()["_read_env"](os.path.join(spec_dir, ".env"))

    # load_env() sets with setdefault and is idempotent behind a flag, so the
    # keys have to be clear and the flag reset, or a real value in this
    # process's environment would answer for the file.
    for key in from_spec:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "app_dir", lambda: spec_dir)
    monkeypatch.setattr(config, "_env_loaded", False)
    monkeypatch.chdir(tmp_path)
    config.load_env()

    from_app = {k: os.environ[k] for k in from_spec if k in os.environ}
    assert from_spec == from_app
    assert from_spec["ENTRY_SECRET"] == "spaced out"
    assert from_spec["MB_MONGO_URI"] == "mongodb://h:27017/?a=1&b=2"
