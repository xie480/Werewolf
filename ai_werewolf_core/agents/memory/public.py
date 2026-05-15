from typing import List, Dict, Any
from ai_werewolf_core.schemas.models import Event
from ai_werewolf_core.schemas.enums import Visibility, EventType, GamePhase
from ai_werewolf_core.core.event.bus import event_bus
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.schemas.models import PublicEventLog, RoundMemory
from ai_werewolf_core.config import settings
from ai_werewolf_core.agents.memory.pruner import MemoryPruner

logger = get_logger(__name__)

class PublicMemoryManager:
    """公共记忆管理器
    
    负责从全局事件总线（EventBus）中重构公共时间线。
    """
    
    def __init__(self):
        self.event_bus = event_bus

    async def fetch_round_memories(self, game_id: str, start_seq: int = 0, max_events: int = 100) -> List[RoundMemory]:
        """
        获取按轮次聚合的公共记忆。
        
        Args:
            game_id: 对局 ID
            start_seq: 起始序号
            max_events: 最大获取数量
            
        Returns:
            按轮次聚合的 RoundMemory 列表（仅包含 public_events）
        """
        # 传入一个不存在的 agent_id，这样 get_events 只会返回 PUBLIC 事件
        raw_events = await self.event_bus.get_events(
            game_id=game_id,
            agent_id="__public_only__",
            start_seq=start_seq,
            count=max_events
        )
        
        round_dict: Dict[int, List[PublicEventLog]] = {}
        
        for event in raw_events:
            if event.visibility != Visibility.PUBLIC:
                continue
                
            # 仅保留关键事实：发言、投票、死亡
            if event.event_type not in (EventType.SPEECH_EVENT, EventType.VOTE_EVENT, EventType.PLAYER_DEATH):
                continue
                
            desc = self._format_event_to_nl(event)
            if desc:
                # 尝试从 payload 中获取 phase，如果不存在则默认 INIT
                phase_str = event.payload.get("phase", GamePhase.INIT.value)
                try:
                    phase = GamePhase(phase_str)
                except ValueError:
                    phase = GamePhase.INIT
                    
                round_num = event.payload.get("round", 1)
                
                if round_num not in round_dict:
                    round_dict[round_num] = []
                    
                round_dict[round_num].append(PublicEventLog(
                    seq_num=event.seq_num,
                    phase=phase,
                    description=desc
                ))
            
        round_memories = []
        for round_num in sorted(round_dict.keys()):
            round_memories.append(RoundMemory(
                round_num=round_num,
                public_events=sorted(round_dict[round_num], key=lambda x: x.seq_num),
                private_facts=[],
                reasoning=[]
            ))
        
        # 自动触发压缩：检查 token 并在超限时压缩
        pruner = MemoryPruner(settings.compression_model_name)
        compressed_round_memories = await pruner.compress_events(round_memories, game_id)
        return compressed_round_memories
        
    async def get_memory_context(self, game_id: str) -> Dict[str, Any]:
        """
        获取完整的记忆上下文，包含历史压缩记忆和近期未压缩的全量记忆。
        """
        from ai_werewolf_core.utils.redis_client import RedisClientManager
        from ai_werewolf_core.constant.redis_keys import RedisKeys
        import json
        from ai_werewolf_core.schemas.models import CompressionResponse
        from typing import Any
        
        redis = await RedisClientManager.get_client()
        key = RedisKeys.compressed_memory_summary(game_id)
        
        # 1. 获取所有压缩记忆
        raw_data = await redis.hgetall(key)
        compressed_memories = {}
        max_compressed_round = 0
        
        if raw_data:
            for round_str, json_str in raw_data.items():
                try:
                    round_num = int(round_str)
                    data = json.loads(json_str)
                    compressed_memories[round_num] = CompressionResponse(**data)
                    max_compressed_round = max(max_compressed_round, round_num)
                except (ValueError, json.JSONDecodeError):
                    continue
                    
        # 2. 获取未压缩的近期记忆  TODO 我觉得这段效率太低了
        all_round_memories = await self.fetch_round_memories(game_id, max_events=999999)
        # 使用大 max_events 确保能拉取到所有事件，避免因默认 100 条限制导致丢失最新记忆
        recent_memories = [rm for rm in all_round_memories if rm.round_num > max_compressed_round]
        
        return {
            "compressed_memories": compressed_memories,
            "recent_memories": recent_memories
        }

    def _format_event_to_nl(self, event: Event) -> str:
        """将结构化 Event 转换为自然语言描述"""
        payload = event.payload
        
        if event.event_type == EventType.SPEECH_EVENT:
            actor = payload.get("actor_id", "未知玩家")
            content = payload.get("speech", "")
            return f"{actor}发言：{content}"
            
        elif event.event_type == EventType.VOTE_EVENT:
            actor = payload.get("actor_id", "未知玩家")
            target = payload.get("target_id")
            if target:
                return f"{actor}投票给{target}"
            else:
                return f"{actor}弃权"
                
        elif event.event_type == EventType.PHASE_TRANSITION_EVENT:
            new_phase = payload.get("new_phase", "未知阶段")
            return f"游戏进入新阶段：{new_phase}"
            
        elif event.event_type == EventType.SYSTEM_ANNOUNCEMENT:
            content = payload.get("message", "")
            return f"系统公告：{content}"
            
        elif event.event_type == EventType.PLAYER_DEATH:
            player = payload.get("player_id", "未知玩家")
            return f"玩家死亡播报：{player}已死亡"
            
        elif event.event_type == EventType.GAME_OVER_EVENT:
            winners = payload.get("winners", [])
            return f"游戏结束，获胜阵营：{winners}"
            
        return f"未知公共事件：{event.event_type.value}"
