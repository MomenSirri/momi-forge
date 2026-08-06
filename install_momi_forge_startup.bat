@echo off
setlocal
set "INSTALLER=%~dp0scripts\Install-MomiForgeStartup.ps1"

if not exist "%INSTALLER%" (
  echo ERROR: Startup installer not found:
  echo %INSTALLER%
  pause
  exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%INSTALLER%"
set "RESULT=%ERRORLEVEL%"

if not "%RESULT%"=="0" (
  echo.
  echo Startup installation failed with exit code %RESULT%.
  pause
  exit /b %RESULT%
)

echo.
echo Momi Forge startup installation completed.
echo Restart Windows to test pre-login startup.
pause
exit /b 0
