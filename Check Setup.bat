@echo off
REM  Reports what START.bat can and cannot see on this machine.
REM  Run this if the launcher says Python is missing when you know it is not.

setlocal EnableDelayedExpansion
title Check Setup - MB Ballet Academy
cd /d "%~dp0"

set "PY="
set "VPY="
set "FOUNDANY="
set "VENVDIR=.venv-windows"

echo.
echo    ==========================================================
echo      SETUP CHECK
echo    ==========================================================
echo.

echo    Windows version
ver
echo.

echo    Python on PATH
echo    --------------
where py.exe 2>nul
where python.exe 2>nul
where python3.exe 2>nul
if errorlevel 1 echo    (nothing on PATH)
echo.

echo    Testing each candidate
echo    ----------------------
for /f "delims=" %%P in ('where py.exe 2^>nul') do call :report "%%~P"
for /f "delims=" %%P in ('where python.exe 2^>nul') do call :report "%%~P"
for /f "delims=" %%P in ('where python3.exe 2^>nul') do call :report "%%~P"
call :scan_dir "%LOCALAPPDATA%\Programs\Python"
call :scan_dir "%ProgramFiles%"
call :scan_dir "C:\"
if exist "%~dp0runtime\python.exe" call :report "%~dp0runtime\python.exe"
echo.

echo    Private environment
echo    -------------------
if exist "%VENVDIR%\Scripts\python.exe" (
    call :venv_report
) else (
    echo    %VENVDIR%   not created yet
    echo                    START.bat will make one on first run, so nothing
    echo                    is installed into the machine's own Python.
)
if exist ".venv\" echo    .venv           old shared folder, no longer used - safe to delete
echo.

echo    Result
echo    ------
if defined PY (
    echo    OK - base Python !PYINFO!
    echo         !PY!
    echo.
    echo    Checking the packages...
    call :pkg_report
) else (
    if defined FOUNDANY (
        echo    A Python was found but it is too old: !FOUNDANY!
        echo    This program needs 3.10 or newer.
    ) else (
        echo    No usable Python found.
        echo    START.bat will download a private copy into this folder.
    )
)

echo.
echo    Files in this folder
echo    --------------------
if exist "server.py"        (echo    server.py         yes) else (echo    server.py         MISSING)
if exist "static\index.html" (echo    static folder     yes) else (echo    static folder     MISSING)
if exist "requirements.txt" (echo    requirements.txt  yes) else (echo    requirements.txt  MISSING)
if exist ".env"             (echo    .env              yes) else (echo    .env              not yet, will be created)
if exist "academy.db"       (echo    academy.db        yes) else (echo    academy.db        not yet, will be created)
if exist "%VENVDIR%"        (echo    %VENVDIR%    yes) else (echo    %VENVDIR%    not yet, will be created)
echo.
pause
exit /b 0


:venv_report
set "VVER="
for /f "usebackq tokens=2" %%V in (`"%VENVDIR%\Scripts\python.exe" -V 2^>^&1`) do set "VVER=%%V"
if defined VVER (
    echo    %VENVDIR%   present, Python !VVER!
    set "VPY=%~dp0%VENVDIR%\Scripts\python.exe"
) else (
    echo    %VENVDIR%   present but BROKEN - START.bat will rebuild it
)
exit /b


:pkg_report
set "TESTPY=!PY!"
if defined VPY set "TESTPY=!VPY!"
"!TESTPY!" -c "import fastapi, uvicorn, qrcode, PIL, multipart" >nul 2>&1
if errorlevel 1 (
    echo    Packages are NOT installed yet. START.bat installs them into
    echo    .venv on first run, which needs an internet connection.
) else (
    echo    All packages installed in !TESTPY!
)
exit /b


:scan_dir
if not exist "%~1" exit /b
for /d %%D in ("%~1\Python3*") do call :report "%%~D\python.exe"
exit /b


:report
if not exist "%~1" exit /b

echo "%~1" | find /i "WindowsApps" >nul
if not errorlevel 1 (
    echo    SKIP  %~1
    echo          ^(Microsoft Store stub - opens the Store, cannot run^)
    exit /b
)

set "VER="
for /f "usebackq tokens=2" %%V in (`"%~1" -V 2^>^&1`) do set "VER=%%V"
if not defined VER (
    echo    FAIL  %~1
    echo          ^(did not answer when asked its version^)
    exit /b
)

set "FOUNDANY=!VER!"
for /f "tokens=1,2 delims=." %%a in ("!VER!") do set "MAJ=%%a" & set "MIN=%%b"

set "OK=1"
if !MAJ! LSS 3 set "OK="
if !MAJ! EQU 3 if !MIN! LSS 10 set "OK="

if defined OK (
    echo    GOOD  %~1
    echo          version !VER!
    if not defined PY set "PY=%~1"
    if not defined PYINFO set "PYINFO=!VER!"
) else (
    echo    OLD   %~1
    echo          version !VER! - needs 3.10 or newer
)
exit /b
