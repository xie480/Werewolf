"""
狼人投票管理器 (WolfVoteManager) 模块 —— 基于 Redis Hash 的原子投票管理。

**Why**: 狼人夜间投票（NIGHT_WOLF_ACT）与白天放逐投票有本质区别：
1. 只有存活狼人有投票权，不是全体玩家。
2. 投票类型是 WOLF_KILL 而非 VOTE。
3. 投票收集完毕后需要立即触发结算+阶段推进，不能等待定时器。
4. 需要记录完整的时间戳审计链（5 个关键时间点）。

本模块遵循单一职责原则 (SRP)，从 ActionResolver 中拆分出狼人投票逻辑，
与 VoteManager（白天投票）并行存在，职责分明。

**Redis 数据模型**:
    - Key: ``werewolf:wolf_vote:{game_id}:{round}`` (Hash)
    - Field: ``voter_id`` (投票人)
    - Value: ``target_id`` (被投人，空字符串表示弃权)
    - TTL: 24 小时 (86400 秒)
    - 原子性保证: Lua 脚本 ``wolf_vote_submit`` + ``wolf_vote_settle``

**审计时间戳字段 (meta:*)**:
    - ``meta:status``: "OPEN" | "CLOSED" | "SETTLED"
    - ``meta:opened_at``: 投票回合开启时间
    - ``meta:vote_start_at``: 首张狼人选票提交时间
    - ``meta:vote_end_at``: 全部选票收集完毕时间
    - ``meta:settled_at``: 结算完成时间

**规则硬编码**: 所有投票计票逻辑、平票判断均在 Python 中硬编码，
不依赖 LLM 判定，确保确定性。

参考:
- :doc:`../../plans/狼人并行投票与原子结算设计.md`
- :doc:`./vote_manager.py`
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import Counter
from typing import Dict, List, Optional, Set

import redis.asyncio as aioredis

from ai_werewolf_core.constant.redis_keys import RedisKeys
from ai_werewolf_core.core.engine.exceptions import ActionValidationError
from ai_werewolf_core.core.engine.player_manager import PlayerStatusManager
from ai_werewolf_core.core.engine.roles.base import BaseRole
from ai_werewolf_core.core.event.bus import EventBus
from ai_werewolf_core.schemas.enums import (
    ActionType,
    EventType,
    Faction,
    GamePhase,
    Role,
    Visibility,
)
from ai_werewolf_core.schemas.models import AgentAction, Event
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.utils.redis_client import RedisClientManager
from ai_werewolf_core.utils.redis_lua_loader import LuaScriptManager
from ai_werewolf_core.utils.redis_seq import RedisUnavailableException
from ai_werewolf_core.utils.time_utils import now_tz

logger = get_logger(__name__)

# ============================================================================
# 常量定义
# ============================================================================

# 投票 Hash 的 TTL (秒)
WOLF_VOTE_TTL_SEC: int = 86400  # 24 小时

# Redis 操作重试配置
RETRY_COUNT: int = 3
RETRY_DELAY_SEC: float = 0.1


# ------------------------------------------------------------------
# 狼人投票结算结果数据类
# ------------------------------------------------------------------


class WolfVoteResolveResult:
    """狼人投票结算结果 —— 封装一轮狼人投票结算后的完整信息。

    **Why**: 将结算结果封装为独立的数据类，便于 Game Engine 和下游模块
    统一消费。平票和非平票场景使用同一数据结构。

    Attributes:
        is_tie: 是否为平票（最高票有多人并列）。
        wolf_target: 被刀目标 player_id（平票或无人投票时为 None）。
        vote_details: ``voter_id → target_id`` 的选票明细映射。
        vote_count: ``target_id → 得票数`` 的计票结果映射。
        total_voters: 参与投票的狼人总数。
        audit_timestamps: 审计时间戳字典。
    """

    def __init__(
        self,
        is_tie: bool,
        wolf_target: Optional[str],
        vote_details: Dict[str, str],
        vote_count: Dict[str, int],
        total_voters: int,
    ) -> None:
        self.is_tie = is_tie
        self.wolf_target = wolf_target
        self.vote_details = vote_details
        self.vote_count = vote_count
        self.total_voters = total_voters


# ------------------------------------------------------------------
# 狼人投票管理器
# ------------------------------------------------------------------


class WolfVoteManager:
    """狼人投票管理器 —— 专职处理狼人夜间投票。

    作为 Game Engine 与狼人投票逻辑之间的中间层，负责：
    1. **开启投票回合**：初始化 Redis Hash，设置 meta:status = "OPEN"。
    2. **接收与校验选票**：验证投票人存活、身份、阶段合法性。
    3. **投票完成检测**：判断所有存活狼人是否已提交选票。
    4. **结算投票**：统计票数，确定刀人目标，平票时返回 None。

    **与 VoteManager（白天投票）的区别**:
    - 只允许 WOLF_KILL 动作类型，不允许 VOTE。
    - 只允许存活狼人投票，不是全体存活玩家。
    - 投票完成后立即触发结算，提前结束本阶段。
    - 记录完整的时间戳审计链。

    **Redis 无状态设计**:
        投票数据存储在 Redis Hash 中，不在实例上保存任何投票状态。
        多 Worker 进程共享同一 Hash，Lua 脚本保证原子操作。

    **CAS 防重设计**:
        使用 Lua 脚本 ``wolf_vote_settle.lua`` 检查 meta:status，
        确保结算操作仅执行一次。多 Worker 并发调用时，先调用者成功，
        后续调用者收到 "ALREADY_SETTLED" 跳过。

    Attributes:
        game_id: 对局唯一标识。
        event_bus: 事件总线实例。
        _current_round: 当前投票轮次（从外部传入）。
    """

    def __init__(self, game_id: str, event_bus: EventBus) -> None:
        """初始化狼人投票管理器。

        Args:
            game_id: 对局唯一标识，用于日志追踪和事件路由。
            event_bus: 事件总线实例，用于发布投票事件和死亡事件。
        """
        self.game_id: str = game_id
        self.event_bus: EventBus = event_bus

        # Redis 客户端（懒初始化，共享连接池）
        self._redis: Optional[aioredis.Redis] = None

        # 当前投票控制状态（内存管理——非游戏状态，而是"控制面"参数）
        self._current_round: int = 0
        """当前投票轮次（由 begin_vote 传入）。"""

        self._player_status: PlayerStatusManager = PlayerStatusManager()
        """玩家状态缓存管理器，用于同步更新 Redis BitMap。"""

        self._logger = logger.bind(game_id=self.game_id, module="WolfVoteManager")

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
        try:
            return await RedisClientManager.get_client()
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            raise RedisUnavailableException(
                f"WolfVoteManager 无法获取 Redis 客户端: game_id={self.game_id}"
            ) from e

    # ------------------------------------------------------------------
    # Key 构建
    # ------------------------------------------------------------------

    def _vote_key(self) -> str:
        """构建当前轮次的狼人投票 Redis Hash Key。

        Returns:
            Redis Key 字符串: ``werewolf:wolf_vote:{game_id}:{round}``
        """
        return RedisKeys.wolf_vote_hash(self.game_id, self._current_round)

    # ------------------------------------------------------------------
    # Redis 操作（带重试）
    # ------------------------------------------------------------------

    async def _redis_hgetall(self) -> Dict[str, str]:
        """执行 HGETALL 操作（带重试）。

        Returns:
            ``voter_id → target_id`` 的全量选票映射（包含 meta:* 字段）。

        Raises:
            RedisUnavailableException: 重试耗尽后 Redis 仍不可用。
        """
        key = self._vote_key()
        redis = await self._get_redis()

        for attempt in range(1, RETRY_COUNT + 1):
            try:
                result = await redis.hgetall(key)
                return result or {}
            except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
                self._logger.warning(
                    "Redis HGETALL 连接异常，重试中",
                    key=key,
                    attempt=attempt,
                    max_retries=RETRY_COUNT,
                    error=str(e),
                )
                if attempt < RETRY_COUNT:
                    await asyncio.sleep(RETRY_DELAY_SEC * attempt)
                else:
                    raise RedisUnavailableException(
                        f"狼人投票查询失败: game_id={self.game_id}, "
                        f"重试 {RETRY_COUNT} 次后 Redis 仍不可用"
                    ) from e

    async def _redis_delete(self) -> None:
        """删除当前轮次的投票 Hash（清空选票）。"""
        key = self._vote_key()
        try:
            redis = await self._get_redis()
            await redis.delete(key)
            self._logger.debug("wolf_vote_hash_deleted", key=key)
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            self._logger.warning(
                "删除狼人投票 Hash 失败",
                key=key,
                error=str(e),
            )

    async def _redis_hset_meta(self, field: str, value: str) -> None:
        """设置 meta 字段（带重试）。

        Args:
            field: meta 字段名（如 "meta:status"）。
            value: 字段值。
        """
        key = self._vote_key()
        redis = await self._get_redis()

        for attempt in range(1, RETRY_COUNT + 1):
            try:
                await redis.hset(key, field, value)
                return
            except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
                self._logger.warning(
                    "Redis HSET meta 连接异常，重试中",
                    key=key,
                    field=field,
                    attempt=attempt,
                    error=str(e),
                )
                if attempt < RETRY_COUNT:
                    await asyncio.sleep(RETRY_DELAY_SEC * attempt)
                else:
                    raise RedisUnavailableException(
                        f"狼人投票 meta 写入失败: game_id={self.game_id}"
                    ) from e

    # ------------------------------------------------------------------
    # 审计时间戳记录
    # ------------------------------------------------------------------

    async def _record_audit_timestamp(self, label: str) -> None:
        """记录审计时间戳到 Redis Hash。

        时间戳以 ISO 格式写入 ``audit:{label}`` 字段。

        Args:
            label: 时间戳标签（如 "phase_entered", "vote_completed"）。
        """
        try:
            redis = await self._get_redis()
            await redis.hset(
                self._vote_key(),
                f"audit:{label}",
                now_tz().isoformat(),
            )
        except Exception as e:
            self._logger.warning(
                "审计时间戳写入失败",
                label=label,
                error=str(e),
            )

    # ------------------------------------------------------------------
    # 投票回合管理
    # ------------------------------------------------------------------

    async def begin_vote(self, round_num: int) -> None:
        """开启新一轮狼人投票回合。

        **Why**: 每轮狼人投票开始前必须调用此方法，设置当前轮次以构建
        正确的 Redis Key，确保不同轮次的选票不会互相污染。
        同时在 Redis 中初始化 meta:status 和时间戳。

        通常在状态机进入 ``NIGHT_WOLF_ACT`` 阶段时由 Game Engine 调用。

        Args:
            round_num: 当前游戏轮次。
        """
        self._current_round = round_num
        key = self._vote_key()
        timestamp = now_tz().isoformat()

        try:
            redis = await self._get_redis()
            await redis.hset(key, mapping={
                "meta:status": "OPEN",
                "meta:opened_at": timestamp,
            })
            await redis.expire(key, WOLF_VOTE_TTL_SEC)
            self._logger.info(
                "wolf_vote_begin",
                round=round_num,
                key=key,
            )
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            raise RedisUnavailableException(
                f"无法开启狼人投票回合: game_id={self.game_id}, round={round_num}"
            ) from e

    # ------------------------------------------------------------------
    # 投票提交与校验
    # ------------------------------------------------------------------

    async def submit_vote(
        self,
        action: AgentAction,
        roles: Dict[str, BaseRole],
        current_phase: GamePhase,
    ) -> bool:
        """提交并校验狼人选票。

        执行以下校验（按顺序）：
        1. **动作类型校验**：必须是 ``ActionType.WOLF_KILL``。
        2. **投票人存活校验**：投票人必须存在且存活。
        3. **投票人身份校验**：投票人必须是狼人阵营（WEREWOLF）。
        4. **Lua 原子写入**: 通过 ``wolf_vote_submit.lua`` 原子写入，
           同时检查投票回合状态。

        **投票覆盖策略**: 同一狼人多次提交以最后一次为准（与白天投票一致）。

        **Why (Lua 脚本而非普通 HSET)**: 需要原子检测 meta:status，
        防止投票回合关闭后仍有新投票写入。多 Worker 并发时普通 HSET
        无法原子完成"检查状态 → 写入选票"两步。

        Args:
            action: Agent 提交的投票动作。
            roles: 当前所有角色的映射 ``player_id → BaseRole``。
            current_phase: 当前游戏阶段。

        Returns:
            ``True`` 表示选票已记录。

        Raises:
            ActionValidationError: 选票非法。
            RedisUnavailableException: Redis 不可用。
        """
        # ── 校验 1: 动作类型 ──
        action_type_str = action.action_type.value if hasattr(action.action_type, 'value') else str(action.action_type)
        if action.action_type not in (ActionType.WOLF_KILL, ActionType.PASS):
            raise ActionValidationError(
                action,
                f"非狼人投票动作: 期望 {ActionType.WOLF_KILL.value} 或 {ActionType.PASS.value}，"
                f"实际 {action_type_str}",
            )

        # ── 校验 2: 投票人存活 ──
        voter_id = action.actor_id
        voter = roles.get(voter_id)
        if voter is None:
            raise ActionValidationError(
                action,
                f"投票人 [{voter_id}] 不存在于当前对局中",
            )
        if not voter.is_alive:
            raise ActionValidationError(
                action,
                f"投票人 [{voter_id}] 已死亡，无法参与狼人投票",
            )

        # ── 校验 3: 投票人身份 ──
        if voter.faction != Faction.WEREWOLF:
            raise ActionValidationError(
                action,
                f"玩家 [{voter_id}] 不是狼人阵营，无权参与狼人投票",
            )

        # ── 校验 4: 目标有效性 ──
        if action.action_type == ActionType.WOLF_KILL and action.target_id is not None:
            target_role = roles.get(action.target_id)
            if target_role is None:
                raise ActionValidationError(
                    action,
                    f"刀人目标 [{action.target_id}] 不存在于当前对局中",
                )

        # ── 校验 5: 不能刀自己 ──
        if action.action_type == ActionType.WOLF_KILL and action.target_id == voter_id:
            raise ActionValidationError(
                action,
                "狼人不能刀自己",
            )

        # ── 通过 Lua 脚本原子写入 Redis ──
        target_value = action.target_id or ""  # 空字符串表示弃权
        if action.action_type == ActionType.PASS:
            target_value = ""
            
        timestamp = now_tz().isoformat()

        try:
            result = await LuaScriptManager.evalsha(
                "wolf_vote_submit",
                keys=[self._vote_key()],
                args=[voter_id, target_value, str(WOLF_VOTE_TTL_SEC), timestamp],
            )
            status = result[0]
            previous_target = result[2]
        except RedisUnavailableException:
            self._logger.error(
                "狼人投票写入 Redis 失败",
                voter_id=voter_id,
                target_id=action.target_id,
                exc_info=True,
            )
            raise

        # ── 处理 Lua 返回状态 ──
        if status == "CLOSED":
            raise ActionValidationError(
                action,
                "狼人投票回合已关闭，无法提交选票（可能已进入结算阶段）",
            )
        elif status != "OK":
            raise ActionValidationError(
                action,
                f"狼人投票提交失败: Lua 返回状态 [{status}]",
            )

        # ── 日志 ──
        if previous_target:
            self._logger.info(
                "wolf_vote_updated",
                voter_id=voter_id,
                new_target=action.target_id or "(弃权)",
                previous_target=previous_target or "(弃权)",
            )
        else:
            self._logger.info(
                "wolf_vote_submitted",
                voter_id=voter_id,
                target_id=action.target_id or "(弃权)",
            )

        # ── 发布私密行动事件（用于前端透视和上帝视角展示） ──
        event = Event(
            event_id=str(uuid.uuid4()),
            game_id=self.game_id,
            seq_num=0,
            event_type=EventType.PRIVATE_RESOLUTION_EVENT,
            visibility=Visibility.PRIVATE,
            target_agents=[action.actor_id],
            timestamp=now_tz(),
            payload={
                "actor_id": action.actor_id,
                "action_type": action.action_type.value if hasattr(action.action_type, 'value') else str(action.action_type),
                "target_id": action.target_id,
                "inner_thought": action.inner_thought,
                "reason": action.reason,
                "round": action.round,
                "phase": current_phase.value,
            },
        )
        await self.event_bus.publish(event)

        return True

    # ------------------------------------------------------------------
    # 投票完成度检测
    # ------------------------------------------------------------------

    async def is_vote_complete(self, roles: Dict[str, BaseRole]) -> bool:
        """检测是否所有存活狼人已完成投票。

        通过比较 Redis 中已投票的非 meta 字段数量与存活狼人数量来判断。

        **Why (忽略 meta: 字段)**: Redis Hash 中额外存储了 ``meta:status``
        等元数据字段，HLEN 会把这些也算进去。因此必须手动过滤。

        Args:
            roles: 角色映射。

        Returns:
            True 表示所有存活狼人已提交选票。
        """
        # 获取所有存活狼人
        alive_wolves = [
            pid for pid, role in roles.items()
            if role.is_alive and role.faction == Faction.WEREWOLF
        ]
        if not alive_wolves:
            return False

        # 获取已投票的狼人数量（排除 meta:* 字段）
        all_votes = await self._redis_hgetall()
        voted_wolves = [
            field for field in all_votes
            if not field.startswith("meta:") and not field.startswith("audit:")
        ]

        return len(voted_wolves) >= len(alive_wolves)

    async def get_voter_count(self) -> int:
        """获取已投票的狼人数量（排除 meta 和 audit 字段）。

        Returns:
            当前回合已投票的狼人数量。
        """
        try:
            all_votes = await self._redis_hgetall()
            return sum(
                1 for field in all_votes
                if not field.startswith("meta:") and not field.startswith("audit:")
            )
        except RedisUnavailableException:
            self._logger.warning("get_voter_count 失败：Redis 不可用")
            return 0

    # ------------------------------------------------------------------
    # 投票结算
    # ------------------------------------------------------------------

    async def resolve_vote(
        self,
        roles: Dict[str, BaseRole],
        current_round: int = 0,
    ) -> WolfVoteResolveResult:
        """结算狼人投票回合，统计票数并确定刀人目标。

        使用 Lua 脚本 ``wolf_vote_settle.lua`` 原子执行结算：
        1. 检查 meta:status 防止重复结算（CAS 语义）。
        2. 关闭投票回合（meta:status = "CLOSED"）。
        3. 收集全量选票，统计得票数。

        结算逻辑（Python 侧硬编码）：
        1. 统计每个目标的得票数。
        2. 确定最高票：
           - 唯一最高票 → 成为刀人目标。
           - 平票（最高票多人并列）→ 无人被刀（平安夜）。
           - 无人得票（全弃权）→ 无人被刀。
        3. 更新 redis meta:status = "SETTLED" 并记录结算时间戳。

        Args:
            roles: 当前所有角色的映射 ``player_id → BaseRole``。
            current_round: 当前游戏轮次。

        Returns:
            :class:`WolfVoteResolveResult` 包含平票状态、刀人目标等。

        Raises:
            RedisUnavailableException: Redis 不可用。
        """
        timestamp = now_tz().isoformat()
        self._logger.info(
            "wolf_vote_resolve_start",
            round=self._current_round,
        )

        # ── Step 1: 通过 Lua 脚本原子结算（关闭投票 + 收集选票） ──
        try:
            settle_result = await LuaScriptManager.evalsha(
                "wolf_vote_settle",
                keys=[self._vote_key()],
                args=[str(WOLF_VOTE_TTL_SEC), timestamp],
            )
            settle_status = settle_result[0]
        except RedisUnavailableException as e:
            self._logger.error(
                "狼人投票结算失败：Redis 不可用",
                round=self._current_round,
                error=str(e),
            )
            raise

        # ── 处理 CAS 结果 ──
        if settle_status == "ALREADY_SETTLED":
            self._logger.warning(
                "wolf_vote_already_settled",
                round=self._current_round,
            )
            # 如果已结算，从 Redis 拉取当前数据
            all_votes = await self._extract_votes_from_redis()
        else:
            # 解析 Lua 返回的 JSON
            vote_count_raw = settle_result[1]
            vote_details_raw = settle_result[2]
            all_votes = json.loads(vote_details_raw) if vote_details_raw else {}

        # ── Step 2: 统计选票（Python 侧硬编码，保证确定性） ──
        vote_targets = [target for target in all_votes.values() if target]
        vote_count: Dict[str, int] = dict(Counter(vote_targets))
        total_voters = len(all_votes)

        # ── Step 3: 确定最高票 ──
        wolf_target: Optional[str] = None
        is_tie = False

        if vote_count:
            max_votes = max(vote_count.values())
            highest_voted = [
                player_id for player_id, count in vote_count.items()
                if count == max_votes
            ]
            is_tie = len(highest_voted) > 1

            if not is_tie and len(highest_voted) == 1:
                wolf_target = highest_voted[0]

        self._logger.info(
            "wolf_vote_tally",
            vote_count=vote_count,
            wolf_target=wolf_target,
            is_tie=is_tie,
            total_voters=total_voters,
        )

        # ── Step 4: 如果平票，无人被刀 ──
        if is_tie:
            wolf_target = None

        # ── Step 5: 更新 meta:status 为 SETTLED ──
        try:
            await self._redis_hset_meta("meta:status", "SETTLED")
            await self._redis_hset_meta("meta:settled_at", timestamp)
        except Exception as e:
            self._logger.warning(
                "结算状态更新失败（不影响结算结果）",
                error=str(e),
            )

        # ── Step 6: 发布投票结算事件 ──
        await self._publish_wolf_vote_event(
            is_tie=is_tie,
            wolf_target=wolf_target,
            vote_count=vote_count,
            total_voters=total_voters,
        )

        # ── Step 7: 如果存在唯一目标，执行死亡结算 ──
        if wolf_target is not None:
            await self._execute_wolf_kill(wolf_target, roles, vote_count, current_round)

        result = WolfVoteResolveResult(
            is_tie=is_tie,
            wolf_target=wolf_target,
            vote_details=dict(all_votes),
            vote_count=vote_count,
            total_voters=total_voters,
        )

        self._logger.info(
            "wolf_vote_resolve_complete",
            is_tie=is_tie,
            wolf_target=wolf_target,
        )

        return result

    async def _extract_votes_from_redis(self) -> Dict[str, str]:
        """从 Redis 提取选票（排除 meta:* 和 audit:* 字段）。

        Returns:
            ``voter_id → target_id`` 的选票映射。
        """
        all_fields = await self._redis_hgetall()
        return {
            field: value
            for field, value in all_fields.items()
            if not field.startswith("meta:") and not field.startswith("audit:")
        }

    # ------------------------------------------------------------------
    # 死亡执行
    # ------------------------------------------------------------------

    async def _execute_wolf_kill(
        self,
        target_id: str,
        roles: Dict[str, BaseRole],
        vote_count: Dict[str, int],
        current_round: int,
    ) -> None:
        """执行狼人刀人死亡结算。

        调用目标角色的 ``die()`` 方法并发布 ``PLAYER_DEATH`` 事件。

        **Why (即时执行而非延迟结算)**: 狼人投票阶段女巫尚未参与，
        此时执行死亡是"预期死亡"（pending death）。真正的最终结算
        在 NIGHT_RESOLVE 阶段由 ActionResolver 统一完成：
        女巫救活 → 从死亡名单移除，女巫毒杀 → 加入死亡名单。

        因此此处**不**调用 ``role.die()``，而是更新 ActionResolver
        的 ``pending_deaths`` 草稿，让 NIGHT_RESOLVE 统一结算。

        Args:
            target_id: 被刀目标玩家 ID。
            roles: 角色映射。
            vote_count: 投票统计（用于事件 payload）。
            current_round: 当前轮次。

        Raises:
            ActionValidationError: 目标角色不存在。
        """
        target_role = roles.get(target_id)
        if target_role is None:
            raise ActionValidationError(
                AgentAction(
                    action_type=ActionType.WOLF_KILL,
                    actor_id="SYSTEM",
                    target_id=target_id,
                    phase=GamePhase.NIGHT_RESOLVE,
                    round=current_round,
                    reason="狼人投票结算",
                ),
                f"狼人刀人目标 [{target_id}] 不存在于当前对局中",
            )

        if not target_role.is_alive:
            self._logger.warning(
                "wolf_kill_target_already_dead",
                target_id=target_id,
            )
            return

        # 发布狼人刀人事件（用于前端展示和审计）
        # 注意：此处不实际修改 is_alive，真正的死亡结算在 NIGHT_RESOLVE 执行
        kill_event = Event(
            event_id=str(uuid.uuid4()),
            game_id=self.game_id,
            seq_num=0,
            event_type=EventType.PLAYER_DEATH,
            visibility=Visibility.PUBLIC,
            target_agents=[target_id],
            timestamp=now_tz(),
            payload={
                "player_id": target_id,
                "death_reason": "WOLF_KILL_PENDING",
                "role_type": target_role.role_type.value,
                "faction": target_role.faction.value,
                "votes_received": vote_count.get(target_id, 0),
                "round": current_round,
                "is_pending": True,
            },
        )
        await self.event_bus.publish(kill_event)

    # ------------------------------------------------------------------
    # 事件发布
    # ------------------------------------------------------------------

    async def _publish_wolf_vote_event(
        self,
        is_tie: bool,
        wolf_target: Optional[str],
        vote_count: Dict[str, int],
        total_voters: int,
    ) -> None:
        """发布狼人投票结算事件。

        Args:
            is_tie: 是否为平票。
            wolf_target: 刀人目标（平票时为 None）。
            vote_count: 计票统计。
            total_voters: 总投票人数。
        """
        event = Event(
            event_id=str(uuid.uuid4()),
            game_id=self.game_id,
            seq_num=0,
            event_type=EventType.VOTE_EVENT,
            visibility=Visibility.PUBLIC,
            target_agents=[],
            timestamp=now_tz(),
            payload={
                "announcement_type": "wolf_vote_result",
                "vote_type": "WOLF_KILL",
                "is_tie": is_tie,
                "wolf_target": wolf_target,
                "vote_count": vote_count,
                "total_voters": total_voters,
                "round": self._current_round,
                "peaceful_night": wolf_target is None,
            },
        )
        await self.event_bus.publish(event)

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    async def get_current_votes(self) -> Dict[str, str]:
        """获取当前选票的只读副本（排除 meta 和 audit 字段）。

        Returns:
            ``voter_id → target_id`` 的映射副本。
        """
        try:
            return await self._extract_votes_from_redis()
        except RedisUnavailableException:
            self._logger.warning("get_current_votes 失败：Redis 不可用")
            return {}

    async def get_audit_timestamps(self) -> Dict[str, str]:
        """获取审计时间戳。

        Returns:
            时间戳标签 → ISO 时间字符串 的映射。
        """
        try:
            all_fields = await self._redis_hgetall()
            audit_fields = {}
            for field, value in all_fields.items():
                if field.startswith("audit:") or field.startswith("meta:opened_at") or \
                   field.startswith("meta:vote_start_at") or field.startswith("meta:vote_end_at") or \
                   field.startswith("meta:settled_at"):
                    audit_fields[field] = value
            return audit_fields
        except RedisUnavailableException:
            return {}

    # ------------------------------------------------------------------
    # 生命周期管理
    # ------------------------------------------------------------------

    async def reset(self) -> None:
        """完全重置狼人投票管理器状态。

        在对局结束或重新开始时调用，清除 Redis 中的选票数据
        和内存中的控制参数，确保跨对局的状态隔离。
        """
        self._current_round = 0

        # 清除当前轮次的 Redis 选票数据
        await self._redis_delete()

        self._logger.info("wolf_vote_manager_reset")
