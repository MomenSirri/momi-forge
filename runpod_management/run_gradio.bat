@echo off
setlocal

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
    set "PYTHON_EXE=python"
)

echo Starting Gradio app...
"%PYTHON_EXE%" app.py

if errorlevel 1 (
    echo.
    echo App stopped with an error.
    pause
)
