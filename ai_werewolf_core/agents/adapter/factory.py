from typing import Dict, Type
from .base import BaseModelAdapter
from .openai_adapter import OpenAIAdapter
from ai_werewolf_core.agents.model.registry import ModelRegistry

class AdapterFactory:
    _instances: Dict[str, BaseModelAdapter] = {}

    @classmethod
    def get_adapter(cls, model_id: str) -> BaseModelAdapter:
        if model_id in cls._instances:
            return cls._instances[model_id]
            
        # 获取配置
        cfg = ModelRegistry.get_config(model_id)
        
        if str(cfg.get("provider", "")).lower() == "openai":
            adapter = OpenAIAdapter(cfg)
        else:
            raise NotImplementedError(f"Provider {cfg.get('provider')} not supported")
            
        cls._instances[model_id] = adapter
        return adapter
        
    @classmethod
    async def close(cls):
        for adapter in cls._instances.values():
            await adapter.close()
        cls._instances.clear()
