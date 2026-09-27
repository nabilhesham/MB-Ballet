# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build spec for MB Ballet Academy.

Kept as a file rather than a long command line so the hidden imports are
reviewable. uvicorn and starlette load several modules by string name at
runtime, which PyInstaller's static analysis cannot see — leaving any of these
out produces an .exe that opens a console and closes instantly.

It also bakes the project's `.env` into the binary. There is exactly one
`.env` in this project, in the folder holding this file, and a packaged build
carries its values rather than reading a second copy from beside the
executable -- see `_bake_env()` below for why that had to change.
"""

import os

# The React build (frontend/, committed into static/app/) is already inside
# static/, so this needs no entry of its own — and must not get one. Adding
# a separate ("frontend", "frontend") entry would bundle node_modules into
# the exe; the whole point of committing static/app/ is that this build
# never touches frontend/ at all. static/fonts/ rides along the same way,
# and must: the card sets its own typefaces from there rather than from
# whatever the machine happens to have installed.
datas = [("static", "static")]


def _read_env(path):
    """
    Parse `.env` exactly the way config.load_env() does.

    Deliberately a copy of those few lines rather than an import of config:
    the spec runs under PyInstaller's own interpreter before anything of the
    app is importable. Keep the two in step -- a build that reads the file
    differently from the app is the class of bug this whole change exists to
    remove.
    """
    values = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    return values


def _bake_env(spec_dir, work_dir):
    """
    Read the project's own `.env` and write it into a module the build bundles.

    ENTRY_SECRET signs every member card. A packaged build used to carry no
    secret at all: config.app_dir() is the folder holding the executable, so
    the binary looked for `.env` beside itself in dist/ and server.py's
    startup event invented a fresh random one when it found none. Every card
    printed against the project's own secret then stopped verifying, silently,
    against a build that looked like it had worked.

    So the values travel inside the binary, and the build refuses to produce
    one that cannot verify the cards already in clients' hands.

    A generated module rather than a `datas` entry on purpose: a data file is
    unpacked into sys._MEIPASS, a real directory on disk for as long as the
    app runs. This compiles into the PYZ instead.
    """
    env_path = os.path.join(spec_dir, ".env")
    if not os.path.exists(env_path):
        raise SystemExit(
            "\n  No .env found in {}\n\n"
            "  The build embeds this project's settings into the binary, and\n"
            "  ENTRY_SECRET is what signs every member card. Without it the\n"
            "  app you would be shipping cannot verify a single card that has\n"
            "  already been printed.\n\n"
            "  Copy .env.example to .env and fill in ENTRY_SECRET, then run\n"
            "  this build again.\n".format(spec_dir))

    values = _read_env(env_path)
    if not values.get("ENTRY_SECRET"):
        raise SystemExit(
            "\n  ENTRY_SECRET is empty in {}\n\n"
            "  That value signs every member card, and the build puts it\n"
            "  inside the binary. An empty one means no card verifies.\n\n"
            "  Fill it in and run this build again. If this is a fresh\n"
            "  checkout, running ./start.sh (or START.bat) once generates\n"
            "  one for you.\n".format(env_path))

    # The door's password never travels inside the binary.
    #
    # Everything else in `.env` is baked, deliberately -- that is the whole
    # point of this function. These two are the exception because they are
    # the EZVIZ account that opens the academy's front door, the app does
    # not need them (the cached session beside the binary is the warm path,
    # and an interactive first sign-in with its SMS code cannot happen in a
    # server anyway), and anyone who can get hold of the binary can read
    # what is compiled into it. A serial is a device id and stays; a
    # password that opens a door does not.
    #
    # Dropped here rather than asked of whoever runs the build, because a
    # step like "remember to comment those two lines out first" gets skipped
    # exactly when it matters.
    DOOR_SECRETS = ("EZVIZ_EMAIL", "EZVIZ_PASSWORD")
    dropped = [k for k in DOOR_SECRETS if values.pop(k, None)]
    if dropped:
        print("  Not baking {} -- they open the front door, and the app "
              "does not need them.".format(", ".join(dropped)))

    # Nor a setting only the test suite reads.
    #
    # `.env.example` documents MB_TEST_MONGO_URI because tests/conftest.py
    # reads `.env` the way the app does -- but nothing in the running app
    # ever looks at it, and it carries an Atlas password. Baking it put a
    # live database credential inside a binary that gets handed around, in
    # exchange for nothing at all: a value no running code reads cannot even
    # be the reason a build behaves differently.
    TEST_ONLY = ("MB_TEST_MONGO_URI",)
    test_keys = [k for k in TEST_ONLY if values.pop(k, None)]
    if test_keys:
        print("  Not baking {} -- the test suite reads it, the app never "
              "does.".format(", ".join(test_keys)))

    baked_dir = os.path.join(work_dir, "baked")
    os.makedirs(baked_dir, exist_ok=True)
    with open(os.path.join(baked_dir, "_baked_env.py"), "w", encoding="utf-8") as f:
        f.write(
            "# Generated by academy.spec at build time from the project's .env.\n"
            "# Not committed, and rewritten on every build. config._baked()\n"
            "# is the only reader.\n"
            "VALUES = {!r}\n".format(values))
    return baked_dir, sorted(values)


# `build/` is git-ignored, same as `dist/`, so the generated module holding
# the secret never lands anywhere git can pick it up.
BAKED_DIR, BAKED_KEYS = _bake_env(SPECPATH, os.path.join(SPECPATH, "build"))
print("academy.spec: baking {} setting(s) from .env: {}".format(
    len(BAKED_KEYS), ", ".join(BAKED_KEYS)))


hiddenimports = [
    # The .env baked in above. Imported by name only, from config._baked().
    "_baked_env",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "multipart",
    "multipart.multipart",
    "anyio._backends._asyncio",
    "sqlite3",
    # local modules, imported normally but listed so a rename cannot break
    # the build silently
    "server", "access", "cards", "config", "db", "door", "images", "tokens",
    "repo", "repo.base", "repo.ports", "repo.errors", "repo.filters",
    "repo.sqlite", "repo.sqlite.filters", "repo.sqlite.ports",
    # The Mongo backend is imported lazily by repo/__init__.py, so a build
    # without pymongo still produces a working SQLite-only exe. Listed here
    # so that a build *with* it bundles the whole package rather than the
    # one module PyInstaller can see from a lazy import.
    "repo.mongo", "repo.mongo.client", "repo.mongo.filters",
    "repo.mongo.ids", "repo.mongo.ports", "repo.mongo.schema",
    # pymongo and bson load pieces by string name and ship C extensions, so
    # static analysis misses them -- the same reason uvicorn's are listed
    # above. dnspython is only needed for mongodb+srv:// URIs.
    "pymongo", "pymongo._cmessage", "bson", "bson._cbson",
    "dns", "dns.resolver",
    # The door. door.py imports it inside a function so a laptop without it
    # still runs, which also means PyInstaller sees a conditional import --
    # naming it here is what bundles the whole package. A build on a machine
    # where it is not installed warns and produces an exe with no door,
    # which is the same state a 3.11 install is in. See requirements.txt.
    "pyezvizapi", "pyezvizapi.client", "pyezvizapi.exceptions",
    "requests",
    # A Windows build of this prints `Hidden import "tzdata" not found!` and
    # that warning is noise -- checked rather than assumed, because it names
    # the door's library and Windows has no system timezone database. zoneinfo
    # itself is stdlib and imports with no tz data at all; the one function
    # that constructs a ZoneInfo (utils.parse_timezone_value) catches
    # ZoneInfoNotFoundError and falls back to the machine's own offset, and it
    # is reached only from camera.py, which nothing in door.py touches. Do not
    # add tzdata to requirements.txt to silence it.
    # The CA bundle Atlas is verified against. PyInstaller's own hook
    # collects certifi's cacert.pem as a data file; naming the module here is
    # what makes sure the hook runs at all.
    "certifi",
    "api", "api.clients", "api.plans", "api.classes",
    "api.instructors", "api.sessions", "api.access_routes", "api.dashboard",
    "api.images", "api.appointments",
]

a = Analysis(
    ["run_app.py"],
    pathex=[BAKED_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MB Ballet Academy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,          # reception needs to see it running, and to stop it
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
