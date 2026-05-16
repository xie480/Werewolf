@echo off
setlocal enabledelayedexpansion
title Werewolf Backend
echo ===== Werewolf Backend ^| http://localhost:8000 =====
echo.

cd /d %~dp0

:: 检查 8000 端口是否被占用，如被占用则尝试释放
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr LISTENING') do (
    echo [警告] 发现端口 8000 被 PID %%a 占用，正在释放...
    taskkill /F /PID %%a >nul 2>&1
    if !errorlevel! equ 0 (
        echo [信息] 已释放端口 8000 (PID: %%a)
    )
    timeout /t 1 /nobreak >nul
)

uvicorn ai_werewolf_core.main:app --reload --host 0.0.0.0 --port 8000
pause
endlocal
