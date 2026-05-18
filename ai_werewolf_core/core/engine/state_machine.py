"""
Game Engine 阶段状态机 (Phase State Machine) 模块 —— 基于 Redis Hash 的无状态设计。

**Why**: 本模块是游戏引擎的核心骨架，负责硬编码管理狼人杀对局内各阶段的
严格流转。所有状态迁移路径必须在 :attr:`PhaseStateMachine.VALID_TRANSITIONS`
中预先定义为有向图，杜绝 LLM 或任何外部输入决定状态流转。

**Redis 上下文存储**:
    阶段和轮次状态不再保存在实例变量中，而是写入 Redis Hash:
    - Key: ``werewolf:game:{game_id}:context``
    - Fields: ``phase`` (当前阶段), ``round`` (当前轮次)

    这确保多 Worker 进程共享同一份状态，任意进程都可以读取和更新阶段信息。

参考: :doc:`docs/plan/状态机与生命周期设计`
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

import redis.asyncio as aioredis
from sqlalchemy import update

from ai_werewolf_core.constant.redis_keys import RedisKeys
from ai_werewolf_core.core.engine.exceptions import InvalidTransitionError
from ai_werewolf_core.core.event.bus import EventBus
from ai_werewolf_core.db.models import GameRecord
from ai_werewolf_core.db.session import async_session_factory
from ai_werewolf_core.schemas.enums import EventType, GamePhase, Visibility
from ai_werewolf_core.schemas.models import Event
from ai_werewolf_core.utils.logger import bind_game_context, get_logger
from ai_werewolf_core.utils.redis_client import RedisClientManager
from ai_werewolf_core.utils.redis_lua_loader import LuaScriptManager
from ai_werewolf_core.utils.redis_seq import RedisUnavailableException
from ai_werewolf_core.utils.snowflake import get_snowflake
from ai_werewolf_core.utils.time_utils import now_tz

logger = get_logger(__name__)

# ============================================================================
# 常量定义
# ============================================================================

# Redis 操作重试配置
RETRY_COUNT: int = 3
RETRY_DELAY_SEC: float = 0.1

# Redis Hash 字段名
CTX_FIELD_PHASE: str = "phase"
CTX_FIELD_ROUND: str = "round"


class PhaseStateMachine:
    """
    游戏对局阶段状态机 —— 基于 Redis Hash 的无状态设计。

    持有对局唯一标识和事件总线引用，但不再在实例变量中保存
    ``current_phase`` 和 ``round``。这些状态存储在 Redis Hash 中，
    确保多 Worker 进程共享同一份权威状态。

    **Why**: 狼人杀对局有严格的时序逻辑（天黑→夜间行动→结算→天亮→
    讨论→投票→遗言→检查胜负），任何跳步或逆序都会导致游戏逻辑崩溃。
    因此所有合法迁移路径必须硬编码在校验字典中。

    Attributes:
        game_id: 对局唯一标识。
        event_bus: 用于发布阶段变更事件的 EventBus 实例。
    """

    VALID_TRANSITIONS: dict[Optional[GamePhase], list[GamePhase | None]] = {
        None: [GamePhase.INIT],
        GamePhase.INIT: [GamePhase.NIGHT_START],

        # 夜晚阶段
        GamePhase.NIGHT_START: [GamePhase.NIGHT_WOLF_ACT],
        GamePhase.NIGHT_WOLF_ACT: [GamePhase.NIGHT_WITCH_ACT],
        GamePhase.NIGHT_WITCH_ACT: [GamePhase.NIGHT_SEER_ACT],
        GamePhase.NIGHT_SEER_ACT: [GamePhase.NIGHT_RESOLVE],
        GamePhase.NIGHT_RESOLVE: [
            GamePhase.DAY_START
        ],

        # 白天阶段
        GamePhase.DAY_START: [
            GamePhase.DAY_DISCUSSION,# 正常进入讨论
            GamePhase.HUNTER_SHOOT,  # 夜晚猎人死亡，天亮开枪
            GamePhase.LAST_WORDS,    # 首夜死亡遗言
            GamePhase.GAME_OVER      # 天亮播报后游戏结束
        ],
        GamePhase.DAY_DISCUSSION: [GamePhase.DAY_VOTE],
        GamePhase.DAY_VOTE: [
            GamePhase.VOTE_RESOLVE,
            GamePhase.DAY_PK_DISCUSSION
        ],

        # 投票结算
        GamePhase.VOTE_RESOLVE: [
            GamePhase.HUNTER_SHOOT,  # 猎人被票出局
            GamePhase.LAST_WORDS,    # 被票出局者遗言
            GamePhase.NIGHT_START,   # 平安日，无人出局，直接天黑
            GamePhase.GAME_OVER,     # 投票后游戏结束
        ],

        # PK 阶段
        GamePhase.DAY_PK_DISCUSSION: [GamePhase.DAY_PK_VOTE],
        GamePhase.DAY_PK_VOTE: [
            GamePhase.VOTE_RESOLVE
        ],

        # 特殊结算阶段
        GamePhase.HUNTER_SHOOT: [
            GamePhase.LAST_WORDS,    # 开枪后发表遗言
            GamePhase.DAY_DISCUSSION,# 夜晚死亡开枪后，进入白天讨论
            GamePhase.GAME_OVER      # 开枪后游戏结束
        ],
        GamePhase.LAST_WORDS: [
            GamePhase.DAY_DISCUSSION,# 首夜死亡遗言后，进入白天讨论
            GamePhase.NIGHT_START,   # 白天被票遗言后，进入夜晚
        ],

        # 游戏结束
        GamePhase.GAME_OVER: [
            GamePhase.INIT,          # 再来一局
            None                     # 彻底结束
        ],
    }

    def __init__(self, game_id: str, event_bus: EventBus) -> None:
        """
        初始化阶段状态机。

        Args:
            game_id: 对局唯一标识，用于日志追踪和事件路由。
            event_bus: 事件总线实例。阶段变更时将发布事件以驱动
                Agent Runtime、前端推送等下游模块。
        """
        self.game_id: str = game_id
        self.event_bus: EventBus = event_bus

        # Redis 客户端 (懒初始化，共享连接池)
        self._redis: Optional[aioredis.Redis] = None

        self._logger = logger.bind(game_id=self.game_id, module="PhaseStateMachine")

    # ------------------------------------------------------------------
    # Redis 客户端懒初始化
    # ------------------------------------------------------------------

    async def _get_redis(self) -> aioredis.Redis:
        """获取 Redis 异步客户端（共享连接池）。"""
        try:
            return await RedisClientManager.get_client()
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            raise RedisUnavailableException(
                f"PhaseStateMachine 无法获取 Redis 客户端: game_id={self.game_id}"
            ) from e

    # ------------------------------------------------------------------
    # Redis 上下文读写
    # ------------------------------------------------------------------

    def _context_key(self) -> str:
        """构建对局上下文 Redis Hash Key。"""
        return RedisKeys.game_context(self.game_id)

    async def _load_context(self) -> dict:
        """从 Redis 加载当前对局上下文。

        支持重试：当 Redis 连接意外断开时，自动重试并重新获取客户端。
        Celery Worker 中长时间闲置的连接可能被服务器关闭，
        通过重试可以触发 _get_redis() 中的懒初始化获取新连接。

        Returns:
            包含 ``phase`` 和 ``round`` 的字典。
            Key 不存在时返回初始值 ``{"phase": None, "round": 0}``。
        """
        key = self._context_key()
        last_error = None
        for attempt in range(1, RETRY_COUNT + 1):
            try:
                redis = await self._get_redis()
                raw = await redis.hgetall(key)
                if not raw:
                    return {CTX_FIELD_PHASE: None, CTX_FIELD_ROUND: 0}

                phase_str = raw.get(CTX_FIELD_PHASE)
                round_str = raw.get(CTX_FIELD_ROUND, "0")

                return {
                    CTX_FIELD_PHASE: GamePhase(phase_str) if phase_str and phase_str != "None" else None,
                    CTX_FIELD_ROUND: int(round_str),
                }
            except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
                last_error = e
                self._logger.warning(
                    "加载对局上下文失败，重试中",
                    attempt=attempt,
                    error=str(e),
                )
                # 重置客户端引用，下次 _get_redis() 会通过共享连接池获取新连接
                self._redis = None
                if attempt < RETRY_COUNT:
                    await asyncio.sleep(RETRY_DELAY_SEC * attempt)
                else:
                    self._logger.error("加载对局上下文失败，重试耗尽", error=str(e), exc_info=True)
                    raise RedisUnavailableException(
                        f"无法加载对局上下文: game_id={self.game_id}"
                    ) from e

    async def _save_context(self, phase: Optional[GamePhase], round_num: int) -> None:
        """保存当前对局上下文到 Redis 并同步 DB（Write-Through 模式）。

        先写 Redis 缓存层，成功后同步更新 PostgreSQL GameRecord 表。
        如果 DB 写入失败，Redis 中的数据仍然有效（最终一致性），
        仅记录 ERROR 日志。

        Args:
            phase: 当前游戏阶段（None 表示尚未开始）。
            round_num: 当前轮次。

        Raises:
            RedisUnavailableException: Redis 不可用。
        """
        key = self._context_key()
        phase_value = phase.value if phase else "None"

        for attempt in range(1, RETRY_COUNT + 1):
            try:
                redis = await self._get_redis()
                await redis.hset(key, mapping={
                    CTX_FIELD_PHASE: phase_value,
                    CTX_FIELD_ROUND: str(round_num),
                })
                self._logger.debug(
                    "context_saved",
                    phase=phase_value,
                    round=round_num,
                )
                # Write-Through: 同步更新 DB GameRecord
                await self._sync_context_to_db(phase, round_num)
                return
            except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
                self._logger.warning(
                    "保存对局上下文失败，重试中",
                    attempt=attempt,
                    error=str(e),
                )
                if attempt < RETRY_COUNT:
                    await asyncio.sleep(RETRY_DELAY_SEC * attempt)
                else:
                    raise RedisUnavailableException(
                        f"无法保存对局上下文: game_id={self.game_id}"
                    ) from e
            except aioredis.ResponseError as e:
                raise RedisUnavailableException(
                    f"Redis 返回错误响应: {e}"
                ) from e

    async def _sync_context_to_db(
        self, phase: Optional[GamePhase], round_num: int
    ) -> None:
        """Write-Through: 将阶段和轮次同步写入 PostgreSQL GameRecord 表。

        采用最终一致性策略：DB 写入失败不阻塞 Redis 已成功的数据，
        仅记录 ERROR 日志，后续可通过对账修复。

        Args:
            phase: 当前游戏阶段（None 表示尚未开始）。
            round_num: 当前轮次。
        """
        try:
            db_phase = phase if phase is not None else GamePhase.INIT
            self._logger.debug("syncing_context_to_db_attempt", phase=db_phase.value, round=round_num)
            async with async_session_factory() as session:
                stmt = (
                    update(GameRecord)
                    .where(GameRecord.id == self.game_id)
                    .values(phase=db_phase, round=round_num)
                )
                await session.execute(stmt)
                await session.commit()
                self._logger.debug(
                    "context_synced_to_db",
                    phase=db_phase.value,
                    round=round_num,
                )
        except Exception as e:
            # DB 写入失败不阻塞主流程——Redis 中的数据仍然有效
            self._logger.error(
                "DB 上下文同步失败（Redis 已更新，存在短暂不一致）",
                phase=phase.value if phase else None,
                round=round_num,
                error=str(e),
                exc_info=True,
            )

    async def init_context(self) -> None:
        """初始化对局上下文到 Redis（首次对局时由 LifecycleManager 调用）。

        设置 phase=None, round=0 作为初始状态。
        """
        await self._save_context(None, 0)
        self._logger.info("context_initialized")

    # ------------------------------------------------------------------
    # 阶段查询属性
    # ------------------------------------------------------------------

    @property
    async def current_phase(self) -> Optional[GamePhase]:
        """当前所处的游戏阶段 (:class:`GamePhase`)，可能为 ``None`` 表示尚未开始。

        **Why 异步属性**: 需要从 Redis 加载状态，因此使用 ``async def`` 模式。
        注意：Python 原生不支持 async property，这里采用方法签名的文档约定。
        实际调用需使用 ``await state_machine.current_phase``。
        """
        ctx = await self._load_context()
        return ctx[CTX_FIELD_PHASE]

    @property
    async def round(self) -> int:
        """当前轮次，从 0 开始。进入 :attr:`GamePhase.NIGHT_START` 时自增。

        **Why 异步属性**: 同 :meth:`current_phase`，需从 Redis 加载。
        """
        ctx = await self._load_context()
        return ctx[CTX_FIELD_ROUND]

    async def get_current_phase(self) -> Optional[GamePhase]:
        """获取当前游戏阶段（显式 async 方法）。

        Returns:
            当前 :class:`GamePhase` 或 ``None``。
        """
        ctx = await self._load_context()
        return ctx[CTX_FIELD_PHASE]

    async def get_round(self) -> int:
        """获取当前轮次（显式 async 方法）。

        Returns:
            当前轮次数。
        """
        ctx = await self._load_context()
        return ctx[CTX_FIELD_ROUND]

    # ------------------------------------------------------------------
    # 阶段迁移
    # ------------------------------------------------------------------

    async def transition_to(
        self, next_phase: GamePhase, context: Optional[dict] = None
    ) -> None:
        """
        执行阶段迁移：校验 -> 更新 Redis -> 记录日志 -> 发布事件。

        **合法性校验**:
        根据 :attr:`VALID_TRANSITIONS` 判断 ``next_phase`` 是否是当前阶段的
        合法后继。若校验失败则抛出 :class:`InvalidTransitionError`。

        **轮次递增**:
        当目标阶段为 :attr:`GamePhase.NIGHT_START` 时，轮次自增 1。
        这标记着新的"天黑-天亮"循环开始。

        **原子性保证**:
        Phase 迁移成功 = Redis HSET 成功 + EventBus publish 成功。
        如果 Redis 写入失败，整个迁移中止并抛出异常，阻止状态不一致。

        **事件发布**:
        以 :attr:`EventType.PHASE_TRANSITION_EVENT` 类型、:attr:`Visibility.PUBLIC`
        可见性发布事件，payload 中包含 ``old_phase``、``new_phase``、``round``
        以及调用方传入的 ``context``。

        Args:
            next_phase: 目标游戏阶段。
            context: 可选上下文数据（如 ``{"pk_triggered": True}``），
                会合并到发布事件的 payload 中。

        Raises:
            InvalidTransitionError: 当前阶段到 ``next_phase`` 的迁移路径
                不在 :attr:`VALID_TRANSITIONS` 定义中。
            RedisUnavailableException: Redis 不可用，无法保存新状态。
        """
        # 1. 加载当前状态
        current_ctx = await self._load_context()
        old_phase: Optional[GamePhase] = current_ctx[CTX_FIELD_PHASE]
        current_round: int = current_ctx[CTX_FIELD_ROUND]

        # 2. 计算新轮次
        new_round = current_round
        if next_phase == GamePhase.NIGHT_START:
            new_round += 1

        # 3. 原子阶段迁移（Lua 脚本：加载→校验→更新 一次完成）
        # 将 VALID_TRANSITIONS 序列化为 JSON 传入 Lua，Python 侧保持权威来源
        self._logger.debug("serializing_transitions", transitions=self.VALID_TRANSITIONS)
        transitions_json = json.dumps({
            str(k.value) if k else "None": [str(v.value) if v else "None" for v in vals]
            for k, vals in self.VALID_TRANSITIONS.items()
        })
        old_phase_str = old_phase.value if old_phase else "None"
        next_phase_str = next_phase.value

        result = await LuaScriptManager.evalsha(
            "phase_transition",
            keys=[self._context_key()],
            args=[old_phase_str, next_phase_str, str(new_round), transitions_json],
        )
        status = result[0]
        actual_old = result[1] if len(result) > 1 else old_phase_str

        if status == "PHASE_MISMATCH":
            current_from_redis = GamePhase(actual_old) if actual_old != "None" else None
            raise InvalidTransitionError(
                current_state=current_from_redis,
                target_state=next_phase,
            )
        elif status == "INVALID_TRANSITION":
            raise InvalidTransitionError(
                current_state=old_phase,
                target_state=next_phase,
            )
        # status == "OK": 迁移成功，继续后续流程

        # 4. Write-Through: 同步更新 DB GameRecord（阶段和轮次）
        await self._sync_context_to_db(next_phase, new_round)

        # 5. 记录日志
        logger.info(
            "phase_transition",
            game_id=self.game_id,
            old_phase=old_phase.value if old_phase else None,
            new_phase=next_phase.value,
            round=new_round,
        )

        # 6. 绑定当前阶段到日志上下文
        bind_game_context(self.game_id, next_phase.value)

        # 7. 发布阶段变更事件
        await self._publish_phase_change(old_phase, next_phase, new_round, context or {})

    async def _publish_phase_change(
        self,
        old_phase: Optional[GamePhase],
        new_phase: GamePhase,
        round_num: int,
        context: dict,
    ) -> None:
        """
        创建并发布阶段变更事件。

        **Why**: 事件发布逻辑从 ``transition_to`` 中提取为独立方法，
        便于子类覆盖或测试 mock。

        Args:
            old_phase: 迁移前的阶段，可能为 ``None``。
            new_phase: 迁移后的阶段。
            round_num: 当前轮次。
            context: 合并到事件 payload 的额外上下文。
        """
        payload: dict = {
            "old_phase": old_phase.value if old_phase else None,
            "new_phase": new_phase.value,
            "round": round_num,
            **context,
        }

        event = Event(
            event_id=get_snowflake().next_id(),
            game_id=self.game_id,
            seq_num=0,  # EventBus 自动分配
            event_type=EventType.PHASE_TRANSITION_EVENT,
            visibility=Visibility.PUBLIC,
            target_agents=[],
            timestamp=now_tz(),
            payload=payload,
        )

        await self.event_bus.publish(event)
