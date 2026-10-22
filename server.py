"""
MB Ballet Academy — local server.

    ENTRY_SECRET=... python server.py
    open http://127.0.0.1:8000

Route handlers live in the api/ package, one module per resource
(api/clients.py, api/plans.py, ...). This file only wires them together:
paths, the FastAPI app, the startup event, and the static mounts.
"""

import asyncio
import contextlib
import datetime
import os
import sys

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Before `import db`, which used to bind its path at import time, and
# before anything reads an environment variable. load_env() also does the
# chdir into the folder holding academy.db and .env.
import config
config.load_env()

import access  # noqa: E402
import door  # noqa: E402
import images  # noqa: E402
import repo as data  # noqa: E402

from api.clients import router as clients_router
from api.plans import router as plans_router
from api.instructors import router as instructors_router
from api.classes import router as classes_router
from api.sessions import router as sessions_router
from api.access_routes import router as access_router
from api.dashboard import router as dashboard_router
from api.images import router as images_router
from api.appointments import router as appointments_router
from api.products import router as products_router

# --------------------------------------------------------------------------
# Paths.
#
# When packaged as a single .exe, PyInstaller unpacks the bundled files into a
# temporary folder that is wiped on exit — so static assets are read from there,
# but the database must live next to the .exe or the academy loses its records
# every time the program closes. `.env` is not one of them: there is one, in
# the source folder, and its values are baked into the build.
#
# Photos and cards used to need the same care, as folders beside the exe. They
# are rows in the database now (see images.py), so there is one thing left to
# keep next to the binary instead of three.
# --------------------------------------------------------------------------
APP_DIR = config.app_dir()
BUNDLE_DIR = config.bundle_dir()
STATIC_DIR = os.path.join(BUNDLE_DIR, "static")


