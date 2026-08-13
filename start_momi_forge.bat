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
set "RUNPOD_MANAGEMENT_ROOT=%ROOT%\runpod_management"
set "RUNPOD_MANAGEMENT_BACKEND_DIR=%RUNPOD_MANAGEMENT_ROOT%\webapp\backend"
set "RUNPOD_MANAGEMENT_DIST_DIR=%RUNPOD_MANAGEMENT_ROOT%\webapp\frontend\dist"
set "RUNPOD_MANAGEMENT_API_PORT=8843"
set "RUNPOD_MANAGEMENT_API_UPSTREAM_URL=https://127.0.0.1:%RUNPOD_MANAGEMENT_API_PORT%"
set "APP_PUBLIC_HOST="
set "APP_SCHEME=http"
if exist "%APP_SSL_CERTFILE%" if exist "%APP_SSL_KEYFILE%" set "APP_SCHEME=https"
set "WEBAPP_ORIGIN=%APP_SCHEME%://127.0.0.1:%APP_SERVER_PORT%"
set "WEBAPP_API_PORT=%RUNPOD_MANAGEMENT_API_PORT%"

rem Per-poll RunPod trace logs (JSONL, one file per job) used to diagnose long
rem "Preparation" waits: they separate queue delay from worker startup time.
rem Both values are only defaulted, so the parent environment wins - run
rem `set RUNPOD_TRACE_DEBUG=0` before this script to launch without tracing.
if not defined RUNPOD_TRACE_DEBUG set "RUNPOD_TRACE_DEBUG=1"
if not defined RUNPOD_TRACE_DIR set "RUNPOD_TRACE_DIR=%ROOT%\trace_logs"

rem The Python app and both Node services must sign portal tokens with the same
rem value. .env is the single source of truth; the Node services only see it if
rem this script exports it, because they do not read .env themselves.
set "HISTORY_PORTAL_SSO_SECRET="
if exist "%ROOT%\.env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ROOT%\.env") do (
    if /i "%%A"=="HISTORY_PORTAL_SSO_SECRET" set "HISTORY_PORTAL_SSO_SECRET=%%~B"
  )
)
if not defined HISTORY_PORTAL_SSO_SECRET (
  echo [momi] ERROR: HISTORY_PORTAL_SSO_SECRET is not set in "%ROOT%\.env"
  echo [momi] It signs the history portal and RunPod management access tokens.
  echo [momi] Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"
  pause
  exit /b 1
)
if /i "%HISTORY_PORTAL_SSO_SECRET%"=="momi-forge-local-sso-secret" (
  echo [momi] ERROR: HISTORY_PORTAL_SSO_SECRET is still the public placeholder value.
  echo [momi] Anyone could forge an admin portal token with it. Replace it with:
  echo [momi] python -c "import secrets; print(secrets.token_urlsafe(32))"
  pause
  exit /b 1
)

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
echo [momi] RUNPOD_MANAGEMENT_BACKEND_DIR=%RUNPOD_MANAGEMENT_BACKEND_DIR%
echo [momi] RUNPOD_MANAGEMENT_DIST_DIR=%RUNPOD_MANAGEMENT_DIST_DIR%
echo [momi] RUNPOD_MANAGEMENT_API_UPSTREAM_URL=%RUNPOD_MANAGEMENT_API_UPSTREAM_URL%
echo [momi] RUNPOD_TRACE_DEBUG=%RUNPOD_TRACE_DEBUG%
if not "%RUNPOD_TRACE_DEBUG%"=="0" (
  set "TRACE_USAGE=unknown"
  for /f "delims=" %%I in ('powershell -NoProfile -Command "$d='%RUNPOD_TRACE_DIR%'; if (Test-Path -LiteralPath $d) { $f = @(Get-ChildItem -LiteralPath $d -Filter 'runpod_trace_*.jsonl' -File -ErrorAction SilentlyContinue); $s = ($f | Measure-Object -Property Length -Sum).Sum; if (-not $s) { $s = 0 }; '{0} files, {1:N0} MB' -f $f.Count, ($s/1MB) } else { 'empty' }"') do set "TRACE_USAGE=%%I"
  echo [momi] RUNPOD_TRACE_DIR=%RUNPOD_TRACE_DIR%
  call echo [momi] Trace logs on ^(%%TRACE_USAGE%%^). The app keeps only the newest ones.
)
echo.

echo [momi] Reclaiming port %HISTORY_PORTAL_PORT% if occupied...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetTCPConnection -LocalPort %HISTORY_PORTAL_PORT% -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>nul

echo [momi] Reclaiming port %RUNPOD_MANAGEMENT_API_PORT% if occupied...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetTCPConnection -LocalPort %RUNPOD_MANAGEMENT_API_PORT% -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>nul

if not exist "%ROOT%\history_portal\package.json" (
  echo [momi] ERROR: History portal folder is missing package.json
  echo [momi] Expected: "%ROOT%\history_portal\package.json"
  pause
  exit /b 1
)

if not exist "%RUNPOD_MANAGEMENT_BACKEND_DIR%\package.json" (
  echo [momi] ERROR: RunPod management backend is missing package.json
  echo [momi] Expected: "%RUNPOD_MANAGEMENT_BACKEND_DIR%\package.json"
  pause
  exit /b 1
)

if not exist "%RUNPOD_MANAGEMENT_DIST_DIR%\index.html" (
  echo [momi] ERROR: RunPod management frontend build is missing index.html
  echo [momi] Expected: "%RUNPOD_MANAGEMENT_DIST_DIR%\index.html"
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

if not exist "%RUNPOD_MANAGEMENT_BACKEND_DIR%\node_modules" (
  echo [momi] Installing RunPod management backend dependencies...
  pushd "%RUNPOD_MANAGEMENT_BACKEND_DIR%"
  call npm.cmd install
  if errorlevel 1 (
    echo [momi] ERROR: RunPod management backend npm install failed.
    popd
    pause
    exit /b 1
  )
  popd
)

start "Momi Forge - History Portal" /D "%ROOT%\history_portal" cmd /k npm.cmd start
start "Momi Forge - RunPod Manager Backend" /D "%RUNPOD_MANAGEMENT_BACKEND_DIR%" cmd /k npm.cmd run start

timeout /t 2 /nobreak >nul

start "Momi Forge - App" /D "%ROOT%" "%ROOT%\.venv\Scripts\python.exe" "%ROOT%\app.py"

echo [momi] Services launched.
echo [momi] Open %APP_SCHEME%://127.0.0.1:%APP_SERVER_PORT%
echo [momi] History runs through same port via /history-proxy.
echo [momi] RunPod Manager runs through same port via /runpod-management.
if defined APP_PUBLIC_HOST (
  echo [momi] Share URL: %APP_SCHEME%://%APP_PUBLIC_HOST%:%APP_SERVER_PORT%
)
echo.

endlocal
