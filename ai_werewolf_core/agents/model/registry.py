from ai_werewolf_core.config import settings
from ai_werewolf_core.db.session import async_session_factory
from ai_werewolf_core.db.models import ModelConfig as ORMModelConfig
from sqlalchemy import select

class ModelRegistry:
    """全局模型注册表，负责合并 config 与 DB，提供统一查询接口"""
    _registry: dict[str, dict] = {}

    @classmethod
    async def init(cls) -> None:
        # Reset registry
        cls._registry.clear()
        try:
            async with async_session_factory() as session:
                result = await session.execute(select(ORMModelConfig))
                db_models = result.scalars().all()

                # 如果 DB 为空，写入默认配置
                if not db_models:
                    from ai_werewolf_core.utils.crypto import encrypt_api_key
                    new_models = []
                    for c in settings.models:
                        model_data = c.model_dump(exclude={"model_id"})
                        if model_data.get("api_key"):
                            model_data["api_key"] = encrypt_api_key(model_data["api_key"])
                        new_models.append(ORMModelConfig(**model_data, id=c.model_id))
                    session.add_all(new_models)
                    await session.commit()
                    result = await session.execute(select(ORMModelConfig))
                    db_models = result.scalars().all()

                for row in db_models:
                    cls._registry[row.id] = row.to_adapter_config()
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

    @classmethod
    async def reload(cls) -> None:
        """重新加载模型配置"""
        await cls.init()
