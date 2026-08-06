@echo off
setlocal

set "ROOT_DIR=%~dp0"
set "WEBAPP_DIR=%ROOT_DIR%webapp"
set "BACKEND_DIR=%WEBAPP_DIR%\backend"
set "FRONTEND_DIR=%WEBAPP_DIR%\frontend"

if not exist "%WEBAPP_DIR%" (
    echo webapp folder not found: %WEBAPP_DIR%
    pause
    exit /b 1
)

echo Releasing old dev ports if already in use...
call :free_port 8843
call :free_port 5173

echo Installing backend dependencies (if needed)...
pushd "%BACKEND_DIR%"
call npm install
if errorlevel 1 (
    echo Backend dependency install failed.
    popd
    pause
    exit /b 1
)
popd

echo Installing frontend dependencies (if needed)...
pushd "%FRONTEND_DIR%"
call npm install
if errorlevel 1 (
    echo Frontend dependency install failed.
    popd
    pause
    exit /b 1
)
popd

echo Starting HTTPS backend...
start "RunPod Backend HTTPS" /D "%BACKEND_DIR%" cmd /k npm run dev

echo Waiting for backend to become ready...
call :wait_url "https://localhost:8843/api/health" 40
if errorlevel 1 (
    echo Backend did not become ready on https://localhost:8843
    pause
    exit /b 1
)

echo Starting HTTPS frontend...
start "RunPod Frontend HTTPS" /D "%FRONTEND_DIR%" cmd /k npm run dev

echo Waiting for frontend to become ready...
call :wait_url "https://localhost:5173" 40
if errorlevel 1 (
    echo Frontend did not become ready on https://localhost:5173
    pause
    exit /b 1
)

echo Opening dashboard in browser...
start https://localhost:5173

echo.
echo Frontend: https://localhost:5173
echo Backend:  https://localhost:8843
echo.
echo Close the two opened terminal windows to stop services.
exit /b 0

:free_port
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%~1 " ^| findstr "LISTENING"') do (
    echo Stopping process %%P on port %~1...
    taskkill /PID %%P /F >nul 2>&1
)
exit /b 0

:wait_url
setlocal
set "WAIT_URL=%~1"
set "WAIT_TRIES=%~2"
for /l %%I in (1,1,%WAIT_TRIES%) do (
    curl.exe -k -s -f "%WAIT_URL%" >nul 2>&1
    if not errorlevel 1 (
        endlocal
        exit /b 0
    )
    timeout /t 1 >nul
)
endlocal
exit /b 1
