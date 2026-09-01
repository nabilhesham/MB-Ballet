@echo off
REM ===========================================================================
REM  MB Ballet Academy - one-click start
REM
REM  Written for a reception laptop with nothing installed and a user who
REM  cannot be asked to install anything.
REM
REM  BATCH NOTE FOR WHOEVER MAINTAINS THIS:
REM  cmd.exe parses a whole parenthesised block before running any of it, so a
REM  ")" or ">" inside a command in such a block terminates it early. An
REM  earlier version inlined
REM      python -c "sys.exit(0 if sys.version_info>=(3,10) else 1)"
REM  inside an if-block and silently failed to detect any Python at all.
REM  Every command containing parentheses, pipes or redirects therefore lives
REM  in its own subroutine below. Keep it that way.
REM ===========================================================================

setlocal EnableDelayedExpansion
title MB Ballet Academy
cd /d "%~dp0"

set "PYVER=3.12.7"
set "RUNTIME=%~dp0runtime"

REM  A virtual environment holds compiled, platform-specific binaries, so a
REM  single .venv shared between Windows and Linux destroys itself every time
REM  the other platform runs. Each gets its own folder; the database, .env,
REM  cards and photos are plain files and stay shared.
set "VENVDIR=.venv-windows"
set "PY="
set "FOUNDANY="

cls
echo.
echo    ==========================================================
echo      MB BALLET ACADEMY
echo    ==========================================================
echo.

REM ------------------------------------------------------------------ find it
if exist "%RUNTIME%\python.exe" (
    call :test_python "%RUNTIME%\python.exe"
)
if defined PY (
    set "EMBEDDED=1"
    echo    [1/7]  Using the Python bundled in this folder
    goto :have_python
)

call :find_python
if defined PY (
    echo    [1/7]  Found Python !PYINFO!
    goto :have_python
)

REM ------------------------------------------------------------------ install it
if defined FOUNDANY (
    echo    [1/7]  Python was found but it is too old.
    echo           Need 3.10 or newer, this machine has !FOUNDANY!
) else (
    echo    [1/7]  Python is not installed on this computer.
)
echo.

where winget >nul 2>&1
if not errorlevel 1 (
    echo           Installing it automatically. This takes a few minutes.
    echo.
    call :winget_install
    call :find_python
    if defined PY (
        echo           Installed successfully.
        goto :have_python
    )
    echo           That did not work. Trying another way...
    echo.
)

echo           Downloading a private copy of Python into this folder.
echo           Nothing will be installed on the computer itself.
echo.
call :install_embedded
if defined PY (
    set "EMBEDDED=1"
    goto :have_python
)
goto :no_internet

REM ------------------------------------------------------------------ run it
:have_python
if not exist "server.py" (
    echo.
    echo           server.py is missing from this folder.
    echo           START.bat must sit in the same folder as the program.
    goto :fail
)
echo    [2/7]  Program files found
if exist ".venv\" (
    echo           Note: an old shared ".venv" folder is present and no longer
    echo           used. You can delete it to save space.
)

REM  --------------------------------------------------------------------
REM  Everything this program needs goes into a .venv folder right here,
REM  never into the machine's own Python. Two reasons: installing into a
REM  system Python often needs admin rights on a locked-down reception
REM  laptop, and a folder of our own means uninstalling is just deleting
REM  this folder.
REM
REM  The embeddable Python is the exception - that build ships without the
REM  venv module, but it is already private to this folder, so it needs no
REM  isolating.
REM  --------------------------------------------------------------------
set "VPY="
if defined EMBEDDED (
    set "VPY=!PY!"
    echo    [3/7]  Bundled Python is already private to this folder
    goto :have_venv
)

if exist "!VENVDIR!\Scripts\python.exe" (
    call :check_venv
    if not errorlevel 1 (
        set "VPY=%~dp0!VENVDIR!\Scripts\python.exe"
        echo    [3/7]  Environment ready
        goto :have_venv
    )
    echo    [3/7]  The environment is broken, rebuilding it
    call :remove_venv
) else (
    echo    [3/7]  Creating a private environment in this folder
)

call :make_venv
if not exist "!VENVDIR!\Scripts\python.exe" (
    echo.
    echo           Could not create !VENVDIR!.
    echo           Falling back to the machine's own Python.
    echo.
    set "VPY=!PY!"
) else (
    set "VPY=%~dp0!VENVDIR!\Scripts\python.exe"
)

