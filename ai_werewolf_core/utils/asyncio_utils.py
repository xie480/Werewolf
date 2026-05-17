import asyncio
import threading

_shared_loop = None
_shared_loop_lock = threading.Lock()
_agent_loop = None
_agent_loop_lock = threading.Lock()

def get_shared_loop() -> asyncio.AbstractEventLoop:
    """获取共享的持久事件循环（用于游戏推进、事件处理等轻量任务）。"""
    global _shared_loop
    if _shared_loop is not None and not _shared_loop.is_closed():
        return _shared_loop
    with _shared_loop_lock:
        if _shared_loop is not None and not _shared_loop.is_closed():
            return _shared_loop
        try:
            _shared_loop = asyncio.get_event_loop()
            if _shared_loop.is_closed():
                _shared_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(_shared_loop)
        except RuntimeError:
            _shared_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_shared_loop)
    return _shared_loop

def get_agent_loop() -> asyncio.AbstractEventLoop:
    """获取 Agent 专用的独立事件循环。
    
    Why: LangGraph 的 RunnableCallable.ainvoke() 会在事件循环中创建子 Task，
    如果与 Celery advance_phase 等任务共享同一循环，会导致 Task 上下文管理错乱
    （RuntimeError: Leaving task X does not match current task Y）。
    使用独立循环可完全隔离两类任务的 Task 调度。
    """
    global _agent_loop
    if _agent_loop is not None and not _agent_loop.is_closed():
        return _agent_loop
    with _agent_loop_lock:
        if _agent_loop is not None and not _agent_loop.is_closed():
            return _agent_loop
        _agent_loop = asyncio.new_event_loop()
        _agent_loop.set_debug(False)
    return _agent_loop

import nest_asyncio
# 对两个循环都应用 nest_asyncio
try:
    nest_asyncio.apply(get_shared_loop())
except Exception:
    pass
try:
    nest_asyncio.apply(get_agent_loop())
except Exception:
    pass

def run_async(coro):
    """在共享事件循环中运行协程。"""
    loop = get_shared_loop()
    return loop.run_until_complete(coro)

def run_agent_async(coro):
    """在 Agent 专用独立事件循环中运行协程。
    
    用于 agent_tasks.py 中执行 LangGraph 工作流，与游戏引擎推进任务隔离，
    避免 Task 上下文污染导致的 RuntimeError。
    """
    loop = get_agent_loop()
    return loop.run_until_complete(coro)
