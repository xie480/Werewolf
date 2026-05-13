"""
投票管理器 (VoteManager) 模块 —— 基于 Redis Hash 的无状态投票管理。

**Why**: 狼人杀白天投票（放逐投票、PK 投票）是群体性的即时结算行为，
与夜晚的串行暂存-统一结算模式有本质区别。本模块遵循单一职责原则 (SRP)，
从 GameEngine 主流程中拆分出专门的投票管理逻辑：

1. **选票收集与校验**：记录投票意图，校验投票人存活状态、阶段合法性、
   以及 PK 阶段的候选人限制。
2. **计票与平票处理**：统计最高票，若出现平票则返回平票名单供引擎触发 PK 流程。
3. **即时结算**：若产生唯一最高票，直接调用目标角色的 ``die()`` 方法，
   并通过 EventBus 发布 ``PLAYER_DEATH`` 事件。

**Redis 数据模型**:
    - Key: ``werewolf:vote:{game_id}:{round}`` (Hash)
    - Field: ``voter_id`` (投票人)
    - Value: ``target_id`` (被投人，空字符串表示弃权)
    - TTL: 24 小时 (86400 秒)
    - 原子性保证: HSET 天然支持覆盖更新，多 Worker 并发无竞态

**规则硬编码**: 所有投票平票逻辑、票数统计均在 Python 代码中硬编码，
不依赖 LLM 判定，确保游戏逻辑的确定性和可审计性。

参考:
- :doc:`docs/plan/白天行动结算与投票管理器设计`
- :doc:`docs/plan/Redis缓存架构优化方案`
- :doc:`docs/agent.md`
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from typing import Dict, List, Optional

import redis.asyncio as aioredis

from ai_werewolf_core.constant.redis_keys import RedisKeys
from ai_werewolf_core.core.event.bus import EventBus
from ai_werewolf_core.core.engine.exceptions import ActionValidationError
from ai_werewolf_core.core.engine.roles.base import BaseRole
from ai_werewolf_core.schemas.enums import ActionType, EventType, GamePhase, Visibility
from ai_werewolf_core.schemas.models import AgentAction, Event
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.core.engine.player_manager import PlayerStatusManager
from ai_werewolf_core.utils.redis_client import RedisClientManager
from ai_werewolf_core.utils.redis_lua_loader import LuaScriptManager
from ai_werewolf_core.utils.redis_seq import RedisUnavailableException
from ai_werewolf_core.utils.time_utils import now_tz

logger = get_logger(__name__)

# ============================================================================
# 常量定义
# ============================================================================

# 投票 Hash 的 TTL (秒)
VOTE_TTL_SEC: int = 86400  # 24 小时

# Redis 操作重试配置
RETRY_COUNT: int = 3
RETRY_DELAY_SEC: float = 0.1


# ------------------------------------------------------------------
# 投票结算结果数据类
# ------------------------------------------------------------------


class VoteResolveResult:
    """投票结算结果 —— 封装一轮投票结算后的完整信息。

    **Why**: 将结算结果封装为独立的数据类，便于 Game Engine 和下游模块
    统一消费。平票和非平票场景使用同一数据结构，引擎根据 ``is_tie``
    字段决定是否进入 PK 流程。

    Attributes:
        is_tie: 是否为平票（最高票有多人并列）。
        highest_voted: 最高票玩家 ID 列表（唯一最高票时长度为 1，
            平票时包含所有并列玩家）。
        vote_details: ``voter_id → target_id`` 的选票明细映射。
        vote_count: ``target_id → 得票数`` 的计票结果映射。
        total_voters: 参与投票的总人数。
    """

    def __init__(
        self,
        is_tie: bool,
        highest_voted: List[str],
        vote_details: Dict[str, str],
        vote_count: Dict[str, int],
        total_voters: int,
    ) -> None:
        self.is_tie = is_tie
        self.highest_voted = highest_voted
        self.vote_details = vote_details
        self.vote_count = vote_count
        self.total_voters = total_voters

    @property
    def is_unanimous(self) -> bool:
        """是否全票通过（仅一人得票）。"""
        return not self.is_tie and len(self.highest_voted) == 1

    @property
    def sole_voted_out(self) -> Optional[str]:
        """若为唯一最高票，返回被放逐玩家 ID；否则返回 None。"""
        if not self.is_tie and len(self.highest_voted) == 1:
            return self.highest_voted[0]
        return None


# ------------------------------------------------------------------
# 投票管理器
# ------------------------------------------------------------------


class VoteManager:
    """投票管理器 —— 专职处理群体性的投票行为。

    作为 Game Engine 与投票逻辑之间的中间层，负责：
    1. **开启投票回合**：设置可选的 PK 候选人名单。
    2. **接收与校验选票**：验证投票人存活、阶段合法性、PK 候选人限制。
    3. **结算投票**：统计票数，若存在唯一最高票则执行即时死亡结算，
       若平票则返回平票名单供引擎处理。

    **Why (即时结算而非延迟结算)**: 白天投票的结果立即可知且不可逆转，
    不存在像夜晚那样"狼刀 → 女巫救 → 女巫毒"的因果链反转。
    因此采用即时结算模式，简化设计并降低状态管理复杂度。

    **Redis 无状态设计**:
        投票数据存储在 Redis Hash 中，不在实例上保存任何投票状态。
        多 Worker 进程共享同一 Hash，HSET 天然支持并发覆盖更新。

    Attributes:
        game_id: 对局唯一标识。
        event_bus: 事件总线实例。
        pk_candidates: 当前 PK 候选人名单（None 表示普通投票，无候选人限制）。
        _current_round: 当前投票轮次（从外部传入）。
    """

    def __init__(self, game_id: str, event_bus: EventBus) -> None:
        """初始化投票管理器。

        Args:
            game_id: 对局唯一标识，用于日志追踪和事件路由。
            event_bus: 事件总线实例，用于发布投票事件和死亡事件。
        """
        self.game_id: str = game_id
        self.event_bus: EventBus = event_bus

        # Redis 客户端 (懒初始化，共享连接池)
        self._redis: Optional[aioredis.Redis] = None

        # 当前投票控制状态 (内存管理——非游戏状态，而是"控制面"参数)
        self.pk_candidates: Optional[List[str]] = None
        """当前 PK 候选人名单。None 表示普通投票（无候选人限制）。"""

        self._current_round: int = 0
        """当前投票轮次（由 begin_vote 传入）。"""

        self._player_status: PlayerStatusManager = PlayerStatusManager()
        """玩家状态缓存管理器，用于同步更新 Redis BitMap。"""

        self._logger = logger.bind(game_id=self.game_id, module="VoteManager")

    # ------------------------------------------------------------------
    # Redis 客户端懒初始化
    # ------------------------------------------------------------------

    async def _get_redis(self) -> aioredis.Redis:
        """获取 Redis 异步客户端（懒初始化，共享连接池）。

        Returns:
            共享的 Redis 异步客户端实例。

        Raises:
            RedisUnavailableException: Redis 连接池初始化失败。
        """
        if self._redis is None:
            try:
                self._redis = await RedisClientManager.get_client()
                self._logger.debug("VoteManager 已获取共享 Redis 客户端")
            except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
                raise RedisUnavailableException(
                    f"VoteManager 无法获取 Redis 客户端: game_id={self.game_id}"
                ) from e
        return self._redis

    # ------------------------------------------------------------------
    # Key 构建
    # ------------------------------------------------------------------

    def _vote_key(self) -> str:
        """构建当前轮次的投票 Redis Hash Key。

        Returns:
            Redis Key 字符串: ``werewolf:vote:{game_id}:{round}``
        """
        return RedisKeys.vote_hash(self.game_id, self._current_round)

    # ------------------------------------------------------------------
    # Redis 操作（带重试）
    # ------------------------------------------------------------------

    async def _redis_hset(self, field: str, value: str) -> None:
        """执行原子 HSET + EXPIRE 操作（带重试）。

        使用 Lua 脚本 ``hset_with_ttl.lua`` 将 HSET 和 EXPIRE 合并为
        单次原子操作，防止进程在两条命令之间崩溃导致 Key 永不过期。

        Args:
            field: Hash field (voter_id)。
            value: Hash value (target_id)。

        Raises:
            RedisUnavailableException: 重试耗尽后 Redis 仍不可用。
        """
        key = self._vote_key()

        for attempt in range(1, RETRY_COUNT + 1):
            try:
                await LuaScriptManager.evalsha(
                    "hset_with_ttl",
                    keys=[key],
                    args=[field, value, str(VOTE_TTL_SEC)],
                )
                return
            except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
                self._logger.warning(
                    "Redis Lua HSET+TTL 连接异常，重试中",
                    key=key,
                    field=field,
                    attempt=attempt,
                    max_retries=RETRY_COUNT,
                    error=str(e),
                )
                if attempt < RETRY_COUNT:
                    await asyncio.sleep(RETRY_DELAY_SEC * attempt)
                else:
                    raise RedisUnavailableException(
                        f"投票记录失败: game_id={self.game_id}, "
                        f"voter={field}, 重试 {RETRY_COUNT} 次后 Redis 仍不可用"
                    ) from e
            except Exception as e:
                self._logger.error(
                    "Redis Lua 脚本执行异常",
                    key=key,
                    field=field,
                    error=str(e),
                    exc_info=True,
                )
                raise RedisUnavailableException(
                    f"Redis Lua 脚本执行失败: {e}"
                ) from e

    async def _redis_hgetall(self) -> Dict[str, str]:
        """执行 HGETALL 操作（带重试）。

        Returns:
            ``voter_id → target_id`` 的全量选票映射。

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
                        f"投票查询失败: game_id={self.game_id}, "
                        f"重试 {RETRY_COUNT} 次后 Redis 仍不可用"
                    ) from e
            except aioredis.ResponseError as e:
                self._logger.error(
                    "Redis HGETALL 响应异常",
                    key=key,
                    error=str(e),
                    exc_info=True,
                )
                raise RedisUnavailableException(
                    f"Redis 返回错误响应: {e}"
                ) from e

    async def _redis_hlen(self) -> int:
        """执行 HLEN 操作，获取已投票人数。

        Returns:
            已投票人数。Redis 不可用时返回 0。

        Raises:
            RedisUnavailableException: 重试耗尽后 Redis 仍不可用。
        """
        key = self._vote_key()
        redis = await self._get_redis()

        for attempt in range(1, RETRY_COUNT + 1):
            try:
                return await redis.hlen(key)
            except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
                self._logger.warning(
                    "Redis HLEN 连接异常，重试中",
                    key=key,
                    attempt=attempt,
                    error=str(e),
                )
                if attempt < RETRY_COUNT:
                    await asyncio.sleep(RETRY_DELAY_SEC * attempt)
                else:
                    raise RedisUnavailableException(
                        f"投票人数查询失败: game_id={self.game_id}"
                    ) from e

    async def _redis_hexists(self, voter_id: str) -> bool:
        """检查指定玩家是否已投票。

        Args:
            voter_id: 玩家 ID。

        Returns:
            ``True`` 如果已投票。
        """
        key = self._vote_key()
        try:
            redis = await self._get_redis()
            return await redis.hexists(key, voter_id)
        except (aioredis.ConnectionError, aioredis.TimeoutError):
            self._logger.warning(
                "Redis HEXISTS 失败",
                key=key,
                voter_id=voter_id,
            )
            return False

    async def _redis_delete(self) -> None:
        """删除当前轮次的投票 Hash（清空选票）。

        在 begin_vote 时调用，清除上一轮的选票数据。
        """
        key = self._vote_key()
        try:
            redis = await self._get_redis()
            await redis.delete(key)
            self._logger.debug("vote_hash_deleted", key=key)
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            self._logger.warning(
                "删除投票 Hash 失败",
                key=key,
                error=str(e),
            )

    # ------------------------------------------------------------------
    # 投票回合管理
    # ------------------------------------------------------------------

    def begin_vote(
        self, round_num: int, pk_candidates: Optional[List[str]] = None
    ) -> None:
        """开启新一轮投票。

        **Why**: 每轮投票开始前必须调用此方法，设置当前轮次以便构建
        正确的 Redis Key，确保不同轮次的选票不会互相污染。

        若为 PK 投票，需传入 PK 候选人名单以限制投票目标。

        通常在状态机进入 ``DAY_VOTE`` 或 ``DAY_PK_VOTE`` 阶段时
        由 Game Engine 调用。

        Args:
            round_num: 当前游戏轮次。
            pk_candidates: PK 候选人名单。若为普通放逐投票，传 None。
        """
        self._current_round = round_num
        self.pk_candidates = pk_candidates
        self._logger.info(
            "vote_begin",
            round=round_num,
            is_pk_vote=pk_candidates is not None,
            pk_candidates=pk_candidates,
        )

    # ------------------------------------------------------------------
    # 选票提交与校验
    # ------------------------------------------------------------------

    async def submit_vote(
        self,
        action: AgentAction,
        roles: Dict[str, BaseRole],
        current_phase: GamePhase,
    ) -> bool:
        """提交并校验选票。

        执行以下校验（按顺序）：
        1. **动作类型校验**：必须是 ``ActionType.VOTE``。
        2. **投票人存活校验**：投票人必须存在且存活。
        3. **PK 候选人校验**：如果当前是 PK 投票，目标必须在 PK 名单中。
        4. **写入 Redis**: 通过 HSET 原子写入，天然支持覆盖更新。

        **Why (允许覆盖而非拒绝)**: AI Agent 可能在投票阶段多次调用 LLM
        并提交不同投票，采用"最后一次为准"的策略可以避免因中间态的决策变更
        导致的拒绝处理复杂度。HSET 命令天然支持覆盖更新。

        **Why (Redis HSET)**: 在多 Worker 部署下，不同进程可能同时接收
        不同 Agent 的投票请求。HSET 操作是原子性的，多个进程并发写入
        同一 Hash 的不同 Field 不会相互干扰，写入同一 Field 时最后
        写入的值生效。

        Args:
            action: Agent 提交的投票动作。
            roles: 当前所有角色的映射 ``player_id → BaseRole``。
            current_phase: 当前游戏阶段。

        Returns:
            ``True`` 表示选票已记录。

        Raises:
            ActionValidationError: 选票非法（类型错误 / 投票人不存在或已死亡 /
                PK 投票目标不在候选人名单中）。
            RedisUnavailableException: Redis 不可用，无法记录选票。
        """
        # ── 校验 1: 动作类型 ──
        if action.action_type != ActionType.VOTE:
            raise ActionValidationError(
                action,
                f"非投票动作: 期望 {ActionType.VOTE.value}，"
                f"实际 {action.action_type.value}",
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
                f"投票人 [{voter_id}] 已死亡，无法投票",
            )

        # ── 校验 3: PK 候选人限制 ──
        if self.pk_candidates is not None and action.target_id is not None:
            if action.target_id not in self.pk_candidates:
                raise ActionValidationError(
                    action,
                    f"PK 投票阶段只能投给 PK 候选人: {self.pk_candidates}，"
                    f"不可投给 [{action.target_id}]",
                )

        # ── 记录选票到 Redis Hash（原子检测+写入+TTL） ──
        target_value = action.target_id or ""  # 空字符串表示弃权
        try:
            key = self._vote_key()  # 添加缺失的key变量定义
            result = await LuaScriptManager.evalsha(
                "vote_submit",
                keys=[key],
                args=[voter_id, target_value, str(VOTE_TTL_SEC)],
            )
            had_previous = bool(result[0])
            if had_previous:
                self._logger.info(
                    "vote_updated",
                    voter_id=voter_id,
                    new_target=action.target_id or "(弃权)",
                )
            else:
                self._logger.info(
                    "vote_submitted",
                    voter_id=voter_id,
                    target_id=action.target_id or "(弃权)",
                )
        except RedisUnavailableException:
            self._logger.error(
                "投票写入 Redis 失败",
                voter_id=voter_id,
                target_id=action.target_id,
                exc_info=True,
            )
            raise

        return True

    # ------------------------------------------------------------------
    # 投票结算
    # ------------------------------------------------------------------

    async def resolve_vote(
        self, roles: Dict[str, BaseRole], current_round: int = 0
    ) -> VoteResolveResult:
        """结算当前投票回合，统计票数并处理结果。

        结算逻辑：
        1. **从 Redis 拉取全量选票**：使用 HGETALL 获取所有 voter_id → target_id。
        2. **统计投票分布**：使用 ``Counter`` 统计每个目标获得的票数。
        3. **确定最高票**：找出得票最高的目标（可能为多人并列）。
        4. **平票判断**：
           - 若最高票有多人并列 → 返回平票结果，由引擎决定是否进入 PK。
           - 若唯一最高票 → 执行即时死亡结算（调用 ``die()`` + 发布事件）。
        5. **无人得票处理**：若所有选票均为弃权，``highest_voted`` 为空列表。

        **Why (即时执行死亡)**: 白天放逐投票的结果一经确定即刻生效，
        不存在反悔或撤销机制。直接在此处调用 ``die()`` 可以保证状态一致性，
        并立即触发后续流程（如猎人开枪检查）。

        Args:
            roles: 当前所有角色的映射 ``player_id → BaseRole``。
                结算过程中可能修改被放逐角色的 ``is_alive`` 状态。
            current_round: 当前游戏轮次，用于死亡事件的 payload。

        Returns:
            :class:`VoteResolveResult` 包含平票状态、最高票名单、
            选票明细和计票统计。

        Raises:
            ActionValidationError: 如果被放逐的目标角色不存在。
            RedisUnavailableException: Redis 不可用，无法拉取选票。
        """
        self._logger.info(
            "vote_resolve_start",
            round=self._current_round,
            pk_candidates=self.pk_candidates,
        )

        # ── Step 1: 从 Redis 拉取全量选票 ──
        try:
            all_votes = await self._redis_hgetall()
        except RedisUnavailableException as e:
            self._logger.error(
                "结算失败：Redis 不可用，无法拉取选票",
                round=self._current_round,
                error=str(e),
            )
            raise

        # ── Step 2: 统计投票分布 ──
        # Why: 使用 Counter 而非手动累加，代码更简洁且不易出错。
        # 过滤掉空字符串（弃权票），不计入得票统计。
        vote_targets = [
            target for target in all_votes.values() if target
        ]
        vote_count: Dict[str, int] = dict(Counter(vote_targets))

        total_voters = len(all_votes)

        # ── Step 3: 确定最高票 ──
        highest_voted: List[str] = []
        if vote_count:
            max_votes = max(vote_count.values())
            highest_voted = [
                player_id for player_id, count in vote_count.items()
                if count == max_votes
            ]

        is_tie = len(highest_voted) > 1

        self._logger.info(
            "vote_tally",
            vote_count=vote_count,
            highest_voted=highest_voted,
            max_votes=max(vote_count.values()) if vote_count else 0,
            is_tie=is_tie,
            total_voters=total_voters,
        )

        # ── Step 4: 处理唯一最高票 —— 即时死亡结算 ──
        if not is_tie and len(highest_voted) == 1:
            voted_out_id = highest_voted[0]
            await self._execute_elimination(
                voted_out_id, roles, vote_count, current_round
            )

        # ── Step 5: 发布投票结算事件 ──
        await self._publish_vote_resolve_event(
            is_tie=is_tie,
            highest_voted=highest_voted,
            vote_count=vote_count,
            total_voters=total_voters,
        )

        # ── Step 6: 构建结算结果 ──
        result = VoteResolveResult(
            is_tie=is_tie,
            highest_voted=highest_voted,
            vote_details=dict(all_votes),
            vote_count=vote_count,
            total_voters=total_voters,
        )

        self._logger.info(
            "vote_resolve_complete",
            is_tie=is_tie,
            highest_voted=highest_voted,
            sole_voted_out=result.sole_voted_out,
        )

        return result

    async def _execute_elimination(
        self,
        target_id: str,
        roles: Dict[str, BaseRole],
        vote_count: Dict[str, int],
        current_round: int,
    ) -> None:
        """执行放逐死亡结算。

        调用目标角色的 ``die()`` 方法并发布 ``PLAYER_DEATH`` 事件。

        **Why (独立方法)**: 将死亡执行逻辑与计票逻辑分离，便于测试和
        后续扩展（如添加遗言触发、特殊角色被票出局的额外效果）。

        Args:
            target_id: 被放逐的玩家 ID。
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
                    action_type=ActionType.VOTE,
                    actor_id="SYSTEM",
                    target_id=target_id,
                    phase=GamePhase.VOTE_RESOLVE,
                    round=current_round,
                    reason="投票结算放逐",
                ),
                f"放逐目标 [{target_id}] 不存在于当前对局中",
            )

        if not target_role.is_alive:
            self._logger.warning(
                "elimination_target_already_dead",
                target_id=target_id,
            )
            return

        # 执行死亡
        target_role.die()
        # 同步更新 Redis BitMap 存活状态——多 Worker 一致性要求
        await self._player_status.mark_dead(
            self.game_id, target_id, target_role.seat_number
        )
        self._logger.info(
            "player_eliminated_by_vote",
            player_id=target_id,
            role_type=target_role.role_type.value,
            votes_received=vote_count.get(target_id, 0),
        )

        # 发布死亡事件
        death_event = Event(
            event_id=str(uuid.uuid4()),
            game_id=self.game_id,
            seq_num=0,  # EventBus 自动分配
            event_type=EventType.PLAYER_DEATH,
            visibility=Visibility.PUBLIC,
            target_agents=[target_id],
            timestamp=now_tz(),
            payload={
                "player_id": target_id,
                "death_reason": "VOTED_OUT",
                "role_type": target_role.role_type.value,
                "faction": target_role.faction.value,
                "votes_received": vote_count.get(target_id, 0),
                "total_voters": sum(vote_count.values()),
                "round": current_round,
            },
        )
        await self.event_bus.publish(death_event)

    async def _publish_vote_resolve_event(
        self,
        is_tie: bool,
        highest_voted: List[str],
        vote_count: Dict[str, int],
        total_voters: int,
    ) -> None:
        """发布投票结算事件。

        **Why**: 投票结果对所有玩家公开，以 ``VOTE_EVENT`` 类型
        发布，payload 包含完整的计票信息供前端展示和复盘分析。

        Args:
            is_tie: 是否为平票。
            highest_voted: 最高票玩家列表。
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
                "announcement_type": "vote_result",
                "is_tie": is_tie,
                "highest_voted": highest_voted,
                "vote_count": vote_count,
                "total_voters": total_voters,
                "is_pk_vote": self.pk_candidates is not None,
                "round": self._current_round,
            },
        )
        await self.event_bus.publish(event)

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    async def get_current_votes(self) -> Dict[str, str]:
        """获取当前选票的只读副本。

        Returns:
            ``voter_id → target_id`` 的映射副本（空字符串表示弃权）。
        """
        try:
            return await self._redis_hgetall()
        except RedisUnavailableException:
            self._logger.warning("get_current_votes 失败：Redis 不可用")
            return {}

    async def get_voter_count(self) -> int:
        """获取已投票人数。

        Returns:
            当前回合已投票的玩家数量。
        """
        try:
            return await self._redis_hlen()
        except RedisUnavailableException:
            self._logger.warning("get_voter_count 失败：Redis 不可用")
            return 0

    async def has_voted(self, voter_id: str) -> bool:
        """检查指定玩家是否已投票。

        Args:
            voter_id: 玩家 ID。

        Returns:
            ``True`` 如果该玩家已投票。
        """
        return await self._redis_hexists(voter_id)

    def is_pk_vote(self) -> bool:
        """检查当前是否为 PK 投票回合。

        Returns:
            ``True`` 如果是 PK 投票（存在候选人限制）。
        """
        return self.pk_candidates is not None

    # ------------------------------------------------------------------
    # 生命周期管理
    # ------------------------------------------------------------------

    async def reset(self) -> None:
        """完全重置投票管理器状态。

        **Why**: 在对局结束或重新开始时调用，清除 Redis 中的选票数据
        和内存中的控制参数，确保跨对局的状态隔离。
        """
        self.pk_candidates = None
        self._current_round = 0

        # 清除当前轮次的 Redis 选票数据
        await self._redis_delete()

        self._logger.info("vote_manager_reset")