:have_venv
call :check_packages
if errorlevel 1 (
    echo    [4/7]  Installing what the program needs
    echo           ^(first time only, about a minute^)
    call :install_packages
    if errorlevel 1 (
        echo.
        echo           Could not download the packages.
        echo           Check that this computer is online, then try again.
        goto :fail
    )
) else (
    echo    [4/7]  Packages ready
)

if not exist ".env" (
    echo    [5/7]  Creating the security key
    call :make_secret
    echo           Saved as .env  -  keep a backup of this file.
) else (
    echo    [5/7]  Security key found
)
call :load_env

if not exist "academy.db" (
    echo    [6/7]  Setting up the database
    call :init_db
    if errorlevel 1 (
        echo           Could not create the database.
        goto :fail
    )
) else (
    echo    [6/7]  Database found
)

echo    [7/7]  Starting
echo.
echo    ----------------------------------------------------------
echo      The academy system is running.
echo.
echo      Your browser will open in a moment. If it does not,
echo      type this into the address bar:
echo.
echo            http://127.0.0.1:8000
echo.
echo      KEEP THIS BLACK WINDOW OPEN while using the system.
echo      Closing it stops the program.
echo    ----------------------------------------------------------
echo.

start "" http://127.0.0.1:8000/reception
"%VPY%" server.py

echo.
echo    The system has stopped.
pause
exit /b 0


REM ===========================================================================
REM  Subroutines. Anything with parentheses, pipes or redirects belongs here.
REM ===========================================================================

:find_python
REM  1. whatever is on PATH
for /f "delims=" %%P in ('where py.exe 2^>nul') do call :test_python "%%~P"
if defined PY exit /b
for /f "delims=" %%P in ('where python.exe 2^>nul') do call :test_python "%%~P"
if defined PY exit /b
for /f "delims=" %%P in ('where python3.exe 2^>nul') do call :test_python "%%~P"
if defined PY exit /b

REM  2. the usual install folders. A very common case is Python installed
REM     with "Add to PATH" left unticked, which makes it invisible to where.
call :scan_dir "%LOCALAPPDATA%\Programs\Python"
if defined PY exit /b
call :scan_dir "%ProgramFiles%"
if defined PY exit /b
set "PF86=%ProgramFiles(x86)%"
if defined PF86 call :scan_dir "!PF86!"
if defined PY exit /b
call :scan_dir "C:\"
if defined PY exit /b

REM  3. wherever the installer recorded itself
call :scan_dir
REM  %1 = a folder that may contain PythonNNN subfolders
if not exist "%~1" exit /b
for /d %%D in ("%~1\Python3*") do call :test_python "%%~D\python.exe"
exit /b


:find_python_registry
exit /b


:scan_dir
REM  %1 = a folder that may contain PythonNNN subfolders
if not exist "%~1" exit /b
for /d %%D in ("%~1\Python3*") do call :test_python "%%~D\python.exe"
exit /b


:find_python_registry
for /f "skip=2 tokens=2,*" %%A in ('reg query "HKCU\Software\Python\PythonCore" /s /v InstallPath 2^>nul ^| findstr /i "REG_SZ"') do call :test_python "%%~B\python.exe"
if defined PY exit /b
for /f "skip=2 tokens=2,*" %%A in ('reg query "HKLM\Software\Python\PythonCore" /s /v InstallPath 2^>nul ^| findstr /i "REG_SZ"') do call :test_python "%%~B\python.exe"
exit /b


:test_python
REM  %1 = full path to a candidate python.exe
if defined PY exit /b
if not exist "%~1" exit /b

REM  Windows ships a stub python.exe under WindowsApps that only opens the
REM  Microsoft Store. It answers "where" but cannot run anything.
echo "%~1" | find /i "WindowsApps" >nul
if not errorlevel 1 exit /b

REM  Ask it its version as plain text. No parentheses, no comparison
REM  operators, nothing the batch parser can choke on.
set "VER="
for /f "usebackq tokens=2" %%V in (`"%~1" -V 2^>^&1`) do set "VER=%%V"
if not defined VER exit /b

set "FOUNDANY=!VER!"
for /f "tokens=1,2 delims=." %%a in ("!VER!") do set "MAJ=%%a" & set "MIN=%%b"
if not defined MAJ exit /b
if not defined MIN exit /b
if !MAJ! LSS 3 exit /b
if !MAJ! EQU 3 if !MIN! LSS 10 exit /b

set "PY=%~1"
set "PYINFO=!VER!"
exit /b


