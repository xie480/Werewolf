"""
Celery Worker 入口 —— 异步任务执行节点。

**Why**: 遵循项目架构规范——FastAPI is ingress only，所有重计算
（LangGraph Agent 推理、批量结算、评测统计）委托给 Celery Worker 执行。
Worker 通过 Redis 作为 Broker 接收任务，与 API 进程解耦。

当前状态（Phase 3）:
    - Celery 应用实例已配置
    - 对局任务预留（将在 Phase 4 Agent Runtime 完成后挂载具体任务）
    - Worker 启动命令: celery -A worker.celery_app worker --loglevel=info

参考 [`docs/plan/Phase 3 FastAPI API.md`](../../docs/plan/Phase%203%20FastAPI%20API.md)。
"""

from __future__ import annotations

from celery import Celery

from ai_werewolf_core.config import settings
from ai_werewolf_core.utils.logger import get_logger

logger = get_logger(__name__)

# ============================================================================
# Celery 应用实例
# ============================================================================

celery_app = Celery(
    "werewolf_tasks",
    broker=settings.redis_url,
    backend=settings.redis_url,  # 结果后端（生产环境可切换为 DB）
)

# ============================================================================
# Celery 配置
# ============================================================================

celery_app.conf.update(
    # 任务序列化
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # 任务路由（按模块自动分发到不同队列）
    task_routes={
        "ai_werewolf_core.tasks.game.*": {"queue": "game"},
        "ai_werewolf_core.tasks.agent.*": {"queue": "agent"},
        "ai_werewolf_core.tasks.eval.*": {"queue": "eval"},
    },

    # 并发控制
    worker_prefetch_multiplier=1,  # 每次只取一个任务——LangGraph 推理是重任务
    task_acks_late=True,  # 任务完成后才确认——防止 Worker 崩溃丢任务

    # 重试策略
    task_default_retry_delay=5,  # 重试间隔 5 秒
    task_max_retries=3,          # 最多重试 3 次

    # 结果过期时间（1 小时）
    result_expires=3600,
)

# ============================================================================
# 任务模块导入
# ============================================================================

# Phase 3: 对局生命周期任务
import ai_werewolf_core.tasks.game   # noqa: F401

# Phase 4 完成后导入具体任务模块:
import ai_werewolf_core.tasks.agent  # noqa: F401 - Agent 推理（Phase 4 占位）
import ai_werewolf_core.tasks.eval   # noqa: F401 - 评测统计（Phase 5 占位）

logger.info("celery_app_initialized", broker=settings.redis_url)
