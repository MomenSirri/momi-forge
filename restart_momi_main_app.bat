@echo off
setlocal

net session >nul 2>&1
if errorlevel 1 (
  powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo Restarting the Momi Forge main application...
schtasks.exe /End /TN "\Momi Forge - Main App (boot)" >nul 2>&1
timeout /t 3 /nobreak >nul

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-NetTCPConnection -LocalPort 8188 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"

timeout /t 2 /nobreak >nul
schtasks.exe /Run /TN "\Momi Forge - Main App (boot)"
if errorlevel 1 (
  echo ERROR: The Momi Forge scheduled task could not be started.
  pause
  exit /b 1
)

echo Waiting for Momi Forge to start...
timeout /t 15 /nobreak >nul

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { $r = Invoke-WebRequest -Uri 'https://127.0.0.1:8188/?__theme=dark' -SkipCertificateCheck -TimeoutSec 10; if ($r.StatusCode -eq 200) { Write-Host 'Momi Forge HTTPS is working.' -ForegroundColor Green; exit 0 } } catch {}; Write-Host 'Momi Forge did not start.' -ForegroundColor Red; exit 1"

echo.
echo Team URL: https://momi-02.brick.corp:8188/
pause
endlocal
