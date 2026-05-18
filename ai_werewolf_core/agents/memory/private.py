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
        redis = await RedisClientManager.get_client()
        
        # 获取对应的PrivateState
        raw_data = await redis.hget(key, "state")
        if not raw_data:
            raise MemoryNotFoundError(f"Private memory not found for {player_id} in game {game_id}")
            
        try:
            state = PrivateState.model_validate_json(raw_data)
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
        reasoning_key = RedisKeys.private_memory_reasoning(game_id, player_id)
        redis = await RedisClientManager.get_client()
        
        # 清理可能存在的旧反馈数据和推理数据
        await redis.delete(feedbacks_key)
        await redis.delete(reasoning_key)
        
        # 保存基础状态
        await redis.hset(key, "state", state.model_dump_json())
            
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

    async def save_reasoning(self, game_id: str, player_id: str, round_num: int, phase: str, reasoning: str) -> None:
        """
        保存 Agent 的内心 OS，用于后续回合的连贯性。
        
        Args:
            game_id: 对局 ID
            player_id: 玩家 ID
            round_num: 轮次编号
            phase: 当前阶段
            reasoning: 推理内容
        """
        reasoning_key = RedisKeys.private_memory_reasoning(game_id, player_id)
        redis = await RedisClientManager.get_client()
        
        data = json.dumps({"round_num": round_num, "phase": phase, "content": reasoning})
        await redis.rpush(reasoning_key, data)
        logger.debug("内心OS已保存", game_id=game_id, player_id=player_id, round_num=round_num, phase=phase)
        
    async def get_private_round_data(self, game_id: str, player_id: str) -> dict[int, dict]:
        """
        获取按轮次聚合的私有事实和推理。
        
        Args:
            game_id: 对局 ID
            player_id: 玩家 ID
            
        Returns:
            按轮次聚合的字典: {round_num: {"private_facts": [PrivateEventLog], "reasoning": [str]}}
        """
        feedbacks_key = RedisKeys.private_memory_feedbacks(game_id, player_id)
        reasoning_key = RedisKeys.private_memory_reasoning(game_id, player_id)
        redis = await RedisClientManager.get_client()
        
        raw_feedbacks = await redis.lrange(feedbacks_key, 0, -1)
        raw_reasoning = await redis.lrange(reasoning_key, 0, -1)
        
        round_data = {}
        
        for f in raw_feedbacks:
            try:
                log = PrivateEventLog.model_validate_json(f)
                r_num = log.round_num
                if r_num not in round_data:
                    round_data[r_num] = {"private_facts": [], "reasoning": []}
                round_data[r_num]["private_facts"].append(log)
            except Exception as e:
                logger.error("解析私有事实失败", error=str(e))
                
        for r in raw_reasoning:
            try:
                r_str = r.decode('utf-8') if isinstance(r, bytes) else r
                data = json.loads(r_str)
                # 兼容旧数据
                if isinstance(data, dict) and "round_num" in data and "content" in data:
                    r_num = data["round_num"]
                    content = data["content"]
                    if "phase" in data:
                        content = f"[{data['phase']}] {content}"
                else:
                    r_num = 1
                    content = r_str
                    
                if r_num not in round_data:
                    round_data[r_num] = {"private_facts": [], "reasoning": []}
                round_data[r_num]["reasoning"].append(content)
            except Exception as e:
                logger.error("解析推理记录失败", error=str(e))
                
        return round_data

    async def save_suspect_list(self, game_id: str, player_id: str, suspect_list: dict[str, float]) -> None:
        """
        保存 Agent 的嫌疑人列表。
        
        Args:
            game_id: 对局 ID
            player_id: 玩家 ID
            suspect_list: 嫌疑人列表字典
        """
        if not suspect_list:
            return
            
        suspect_key = RedisKeys.private_memory_suspect_list(game_id, player_id)
        redis = await RedisClientManager.get_client()
        
        # 将 float 转换为字符串存储
        mapping = {k: str(v) for k, v in suspect_list.items()}
        await redis.hset(suspect_key, mapping=mapping)
        logger.debug("嫌疑人列表已保存", game_id=game_id, player_id=player_id)
        
    async def get_last_suspect_list(self, game_id: str, player_id: str) -> dict[str, float]:
        """
        获取上一次的嫌疑人列表。
        
        Args:
            game_id: 对局 ID
            player_id: 玩家 ID
            
        Returns:
            嫌疑人列表字典
        """
        suspect_key = RedisKeys.private_memory_suspect_list(game_id, player_id)
        redis = await RedisClientManager.get_client()
        
        raw_data = await redis.hgetall(suspect_key)
        if not raw_data:
            return {}
            
        return {
            k.decode('utf-8') if isinstance(k, bytes) else k: float(v)
            for k, v in raw_data.items()
        }
