import asyncio

_shared_loop = None

def get_shared_loop() -> asyncio.AbstractEventLoop:
    """获取一个共享的持久事件循环。
    
    主要用于 Celery worker 进程中，避免每次执行任务都创建新的事件循环，
    从而导致绑定到事件循环的资源（如 Redis 连接池、锁）失效或泄漏。
    """
    global _shared_loop
    if _shared_loop is None or _shared_loop.is_closed():
        try:
            _shared_loop = asyncio.get_event_loop()
            if _shared_loop.is_closed():
                _shared_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(_shared_loop)
        except RuntimeError:
            _shared_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_shared_loop)
    return _shared_loop

def run_async(coro):
    """在共享事件循环中运行协程。"""
    loop = get_shared_loop()
    return loop.run_until_complete(coro)
