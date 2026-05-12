@echo off
title Werewolf Backend
echo ===== Werewolf Backend ^| http://localhost:8000 =====
echo.
cd /d %~dp0
uvicorn ai_werewolf_core.main:app --reload --host 0.0.0.0 --port 8000
pause
