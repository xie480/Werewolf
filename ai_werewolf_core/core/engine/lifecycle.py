"""
Game Engine 生命周期管理器 (Lifecycle Manager) 模块 —— Write-Through 模式。

**Why**: 本模块统一协调狼人杀对局的完整生命周期 —— 从创建房间、初始化身份、
启动对局、推进阶段、到结算结束或异常中止。它确保所有操作均符合全局状态机
（:attr:`LifecycleManager.VALID_STATUS_TRANSITIONS`）的制约，并通过内部的
:class:`PhaseStateMachine` 管理对局内阶段流转。

**Redis Write-Through 设计**:
    全局状态（status）存储在 Redis Hash ``werewolf:game:{game_id}:context`` 中，
    同时同步写入 PostgreSQL 的 GameRecord 表。
    阶段和轮次由 :class:`PhaseStateMachine` 独立管理。

    写入顺序: 校验 → Redis HSET → 同步更新 DB → 发布事件
    这确保了 Redis 缓存和 DB 之间的一致性。

参考: :doc:`docs/plan/状态机与生命周期设计`
"""

from __future__ import annotations

import json
import uuid
from typing import Optional

import redis.asyncio as aioredis
from sqlalchemy import update

from ai_werewolf_core.utils.time_utils import now_tz

from ai_werewolf_core.config import settings
from ai_werewolf_core.constant.redis_keys import RedisKeys
from ai_werewolf_core.core.engine.exceptions import (
    GameNotRunnableError,
    InvalidTransitionError,
)
from ai_werewolf_core.core.engine.state_machine import PhaseStateMachine
from ai_werewolf_core.core.event.bus import EventBus
from ai_werewolf_core.db.models import GameRecord
from ai_werewolf_core.db.session import async_session_factory
from ai_werewolf_core.schemas.enums import (
    EventType,
    GamePhase,
    GameStatus,
    NIGHT_ACT_PHASES,
    RESOLVE_PHASES,
    SPEECH_PHASES,
    VOTE_PHASES,
    Visibility,
)
from ai_werewolf_core.schemas.models import Event
from ai_werewolf_core.utils.logger import (
    bind_game_context,
    clear_all_context,
    get_logger,
)
from ai_werewolf_core.utils.redis_client import RedisClientManager
from ai_werewolf_core.utils.redis_lua_loader import LuaScriptManager
from ai_werewolf_core.utils.redis_seq import RedisUnavailableException

logger = get_logger(__name__)

# ============================================================================
# 常量定义
# ============================================================================

# Redis Hash 字段名
CTX_FIELD_STATUS: str = "status"
CTX_FIELD_PHASE: str = "phase"
CTX_FIELD_ROUND: str = "round"
CTX_FIELD_TASK_ID: str = "current_task_id"

# 对局上下文 TTL (秒) —— 对局结束后 1 小时
GAME_CONTEXT_TTL_SEC: int = 3600

# 阶段倒计时规则 (秒)
# 结算/过渡阶段 3 秒快速通过，行动/投票/发言阶段 60 秒
PHASE_TIMEOUT_QUICK: int = 3
PHASE_TIMEOUT_NORMAL: int = 60


