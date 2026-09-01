# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build spec for MB Ballet Academy.

Kept as a file rather than a long command line so the hidden imports are
reviewable. uvicorn and starlette load several modules by string name at
runtime, which PyInstaller's static analysis cannot see — leaving any of these
out produces an .exe that opens a console and closes instantly.
"""

datas = [("static", "static")]

hiddenimports = [
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
    "server", "access", "cards", "db", "tokens",
]

a = Analysis(
    ["run_app.py"],
    pathex=[],
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
