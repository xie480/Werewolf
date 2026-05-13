from ai_werewolf_core.config import settings
from ai_werewolf_core.db.session import async_session_factory
from ai_werewolf_core.db.models import ModelConfig as ORMModelConfig
from sqlalchemy import select

class ModelRegistry:
    """全局模型注册表，负责合并 config 与 DB，提供统一查询接口"""
    _registry: dict[str, dict] = {}

    @classmethod
    async def init(cls) -> None:
        # 1. 加载 config.py 中的静态列表
        for cfg in settings.models:
            cls._registry[cfg.model_id] = cfg.model_dump()

        # 2. 从数据库读取（若存在则覆盖）
        try:
            async with async_session_factory() as session:
                result = await session.execute(select(ORMModelConfig))
                db_models = result.scalars().all()
                for row in db_models:
                    cls._registry[row.id] = row.to_adapter_config()

                # 3. 若 DB 为空，将默认 config 写入
                if not db_models:
                    session.add_all([ORMModelConfig(**c.model_dump(exclude={"model_id"}), id=c.model_id) for c in settings.models])
                    await session.commit()
        except Exception as e:
            import structlog
            logger = structlog.get_logger(__name__)
            logger.warning("failed_to_sync_model_config_from_db", error=str(e))

    @classmethod
    def get_config(cls, model_id: str) -> dict:
        if model_id not in cls._registry:
            raise ValueError(f"Model {model_id} not registered")
        return cls._registry[model_id]

    @classmethod
    def list_models(cls) -> list[str]:
        return list(cls._registry.keys())
