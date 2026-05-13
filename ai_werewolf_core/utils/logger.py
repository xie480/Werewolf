"""
日志系统核心模块

基于 structlog 的结构化日志系统，支持上下文绑定与异步隔离。
开发环境输出带颜色的 Console 格式，生产环境输出 JSON 格式。

上下文绑定函数：
    bind_game_context(game_id, phase)：绑定对局级别的 context 变量
    bind_agent_context(agent_id)：绑定智能体级别的 context 变量
    clear_agent_context()：清除智能体上下文（如 phase 切换时）
    clear_all_context()：清除全部上下文（如游戏结束时）

使用方式：
    from utils.logger import get_logger
    logger = get_logger(__name__)
    logger.info("game started", event_type="game_start")
"""

import logging
import os
from typing import Any, Optional

import structlog
from structlog.types import Processor


# ---------------------------------------------------------------------------
# 上下文变量（利用 contextvars 实现 asyncio 安全）
# ---------------------------------------------------------------------------

from contextvars import ContextVar

# 对局id
_game_id_ctx: ContextVar[Optional[str]] = ContextVar("game_id", default=None)
# 游戏阶段
_phase_ctx: ContextVar[Optional[str]] = ContextVar("phase", default=None)
# 智能体id
_agent_id_ctx: ContextVar[Optional[str]] = ContextVar("agent_id", default=None)


def bind_game_context(game_id: str, phase: str) -> None:
    """
    绑定对局级别的上下文变量。

    应在 Game Engine 初始化时调用，用于设置当前对局的 game_id 和 phase。

    Args:
        game_id: 对局唯一标识
        phase: 当前游戏阶段，如 GamePhase.NIGHT_ACTION.value
    """
    _game_id_ctx.set(game_id)
    _phase_ctx.set(phase)


def bind_agent_context(agent_id: str) -> None:
    """
    绑定智能体上下文变量。

    应在 Agent 执行逻辑前调用，如 LangGraph 节点入口。

    Args:
        agent_id: 智能体唯一标识
    """
    _agent_id_ctx.set(agent_id)


def clear_agent_context() -> None:
    """
    清除智能体上下文，通常在 phase 切换或 agent 执行结束时调用。
    """
    _agent_id_ctx.set(None)


def clear_all_context() -> None:
    """
    清除所有上下文变量，通常在游戏结束后调用，避免变量泄露到下一局。
    """
    _game_id_ctx.set(None)
    _phase_ctx.set(None)
    _agent_id_ctx.set(None)


# ---------------------------------------------------------------------------
# 上下文注入 Processor
# ---------------------------------------------------------------------------

def _inject_context(_, __, event_dict: dict) -> dict:
    """
    structlog Processor：将 contextvars 中的上下文变量注入到每条日志中。

    所有通过 bind_* 函数设置的上下文都会自动出现在 JSON 日志的顶层字段中。
    """
    game_id = _game_id_ctx.get()
    if game_id is not None:
        event_dict["game_id"] = game_id

    phase = _phase_ctx.get()
    if phase is not None:
        event_dict["phase"] = phase

    agent_id = _agent_id_ctx.get()
    if agent_id is not None:
        event_dict["agent_id"] = agent_id

    return event_dict


# ---------------------------------------------------------------------------
# 日志初始化
# ---------------------------------------------------------------------------

def _build_processors(env: str) -> list[Processor]:
    """
    根据环境构建 structlog 的 Processor 链条。

    生产环境 (prod) 使用 JSONRenderer + 标准 logging 输出；
    开发环境 (dev) 使用 ConsoleRenderer + 彩色输出。
    """
    from ai_werewolf_core.utils.time_utils import now_tz

    def _add_timestamp(_, __, event_dict: dict) -> dict:
        event_dict["timestamp"] = now_tz().isoformat()
        return event_dict

    shared_processors: list[Processor] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _add_timestamp,
        _inject_context,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if env == "prod":
        # 生产环境：纯 JSON 输出
        renderer = structlog.processors.JSONRenderer()
    else:
        # 默认开发环境：带颜色的控制台输出
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    return shared_processors + [
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ]


def _setup_stdlib_logging(env: str) -> None:
    """
    配置标准库 logging，将 structlog 的输出重定向到标准库 Handler。
    这样可以兼容所有使用标准 logging 的第三方库（如 uvicorn, langchain）。
    """
    level = logging.DEBUG if env == "dev" else logging.INFO

    # 1. 配置 root logger 的基础 Handler
    handler = logging.StreamHandler()
    handler.setLevel(level)

    # 使用 structlog 的 ProcessorFormatter 来格式化标准库日志
    if env == "prod":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    from ai_werewolf_core.utils.time_utils import now_tz

    def _add_timestamp(_, __, event_dict: dict) -> dict:
        event_dict["timestamp"] = now_tz().isoformat()
        return event_dict

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            _add_timestamp,
            _inject_context,
        ],
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # 2. 降低第三方库的日志噪音
    for noisy_logger in ("uvicorn.access", "httpx", "openai", "celery"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


def setup_logger() -> None:
    """
    全局日志初始化入口。

    根据环境变量 ENVIRONMENT 决定输出格式：
        - dev（默认）：彩色 Console 输出
        - prod：JSON 输出

    应在 FastAPI 启动事件或 Worker 启动时调用一次。
    """
    env = os.getenv("ENVIRONMENT", "dev").lower()
    _setup_stdlib_logging(env)

    structlog.configure(
        processors=_build_processors(env),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


# ---------------------------------------------------------------------------
# 便捷获取 Logger
# ---------------------------------------------------------------------------

def get_logger(name: str = __name__) -> structlog.stdlib.BoundLogger:
    """
    获取一个 bound logger 实例。

    基本用法：
        logger = get_logger(__name__)
        logger.info("message", event_type="some_event", extra_field=42)

    Args:
        name: logger 名称，通常传入 __name__

    Returns:
        structlog.stdlib.BoundLogger 实例
    """
    return structlog.get_logger(name)
