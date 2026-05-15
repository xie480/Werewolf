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
                    
        # 2. 获取未压缩的近期记忆
        recent_memories = await self.fetch_recent_uncompressed_memories(game_id, max_compressed_round)
        
        return {
            "compressed_memories": compressed_memories,
            "recent_memories": recent_memories
        }

    async def fetch_recent_uncompressed_memories(self, game_id: str, max_compressed_round: int) -> List[RoundMemory]:
        """使用 Lua 脚本高效获取未压缩的近期公共记忆

        Args:
            game_id (str): 游戏ID，用于标识特定的游戏实例
            max_compressed_round (int): 最大已压缩轮次，函数将返回此轮次之后的记忆

        Returns:
            List[RoundMemory]: 返回从max_compressed_round+1开始的所有轮次的记忆列表，
                              每个RoundMemory对象包含该轮次的公共事件、私有事实和推理信息
        """
        from ai_werewolf_core.utils.redis_lua_loader import LuaScriptManager
        from ai_werewolf_core.constant.redis_keys import RedisKeys
        from ai_werewolf_core.schemas.models import Event
        from ai_werewolf_core.schemas.enums import EventType, Visibility, GamePhase
        import json
        from datetime import datetime
        from ai_werewolf_core.utils.time_utils import now_tz
        
        # 构建Redis流键名，用于存储游戏事件
        stream_key = RedisKeys.event_stream(game_id)
        
        # 尝试使用Lua脚本获取最近的公共事件，如果失败则回退到旧方法
        try:
            raw_messages = await LuaScriptManager.evalsha(
                "get_recent_public_events",
                keys=[stream_key],
                args=[str(max_compressed_round), "1000"]
            )
        except Exception as e:
            logger.warning("lua_get_recent_public_events_failed", error=str(e))
            # Fallback to old method
            all_round_memories = await self.fetch_round_memories(game_id, max_events=999999)
            return [rm for rm in all_round_memories if rm.round_num > max_compressed_round]
            
        if not raw_messages:
            return []
            
        # 初始化字典以按轮次组织事件日志
        round_dict: Dict[int, List[PublicEventLog]] = {}
        
        for msg in raw_messages:
            # msg is [msg_id, [field1, value1, field2, value2, ...]]
            if len(msg) != 2:
                continue
            msg_id = msg[0]
            fields_list = msg[1]
            
            # 解析消息字段
            fields = {}
            for i in range(0, len(fields_list), 2):
                k = fields_list[i].decode('utf-8') if isinstance(fields_list[i], bytes) else fields_list[i]
                v = fields_list[i+1].decode('utf-8') if isinstance(fields_list[i+1], bytes) else fields_list[i+1]
                fields[k] = v
                
            try:
                # 从解析的字段创建事件对象
                event = Event(
                    event_id=fields.get("event_id", ""),
                    game_id=fields.get("game_id", ""),
                    seq_num=int(fields.get("seq_num", 0)),
                    event_type=EventType(fields.get("event_type", "")),
                    visibility=Visibility(fields.get("visibility", "")),
                    target_agents=json.loads(fields.get("target_agents", "[]")),
                    timestamp=datetime.fromisoformat(
                        fields.get("timestamp", now_tz().isoformat())
                    ),
                    payload=json.loads(fields.get("payload", "{}")),
                )
                
                # 格式化事件描述文本
                desc = self._format_event_to_nl(event)
                if desc:
                    # 提取并验证游戏阶段
                    phase_str = event.payload.get("phase", GamePhase.INIT.value)
                    try:
                        phase = GamePhase(phase_str)
                    except ValueError:
                        phase = GamePhase.INIT
                        
                    # 获取事件所属的轮次号
                    round_num = event.payload.get("round", 1)
                    
                    # 将事件添加到对应轮次的事件列表中
                    if round_num not in round_dict:
                        round_dict[round_num] = []
                        
                    round_dict[round_num].append(PublicEventLog(
                        seq_num=event.seq_num,
                        phase=phase,
                        description=desc
                    ))
            except Exception as e:
                logger.error("parse_event_failed", error=str(e))
                continue
                
        # 按轮次排序并构建最终的RoundMemory对象列表
        round_memories = []
        for round_num in sorted(round_dict.keys()):
            round_memories.append(RoundMemory(
                round_num=round_num,
                public_events=sorted(round_dict[round_num], key=lambda x: x.seq_num),
                private_facts=[],
                reasoning=[]
            ))
            
        return round_memories

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
