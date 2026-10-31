@echo off
REM Cornflakes Media — Windows One-Click Application Launcher
echo ======================================================================
echo 🌽 CORNFLAKES MEDIA — GENERATION OPS COPILOT LAUNCHER (WINDOWS)
echo ======================================================================

REM Check if docker command is available and daemon is running
docker info >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo [INFO] Docker daemon detected. Launching via Docker Compose...
    echo [INFO] Opening browser in 5 seconds...
    start "" http://localhost:8000
    docker compose up --build
    goto end
)

echo [INFO] Docker not detected or not running.
echo [INFO] Starting standalone high-performance local mode with SQLite fallback...

REM Check for python
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed or not in PATH! Please install Python 3.10+.
    pause
    exit /b 1
)

IF NOT EXIST venv (
    echo [INFO] Creating virtual environment...
    python -m venv venv
)

echo [INFO] Activating virtual environment...
call venv\Scripts\activate.bat

echo [INFO] Checking dependencies...
pip install -r requirements.txt -q

echo [INFO] Launching Ops Copilot server on http://localhost:8000 ...
start "" http://localhost:8000
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

:end
