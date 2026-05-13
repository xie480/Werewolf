import json
from typing import Optional
from ai_werewolf_core.utils.redis_client import RedisClientManager
from ai_werewolf_core.constant.redis_keys import RedisKeys
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.schemas.models import PrivateState, PrivateEventLog
from .exceptions import MemoryNotFoundError, SecurityViolationException, MemorySystemError

logger = get_logger(__name__)

class PrivateMemoryManager:
    """私有记忆管理器
    
    负责管理 Agent 的私密状态，数据存储于 Redis Hash 中。
    """
    
    def __init__(self):
        pass

    async def get_private_state(self, game_id: str, player_id: str, request_agent_id: str) -> PrivateState:
        """
        获取指定玩家的私有状态。
        
        Args:
            game_id: 对局 ID
            player_id: 目标玩家 ID
            request_agent_id: 请求读取的 Agent ID，用于越权校验
            
        Returns:
            PrivateState 对象
            
        Raises:
            SecurityViolationException: 如果 request_agent_id 与 player_id 不一致
            MemoryNotFoundError: 如果未找到私有记忆
        """
        if player_id != request_agent_id:
            logger.warning(
                "越权读取私有记忆尝试被拦截",
                game_id=game_id,
                target_player_id=player_id,
                request_agent_id=request_agent_id
            )
            raise SecurityViolationException(f"Agent {request_agent_id} 试图越权读取 {player_id} 的私有记忆")
            
        key = RedisKeys.private_memory(game_id, player_id)
        feedbacks_key = RedisKeys.private_memory_feedbacks(game_id, player_id)
        redis = await RedisClientManager.get_client()
        
        # 获取对应的PrivateState
        raw_data = await redis.hget(key, "state")
        if not raw_data:
            raise MemoryNotFoundError(f"Private memory not found for {player_id} in game {game_id}")
            
        try:
            state = PrivateState.model_validate_json(raw_data)
            # 获取私密消息
            raw_feedbacks = await redis.lrange(feedbacks_key, 0, -1)
            state.system_feedbacks = [PrivateEventLog.model_validate_json(f) for f in raw_feedbacks]
            return state
        except Exception as e:
            logger.error(
                "私有记忆解析失败",
                game_id=game_id,
                player_id=player_id,
                error=str(e)
            )
            raise MemorySystemError(f"Failed to parse private memory for {player_id}: {e}")

    async def init_private_state(self, game_id: str, player_id: str, state: PrivateState) -> None:
        """
        初始化私有状态。
        
        Args:
            game_id: 对局 ID
            player_id: 玩家 ID
            state: 初始化的 PrivateState 对象
        """
        key = RedisKeys.private_memory(game_id, player_id)
        feedbacks_key = RedisKeys.private_memory_feedbacks(game_id, player_id)
        redis = await RedisClientManager.get_client()
        
        # 清理可能存在的旧反馈数据
        await redis.delete(feedbacks_key)
        
        # 保存基础状态（不包含 feedbacks，feedbacks 存在单独的 List 中）
        state_to_save = state.model_copy()
        state_to_save.system_feedbacks = []
        await redis.hset(key, RedisKeys.PRIVATE_MEMORY_STATE_FIELD, state_to_save.model_dump_json())
        
        # 如果有初始反馈，写入 List
        if state.system_feedbacks:
            await redis.rpush(feedbacks_key, *[f.model_dump_json() for f in state.system_feedbacks])
            
        logger.debug("私有记忆已初始化", game_id=game_id, player_id=player_id)

    async def append_system_feedback(self, game_id: str, player_id: str, feedback: PrivateEventLog) -> None:
        """
        追加系统私密反馈（如法官告知预言家查验结果）。
        这是防幻觉的核心机制。
        
        Args:
            game_id: 对局 ID
            player_id: 玩家 ID
            feedback: 私有事件日志对象
        """
        feedbacks_key = RedisKeys.private_memory_feedbacks(game_id, player_id)
        redis = await RedisClientManager.get_client()
        
        try:
            # 使用 RPUSH 追加到单独的 List 中，天然保证原子性，无需 Lua 脚本
            await redis.rpush(feedbacks_key, feedback.model_dump_json())
            logger.debug("系统反馈已追加", game_id=game_id, player_id=player_id, feedback=feedback.description)
        except Exception as e:
            logger.error("追加系统反馈失败", game_id=game_id, player_id=player_id, error=str(e))

    async def save_reasoning(self, game_id: str, player_id: str, reasoning: str) -> None:
        """
        保存 Agent 的内心 OS，用于后续回合的连贯性。
        
        Args:
            game_id: 对局 ID
            player_id: 玩家 ID
            reasoning: 推理内容
        """
        reasoning_key = RedisKeys.private_memory_reasoning(game_id, player_id)
        redis = await RedisClientManager.get_client()
        
        await redis.rpush(reasoning_key, reasoning)
        logger.debug("内心OS已保存", game_id=game_id, player_id=player_id)
        
    async def get_historical_reasoning(self, game_id: str, player_id: str, limit: int = 5) -> list[str]:
        """
        获取历史内心 OS。
        
        Args:
            game_id: 对局 ID
            player_id: 玩家 ID
            limit: 获取最近的几条记录
            
        Returns:
            历史推理列表
        """
        reasoning_key = RedisKeys.private_memory_reasoning(game_id, player_id)
        redis = await RedisClientManager.get_client()
        
        # 获取最近的 limit 条记录
        # LRANGE key -limit -1
        raw_reasoning = await redis.lrange(reasoning_key, -limit, -1)
        return [r.decode('utf-8') if isinstance(r, bytes) else r for r in raw_reasoning]
