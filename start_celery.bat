@echo off
chcp 65001 >nul
title Werewolf Celery Worker
echo ===== Werewolf Celery Worker ^| Redis Broker =====
echo.
cd /d %~dp0
echo [Celery] 启动后台任务 Worker（用于阶段自动推进）...
REM -P solo: 单线程模式，避免 eventlet 与 asyncio 的底层 socket 冲突
REM Why: 项目中大量使用 redis.asyncio，eventlet 的 monkey patch 会破坏
REM asyncio 的 socket 行为，导致连接池中的连接被静默关闭。
REM solo 模式在 Windows 下兼容性最好，且与本项目的 asyncio 核心架构兼容。
celery -A ai_werewolf_core.worker.celery_app worker --loglevel=info -P solo
pause
