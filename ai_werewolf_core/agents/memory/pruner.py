import tiktoken
from typing import List
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.schemas.models import PublicEventLog, RoundMemory

logger = get_logger(__name__)

class MemoryPruner:
    """记忆裁剪与压缩器
    
    负责控制上下文窗口大小，防止 Token 超限。
    采用滑动窗口与关键帧策略：
    - 近期全量保留
    - 远期摘要保留（模型压缩）
    """
    
    def __init__(self, model_name: str = "gpt-3.5-turbo"):
        self.model_name = model_name
        try:
            self.encoding = tiktoken.encoding_for_model(model_name)
        except KeyError:
            logger.warning(f"未找到模型 {model_name} 的 tiktoken 编码，回退到 cl100k_base")
            self.encoding = tiktoken.get_encoding("cl100k_base")
            
    def count_tokens(self, text: str) -> int:
        """计算文本的 Token 数量"""
        if not text:
            return 0
        return len(self.encoding.encode(text))
        
    def get_budget_allocation(self, max_tokens: int) -> dict:
        """
        获取 Token 预算分配
        1. 全局系统法则: 10%
        2. 身份与阵营策略: 15%
        3. 记忆与上下文: 40%
        4. 当前任务指令: 20%
        5. 输出格式约束: 15%
        """
        return {
            "system": int(max_tokens * 0.10),
            "role": int(max_tokens * 0.15),
            "memory": int(max_tokens * 0.40),
            "task": int(max_tokens * 0.20),
            "format": int(max_tokens * 0.15),
        }

    async def compress_events(self, round_memories: List['RoundMemory'], game_id: str, max_tokens: int = 6000) -> List['RoundMemory']:
        """
        保留兼容旧接口，但实际压缩逻辑已移至异步预归档任务中。
        这里仅做简单的 Token 检查，如果超限则截断。
        """
        if not round_memories:
            return []
            
        result = []
        current_tokens = 0
        
        # 从后往前遍历（保留最新的记忆），超限时截断早期轮次
        for rm in reversed(round_memories):
            try:
                text_content = rm.model_dump_json()
            except Exception:
                text_content = str(rm)
            tokens = self.count_tokens(text_content)
            
            if current_tokens + tokens > max_tokens:
                logger.warning(f"记忆超限，截断早期轮次: {rm.round_num}", game_id=game_id)
                break
                
            current_tokens += tokens
            result.insert(0, rm)
            
        return result