@contextlib.asynccontextmanager
async def _lifespan(app):
    """
    Everything that has to happen once, before the first request.

    A lifespan handler rather than @app.on_event("startup"), which FastAPI
    deprecated and which printed a warning across the launcher's own banner
    on every start -- three lines of framework internals in the middle of
    the four ticks that tell reception the app is healthy.

    The shape is the same: the work above the `yield` is the old startup
    body. What the decorator could not express is the half below it, so the
    hourly sweep is now cancelled on the way out instead of being left to
    die with the process; asyncio warns about a task still pending at loop
    close, and that warning would have landed in the same place.
    """
    # Settings are already loaded, at import. Only provisioning is left, and
    # only in a source checkout: a packaged build was handed its secret at
    # build time and must never invent a second one. Minting one here is how
    # a build ended up signing cards with a key nothing else knew, which
    # every card already printed then failed against.
    if not os.environ.get("ENTRY_SECRET"):
        if getattr(sys, "frozen", False):
            raise RuntimeError(
                "This build carries no signing key, so no member card can be "
                "verified. It was packaged from a checkout whose .env had no "
                "ENTRY_SECRET. Build again from one that does.")
        import secrets
        config.set_env_value("ENTRY_SECRET", secrets.token_urlsafe(32))
        print("  A new security key was created and saved to .env.")
        print("  Keep a backup of that file — losing it invalidates every card.\n")

    print(f"  Database: {config.describe()}")
    # The clock, because it is load-bearing and silent. db.now() decides when
    # a class is over, when a no-show becomes absent and what time a check-in
    # is stamped with -- so a machine an hour fast is an app that is wrong in
    # four ways, each of which reads as a bug in the code. Printing it costs
    # nothing and is the one place somebody can compare it against the clock
    # in the corner of their own screen. /api/clock is the same question
    # asked from the browser, which answers it without anybody looking.
    local = datetime.datetime.now().astimezone()
    print(f"  Clock:    {local:%Y-%m-%d %H:%M} {local:%z} "
          f"(check this matches this computer's own clock)")
    # Through the repository, not db.init(). Calling the SQLite one directly
    # created an empty academy.db beside the binary even when the backend was
    # MongoDB — harmless, but it looks exactly like the app quietly ignoring
    # the configuration.
    starter = _connect_or_explain()
    try:
        starter.init_schema()
        # Any install older than the move into the database still has its
        # faces and cards on the disk. Carry them over on the first start
        # after the upgrade; afterwards this finds nothing and costs a
        # directory listing. See images.import_legacy_files().
        moved = images.import_legacy_files(starter, config.legacy_media_dir())
        if moved:
            print(f"  Moved {moved} photo(s) and card(s) into the database.")

        # And every product written down before the shop had sizes gets the
        # one "no size" shelf row that means it has none. Same shape as the
        # line above -- idempotent, so it finds nothing on every later start
        # -- and here rather than in db.migrate() because migrate() only
        # ever runs against SQLite and this database may be on Atlas. See
        # access.ensure_stock_rows().
        shelved = access.ensure_stock_rows(starter)
        if shelved:
            print(f"  Gave {shelved} product(s) a shelf to be counted on.")

        # Every session that ended while this was not running. The
        # end-of-session event is in-process, so an app that was off over a
        # class has an end nobody acted on -- and nothing settles on a read
        # any more, so it has to be caught up here rather than by whoever
        # opens the first screen. Before `yield`, so the first request is
        # already looking at a settled database.
        caught = access.settle_due_sessions(starter)
        if caught:
            print(f"  Caught up {caught} booking(s) from sessions that have ended.")
    finally:
        starter.close()

    # The door, when a lock is configured at all.
    #
    # `door.configured()` is the serial and nothing else, and a build can
    # carry one while holding no session to unlock with -- that split is
    # deliberate (a serial identifies a device, a session opens a door), and
    # it means the session has to be copied in beside the database by hand.
    # Said here rather than in the launchers because the machine that
    # matters double-clicks a binary and runs neither of them, so the first
    # anyone heard of it was an amber line under a client's verdict with the
    # client standing at the counter. Never blocks the start: a door-less
    # install is a legitimate one, and check-ins are the app's real job.
    if door.configured():
        problem = door.session_problem()
        if problem:
            print(f"  No door yet: {problem}.")
            print("  Copy .ezviz_token.json in beside the database, or beside "
                  "what you launched.")
            print("  Check-ins still work; reception opens the door by hand.")
        else:
            # Which file, not just that there is one. "The session is right
            # there and it says there is none" is the report this answers,
            # and the launch folder is now one of the places it can be.
            using = door.session_file()
            if using:
                print(f"  Door: using the EZVIZ session in {using}")

    # Held in a local: asyncio keeps only a weak reference to a running
    # task, so a bare create_task() may be collected mid-sweep.
    global _rescan
    _rescan = asyncio.Event()
    settle = asyncio.create_task(_session_end_watcher(_rescan))
    try:
        yield
    finally:
        settle.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await settle
        _rescan = None


app = FastAPI(title="MB Ballet Academy", lifespan=_lifespan)

# Registered in the same order the route groups appeared in the old
# single-file server.py: clients, plans, instructors, classes, sessions,
# access, dashboard. Kept in this order deliberately, not just for a tidy
# diff — FastAPI matches routes in registration order, and within
# api/sessions.py POST /api/sessions/repeat depends on being defined before
# any /api/sessions/{sid}-shaped route, exactly as it was before the split.
app.include_router(clients_router)
app.include_router(plans_router)
app.include_router(instructors_router)
app.include_router(classes_router)
app.include_router(sessions_router)
app.include_router(access_router)
app.include_router(dashboard_router)
app.include_router(images_router)
app.include_router(appointments_router)
app.include_router(products_router)


