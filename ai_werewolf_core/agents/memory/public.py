from typing import List
from ai_werewolf_core.schemas.models import Event
from ai_werewolf_core.schemas.enums import Visibility, EventType, GamePhase
from ai_werewolf_core.core.event.bus import event_bus
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.schemas.models import PublicEventLog

logger = get_logger(__name__)

class PublicMemoryManager:
    """公共记忆管理器
    
    负责从全局事件总线（EventBus）中重构公共时间线。
    """
    
    def __init__(self):
        self.event_bus = event_bus

    async def fetch_timeline(self, game_id: str, start_seq: int = 0, max_events: int = 100) -> List[PublicEventLog]:
        """
        获取公共时间线。
        
        Args:
            game_id: 对局 ID
            start_seq: 起始序号
            max_events: 最大获取数量
            
        Returns:
            公共事件日志列表
        """
        # 传入一个不存在的 agent_id，这样 get_events 只会返回 PUBLIC 事件
        raw_events = await self.event_bus.get_events(
            game_id=game_id, 
            agent_id="__public_only__", 
            start_seq=start_seq, 
            count=max_events
        )
        
        timeline = []
        for event in raw_events:
            if event.visibility != Visibility.PUBLIC:
                continue
                
            desc = self._format_event_to_nl(event)
            if desc:
                # 尝试从 payload 中获取 phase，如果不存在则默认 INIT
                phase_str = event.payload.get("phase", GamePhase.INIT.value)
                try:
                    phase = GamePhase(phase_str)
                except ValueError:
                    phase = GamePhase.INIT
                    
                timeline.append(PublicEventLog(
                    seq_num=event.seq_num,
                    phase=phase,
                    description=desc
                ))
            
        return sorted(timeline, key=lambda x: x.seq_num)
        
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
