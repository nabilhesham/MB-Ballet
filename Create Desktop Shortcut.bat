@echo off
REM  Puts an "MB Ballet Academy" icon on the desktop so reception never has to
REM  find this folder. Run once, after setup.

setlocal
cd /d "%~dp0"

powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(\"$env:USERPROFILE\Desktop\MB Ballet Academy.lnk\");" ^
  "$s.TargetPath='%~dp0START.bat';" ^
  "$s.WorkingDirectory='%~dp0';" ^
  "$s.IconLocation='%SystemRoot%\System32\imageres.dll,187';" ^
  "$s.Description='Open the academy system';" ^
  "$s.Save()"

if errorlevel 1 (
    echo   Could not create the shortcut.
) else (
    echo.
    echo   Done. There is now an "MB Ballet Academy" icon on the desktop.
    echo   Reception only ever needs to double-click that.
    echo.
)
pause