class LifecycleManager:
    """
    对局生命周期管理器 —— Write-Through 模式。

    封装了从房间创建到对局结束的全流程控制。内部持有:
    - ``state_machine``: 阶段状态机（:class:`PhaseStateMachine`），负责管理
      ``RUNNING`` 状态下的具体阶段流转。

    **Why**: 将生命周期和阶段流转拆分为两个层级的控制，使得全局状态
    （INIT/START/RUNNING/SETTLING/FINISHED/ABORTED）与局内阶段（NIGHT/DAY/DISCUSSION...）
    可以独立校验和演进，避免状态管理逻辑耦合在单一类中。

    **Write-Through 策略**:
        全局 status 变更时，先写 Redis 再同步写 DB，保证缓存与持久层一致。

    Attributes:
        game_id: 对局唯一标识。
        event_bus: 事件总线实例。
        state_machine: 内部阶段状态机，仅当 ``status == RUNNING`` 时处于活跃状态。
    """

    VALID_STATUS_TRANSITIONS: dict[GameStatus, list[GameStatus]] = {
        GameStatus.INIT: [GameStatus.START],
        GameStatus.START: [GameStatus.RUNNING, GameStatus.ABORTED],
        GameStatus.RUNNING: [GameStatus.SETTLING, GameStatus.ABORTED],
        GameStatus.SETTLING: [GameStatus.FINISHED, GameStatus.ABORTED],
        GameStatus.FINISHED: [],
        GameStatus.ABORTED: [],
    }
    """
    合法全局状态迁移映射表。

    **Why**: 全局生命周期状态必须严格按序流转，绝不允许从 FINISHED 回退到
    RUNNING 等非法操作。终结态（FINISHED、ABORTED）无后继状态。
    """

    def __init__(self, game_id: str, event_bus: EventBus) -> None:
        """
        初始化生命周期管理器。

        Args:
            game_id: 对局唯一标识。
            event_bus: 事件总线实例，用于发布全局状态变更事件。
        """
        self.game_id: str = game_id
        self.event_bus: EventBus = event_bus
        self.state_machine: PhaseStateMachine = PhaseStateMachine(game_id, event_bus)

        # Redis 客户端 (懒初始化，共享连接池)
        self._redis: Optional[aioredis.Redis] = None

        self._logger = logger.bind(game_id=self.game_id, module="LifecycleManager")

    # ------------------------------------------------------------------
    # Redis 客户端懒初始化
    # ------------------------------------------------------------------

    async def _get_redis(self) -> aioredis.Redis:
        """获取 Redis 异步客户端（共享连接池）。"""
        try:
            return await RedisClientManager.get_client()
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            raise RedisUnavailableException(
                f"LifecycleManager 无法获取 Redis 客户端: game_id={self.game_id}"
            ) from e

    # ------------------------------------------------------------------
    # Redis 上下文操作
    # ------------------------------------------------------------------

    def _context_key(self) -> str:
        """构建对局上下文 Redis Hash Key。"""
        return RedisKeys.game_context(self.game_id)

    async def _get_status_from_redis(self) -> Optional[GameStatus]:
        """从 Redis 获取当前全局状态。

        Returns:
            当前 :class:`GameStatus`；Key 不存在时返回 ``None``。
        """
        key = self._context_key()
        try:
            redis = await self._get_redis()
            raw = await redis.hget(key, CTX_FIELD_STATUS)
            if raw is None:
                return None
            return GameStatus(raw)
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            self._logger.error("读取状态失败", error=str(e), exc_info=True)
            raise RedisUnavailableException(
                f"无法从 Redis 读取状态: game_id={self.game_id}"
            ) from e

    async def _set_status_to_redis(self, status: GameStatus) -> None:
        """Write-Through: 更新 Redis 中的全局状态。

        Args:
            status: 目标全局状态。

        Raises:
            RedisUnavailableException: Redis 不可用。
        """
        key = self._context_key()
        try:
            redis = await self._get_redis()
            await redis.hset(key, CTX_FIELD_STATUS, status.value)
            self._logger.debug("status_synced_to_redis", status=status.value)
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            raise RedisUnavailableException(
                f"无法写入状态到 Redis: game_id={self.game_id}"
            ) from e

    async def _set_status_to_db(self, status: GameStatus) -> None:
        """Write-Through: 同步更新 DB GameRecord。

        Args:
            status: 目标全局状态。
        """
        try:
            async with async_session_factory() as session:
                stmt = (
                    update(GameRecord)
                    .where(GameRecord.id == self.game_id)
                    .values(status=status)
                )
                await session.execute(stmt)
                await session.commit()
                self._logger.debug("status_synced_to_db", status=status.value)
        except Exception as e:
            self._logger.error(
                "DB 状态同步失败（Redis 已更新，存在短暂不一致）",
                status=status.value,
                error=str(e),
                exc_info=True,
            )

    async def _insert_game_record_to_db(self) -> None:
        """Write-Through: 在对局初始化时向 PostgreSQL 插入 GameRecord 行。

        使用 Snowflake ID 作为主键，与 Redis 中 game_id 保持一致。
        初始状态: status=INIT, phase=INIT, round=0。

        如果 GameRecord 已存在（如重试场景），记录 WARNING 日志并跳过，
        避免因主键冲突导致初始化失败。
        """
        try:
            async with async_session_factory() as session:
                record = GameRecord(
                    id=self.game_id,
                    status=GameStatus.INIT,
                    phase=GamePhase.INIT,
                    round=0,
                )
                session.add(record)
                await session.commit()
                self._logger.info(
                    "game_record_created",
                    game_id=self.game_id,
                )
        except Exception as e:
            # 主键冲突（重复初始化）不视为致命错误
            self._logger.warning(
                "GameRecord 创建失败（可能已存在），将继续使用现有记录",
                game_id=self.game_id,
                error=str(e),
            )

    # ------------------------------------------------------------------
    # 公开属性
    # ------------------------------------------------------------------

    async def get_status(self) -> GameStatus:
        """获取当前全局游戏生命周期状态。

        Returns:
            当前 :class:`GameStatus`。如果 Redis 中无数据，返回 INIT。
        """
        status = await self._get_status_from_redis()
        if status is None:
            return GameStatus.INIT
        return status

    # ------------------------------------------------------------------
    # 内部校验与事件发布
    # ------------------------------------------------------------------

    def _validate_status_transition(self, current: GameStatus, new_status: GameStatus) -> None:
        """
        校验全局状态迁移是否合法。

        **Why**: 所有状态变更操作（init_game、start_game、end_game、abort_game）
        必须通过此方法校验，防止跳过步骤或逆序操作。

        Args:
            current: 当前全局状态。
            new_status: 目标全局状态。

        Raises:
            InvalidTransitionError: 迁移路径不在 :attr:`VALID_STATUS_TRANSITIONS` 中。
        """
        allowed = self.VALID_STATUS_TRANSITIONS.get(current, [])
        if new_status not in allowed:
            raise InvalidTransitionError(
                current_state=current,
                target_state=new_status,
            )

    async def _publish_status_change(
        self,
        old_status: GameStatus,
        new_status: GameStatus,
        extra: Optional[dict] = None,
    ) -> None:
        """
        发布全局状态变更事件。

        以 :attr:`EventType.SYSTEM_ANNOUNCEMENT` 类型、:attr:`Visibility.PUBLIC`
        可见性发布，payload 中包含 ``old_status``、``new_status`` 及可选的
        额外上下文（如中止原因、胜利阵营）。

        Args:
            old_status: 迁移前的全局状态。
            new_status: 迁移后的全局状态。
            extra: 可选的额外 payload 字段。
        """
        payload: dict = {
            "old_status": old_status.value,
            "new_status": new_status.value,
        }
        if extra:
            payload.update(extra)

        event = Event(
            event_id=str(uuid.uuid4()),
            game_id=self.game_id,
            seq_num=0,  # EventBus 自动分配
            event_type=EventType.SYSTEM_ANNOUNCEMENT,
            visibility=Visibility.PUBLIC,
            target_agents=[],
            timestamp=now_tz(),
            payload=payload,
        )

        await self.event_bus.publish(event)

    async def _set_status(self, new_status: GameStatus) -> None:
        """
        内部原子操作：校验 → Write-Through (Redis Lua + DB) → 日志 → 发布事件。

        **原子性改进**:
        使用 Lua 脚本 ``status_transition.lua`` 将"读取当前状态 →
        校验迁移合法性 → 写入 Redis"三步合并为单次原子操作，
        消除多 Worker 并发下的竞态条件。

        **Write-Through 策略**:
        1. 通过 Lua 脚本原子校验并更新 Redis（缓存层优先）
        2. Lua 成功后同步写 DB（持久层）

        Args:
            new_status: 目标全局状态。

        Raises:
            InvalidTransitionError: 校验失败。
            RedisUnavailableException: Redis 不可用。
        """
        # 1. 确定当前状态（先读取，作为 expected 值传入 Lua）
        current = await self._get_status_from_redis()
        if current is None:
            current = GameStatus.INIT

        # 2. 原子校验+更新 Redis（Lua 脚本：读取→校验→写入 一次完成）
        # 将 VALID_STATUS_TRANSITIONS 序列化为 JSON 传入 Lua
        transitions_json = json.dumps({
            k.value: [v.value for v in vals]
            for k, vals in self.VALID_STATUS_TRANSITIONS.items()
        })

        result = await LuaScriptManager.evalsha(
            "status_transition",
            keys=[self._context_key()],
            args=[current.value, new_status.value, transitions_json],
        )
        status = result[0]
        actual_old_str = result[1] if len(result) > 1 else current.value

        if status == "STATUS_MISMATCH":
            actual_old = GameStatus(actual_old_str)
            raise InvalidTransitionError(
                current_state=actual_old,
                target_state=new_status,
            )
        elif status == "INVALID_TRANSITION":
            raise InvalidTransitionError(
                current_state=current,
                target_state=new_status,
            )
        # status == "OK": Lua 迁移成功
        old_status = current

        # 3. Write-Through: 同步更新 DB
        await self._set_status_to_db(new_status)

        # 4. 日志
        logger.info(
            "lifecycle_status_changed",
            game_id=self.game_id,
            old_status=old_status.value,
            new_status=new_status.value,
        )

        # 5. 发布事件
        await self._publish_status_change(old_status, new_status)

    # ------------------------------------------------------------------
    # 公开接口: 生命周期操作
    # ------------------------------------------------------------------

    async def init_game(self) -> None:
        """
        初始化对局: 将状态从 :attr:`GameStatus.INIT` 迁移到 :attr:`GameStatus.START`。

        此阶段通常对应创建房间、玩家加入、身份分配等准备工作。
        成功后将状态变更为 ``START`` 并广播事件。
        同时初始化阶段状态机的 Redis 上下文。

        **Write-Through 双写**:
            1. 先 INSERT GameRecord 到 PostgreSQL（创建持久化行）
            2. 再写入 Redis Hash 上下文（缓存层）
            3. 后续状态迁移通过 Lua 脚本原子更新 Redis + 同步 UPDATE DB

        Raises:
            InvalidTransitionError: 当前状态不是 ``INIT``。
        """
        self._logger.info("game_init")

        current_status = await self.get_status()
        if current_status != GameStatus.INIT:
            raise InvalidTransitionError(
                current_state=current_status,
                target_state=GameStatus.START,
            )

        # Step 0: Write-Through —— 先向 PostgreSQL 插入 GameRecord 行
        # 确保后续 _set_status_to_db() 的 UPDATE 有目标行可更新
        await self._insert_game_record_to_db()

        # Step 1: 初始化 Redis 对局上下文（首次写入）
        key = self._context_key()
        redis = await self._get_redis()
        await redis.hset(key, mapping={
            CTX_FIELD_STATUS: GameStatus.INIT.value,
            CTX_FIELD_PHASE: "None",
            CTX_FIELD_ROUND: "0",
        })
        # 注意: 不在此时设置 TTL，等对局结束再设

        bind_game_context(self.game_id, GamePhase.INIT.value)

        # 初始化阶段状态机: 进入 INIT 阶段 (对局准备)
        await self.state_machine.init_context()
        await self.state_machine.transition_to(GamePhase.INIT)

        # 状态迁移: INIT → START
        await self._set_status(GameStatus.START)

    async def start_game(self) -> None:
        """
        启动对局: 将状态从 :attr:`GameStatus.START` 迁移到 :attr:`GameStatus.RUNNING`，
        同时激活内部的 :class:`PhaseStateMachine`，进入首轮天黑阶段。

        此方法会:
        1. 校验当前状态为 ``START``。
        2. 将全局状态变更为 ``RUNNING``。
        3. 初始化阶段状态机进入 :attr:`GamePhase.NIGHT_START`，轮次设为 1。

        **Why**: 此处分两步发布事件（先 STATUS 变更，再 PHASE 变更），
        确保下游模块（如 WebSocket 网关）先收到"对局开始"的通知，
        再收到"进入 NIGHT"的通知，保证前端状态同步顺序。

        Raises:
            InvalidTransitionError: 当前状态不是 ``START``。
        """
        self._logger.info("game_start")
        await self._set_status(GameStatus.RUNNING)

        # 激活阶段状态机: 进入首轮 NIGHT_START
        await self.state_machine.transition_to(
            GamePhase.NIGHT_START,
            context={"reason": "game_start"},
        )

    async def advance_phase(
        self, next_phase: GamePhase, context: Optional[dict] = None
    ) -> None:
        """
        推进游戏阶段：委托给内部 :class:`PhaseStateMachine` 执行阶段迁移。

        仅当全局状态为 :attr:`GameStatus.RUNNING` 时允许推进阶段，
        否则抛出 :class:`GameNotRunnableError`。

        **Why**: 此方法是 Game Engine 推进对局的核心入口。Engine 根据
        游戏规则决定下一个阶段后，调用此方法完成实际的状态迁移和事件广播。

        Args:
            next_phase: 目标游戏阶段。
            context: 可选的上下文数据（如 ``{"pk_triggered": True}``）。

        Raises:
            GameNotRunnableError: 全局状态不是 ``RUNNING``。
            InvalidTransitionError: 阶段迁移路径非法（由 PhaseStateMachine 抛出）。
        """
        current_status = await self._get_status_from_redis()
        if current_status != GameStatus.RUNNING:
            raise GameNotRunnableError(
                current_status=current_status or GameStatus.INIT,
                game_id=self.game_id,
            )

        await self.state_machine.transition_to(next_phase, context)

    async def end_game(self, winner_faction: str) -> None:
        """
        正常结束对局: RUNNING → SETTLING → FINISHED。

        此方法:
        1. 将状态从 ``RUNNING`` 迁移到 ``SETTLING``（结算阶段）。
        2. 将 :class:`PhaseStateMachine` 的阶段设置为 :attr:`GamePhase.GAME_OVER`。
        3. 发布 :attr:`EventType.GAME_OVER_EVENT` 事件，payload 包含 ``winner_faction``。
        4. 将状态从 ``SETTLING`` 迁移到 ``FINISHED``。
        5. 设置 Redis 对局上下文 TTL 为 1 小时。
        6. 调用 ``clear_all_context()`` 清除日志上下文。

        **Why**: 分两步（SETTLING → FINISHED）的原因是为结算逻辑（如积分计算、
        成就判定）预留执行窗口，确保结算事件先于终结事件发布。

        Args:
            winner_faction: 胜利阵营标识，如 ``"VILLAGER"`` 或 ``"WEREWOLF"``。

        Raises:
            InvalidTransitionError: 当前状态不是 ``RUNNING`` 或 ``SETTLING``。
        """
        self._logger.info("game_end", winner_faction=winner_faction)

        # Step 1: RUNNING -> SETTLING
        await self._set_status(GameStatus.SETTLING)

        # Step 2: 阶段进入 GAME_OVER
        # HACK: 直接操作 Redis 上下文设置 GAME_OVER 阶段。
        # end_game 是强制结束操作，当前阶段可能是任意值（如 NIGHT_START），
        # 而 GAME_OVER 并非所有阶段的合法后继。因此绕过 PhaseStateMachine
        # 的合法性校验，直接写入 Redis。
        try:
            redis = await self._get_redis()
            key = self._context_key()
            await redis.hset(key, CTX_FIELD_PHASE, GamePhase.GAME_OVER.value)
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            raise RedisUnavailableException(
                f"无法设置 GAME_OVER 阶段: game_id={self.game_id}"
            ) from e

        # Write-Through: 同步更新 DB GameRecord.phase = GAME_OVER
        try:
            async with async_session_factory() as session:
                stmt = (
                    update(GameRecord)
                    .where(GameRecord.id == self.game_id)
                    .values(phase=GamePhase.GAME_OVER)
                )
                await session.execute(stmt)
                await session.commit()
                self._logger.debug("game_over_phase_synced_to_db")
        except Exception as e:
            self._logger.error(
                "DB GAME_OVER 阶段同步失败",
                game_id=self.game_id,
                error=str(e),
                exc_info=True,
            )

        # Step 3: 发布 GAME_OVER 事件
        game_over_event = Event(
            event_id=str(uuid.uuid4()),
            game_id=self.game_id,
            seq_num=0,
            event_type=EventType.GAME_OVER_EVENT,
            visibility=Visibility.PUBLIC,
            target_agents=[],
            timestamp=now_tz(),
            payload={
                "winner_faction": winner_faction,
            },
        )
        await self.event_bus.publish(game_over_event)

        # 触发异步评测任务
        from ai_werewolf_core.tasks.eval import evaluate_game_task
        evaluate_game_task.delay(self.game_id)

        # Step 4: SETTLING -> FINISHED
        await self._set_status(GameStatus.FINISHED)

        # Step 5: 设置 TTL
        try:
            await redis.expire(key, GAME_CONTEXT_TTL_SEC)
        except (aioredis.ConnectionError, aioredis.TimeoutError):
            self._logger.warning("设置对局上下文 TTL 失败，将由后台清理")

        # Step 6: 清理日志上下文
        clear_all_context()
        self._logger.info("game_finished")

    async def abort_game(self, reason: str) -> None:
        """
        异常中止对局: 从任意可中止的状态迁移到 :attr:`GameStatus.ABORTED`。

        根据 :attr:`VALID_STATUS_TRANSITIONS`，可从 START、RUNNING、SETTLING
        三种状态中止。INIT 状态不可中止（尚无对局实体），FINISHED 和 ABORTED
        状态已是终结态。

        Args:
            reason: 中止原因（如 ``"player_disconnected"``）。

        Raises:
            InvalidTransitionError: 当前状态不在允许中止的状态集合中。
        """
        self._logger.warning("game_abort", reason=reason)

        await self._set_status(GameStatus.ABORTED)

        # 设置 TTL
        try:
            redis = await self._get_redis()
            key = self._context_key()
            await redis.expire(key, GAME_CONTEXT_TTL_SEC)
        except (aioredis.ConnectionError, aioredis.TimeoutError):
            self._logger.warning("设置对局上下文 TTL 失败")

        clear_all_context()
        self._logger.info("game_aborted")

    # ------------------------------------------------------------------
    # 状态恢复
    # ------------------------------------------------------------------

    # ==================================================================
    # 阶段倒计时与 Celery 任务管理
    # ==================================================================

    @staticmethod
    def get_phase_timeout(phase: GamePhase) -> int:
        """根据阶段类型获取倒计时时长。

        NIGHT_START (黑暗降临) -> 3 秒
        结算阶段 (NIGHT_RESOLVE, VOTE_RESOLVE) -> 3 秒
        DAY_START (天亮了) -> 3 秒
        夜间行动/投票/发言阶段 -> 60 秒

        Args:
            phase: 当前游戏阶段。

        Returns:
            倒计时秒数。
        """
        if phase == GamePhase.GAME_OVER:
            return 0
        if phase in (GamePhase.NIGHT_START, GamePhase.DAY_START):
            return PHASE_TIMEOUT_QUICK
        if phase in RESOLVE_PHASES:
            return PHASE_TIMEOUT_QUICK
        return PHASE_TIMEOUT_NORMAL

    async def save_task_id(self, task_id: str) -> None:
        """保存 Celery 任务 ID 到 Redis 上下文。

        Args:
            task_id: Celery 任务 ID。
        """
        try:
            redis = await self._get_redis()
            key = self._context_key()
            await redis.hset(key, CTX_FIELD_TASK_ID, task_id)
            self._logger.debug("task_id_saved", task_id=task_id)
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            self._logger.warning(
                "保存 task_id 失败",
                task_id=task_id,
                error=str(e),
            )

    async def get_task_id(self) -> Optional[str]:
        """从 Redis 上下文获取当前 Celery 任务 ID。

        Returns:
            任务 ID 字符串，不存在时返回 None。
        """
        try:
            redis = await self._get_redis()
            key = self._context_key()
            task_id = await redis.hget(key, CTX_FIELD_TASK_ID)
            return task_id.decode() if isinstance(task_id, bytes) else task_id
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            self._logger.warning("读取 task_id 失败", error=str(e))
            return None

    async def clear_task_id(self) -> None:
        """从 Redis 上下文清除 Celery 任务 ID。"""
        try:
            redis = await self._get_redis()
            key = self._context_key()
            await redis.hdel(key, CTX_FIELD_TASK_ID)
            self._logger.debug("task_id_cleared")
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            self._logger.warning("清除 task_id 失败", error=str(e))

    async def load_from_redis(self) -> bool:
        """从 Redis 恢复对局状态（Worker 重启后重建上下文）。

        **Why**: 当 Worker 进程重启或新 Worker 接管对局时，
        可以通过此方法从 Redis 中恢复全局状态和阶段信息，
        避免从头开始初始化。

        Returns:
            ``True`` 如果状态恢复成功，``False`` 如果 Redis 中无数据。
        """
        key = self._context_key()
        try:
            redis = await self._get_redis()
            raw = await redis.hgetall(key)
            if not raw:
                self._logger.info("no_context_to_restore")
                return False

            status = raw.get(CTX_FIELD_STATUS, "INIT")
            phase = raw.get(CTX_FIELD_PHASE, "None")
            round_num = raw.get(CTX_FIELD_ROUND, "0")

            self._logger.info(
                "context_restored",
                status=status,
                phase=phase,
                round=round_num,
            )
            return True
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            self._logger.error("状态恢复失败", error=str(e))
            return False