def _connect_or_explain():
    """
    The first connection, with a readable sentence instead of a topology dump.

    A MongoDB failure here arrives as several hundred characters of
    ServerDescription objects, one per replica-set member, each repeating the
    same underlying error. That is what reception saw on the Mac:

        [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed:
        unable to get local issuer certificate (_ssl.c:1006)

    buried three lines into a wall of text, under a heading that says nothing
    about what to do. This project's rule is that a failure says what to do in
    plain language and never shows a stack trace, and startup is exactly where
    that matters: the window closes, and error.log is all anyone gets.

    The distinctions below are the three real causes, and they need different
    actions from different people -- which is why one message for all three
    would be no better than the dump.

    **`ping()` is what makes any of this run at all.** `data.connect()` builds
    a MongoClient and touches no network, so for a year this handler guarded a
    call that cannot fail the way it describes: the first round trip was
    `init_schema()` on the next line, outside the `try`, and a developer with
    a slow link got precisely the eighty-line ServerDescription wall quoted
    above. The first round trip has to be one this function owns.
    """
    try:
        starter = data.connect()
        starter.ping()
        return starter
    except Exception as exc:
        if config.backend() != config.MONGO:
            raise
        text = str(exc)
        print()
        print("  The database could not be reached, so the system cannot start.")
        print(f"  Trying: {config.describe()}")
        print()
        if "CERTIFICATE_VERIFY" in text or "SSLCertVerificationError" in text:
            print("  The security certificate could not be checked. This build is")
            print("  missing its certificate bundle -- it is a fault in the build,")
            print("  not in this computer or the network. Build again from a")
            print("  checkout whose requirements.txt includes certifi.")
        elif "does not exist" in text or "NXDOMAIN" in text or "SRV" in text:
            print("  The database's address could not be looked up. Some networks")
            print("  block the kind of lookup a mongodb+srv:// address needs.")
            print("  Use the direct mongodb://host1,host2,host3/ form instead --")
            print("  see .env.example.")
        else:
            print("  No answer from the database. Either this computer has no")
            print("  internet, or its current IP address is not on the Atlas")
            print("  access list -- that list is per-network, so a machine that")
            print("  works in one place stops working in another.")
            print("  Check the internet first, then Atlas -> Network Access.")
        print()
        # Written here rather than left to run_app.py, which never sees this:
        # uvicorn catches a lifespan failure, logs "Application startup
        # failed" and returns normally, so nothing above it raises and the
        # promise this line makes was false in the one case it is read in.
        path = _log_startup_failure(exc)
        print(f"  The full technical detail is in {path}.")
        print()
        # `from None` on purpose. The chained pymongo exception is the dump
        # this whole function exists to replace, and uvicorn prints whatever
        # escapes here *after* the block above -- so chaining it would scroll
        # the explanation off a terminal and leave the wall as the last word.
        raise RuntimeError(
            "The database could not be reached, so the system cannot start. "
            "See the lines above, and error.log.") from None


def _log_startup_failure(exc: BaseException) -> str:
    """The traceback, appended to error.log beside the app. Best effort."""
    import datetime
    import traceback
    path = os.path.join(APP_DIR, "error.log")
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"\n{'=' * 70}\n"
                     f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  "
                     f"startup: database unreachable\n{'=' * 70}\n")
            fh.write("".join(traceback.format_exception(
                type(exc), exc, exc.__traceback__)))
    except OSError:
        # A read-only folder must not turn a failure that has already been
        # explained into a second, more confusing one.
        return "error.log (which could not be written)"
    return path


# A write may have moved the moment the next session ends -- a session
# created in the past, an edited start or length, a new freeze. The watcher
# below is asleep until the deadline it last computed, so it has to be told.
# An asyncio.Event rather than a flag in access.py, because waking a sleeping
# task is this file's business and access.py holds no event loop.
#
# **Created in the lifespan, not here at import.** An Event binds to the
# first running loop that touches it and then refuses every other one
# ("bound to a different event loop"). One module-level Event is therefore
# fine for the single uvicorn process and breaks the moment the app is
# served twice in one interpreter -- which is exactly what the test suite
# does, a TestClient per test, each with its own loop. It is a global so the
# middleware can reach it, and None until there is a loop to bind to.
_rescan = None


