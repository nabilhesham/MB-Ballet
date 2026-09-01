@echo off
REM ===========================================================================
REM  Build a standalone MB Ballet Academy.exe
REM
REM  Run this ONCE, on a Windows machine that has Python, when you want to hand
REM  the reception laptop something with nothing to install at all. It produces
REM  dist\MB Ballet Academy.exe — copy that single file to the reception
REM  laptop and double-click it. No Python, no packages, no internet needed.
REM
REM  The database, photos, cards and .env are created next to the .exe, so keep
REM  it in its own folder rather than loose on the desktop.
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

if not exist "academy.spec" (
    echo   academy.spec is missing. Run this from the program folder.
    pause
    exit /b 1
)

echo   [1/3] Installing the build tool...
"%PY%" -m pip install --upgrade pip pyinstaller --quiet
"%PY%" -m pip install -r requirements.txt --quiet

echo   [2/3] Packaging...
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

echo   [3/3] Done.
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
echo     creates academy.db, .env, photos and cards beside
echo     itself. Back up that whole folder, not just the file.
echo.
echo     Test it here first. If the window opens and closes
echo     straight away, an error.log file will be sitting next
echo     to the .exe explaining why.
echo   ------------------------------------------------------------
echo.
pause