:winget_install
winget install --id Python.Python.3.12 --source winget --accept-package-agreements --accept-source-agreements --silent >nul 2>&1
exit /b


:install_embedded
set "ZIP=%TEMP%\mbp-python.zip"
call :download "https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip" "%ZIP%"
if not exist "%ZIP%" exit /b 1

echo           Unpacking...
powershell -NoProfile -Command "Expand-Archive -LiteralPath '%ZIP%' -DestinationPath '%RUNTIME%' -Force" >nul 2>&1
del "%ZIP%" >nul 2>&1
if not exist "%RUNTIME%\python.exe" exit /b 1

REM  The embeddable build disables site-packages, so pip installs would be
REM  invisible. Uncommenting "import site" in the ._pth file turns them on.
for %%F in ("%RUNTIME%\python*._pth") do call :fix_pth "%%~F"

echo           Adding the package installer...
call :download "https://bootstrap.pypa.io/get-pip.py" "%RUNTIME%\get-pip.py"
"%RUNTIME%\python.exe" "%RUNTIME%\get-pip.py" --no-warn-script-location >nul 2>&1
del "%RUNTIME%\get-pip.py" >nul 2>&1

call :test_python "%RUNTIME%\python.exe"
if defined PY echo           Python is ready.
exit /b


:fix_pth
powershell -NoProfile -Command "$f='%~1'; $c=Get-Content $f; $c = $c -replace '^#\s*import\s+site','import site'; Set-Content $f $c" >nul 2>&1
exit /b


:download
REM  curl ships with Windows 10 1803 and later. PowerShell covers older builds.
where curl.exe >nul 2>&1
if not errorlevel 1 (
    curl.exe -L --silent --show-error --fail -o "%~2" "%~1" >nul 2>&1
    if not errorlevel 1 exit /b 0
)
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; try { Invoke-WebRequest -Uri '%~1' -OutFile '%~2' -UseBasicParsing } catch { exit 1 }" >nul 2>&1
exit /b 0


:make_venv
"%PY%" -m venv "%VENVDIR%" >nul 2>&1
exit /b


:check_venv
REM  An environment left behind by an uninstalled or upgraded Python still has
REM  its folder but cannot run, so test it rather than trusting it exists.
"%VENVDIR%\Scripts\python.exe" -c "import sys" >nul 2>&1
exit /b %errorlevel%


:remove_venv
rmdir /s /q "%VENVDIR%" >nul 2>&1
exit /b


:check_packages
"%VPY%" -c "import fastapi, uvicorn, qrcode, PIL, multipart" >nul 2>&1
exit /b %errorlevel%


:install_packages
"%VPY%" -m pip install --upgrade pip --quiet --no-warn-script-location >nul 2>&1
"%VPY%" -m pip install -r requirements.txt --quiet --no-warn-script-location
exit /b %errorlevel%


:make_secret
"%VPY%" -c "import secrets; open('.env','w').write('ENTRY_SECRET=' + secrets.token_urlsafe(32))"
exit /b


:load_env
for /f "usebackq tokens=1,* delims==" %%A in (".env") do set "%%A=%%B"
exit /b


:init_db
"%VPY%" -c "import db; db.init()" 2>nul
exit /b %errorlevel%


REM ===========================================================================
:no_internet
echo.
echo    ----------------------------------------------------------
echo      Could not set Python up automatically.
echo.
echo      This computer needs an internet connection the first
echo      time only. Connect to wifi and run START again.
echo.
echo      If there is no internet here, ask whoever set the
echo      system up to copy the "runtime" folder from another
echo      machine, or to build the standalone .exe instead.
echo    ----------------------------------------------------------
echo.
call :diagnostics
pause
exit /b 1

:fail
echo.
echo    ----------------------------------------------------------
echo      The system could not start.
echo      Show this window to whoever set the system up.
echo    ----------------------------------------------------------
echo.
call :diagnostics
pause
exit /b 1

:diagnostics
echo.
echo    Details for support:
echo    --------------------
where py.exe 2>nul
where python.exe 2>nul
if defined FOUNDANY echo    Highest version seen: %FOUNDANY%
if defined PY echo    Base Python:          %PY%
if defined VPY echo    Running with:         %VPY%
if exist "%VENVDIR%\Scripts\python.exe" (echo    Environment:          %VENVDIR%) else (echo    Environment:          not created)
echo    Folder: %~dp0
echo.
exit /b