async def _wait(rescan: asyncio.Event, seconds: float):
    """Sleep, but wake early if a write says the timetable moved."""
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(rescan.wait(), timeout=seconds)
        # A burst of writes -- repeating a term is one request but editing a
        # week is several -- coalesces into one rescan rather than one each.
        await asyncio.sleep(1)
    rescan.clear()


async def _session_end_watcher(rescan: asyncio.Event):
    """
    Fire the end-of-session event the moment each session's duration runs out.

    **This is the only thing in the app that creates an absence**, and the
    moment it runs is the whole of the decision: `access.session_ended()`
    re-derives nothing about whether the class has finished. Reads do not
    settle anything on the way past any more, so on the machine that matters
    -- a laptop sitting on the kiosk, where nothing reads attendance for
    hours -- this loop is not a backstop, it is the mechanism.

    Each pass works through whatever ends have already arrived
    (`settle_due_sessions`, normally nothing, and the backlog after the
    laptop has been off), then sleeps until the next one. A failed pass waits
    a minute and tries again rather than inheriting a deadline the exception
    may have left half-computed -- long enough that a database that is down
    (an Atlas backend with no internet) is retried rather than hammered,
    short enough that a blip costs one session's worth of lateness at most.
    """
    while True:
        wait = 60
        try:
            repo = data.connect()
            try:
                n = access.settle_due_sessions(repo)
                if n:
                    print(f"[sessions] {n} booking(s) marked absent")
                # After the writes, never before: a session settled a moment
                # ago must not still be the answer to "when is the next end".
                wait = access.seconds_to_next_end(repo)
            finally:
                repo.close()
        except Exception as e:
            print(f"[sessions] skipped: {e}")
        await _wait(rescan, wait)


# ================================================================ caching
#
# A replaced build kept showing the old app until someone pressed
# ctrl-shift-R on every page in turn. Nothing was being cached on purpose:
# a response with no Cache-Control at all lets the browser invent its own
# freshness, and the usual heuristic — a tenth of the file's age — means an
# index.html that has sat on disk for a month is treated as fresh for days.
# The browser then goes on asking for the hashed bundle that copy names,
# which it also still holds. Replacing the folder changes nothing it can see.
#
# So every response now says what it is. There are only two kinds:
#
#   /static/app/assets/*   named by the build with a content hash, so a new
#                          build is a new URL and this one can never go
#                          stale. Cached for a year and never revalidated —
#                          that is the whole point of hashing the names.
#
#   everything else        keeps its name across builds — the entry HTML,
#                          style.css, reception.html, the API, a reissued
#                          card — so a cached copy is a stale copy. no-store.
#
# The cost is one revalidation-free refetch of small files over localhost,
# which is not measurable. The alternative is a receptionist being shown
# last month's app with no way to know it.
HASHED_ASSETS = "/static/app/assets/"


@app.middleware("http")
async def cache_policy(request, call_next):
    # A write may have moved the moment a session next ends -- one created in
    # the past, an edited start or length, a new freeze -- and the watcher is
    # asleep until the deadline it last worked out. This is the one place
    # where "something may have changed" is knowable for the whole app, so a
    # write endpoint added later is covered without anyone remembering to.
    #
    # A rejected write wakes it too. That costs one indexed read that finds
    # nothing, which is the right way round for a guess to be wrong.
    # None before the lifespan has run, which is only reachable in a test
    # driving the middleware without starting the app.
    if request.method != "GET" and _rescan is not None:
        _rescan.set()
    r = await call_next(request)
    if request.url.path.startswith(HASHED_ASSETS):
        r.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        # Belt and braces for anything between the browser and here that
        # predates Cache-Control. Costs two short headers on a local request.
        r.headers["Pragma"] = "no-cache"
        r.headers["Expires"] = "0"
    return r


# ================================================================ static
#
# No /photos or /cards mount: both are served out of the database by
# api/images.py now, so there is no folder to expose and nothing on the disk
# a backup of academy.db can miss.


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "app", "index.html"))


@app.get("/reception")
def reception():
    return FileResponse(os.path.join(STATIC_DIR, "reception.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
