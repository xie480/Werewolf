import tiktoken
from typing import List
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.schemas.models import PublicEventLog

logger = get_logger(__name__)

class MemoryPruner:
    """记忆裁剪与压缩器
    
    负责控制上下文窗口大小，防止 Token 超限。
    采用滑动窗口与关键帧策略：
    - 近期全量保留
    - 远期摘要保留（模型压缩）
    """
    
    def __init__(self, model_name):
        self.model_name = model_name
        try:
            self.encoding = tiktoken.encoding_for_model(model_name)
        except KeyError:
            logger.warning(f"未找到模型 {model_name} 的 tiktoken 编码，回退到 cl100k_base")
            self.encoding = tiktoken.get_encoding("cl100k_base")
            
    def count_tokens(self, text: str) -> int:
        """计算文本的 Token 数量"""
        return len(self.encoding.encode(text))
        
    def count_events_tokens(self, events: List[PublicEventLog]) -> int:
        """计算事件列表的 Token 数量"""
        text = "\n".join([e.description for e in events])
        return self.count_tokens(text)

    async def prune_timeline(self, timeline: List[PublicEventLog], max_tokens: int = 6000) -> List[PublicEventLog]:
        """
        裁剪公共时间线。
        
        如果总 Token 数超过 max_tokens，则触发裁剪逻辑。
        目前实现简单的截断策略，后续可接入 LLM 进行摘要压缩。
        
        Args:
            timeline: 原始公共时间线
            max_tokens: 最大允许的 Token 数量
            
        Returns:
            裁剪后的公共时间线
        """
        current_tokens = self.count_events_tokens(timeline)
        if current_tokens <= max_tokens:
            return timeline
            
        logger.info(f"时间线 Token 超限 ({current_tokens} > {max_tokens})，触发裁剪")
        
        # TODO: 接入轻量级 LLM 进行摘要压缩
        # 理想策略：保留关键帧（如死亡、投票结果），并对早期发言进行摘要
        # 目前降级为直接裁剪：从后往前保留，直到达到 max_tokens
        pruned_timeline = []
        accumulated_tokens = 0
        
        for event in reversed(timeline):
            event_tokens = self.count_tokens(event.description)
            if accumulated_tokens + event_tokens > max_tokens:
                break
            pruned_timeline.insert(0, event)
            accumulated_tokens += event_tokens
            
        logger.info(f"降级裁剪完成，保留了 {len(pruned_timeline)}/{len(timeline)} 条事件，当前 Token: {accumulated_tokens}")
        return pruned_timeline
        
    async def compress_events(self, events: List[PublicEventLog]) -> str:
        """
        使用轻量级 LLM 压缩历史事件（待实现）。
        
        Args:
            events: 需要压缩的历史事件列表
            
        Returns:
            压缩后的摘要文本
        """
        from ai_werewolf_core.agents.memory.compression import MemoryCompressionService
        
        logger.info(f"开始使用 LLM 压缩 {len(events)} 条事件")
        return await MemoryCompressionService.compress(events, game_id="unknown") # TODO: 传递真实的 game_id
