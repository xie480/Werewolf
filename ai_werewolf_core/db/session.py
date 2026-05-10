"""
异步数据库会话管理 - 连接池与依赖注入。

**Why**: 所有数据库 I/O 必须使用 async/await 以支持并发夜间推理。
使用 `create_async_engine` + `async_sessionmaker` 模式管理连接池，
并通过 FastAPI 依赖注入（`Depends(get_db)`）提供请求级别的数据库会话。

参考 [`docs/plan/ORM.md`](../../docs/plan/ORM.md)。
"""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ai_werewolf_core.config import settings
from ai_werewolf_core.utils.logger import get_logger

logger = get_logger(__name__)

# 异步引擎 —— 全局单例，负责连接池管理
# **Why**:
#   `pool_size` 控制核心连接数，`max_overflow` 允许短时超出，
#   `pool_pre_ping=True` 防止因连接断开导致 "connection was closed" 异常。
async_engine = create_async_engine(
    settings.database_url,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    echo=False,  # 生产环境设为 False，调试时可临时开启
    future=True,
)

# 异步会话工厂
async_session_factory = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,  # 防止提交后懒加载异常
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI 依赖注入 —— 获取一个请求级别的数据库会话。

    **Why**: 使用 yield 模式确保会话在请求完成后正确关闭，
    避免连接泄漏和事务悬挂。参考 FastAPI 官方 SQLAlchemy 集成方案。

    用法:
        @app.get("/games")
        async def list_games(db: AsyncSession = Depends(get_db)):
            ...

    Yields:
        AsyncSession: 一个与 asyncpg 后端绑定的异步 SQLAlchemy 会话。

    Raises:
        Exception: 当数据库连接失败时，由 SQLAlchemy 底层抛出。
    """
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            logger.error("数据库会话异常，已回滚", exc_info=True)
            raise
        finally:
            await session.close()


async def close_db_engine() -> None:
    """
    关闭数据库引擎 —— 释放所有连接池资源。

    **Why**: 在应用正常关闭（graceful shutdown）时必须调用，
    否则会导致连接泄漏和进程挂起。应在 FastAPI shutdown 事件中注册。
    """
    logger.info("正在关闭数据库引擎...")
    await async_engine.dispose()
    logger.info("数据库引擎已关闭")
