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
import sys

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import access
import db

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
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = sys._MEIPASS
else:
    APP_DIR = BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))

os.chdir(APP_DIR)
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
    if not os.environ.get("ENTRY_SECRET") and os.path.exists(".env"):
        for line in open(".env"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k, v)

    if not os.environ.get("ENTRY_SECRET"):
        import secrets
        key = secrets.token_urlsafe(32)
        with open(".env", "w") as f:
            f.write(f"ENTRY_SECRET={key}\n")
        os.environ["ENTRY_SECRET"] = key
        print("  A new security key was created and saved to .env.")
        print("  Keep a backup of that file — losing it invalidates every card.\n")

    db.init()
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
            conn = db.connect()
            try:
                n = access.settle_past_sessions(conn)
                if n:
                    print(f"[settle] {n} booking(s) marked absent")
            finally:
                conn.close()
        except Exception as e:
            print(f"[settle] skipped: {e}")
        await asyncio.sleep(3600)


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
