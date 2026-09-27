@echo off
REM ===========================================================================
REM  Build a standalone MB Ballet Academy.exe
REM
REM  Run this ONCE, on a Windows machine that has Python, when you want to hand
REM  the reception laptop something with nothing to install at all. It produces
REM  dist\MB Ballet Academy.exe — copy that single file to the reception
REM  laptop and double-click it. No Python, no packages, no internet needed.
REM
REM  The database is created next to the .exe, so keep it
REM  in its own folder rather than loose on the desktop. Settings are not:
REM  this project's .env is baked into the .exe as it is built, and the
REM  build refuses to run without one.
REM
REM  PyInstaller cannot cross-compile: a Windows .exe must be built on Windows.
REM ===========================================================================

setlocal
cd /d "%~dp0"
title Build MB Ballet Academy

echo.
echo   Building a standalone program file.
echo   This takes a few minutes.
echo.

set "PY="
for %%C in (py.exe python.exe) do (
    if not defined PY (
        for /f "delims=" %%P in ('where %%C 2^>nul') do (
            if not defined PY (
                echo %%P | find /i "WindowsApps" >nul
                if errorlevel 1 set "PY=%%P"
            )
        )
    )
)
if not defined PY (
    echo   Python is needed to BUILD the exe, even though the finished exe
    echo   will not need it. Install Python from python.org and run this again.
    pause
    exit /b 1
)

REM Building needs 3.12 or newer, and that is a higher bar than *running*
REM the app, deliberately.
REM
REM The app itself needs 3.10 -- it uses "int | None" annotations, which 3.9
REM evaluates at runtime and rejects -- and start.sh/START.bat still accept
REM that, because an academy on 3.11 should go on working. But this script
REM produces the .exe reception runs, and the door's library (pyezvizapi)
REM needs 3.12: below it, requirements.txt skips that line without a word
REM and the exe comes out with no door at all. A missing hidden import is
REM only a PyInstaller warning, and afterwards a door-less exe looks exactly
REM like one built on a machine with no lock configured.
REM
REM So a build refuses rather than shipping that. The developer machine this
REM is written for runs 3.12.4.
REM
REM Version is compared as plain text for the reason at the top of
REM START.bat: a parenthesised comparison inside an if-block is parsed
REM before it runs and breaks the block.
call :check_version
if errorlevel 1 (
    echo   Python 3.12 or newer is needed to BUILD this.
    echo   This machine has %PYVER%.
    echo.
    echo   The app itself runs on 3.10 and up, so START.bat is happy with
    echo   less -- but the smart lock needs 3.12, and an .exe built with
    echo   less would work perfectly and never open the door.
    echo.
    echo   Install Python 3.12 from python.org and run this again.
    pause
    exit /b 1
)

if not exist "academy.spec" (
    echo   academy.spec is missing. Run this from the program folder.
    pause
    exit /b 1
)

echo   [1/4] Installing the build tool...
"%PY%" -m pip install --upgrade pip pyinstaller --quiet
"%PY%" -m pip install -r requirements.txt --quiet

REM  Did the door make it in? The version check above means it should
REM  have, so this is the belt to that braces -- it catches the download
REM  failing, a stale cached wheel, or a hand-edited requirements.txt.
REM  Reported rather than fatal: a door-less build is a legitimate thing to
REM  want, and .env decides whether there is a lock at all.
set "DOOR=no"
"%PY%" -c "import pyezvizapi" >nul 2>&1 && set "DOOR=yes"

echo   [2/4] Refreshing the web interface...
REM  static\app\ (the built React interface) is already committed to the
REM  repository, so this step is a freshness check, not a requirement — a
REM  Windows box with Python but no Node.js still produces a working exe,
REM  just with whatever interface build was last committed. Only a
REM  developer who edited frontend\src needs this to actually do anything.
call :find_npm
if not defined NPM (
    echo         Node.js is not installed on this machine.
    echo         Using the interface build already in static\app.
    goto :after_frontend
)
pushd frontend
call "%NPM%" ci
if errorlevel 1 goto :npm_failed
call "%NPM%" run build
if errorlevel 1 goto :npm_failed
popd
goto :after_frontend

:npm_failed
popd
echo         Refreshing the web interface failed. Using the build already
echo         committed in static\app instead.

:after_frontend
echo   [3/4] Packaging...
REM  The hidden imports live in academy.spec rather than on this line: uvicorn
REM  loads several modules by string name at runtime, PyInstaller cannot see
REM  them, and any that are missing produce an .exe that opens a console and
REM  closes instantly. Keeping them in a file makes them reviewable.
"%PY%" -m PyInstaller academy.spec --clean --noconfirm

if errorlevel 1 (
    echo.
    echo   The build failed. Show this window to whoever maintains the system.
    pause
    exit /b 1
)

echo   [4/4] Done.
echo.
echo   ------------------------------------------------------------
echo     Your program is here:
echo.
echo       dist\MB Ballet Academy.exe
echo.
echo     Copy that file into an EMPTY FOLDER on the reception
echo     laptop and double-click it. Nothing else is needed.
echo.
echo     Put it in its own folder, not loose on the desktop: it
echo     creates academy.db beside itself.
echo     Back up that whole folder, not just the file.
echo.
echo     It needs no .env: the settings and the card-signing
echo     key were built into it from this project's own .env,
echo     so cards already printed still scan.
echo.
call :door_note
echo     Test it here first. If the window opens and closes
echo     straight away, an error.log file will be sitting next
echo     to the .exe explaining why.
echo   ------------------------------------------------------------
echo.
pause
exit /b 0

REM  Its own subroutine rather than a for/if inlined where it's called —
REM  the exact parenthesis trap this project has already been bitten by
REM  once (see CLAUDE.md): a for /f loop nested inside an if (...) block
REM  in the same parenthesised group breaks in ways that are silent until
REM  tested on a real machine.
:check_version
set "PYVER="
for /f "tokens=2" %%V in ('"%PY%" -V 2^>^&1') do set "PYVER=%%V"
for /f "tokens=1,2 delims=." %%A in ("%PYVER%") do (
    set "PYMAJOR=%%A"
    set "PYMINOR=%%B"
)
if not defined PYMINOR exit /b 1
if %PYMAJOR% LSS 3 exit /b 1
if %PYMAJOR% EQU 3 if %PYMINOR% LSS 12 exit /b 1
exit /b 0


:find_npm
set "NPM="
for %%C in (npm.cmd npm.exe) do (
    if not defined NPM (
        for /f "delims=" %%P in ('where %%C 2^>nul') do (
            if not defined NPM set "NPM=%%P"
        )
    )
)
exit /b 0


REM  A subroutine for the same reason the others are: the if/else block
REM  below is parsed as one group, so nothing inside it may contain a
REM  parenthesis, a pipe or a redirect. Check the echo lines before editing.
:door_note
if "%DOOR%"=="yes" (
    echo     The door is in. Copy .ezviz_token.json from this
    echo     folder in beside the .exe as well -- the saved EZVIZ
    echo     session is deliberately NOT built into the binary,
    echo     because it opens the front door.
) else (
    echo     NO DOOR in this build: pyezvizapi did not install,
    echo     although Python %PYVER% is new enough for it. The exe
    echo     works and check-ins work; the kiosk just shows no
    echo     Open door button. Re-run this with the laptop online.
)
echo.
exit /b 0
