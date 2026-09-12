"""
MB Ballet Academy — local server.

    ENTRY_SECRET=... python server.py
    open http://127.0.0.1:8000

Route handlers live in the api/ package, one module per resource
(api/clients.py, api/plans.py, ...). This file only wires them together:
paths, the FastAPI app, the startup event, and the static mounts.
"""

import asyncio
import os

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Before `import db`, which used to bind its path at import time, and
# before anything reads an environment variable. load_env() also does the
# chdir into the folder holding academy.db, .env, photos and cards.
import config
config.load_env()

import access  # noqa: E402
import repo as data  # noqa: E402

from api.clients import router as clients_router
from api.plans import router as plans_router
from api.instructors import router as instructors_router
from api.classes import router as classes_router
from api.sessions import router as sessions_router
from api.access_routes import router as access_router
from api.dashboard import router as dashboard_router

# --------------------------------------------------------------------------
# Paths.
#
# When packaged as a single .exe, PyInstaller unpacks the bundled files into a
# temporary folder that is wiped on exit — so static assets are read from there,
# but the database, photos, cards and .env must live next to the .exe or the
# academy loses its records every time the program closes.
# --------------------------------------------------------------------------
APP_DIR = config.app_dir()
BUNDLE_DIR = config.bundle_dir()
STATIC_DIR = os.path.join(BUNDLE_DIR, "static")

# These must exist before the StaticFiles mounts below, which run at import
# time and raise if their directory is missing.
for _d in ("photos", "cards"):
    os.makedirs(os.path.join(APP_DIR, _d), exist_ok=True)

app = FastAPI(title="MB Ballet Academy")

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


@app.on_event("startup")
def _startup():
    # .env is already loaded, at import. Only provisioning is left.
    if not os.environ.get("ENTRY_SECRET"):
        import secrets
        config.set_env_value("ENTRY_SECRET", secrets.token_urlsafe(32))
        print("  A new security key was created and saved to .env.")
        print("  Keep a backup of that file — losing it invalidates every card.\n")

    print(f"  Database: {config.describe()}")
    # Through the repository, not db.init(). Calling the SQLite one directly
    # created an empty academy.db beside the binary even when the backend was
    # MongoDB — harmless, but it looks exactly like the app quietly ignoring
    # the configuration.
    starter = data.connect()
    try:
        starter.init_schema()
    finally:
        starter.close()
    os.makedirs("photos", exist_ok=True)
    os.makedirs("cards", exist_ok=True)
    asyncio.create_task(_settle_loop())


async def _settle_loop():
    """
    Mark past no-shows absent, hourly. The same call runs on every read that
    depends on attendance, so this is only a safety net for a screen left open
    overnight.
    """
    await asyncio.sleep(15)
    while True:
        try:
            repo = data.connect()
            try:
                n = access.settle_past_sessions(repo)
                if n:
                    print(f"[settle] {n} booking(s) marked absent")
            finally:
                repo.close()
        except Exception as e:
            print(f"[settle] skipped: {e}")
        await asyncio.sleep(3600)


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
app.mount("/photos", StaticFiles(directory="photos"), name="photos")
app.mount("/cards", StaticFiles(directory="cards"), name="cards")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "app", "index.html"))


@app.get("/reception")
def reception():
    return FileResponse(os.path.join(STATIC_DIR, "reception.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
