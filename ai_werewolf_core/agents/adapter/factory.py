from typing import Dict, Type
from .base import BaseModelAdapter
from .openai_adapter import OpenAIAdapter
from ai_werewolf_core.agents.model.registry import ModelRegistry
import structlog

logger = structlog.get_logger(__name__)

class AdapterFactory:
    _instances: Dict[str, BaseModelAdapter] = {}
    _init_lock = False  # 防止并发重复初始化

    @classmethod
    def _ensure_registry_initialized(cls) -> None:
        """确保 ModelRegistry 已初始化（懒初始化）。
        
        Why: Celery Worker 中 ModelRegistry.init() 可能在模块加载时因事件循环
        未就绪而执行失败，导致 _registry 为空。此方法在首次调用 get_adapter
        时重新尝试初始化，确保配置可用。
        """
        if ModelRegistry._registry:
            return
        if cls._init_lock:
            return
        cls._init_lock = True
        try:
            import asyncio
            from ai_werewolf_core.utils.asyncio_utils import run_async
            run_async(ModelRegistry.init())
            logger.info("model_registry_lazy_init_success", keys=list(ModelRegistry._registry.keys()))
        except Exception as e:
            logger.warning("model_registry_lazy_init_failed", error=str(e))

    @classmethod
    def get_adapter(cls, model_id: str) -> BaseModelAdapter:
        if model_id in cls._instances:
            return cls._instances[model_id]
            
        # 确保注册表已初始化
        cls._ensure_registry_initialized()
        
        # 获取配置
        try:
            cfg = ModelRegistry.get_config(model_id)
        except ValueError as e:
            logger.error("model_config_not_found", model_id=model_id, registry_keys=list(ModelRegistry._registry.keys()), error=str(e))
            raise
        
        provider = str(cfg.get("provider", "")).strip().lower()
        logger.debug("adapter_factory_get_config", model_id=model_id, provider=provider, raw_provider=cfg.get("provider"))
        
        if provider == "openai":
            adapter = OpenAIAdapter(cfg)
        else:
            raise NotImplementedError(
                f"Provider '{cfg.get('provider')}' not supported for model '{model_id}'. "
                f"Supported providers: openai. "
                f"Available models: {list(cls._instances.keys())}"
            )
            
        cls._instances[model_id] = adapter
        return adapter
        
    @classmethod
    async def close(cls):
        for adapter in cls._instances.values():
            await adapter.close()
        cls._instances.clear()
