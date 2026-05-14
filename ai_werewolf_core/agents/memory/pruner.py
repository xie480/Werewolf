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
        
    def count_round_memories_tokens(self, round_memories: List['RoundMemory']) -> int:
        """计算轮次记忆列表的 Token 数量（仅计未压缩的公共事件）"""
        text = ""
        for rm in round_memories:
            if rm.public_events:
                text += "\n".join([e.description for e in rm.public_events]) + "\n"
        return self.count_tokens(text)

    async def compress_events(self, round_memories: List['RoundMemory'], game_id: str, max_tokens: int = 6000) -> List['RoundMemory']:
        """
        检查 Token 数量，如果超限则自动触发轻量级 LLM 压缩历史事件。
        
        Args:
            round_memories: 包含公共事件的轮次记忆列表
            game_id: 对局 ID
            max_tokens: 最大允许的 Token 数量
            
        Returns:
            处理后的轮次记忆列表
        """
        current_tokens = self.count_round_memories_tokens(round_memories)
        if current_tokens <= max_tokens:
            return round_memories
            
        logger.info(f"公共记忆 Token 超限 ({current_tokens} > {max_tokens})，触发自动压缩")
        
        from ai_werewolf_core.agents.memory.compression import MemoryCompressionService
        
        # 找到所有未压缩的轮次（按轮次从小到大排序，优先压缩最早的轮次）
        uncompressed_rounds = [rm for rm in round_memories if not rm.compressed_public and rm.public_events]
        
        for rm in uncompressed_rounds:
            if current_tokens <= max_tokens:
                break
                
            logger.info(f"正在压缩第 {rm.round_num} 轮公共记忆...")
            # 调用压缩服务
            compressed_resp = await MemoryCompressionService.compress(
                events=rm.public_events,
                game_id=game_id,
                round_num=rm.round_num
            )
            
            # 更新内存中的对象
            rm.compressed_public = compressed_resp
            rm.public_events = [] # 清空原始事件
            
            # 重新计算 token
            current_tokens = self.count_round_memories_tokens(round_memories)
            
        logger.info(f"自动压缩完成，当前 Token: {current_tokens}")
        return round_memories