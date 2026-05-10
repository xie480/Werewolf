"""
数据库模块 - SQLAlchemy 声明式基类、ORM 模型与会话管理。

**Why**: 所有 ORM 模型必须继承同一个 `Base` 类，以便 Alembic 能自动检测表结构变化，
并支持 `asyncpg` 驱动的异步数据库操作。

本模块重新导出以下核心组件，供外部统一引用：
- `Base`: SQLAlchemy 声明式基类（来自 `base.py`）
- `GameRecord`, `PlayerRecord`, `EventRecord`: ORM 数据表模型
- `get_db`, `close_db_engine`, `async_engine`: 数据库会话管理
"""

from ai_werewolf_core.db.base import Base
from ai_werewolf_core.db.models import GameRecord, PlayerRecord, EventRecord
from ai_werewolf_core.db.session import get_db, close_db_engine, async_engine, async_session_factory

__all__ = [
    "Base",
    "GameRecord",
    "PlayerRecord",
    "EventRecord",
    "get_db",
    "close_db_engine",
    "async_engine",
    "async_session_factory",
]
