from abc import ABC, abstractmethod
from typing import Any
import structlog
from ai_werewolf_core.schemas.models import AdapterRequest, AdapterResponse

logger = structlog.get_logger(__name__)

class BaseModelAdapter(ABC):
    """模型适配器基类"""
    
    def __init__(self, config: dict):
        self.config = config
        self.client = self._initialize_client()
        
    @abstractmethod
    def _initialize_client(self) -> Any:
        """初始化底层 SDK 客户端"""
        pass

    @abstractmethod
    async def agenerate(self, request: AdapterRequest) -> AdapterResponse:
        """异步生成结构化响应"""
        pass
        
    async def close(self):
        """清理资源"""
        pass
