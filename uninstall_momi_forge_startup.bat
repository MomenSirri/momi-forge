@echo off
setlocal
set "UNINSTALLER=%~dp0scripts\Uninstall-MomiForgeStartup.ps1"

if not exist "%UNINSTALLER%" (
  echo ERROR: Startup uninstaller not found:
  echo %UNINSTALLER%
  pause
  exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%UNINSTALLER%"
set "RESULT=%ERRORLEVEL%"

if not "%RESULT%"=="0" (
  echo.
  echo Startup removal failed with exit code %RESULT%.
  pause
  exit /b %RESULT%
)

echo.
echo Momi Forge startup removal completed.
pause
exit /b 0
