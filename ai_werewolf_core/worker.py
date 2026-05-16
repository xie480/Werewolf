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
import ai_werewolf_core.tasks.agent_tasks  # noqa: F401 - Agent 推理
import ai_werewolf_core.tasks.eval   # noqa: F401 - 评测统计（Phase 5 占位）

from celery.signals import worker_process_init

@worker_process_init.connect
def init_worker(**kwargs):
    """Worker 进程启动时的初始化钩子"""
    import asyncio
    from ai_werewolf_core.agents.model.registry import ModelRegistry
    from ai_werewolf_core.utils.redis_lua_loader import LuaScriptManager
    
    async def _init():
        await ModelRegistry.init()
        logger.info("model_registry_initialized_in_worker")
        
        await LuaScriptManager.load_all_scripts()
        logger.info("lua_scripts_initialized_in_worker")
        
    # 运行异步初始化
    from ai_werewolf_core.utils.asyncio_utils import run_async
    run_async(_init())
    
    # 注册事件分发器
    from ai_werewolf_core.core.event.bus import event_bus
    from ai_werewolf_core.tasks.dispatch import register_dispatchers
    register_dispatchers(event_bus)
    logger.info("dispatchers_registered_in_worker")
    
    # 启动 EventBus Pub/Sub 监听，接收跨进程事件广播
    # NOTE: 必须启动监听，否则 Worker 无法收到其他进程发布的事件
    # 从而 on_phase_transition 不会触发，Agent 任务不会被分发
    try:
        async def _start_listening():
            await event_bus.start_listening()
            logger.info("event_bus_listening_started_in_worker")
        run_async(_start_listening())
    except Exception as e:
        logger.error("event_bus_listening_failed_in_worker", error=str(e), exc_info=True)

logger.info("celery_app_initialized", broker=settings.redis_url)
