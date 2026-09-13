"""
Which database, and where the files are.

Two of these pin bugs that config.py fixes rather than behaviour it
preserves, so they would have failed before it existed.
"""

import os
import sys
import types

import pytest

import config


@pytest.fixture
def env_dir(tmp_path, monkeypatch):
    """An app_dir with its own .env, and a clean environment."""
    monkeypatch.setattr(config, "app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(config, "_env_loaded", False)
    # ENTRY_SECRET included: conftest.py loads the developer's real .env at
    # collection time, and load_env() uses setdefault, so a test asserting on
    # a secret it supplied itself would otherwise be reading that one.
    for k in ("MB_DB_BACKEND", "MB_SQLITE_PATH", "MB_MONGO_URI", "MB_MONGO_DB",
              "ENTRY_SECRET"):
        monkeypatch.delenv(k, raising=False)
    cwd = os.getcwd()
    yield tmp_path
    os.chdir(cwd)


# ---------------------------------------------------------------- loading

def test_env_is_read_even_when_entry_secret_is_already_set(env_dir, monkeypatch):
    """
    The bug this replaces: `.env` was only read when ENTRY_SECRET was unset.

    On a machine where the secret is exported in the shell — which is exactly
    what CLAUDE.md tells a developer to do — every other setting in the file
    was silently ignored. With a backend named in there, that would have
    meant quietly running on the wrong database.
    """
    (env_dir / ".env").write_text("ENTRY_SECRET=from-shell\nMB_DB_BACKEND=mongo\n")
    monkeypatch.setenv("ENTRY_SECRET", "already-set")

    config.load_env()

    assert os.environ["MB_DB_BACKEND"] == "mongo"


def test_a_real_environment_variable_beats_the_file(env_dir, monkeypatch):
    (env_dir / ".env").write_text("MB_DB_BACKEND=mongo\n")
    monkeypatch.setenv("MB_DB_BACKEND", "sqlite")
    config.load_env()
    assert config.backend() == "sqlite"


def test_comments_and_blank_lines_are_skipped(env_dir):
    (env_dir / ".env").write_text("# a comment\n\nMB_MONGO_DB=named\n")
    config.load_env()
    assert config.mongo_db() == "named"


def test_a_missing_env_file_is_not_an_error(env_dir):
    config.load_env()
    assert config.backend() == "sqlite"


# ------------------------------------------------------------ baked, frozen

@pytest.fixture
def frozen(monkeypatch):
    """A packaged build, with a `.env` baked into it at build time."""
    def _freeze(values):
        module = types.ModuleType("_baked_env")
        module.VALUES = dict(values)
        monkeypatch.setitem(sys.modules, "_baked_env", module)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
    yield _freeze


def test_a_packaged_build_reads_the_values_baked_into_it(env_dir, frozen):
    frozen({"ENTRY_SECRET": "from-the-build", "MB_MONGO_DB": "baked"})
    config.load_env()
    assert os.environ["ENTRY_SECRET"] == "from-the-build"
    assert config.mongo_db() == "baked"


def test_a_packaged_build_ignores_a_env_beside_the_binary(env_dir, frozen):
    """
    The bug this pins: app_dir() is the folder holding the exe, so a build
    read — and, finding nothing, wrote — a *second* .env there. Cards signed
    with the project's own secret then failed against a build that looked
    like it had worked. There is one .env, in the source folder.
    """
    (env_dir / ".env").write_text("ENTRY_SECRET=stray\nMB_MONGO_DB=stray\n")
    frozen({"ENTRY_SECRET": "from-the-build", "MB_MONGO_DB": "baked"})

    config.load_env()

    assert os.environ["ENTRY_SECRET"] == "from-the-build"
    assert config.mongo_db() == "baked"


def test_a_real_environment_variable_still_beats_a_baked_value(env_dir, frozen,
                                                               monkeypatch):
    frozen({"MB_DB_BACKEND": "mongo"})
    monkeypatch.setenv("MB_DB_BACKEND", "sqlite")
    config.load_env()
    assert config.backend() == "sqlite"


def test_a_packaged_build_refuses_to_write_env(env_dir, frozen):
    frozen({"ENTRY_SECRET": "from-the-build"})
    os.chdir(env_dir)

    with pytest.raises(RuntimeError, match="packaged build"):
        config.set_env_value("ENTRY_SECRET", "invented")

    assert not (env_dir / ".env").exists()


# ---------------------------------------------------------------- writing

def test_writing_a_key_keeps_the_rest_of_the_file(env_dir):
    """
    The bug this replaces: provisioning a secret opened .env with mode "w".

    That was survivable while ENTRY_SECRET was the only thing in the file.
    With a Mongo URI beside it, first run would have thrown the URI away.
    """
    (env_dir / ".env").write_text("MB_MONGO_URI=mongodb+srv://keep/me\n")
    os.chdir(env_dir)

    config.set_env_value("ENTRY_SECRET", "generated")

    written = (env_dir / ".env").read_text()
    assert "MB_MONGO_URI=mongodb+srv://keep/me" in written
    assert "ENTRY_SECRET=generated" in written


def test_writing_an_existing_key_replaces_it_rather_than_duplicating(env_dir):
    (env_dir / ".env").write_text("ENTRY_SECRET=old\nMB_MONGO_DB=x\n")
    os.chdir(env_dir)

    config.set_env_value("ENTRY_SECRET", "new")

    written = (env_dir / ".env").read_text()
    assert written.count("ENTRY_SECRET=") == 1
    assert "ENTRY_SECRET=new" in written
    assert "MB_MONGO_DB=x" in written


# ---------------------------------------------------------------- backend

def test_the_default_backend_is_sqlite(env_dir):
    """The only one that works when the reception laptop has no internet."""
    assert config.backend() == "sqlite"


def test_an_unknown_backend_is_refused(env_dir, monkeypatch):
    monkeypatch.setenv("MB_DB_BACKEND", "postgres")
    with pytest.raises(RuntimeError, match="MB_DB_BACKEND"):
        config.backend()


def test_mongo_without_a_uri_raises_rather_than_falling_back(env_dir, monkeypatch):
    """
    Never silently fall back to SQLite: that means reception writing a day of
    attendance into a local file nobody looks at again.
    """
    monkeypatch.setenv("MB_DB_BACKEND", "mongo")
    assert config.backend() == "mongo"
    with pytest.raises(RuntimeError, match="MB_MONGO_URI"):
        config.mongo_uri()


def test_describe_never_prints_the_password(env_dir, monkeypatch):
    monkeypatch.setenv("MB_DB_BACKEND", "mongo")
    monkeypatch.setenv("MB_MONGO_URI", "mongodb+srv://user:hunter2@cluster.example/db")
    line = config.describe()
    assert "hunter2" not in line
    assert "cluster.example" in line


def test_describe_names_the_sqlite_file(env_dir):
    assert "SQLite" in config.describe()
    assert "academy.db" in config.describe()
