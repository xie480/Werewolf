"""
发言管理器 (SpeechManager) 模块 —— 基于 Redis List 的轮序发言控制。

**Why**: 狼人杀白天讨论阶段的正确游戏流程要求从座位 1 开始，
按照座位编号递增的顺序逐个发言。现有实现将所有存活玩家并发分发任务，
导致所有玩家同时思考并直接投票，破坏了游戏规则。

本模块负责：
1. **初始化发言队列**：进入发言阶段时，按座位号升序构建玩家发言顺序队列。
2. **轮流发言控制**：每次仅允许当前队列头部的玩家发言，其余玩家等待。
3. **发言完成后推进**：当前玩家发言完毕后，自动弹出队列头部并触发下一个玩家。
4. **提前结束检测**：当队列为空（所有玩家已发言完毕）时，满足提前结束条件。

**Redis 数据模型**:
    - Key: ``werewolf:speech_queue:{game_id}`` (List)
    - 内容: 玩家 ID 字符串列表，按发言顺序排列（左端为即将发言的玩家）
    - LPOP 原子弹出头部，RPUSH 进队列尾部

**Why 用 Redis List**:
    1. LPOP/RPUSH 是原子操作，天然适合轮序控制。
    2. 支持多 Worker 场景，不会出现重复弹出或跳过玩家。
    3. 提供 LLEN 快速查询剩余待发言人数。

参考:
    - :doc:`docs/plan/白天行动结算与投票管理器设计`
"""

from __future__ import annotations

import asyncio
# Removed uuid import; using Snowflake IDs
from typing import Dict, List, Optional

import redis.asyncio as aioredis

from ai_werewolf_core.constant.redis_keys import RedisKeys
from ai_werewolf_core.core.event.bus import EventBus
from ai_werewolf_core.core.engine.exceptions import ActionValidationError
from ai_werewolf_core.core.engine.roles.base import BaseRole
from ai_werewolf_core.schemas.enums import (
    ActionType,
    EventType,
    GamePhase,
    Visibility,
    Emotion,
)
from ai_werewolf_core.schemas.models import AgentAction, Event
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.utils.redis_client import RedisClientManager
from ai_werewolf_core.utils.snowflake import get_snowflake
from ai_werewolf_core.utils.time_utils import now_tz

logger = get_logger(__name__)

# 发言队列 TTL (秒) —— 24 小时
SPEECH_QUEUE_TTL_SEC: int = 86400


