@echo off
chcp 65001 >nul
title Werewolf Celery Worker
echo ===== Werewolf Celery Worker ^| Redis Broker =====
echo.
cd /d %~dp0
echo [Celery] 启动后台任务 Worker（用于阶段自动推进）...
celery -A ai_werewolf_core.worker.celery_app worker --loglevel=info -P eventlet
pause
