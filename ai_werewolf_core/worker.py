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

from celery.signals import worker_process_init, worker_ready

# ---------------------------------------------------------------------------
# Worker 进程启动后的初始化
# Why 使用 worker_ready 而非 worker_process_init:
#   worker_process_init 仅在进程池模式（prefork）下触发，
#   对于 solo/threads/eventlet 模式不触发。
#   worker_ready 在所有模式下都会触发，适用于 solo 模式。
# ---------------------------------------------------------------------------

@worker_ready.connect
def init_worker_after_ready(**kwargs):
    """Worker 进程就绪后的初始化钩子。

    在 Celery Worker 完全就绪后调用。适用于所有 pool 模式（solo/threads/prefork）。
    负责：
    1. 加载 Lua 脚本
    2. 注册事件分发器（Dispatcher）
    3. 启动 EventBus Pub/Sub 监听
    """
    import asyncio
    from ai_werewolf_core.agents.model.registry import ModelRegistry
    from ai_werewolf_core.utils.redis_lua_loader import LuaScriptManager
    from ai_werewolf_core.utils.asyncio_utils import run_async

    async def _init():
        await LuaScriptManager.load_all_scripts()
        logger.info("lua_scripts_initialized_in_worker")

        # 注册事件分发器
        from ai_werewolf_core.core.event.bus import event_bus
        from ai_werewolf_core.tasks.dispatch import register_dispatchers
        register_dispatchers(event_bus)
        logger.info("dispatchers_registered_in_worker")

    run_async(_init())
    logger.info("worker_initialized_and_ready")


# ---------------------------------------------------------------------------
# 模块级初始化（导入时执行，适用于所有模式）
# Why: 将不依赖事件循环的轻量初始化放在模块级，
# 避免 worker_ready 信号在 pool 进程中的延迟触发问题。
# ---------------------------------------------------------------------------

from ai_werewolf_core.agents.model.registry import ModelRegistry
from ai_werewolf_core.utils.asyncio_utils import run_async

# 初始化 ModelRegistry（同步阻塞式，仅在 Worker 进程首次导入时执行一次）
try:
    run_async(ModelRegistry.init())
    logger.info("model_registry_initialized_in_worker")
except Exception as e:
    logger.warning("model_registry_init_failed_in_worker", error=str(e))

logger.info("celery_app_initialized", broker=settings.redis_url)
