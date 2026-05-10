import asyncio
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

# 导入设置和 ORM 元数据
from ai_werewolf_core.config import settings
from ai_werewolf_core.db.base import Base
# 导入所有模型，确保它们注册到 Base.metadata
import ai_werewolf_core.db.models  # noqa: F401

# 这是 Alembic 配置对象，提供对正在使用的 .ini 文件中值的访问
config = context.config

# 使用类型化配置覆盖 sqlalchemy.url
config.set_main_option("sqlalchemy.url", settings.database_url)

# 解析配置文件以用于 Python 日志，此行设置日志记录器
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 在此处添加模型的 MetaData 对象，用于支持自动生成
target_metadata = Base.metadata

# 配置文件中的其他值，根据 env.py 的需要可以获取：
# my_important_option = config.get_main_option("my_important_option")
# ... 等等


def run_migrations_offline() -> None:
    """以'离线'模式运行迁移。

    此模式仅使用 URL 配置上下文，
    而不使用引擎，不过在此处使用引擎也是可以的。
    通过跳过引擎创建，甚至不需要 DBAPI 可用。

    此处对 context.execute() 的调用会将给定字符串输出到脚本输出。
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """使用连接配置并运行迁移的辅助函数。"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """使用异步引擎以'在线'模式运行迁移。

    在这种情况下，需要创建引擎
    并将连接关联到上下文。
    """
    connectable = create_async_engine(
        settings.database_url,
        future=True,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """运行异步循环的在线迁移入口点。"""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
