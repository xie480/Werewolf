"""
SQLAlchemy 声明式基类定义。

**Why**: 所有 ORM 模型必须继承同一个 `Base` 类，以便 Alembic 自动检测
表结构变更，并统一管理 `asyncpg` 驱动的异步元数据。
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """
    SQLAlchemy 声明式基类。

    所有 ORM 模型（GameRecord、PlayerRecord、EventRecord）均继承此类。
    Alembic 通过 `Base.metadata` 自动追踪表结构变更。
    """
    pass
