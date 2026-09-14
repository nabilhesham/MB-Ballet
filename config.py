"""
Where the app's files live, and which database it talks to.

Two things used to make this impossible to answer in one place.

`db.py` bound `DB_PATH = "academy.db"` at import time, and `server.py` read
`.env` in its FastAPI *startup event* — which runs long after `import db`.
That was harmless while the value was a literal, and fatal the moment an
environment variable has to decide which implementation gets constructed. So
nothing here is a module constant: every value is a function, read when it is
asked for.

The frozen/APP_DIR block was also copied into both `server.py` and
`run_app.py`. It is here once instead, because "the database lives
next to the exe" is the same fact in both. `.env` is not in that
list: there is one of those, in the source folder, and a packaged build
carries its values rather than reading a second copy — see load_env().

    MB_DB_BACKEND   sqlite | mongo          (default sqlite)
    MB_SQLITE_PATH  academy.db              (relative to app_dir())
    MB_MONGO_URI    mongodb+srv://...       (required when backend is mongo)
    MB_MONGO_DB     mb_ballet

Everything is MB_-prefixed because load_env() sets *any* KEY=VALUE it finds
and must not collide with something already on the machine.
"""

import os
import sys

SQLITE = "sqlite"
MONGO = "mongo"

ENV_FILE = ".env"
_env_loaded = False


def app_dir() -> str:
    """
    The folder the academy's own files live in — academy.db, and nothing
    else since the photos and cards moved into it (see images.py).

    Not `.env`. In a source checkout this is the folder holding it anyway; in
    a packaged build the settings are baked into the binary and nothing looks
    for a file here. See load_env().

    When packaged, PyInstaller unpacks the bundle into a temporary folder
    that is wiped on exit, so this is deliberately the folder holding the
    executable rather than the bundle. Getting it the other way round
    destroys the database on every close.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def bundle_dir() -> str:
    """Where read-only assets are: static/ and the card's fonts."""
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def _baked() -> dict:
    """
    The settings `academy.spec` read out of `.env` and compiled into the build.

    Empty in a source checkout and under the test suite, where the module does
    not exist — that is the signal to read the real file instead.
    """
    try:
        import _baked_env
    except ImportError:
        return {}
    return dict(_baked_env.VALUES)


def load_env() -> None:
    """
    Change into app_dir() and put the app's settings into the environment.

    Must be the first statement of any entry point, before `import db` binds
    anything. Idempotent, so calling it from several places is fine.

    Real environment variables win: values are set with `setdefault`, so
    `MB_DB_BACKEND=mongo ./start.sh` overrides them rather than being silently
    overridden.

    **There is one `.env`, and it lives in the source folder.** A packaged
    build carries its values inside the binary (see `_baked()`) and never
    reads a file beside the executable — deliberately, because that file is
    the one the app itself used to write. app_dir() is the folder holding the
    exe, so a build would look there, find nothing, and mint a fresh random
    ENTRY_SECRET into a second `.env` nobody knew about; every card already
    printed then stopped verifying, with a build that looked like it worked.
    The chdir stays either way: academy.db still lives beside the exe.

    The file is read unconditionally. It used to be read only when
    ENTRY_SECRET was unset — so on a machine where the secret was exported in
    the shell, every *other* setting in the file was ignored, which would now
    silently mean "the Mongo URI is missing".
    """
    global _env_loaded
    os.chdir(app_dir())
    if _env_loaded:
        return
    _env_loaded = True

    if getattr(sys, "frozen", False):
        for k, v in _baked().items():
            os.environ.setdefault(k, v)
        return

    if not os.path.exists(ENV_FILE):
        return
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def set_env_value(key: str, value: str) -> None:
    """
    Write one key into `.env`, keeping everything else in it.

    The previous version opened the file with mode "w" to save a generated
    ENTRY_SECRET, which truncated it. That was survivable while the secret
    was the only thing in there; with a Mongo URI beside it, provisioning a
    secret on first run would have thrown the URI away.

    Refused outright in a packaged build. There is one `.env` and it is in
    the source folder; writing one beside the exe is exactly how a second
    signing secret came into existence. The guard is here rather than only at
    the call site so a future caller cannot reintroduce it by not knowing.
    """
    if getattr(sys, "frozen", False):
        raise RuntimeError(
            "A packaged build does not write .env. Its settings were baked in "
            "from the project's own .env when it was built; change that file "
            "and build again.")

    lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            lines = f.read().splitlines()

    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")

    with open(ENV_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.environ[key] = value


# ---------------------------------------------------------------- backend

def backend() -> str:
    """
    Which database implementation to construct. SQLite unless told otherwise.

    SQLite is the default deliberately and should stay that way: it is the
    only one that keeps working when the reception laptop has no internet.
    """
    name = (os.environ.get("MB_DB_BACKEND") or SQLITE).strip().lower()
    if name not in (SQLITE, MONGO):
        raise RuntimeError(
            f"MB_DB_BACKEND is {name!r}. It must be {SQLITE!r} or {MONGO!r}.")
    return name


def sqlite_path() -> str:
    return os.environ.get("MB_SQLITE_PATH") or "academy.db"


def legacy_media_dir() -> str:
    """
    Where an install older than images.py left its photos/ and cards/.

    Beside the database, which is where they always sat. In every real
    install that is app_dir() and these two answers are the same; they part
    company only when MB_SQLITE_PATH puts the database somewhere else, and
    then the photos lying in whatever folder the app was launched from
    belong to a different database and must not be read into this one.
    """
    if backend() == "sqlite":
        return os.path.dirname(os.path.abspath(sqlite_path()))
    return app_dir()


def mongo_uri() -> str:
    """
    The Atlas connection string.

    Raises rather than falling back to SQLite when the backend is mongo and
    this is missing. A silent fallback would mean reception writing a day of
    attendance into a local file nobody ever looks at again.

    If a `mongodb+srv://` URI fails to resolve, the network's DNS is probably
    filtering SRV/TXT lookups; the non-SRV form in .env.example works there.
    """
    uri = os.environ.get("MB_MONGO_URI")
    if not uri:
        raise RuntimeError(
            "MB_DB_BACKEND is 'mongo' but MB_MONGO_URI is not set. Put the "
            "Atlas connection string in .env, never in git.")
    return uri


def mongo_db() -> str:
    return os.environ.get("MB_MONGO_DB") or "mb_ballet"


def describe() -> str:
    """One line for the startup banner, with no secret in it."""
    if backend() == SQLITE:
        return f"SQLite  {os.path.join(app_dir(), sqlite_path())}"
    # Never print the URI: it carries the password.
    host = mongo_uri().split("@")[-1].split("/")[0]
    return f"MongoDB  {mongo_db()} at {host}"
