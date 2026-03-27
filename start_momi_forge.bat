@echo off
setlocal

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "USER_DB_PATH=%ROOT%\users.db"
set "APP_SERVER_NAME=0.0.0.0"
set "APP_SERVER_PORT=8188"
set "APP_SSL_ENABLE=auto"
set "APP_SSL_CERTFILE=%ROOT%\openssl\cert.pem"
set "APP_SSL_KEYFILE=%ROOT%\openssl\key.pem"
set "HISTORY_PORTAL_HOST=0.0.0.0"
set "HISTORY_PORTAL_PORT=8199"
set "HISTORY_PORTAL_URL=http://127.0.0.1:8199"
set "HISTORY_PORTAL_USE_PROXY=1"
set "APP_PUBLIC_HOST="
set "HISTORY_PORTAL_SSO_SECRET=momi-forge-local-sso-secret"
set "APP_SCHEME=http"
if exist "%APP_SSL_CERTFILE%" if exist "%APP_SSL_KEYFILE%" set "APP_SCHEME=https"

for /f "delims=" %%I in ('powershell -NoProfile -Command "$ip=''; $route = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Sort-Object RouteMetric, InterfaceMetric | Select-Object -First 1; if ($route) { $ip = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $route.InterfaceIndex -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254*' } | Select-Object -First 1 -ExpandProperty IPAddress }; if (-not $ip) { $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254*' } | Select-Object -First 1 -ExpandProperty IPAddress }; if ($ip) { $ip }"') do set "APP_PUBLIC_HOST=%%I"
echo.
echo [momi] ROOT=%ROOT%
echo [momi] USER_DB_PATH=%USER_DB_PATH%
echo [momi] APP_SERVER_NAME=%APP_SERVER_NAME%
echo [momi] APP_SERVER_PORT=%APP_SERVER_PORT%
echo [momi] APP_SCHEME=%APP_SCHEME%
echo [momi] APP_SSL_CERTFILE=%APP_SSL_CERTFILE%
echo [momi] APP_SSL_KEYFILE=%APP_SSL_KEYFILE%
echo [momi] APP_PUBLIC_HOST=%APP_PUBLIC_HOST%
echo [momi] HISTORY_PORTAL_URL=%HISTORY_PORTAL_URL%
echo.

echo [momi] Reclaiming port %HISTORY_PORTAL_PORT% if occupied...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetTCPConnection -LocalPort %HISTORY_PORTAL_PORT% -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>nul

if not exist "%ROOT%\history_portal\package.json" (
  echo [momi] ERROR: History portal folder is missing package.json
  echo [momi] Expected: "%ROOT%\history_portal\package.json"
  pause
  exit /b 1
)

if not exist "%ROOT%\.venv\Scripts\python.exe" (
  echo [momi] ERROR: Python venv not found at "%ROOT%\.venv\Scripts\python.exe"
  pause
  exit /b 1
)

where npm.cmd >nul 2>nul
if errorlevel 1 (
  echo [momi] ERROR: npm.cmd not found in PATH. Install Node.js or add npm to PATH.
  pause
  exit /b 1
)

start "Momi Forge - History Portal" /D "%ROOT%\history_portal" cmd /k npm.cmd start

timeout /t 2 /nobreak >nul

start "Momi Forge - App" /D "%ROOT%" "%ROOT%\.venv\Scripts\python.exe" "%ROOT%\app.py"

echo [momi] Both windows launched.
echo [momi] Open %APP_SCHEME%://127.0.0.1:%APP_SERVER_PORT%
if defined APP_PUBLIC_HOST (
  echo [momi] Share URL: %APP_SCHEME%://%APP_PUBLIC_HOST%:%APP_SERVER_PORT%
  echo [momi] History runs through same port via /history-proxy.
)
echo.

endlocal
