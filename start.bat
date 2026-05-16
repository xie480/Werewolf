@echo off
title Werewolf - Starting...

echo.
echo ================================================
echo   Werewolf - AI Multi-Agent Game Platform
echo   Launching Frontend + Backend
echo ================================================
echo.

:: [1/4] Check Python
echo [1/4] Checking Python...
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python and add it to PATH.
    pause
    exit /b 1
)
echo        Python OK

:: [2/4] Check Node.js
echo [2/4] Checking Node.js...
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js not found. Please install Node.js and add it to PATH.
    pause
    exit /b 1
)
echo        Node.js OK

:: [3/4] Check backend dependencies
echo [3/4] Checking backend...
if not exist "ai_werewolf_core\venv\Scripts\activate.bat" (
    if not exist "ai_werewolf_core\.venv\Scripts\activate.bat" (
        echo [WARN] No Python venv found, will use system Python
    )
)

:: [4/4] Check frontend dependencies
echo [4/4] Checking frontend...
if not exist "frontend\node_modules" (
    echo [WARN] node_modules not found, running npm install...
    cd frontend
    call npm install
    cd ..
)
echo        Frontend OK

echo.
echo ------------------------------------------------
echo   Starting services...
echo ------------------------------------------------
echo.

:: Launch backend in background
echo [Backend] Starting FastAPI on port 8000...
start /b "Backend" cmd /c "cd /d %~dp0 && uvicorn ai_werewolf_core.main:app --reload --host 0.0.0.0 --port 8000"

:: Wait for backend
echo          Waiting 3s for backend...
timeout /t 3 /nobreak >nul

echo.
echo ================================================
echo   Services started!
echo ================================================
echo.
echo   Backend API docs : http://localhost:8000/docs
echo   Frontend app     : http://localhost:5173
echo   WebSocket        : ws://localhost:8000/ws/games/{game_id}
echo.
echo   !!! IMPORTANT !!!
echo   Game auto-advance requires Celery Worker.
echo   Open a NEW terminal and run:
echo.
echo      start_celery.bat
echo.
echo   Or manually:
echo      celery -A ai_werewolf_core.worker.celery_app worker --loglevel=info -P eventlet
echo.
echo   Press Ctrl+C to stop all services.
echo   Closing this window will also stop all services.
echo ================================================
echo.

:: Launch frontend in current window
echo [Frontend] Starting Vite on port 5173...
cd /d %~dp0frontend
call npm run dev
