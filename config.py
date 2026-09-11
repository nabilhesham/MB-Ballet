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
`run_app.py`. It is here once instead, because "the database, photos, cards
and .env live next to the exe" is the same fact in both.

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
    The folder the academy's own files live in: academy.db, .env, photos,
    cards.

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


def load_env() -> None:
    """
    Change into app_dir() and read `.env` into the environment.

    Must be the first statement of any entry point, before `import db` binds
    anything. Idempotent, so calling it from several places is fine.

    Real environment variables win: values are set with `setdefault`, so
    `MB_DB_BACKEND=mongo ./start.sh` overrides the file rather than being
    silently overridden by it.

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
    """
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