class SpeechManager:
    """发言管理器 —— 专职管理白天讨论阶段的轮序发言流程。

    核心职责：
    1. **初始化发言队列**：在进入 DAY_DISCUSSION / DAY_PK_DISCUSSION / LAST_WORDS
       阶段时，获取所有存活玩家并按座位号升序排列，构建设在 Redis List 中。
    2. **获取当前发言玩家**：通过 Redis LINDEX(0) 查看当前谁在发言。
    3. **提交发言**：Agent 提交 SPEAK 动作时，校验其是否是当前应该发言的玩家，
       校验通过后发布 SPEECH_EVENT，然后弹出队列头部（已完成发言的玩家），
       并发布 SPEECH_TURN_EVENT 通知下一个玩家。
    4. **查询完成度**：检查队列是否为空（所有存活玩家已发言完毕）。
    5. **跳过死亡/沉默玩家**：若玩家死亡或超时，可强制弹出。

    Attributes:
        game_id: 对局唯一标识。
        event_bus: 事件总线实例。
        _redis: Redis 客户端（懒初始化）。
    """

    def __init__(self, game_id: str, event_bus: EventBus) -> None:
        """初始化发言管理器。

        Args:
            game_id: 对局唯一标识，用于日志追踪和事件路由。
            event_bus: 事件总线实例，用于发布发言事件和发言轮次事件。
        """
        self.game_id: str = game_id
        self.event_bus: EventBus = event_bus

        # Redis 客户端 (懒初始化，共享连接池)
        self._redis: Optional[aioredis.Redis] = None

        self._logger = logger.bind(game_id=self.game_id, module="SpeechManager")

    # ------------------------------------------------------------------
    # Redis 客户端懒初始化
    # ------------------------------------------------------------------

    async def _get_redis(self) -> aioredis.Redis:
        """获取 Redis 异步客户端（共享连接池）。

        Returns:
            共享的 Redis 异步客户端实例。

        Raises:
            RedisUnavailableException: Redis 连接池初始化失败。
        """
        from ai_werewolf_core.utils.redis_seq import RedisUnavailableException

        try:
            return await RedisClientManager.get_client()
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            raise RedisUnavailableException(
                f"SpeechManager 无法获取 Redis 客户端: game_id={self.game_id}"
            ) from e

    # ------------------------------------------------------------------
    # Key 构建
    # ------------------------------------------------------------------

    def _queue_key(self) -> str:
        """构建当前游戏的发言队列 Redis List Key。

        Returns:
            Redis Key 字符串: ``werewolf:speech_queue:{game_id}``
        """
        return RedisKeys.speech_queue(self.game_id)

    # ------------------------------------------------------------------
    # 发言队列管理
    # ------------------------------------------------------------------

    async def init_queue(
        self,
        roles: Dict[str, BaseRole],
        current_round: int,
        phase: GamePhase,
    ) -> None:
        """初始化发言队列：按座位号升序排列所有存活玩家。

        在阶段状态机进入发言阶段时（DAY_DISCUSSION / DAY_PK_DISCUSSION /
        LAST_WORDS）由 Game Engine 调用。

        流程：
        1. 从 roles 中过滤出存活玩家。
        2. 按 seat_number 升序排序。
        3. 将玩家 ID 列表写入 Redis List（先删除旧队列）。
        4. 设置 TTL。
        5. 发布第一条 SPEECH_TURN_EVENT，通知第一个玩家发言。

        Args:
            roles: 当前所有角色的映射 ``player_id → BaseRole``。
            current_round: 当前游戏轮次。
            phase: 当前发言阶段类型。
        """
        # 过滤存活玩家并按座位号升序排序
        alive_players = [
            (pid, role)
            for pid, role in roles.items()
            if role.is_alive
        ]
        alive_players.sort(key=lambda x: x[1].seat_number)

        if not alive_players:
            self._logger.warning(
                "init_queue_no_alive_players",
                round=current_round,
                phase=phase.value,
            )
            return

        player_ids = [pid for pid, _ in alive_players]

        self._logger.info(
            "speech_queue_init",
            round=current_round,
            phase=phase.value,
            queue=player_ids,
        )

        # 写入 Redis List
        redis = await self._get_redis()
        key = self._queue_key()

        # 先删除旧队列，再重新构建
        await redis.delete(key)
        if player_ids:
            await redis.rpush(key, *player_ids)
            await redis.expire(key, SPEECH_QUEUE_TTL_SEC)

        # 发布 SPEECH_TURN_EVENT 通知第一个玩家发言
        await self._publish_speech_turn(player_ids[0], current_round, phase)

    async def get_current_speaker(self) -> Optional[str]:
        """获取当前应该发言的玩家 ID（队列头部，但不移除）。

        Returns:
            当前发言玩家 ID，队列为空时返回 None。
        """
        try:
            redis = await self._get_redis()
            key = self._queue_key()
            result = await redis.lindex(key, 0)
            if result is not None:
                return result.decode() if isinstance(result, bytes) else result
            return None
        except Exception as e:
            self._logger.warning(
                "get_current_speaker_failed",
                error=str(e),
            )
            return None

    async def submit_speech(
        self,
        action: AgentAction,
        roles: Dict[str, BaseRole],
        current_phase: GamePhase,
    ) -> bool:
        """提交并校验发言。

        执行以下校验（按顺序）：
        1. **动作类型校验**：必须是 ``ActionType.SPEAK``。
        2. **发言玩家校验**：玩家必须存在且存活。
        3. **发言顺序校验**：当前提交发言的玩家必须在队列头（是当前轮到的玩家）。

        校验通过后：
        1. 发布 ``SPEECH_EVENT``（包含发言内容）。
        2. 弹出队列头部（玩家发言完毕）。
        3. 若队列非空，发布 ``SPEECH_TURN_EVENT`` 通知下一个玩家。
        4. 若队列为空，记录日志（所有玩家已完成发言）。

        Args:
            action: Agent 提交的发言动作。
            roles: 当前所有角色的映射 ``player_id → BaseRole``。
            current_phase: 当前游戏阶段。

        Returns:
            ``True`` 表示发言已接受。

        Raises:
            ActionValidationError: 发言非法（类型错误 / 玩家不存在或已死亡 /
                当前不是该玩家的轮次）。
        """
        # ── 校验 1: 动作类型 ──
        if action.action_type != ActionType.SPEAK:
            raise ActionValidationError(
                action,
                f"非发言动作: 期望 {ActionType.SPEAK.value}，"
                f"实际 {action.action_type.value}",
            )

        speaker_id = action.actor_id

        # ── 校验 2: 发言玩家存活 ──
        speaker = roles.get(speaker_id)
        if speaker is None:
            raise ActionValidationError(
                action,
                f"玩家 [{speaker_id}] 不存在于当前对局中",
            )
        if not speaker.is_alive:
            raise ActionValidationError(
                action,
                f"玩家 [{speaker_id}] 已死亡，无法发言",
            )

        # ── 校验 3: 发言顺序校验 ──
        current_speaker = await self.get_current_speaker()
        if current_speaker != speaker_id:
            raise ActionValidationError(
                action,
                f"当前不是 [{speaker_id}] 的发言回合，"
                f"当前应发言的玩家为 [{current_speaker or '无'}]",
            )

        # ── 发布 SPEECH_EVENT ──
        speech_content = action.speech_content or action.reason or ""
        speech_event = Event(
            event_id=get_snowflake().next_id(),
            game_id=self.game_id,
            seq_num=0,  # EventBus 自动分配
            event_type=EventType.SPEECH_EVENT,
            visibility=Visibility.PUBLIC,
            target_agents=[],
            timestamp=now_tz(),
            payload={
                "actor_id": speaker_id,
                "content": speech_content,
                "inner_thought": action.inner_thought,
                "emotion": Emotion.NEUTRAL.value,
                "phase": current_phase.value,
                "round": action.round,
            },
        )
        await self.event_bus.publish(speech_event)

        # 同时发布私密事件记录内心OS
        if action.inner_thought:
            inner_thought_event = Event(
                event_id=get_snowflake().next_id(),
                game_id=self.game_id,
                seq_num=0,
                event_type=EventType.PRIVATE_RESOLUTION_EVENT,
                visibility=Visibility.PRIVATE,
                target_agents=[speaker_id],
                timestamp=now_tz(),
                payload={
                    "actor_id": speaker_id,
                    "action_type": ActionType.SPEAK.value,
                    "inner_thought": action.inner_thought,
                    "reason": action.reason,
                    "round": action.round,
                    "phase": current_phase.value,
                },
            )
            await self.event_bus.publish(inner_thought_event)

        self._logger.info(
            "speech_accepted",
            speaker_id=speaker_id,
            phase=current_phase.value,
            content_length=len(speech_content),
        )

        # ── 弹出队列头部 ──
        redis = await self._get_redis()
        key = self._queue_key()
        await redis.lpop(key)

        # ── 检查队列是否为空，并通知下一个玩家 ──
        next_speaker = await self.get_current_speaker()
        if next_speaker is not None:
            # 有下一个玩家，发布 SPEECH_TURN_EVENT
            await self._publish_speech_turn(
                next_speaker,
                action.round,
                current_phase,
            )
            self._logger.info(
                "speech_turn_next",
                next_speaker=next_speaker,
            )
        else:
            self._logger.info(
                "speech_queue_empty",
                phase=current_phase.value,
                round=action.round,
                message="所有玩家已完成发言，等待阶段结束",
            )

        return True

    async def skip_current_speaker(
        self,
        current_round: int,
        phase: GamePhase,
    ) -> Optional[str]:
        """跳过当前发言玩家（强制弹出队列头部）。

        用于超时场景：如果当前玩家在规定时间内未提交发言，
        引擎可以强制跳过该玩家，并触发下一个玩家。

        Args:
            current_round: 当前游戏轮次。
            phase: 当前发言阶段。

        Returns:
            被跳过的玩家 ID，队列为空时返回 None。
        """
        redis = await self._get_redis()
        key = self._queue_key()

        # 先查看当前是谁
        current = await redis.lindex(key, 0)
        if current is None:
            return None

        skipped_id = current.decode() if isinstance(current, bytes) else current

        # 弹出队列头部
        await redis.lpop(key)

        self._logger.warning(
            "speech_skip_current",
            skipped_id=skipped_id,
        )

        # 触发下一个玩家
        next_speaker = await self.get_current_speaker()
        if next_speaker is not None:
            await self._publish_speech_turn(next_speaker, current_round, phase)

        return skipped_id

    async def is_queue_empty(self) -> bool:
        """检查发言队列是否为空（所有玩家已发言完毕）。

        Returns:
            ``True`` 表示队列为空（所有待发言玩家已完成发言）。
        """
        try:
            redis = await self._get_redis()
            key = self._queue_key()
            length = await redis.llen(key)
            return length == 0
        except Exception as e:
            self._logger.warning(
                "check_queue_empty_failed",
                error=str(e),
            )
            return False

    async def get_remaining_count(self) -> int:
        """获取剩余待发言玩家人数。

        Returns:
            剩余未发言的玩家数量。
        """
        try:
            redis = await self._get_redis()
            key = self._queue_key()
            return await redis.llen(key)
        except Exception as e:
            self._logger.warning(
                "get_remaining_count_failed",
                error=str(e),
            )
            return 0

    # ------------------------------------------------------------------
    # 事件发布
    # ------------------------------------------------------------------

    async def _publish_speech_turn(
        self,
        player_id: str,
        current_round: int,
        phase: GamePhase,
    ) -> None:
        """发布 ``SPEECH_TURN_EVENT``，通知某个玩家轮到他发言。

        Args:
            player_id: 被通知发言的玩家 ID。
            current_round: 当前游戏轮次。
            phase: 当前发言阶段。
        """
        turn_event = Event(
            event_id=get_snowflake().next_id(),
            game_id=self.game_id,
            seq_num=0,  # EventBus 自动分配
            event_type=EventType.SPEECH_TURN_EVENT,
            visibility=Visibility.PUBLIC,
            target_agents=[player_id],
            timestamp=now_tz(),
            payload={
                "player_id": player_id,
                "phase": phase.value,
                "round": current_round,
            },
        )
        await self.event_bus.publish(turn_event)

        self._logger.info(
            "speech_turn_published",
            player_id=player_id,
            phase=phase.value,
            round=current_round,
        )

    # ------------------------------------------------------------------
    # 生命周期管理
    # ------------------------------------------------------------------

    async def reset(self) -> None:
        """清空发言队列，重置发言管理器状态。

        在对局结束或重新初始化时调用。
        """
        try:
            redis = await self._get_redis()
            key = self._queue_key()
            await redis.delete(key)
            self._logger.info("speech_queue_reset")
        except Exception as e:
            self._logger.warning(
                "speech_queue_reset_failed",
                error=str(e),
            )
