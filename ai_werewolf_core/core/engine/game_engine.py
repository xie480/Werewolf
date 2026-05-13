"""GameEngine 编排器 —— 纯流程调度，不做规则判定。

**Why (编排器而非上帝对象)**:
GameEngine 不包含任何游戏规则逻辑。所有规则判定由以下组件完成：
- 阶段合法性 → PhaseStateMachine
- 生命周期合法性 → LifecycleManager
- 动作合法性 → ActionGate + Role System
- 夜晚结算 → ActionResolver
- 投票结算 → VoteManager
- 特殊行动结算 → SpecialActionResolver
- 胜负判定 → WinEvaluator

Engine 只负责"在正确的时机调用正确的组件"。

参考: :doc:`docs/plan/GameEngine编排器设计`
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import structlog

from ai_werewolf_core.core.action.gate import ActionGate, AdmitResult
from ai_werewolf_core.core.engine.evaluator import WinEvaluationResult, WinEvaluator
from ai_werewolf_core.core.engine.exceptions import (
    ActionValidationError,
    GameNotRunnableError,
)
from ai_werewolf_core.core.engine.lifecycle import (
    LifecycleManager,
    PHASE_TIMEOUT_NORMAL,
    PHASE_TIMEOUT_QUICK,
)
from ai_werewolf_core.core.engine.resolver import ActionResolver, NightResolveResult
from ai_werewolf_core.core.engine.roles.base import BaseRole
from ai_werewolf_core.core.engine.roles import create_role
from ai_werewolf_core.core.engine.player_manager import PlayerStatusManager
from ai_werewolf_core.schemas.enums import Role
from ai_werewolf_core.core.engine.special_action_resolver import (
    SpecialActionResult,
    SpecialActionResolver,
)
from ai_werewolf_core.core.engine.state_machine import PhaseStateMachine
from ai_werewolf_core.core.engine.vote_manager import VoteManager, VoteResolveResult
from ai_werewolf_core.core.event.bus import EventBus
from ai_werewolf_core.schemas.enums import (
    ActionType,
    GamePhase,
    GameStatus,
    NIGHT_ACT_PHASES,
    RESOLVE_PHASES,
    Role,
    SPEECH_PHASES,
    VOTE_PHASES,
)
from ai_werewolf_core.schemas.models import AgentAction

logger = structlog.get_logger(__name__)




# ============================================================================
# 数据类
# ============================================================================

@dataclass(frozen=True)
class GameStartResult:
    """对局启动结果。

    Attributes:
        game_id: 对局唯一标识。
        player_count: 参与对局的玩家总数。
        role_distribution: player_id → role_name 的身份分配映射。
        initial_phase: 启动后的初始阶段。
    """

    game_id: str
    player_count: int
    role_distribution: dict[str, str]
    initial_phase: GamePhase


@dataclass(frozen=True)
class SubmitResult:
    """动作提交结果。

    Attributes:
        accepted: 是否被接受。
        reason: 拒绝原因（仅在 accepted=False 时有意义）。
        requires_retry: Agent 是否可纠正后重试。
    """

    accepted: bool
    reason: Optional[str] = None
    requires_retry: bool = False

    @classmethod
    def accepted_result(cls) -> "SubmitResult":
        """快速构造通过结果。"""
        return cls(accepted=True)

    @classmethod
    def rejected_result(
        cls, reason: str, retry: bool = False
    ) -> "SubmitResult":
        """快速构造拒绝结果。

        Args:
            reason: 人类可读的拒绝原因。
            retry: Agent 是否可纠正参数后重试。
        """
        return cls(accepted=False, reason=reason, requires_retry=retry)


@dataclass(frozen=True)
class AdvanceResult:
    """阶段推进结果。

    Attributes:
        old_phase: 推进前的阶段。
        new_phase: 推进后的阶段。
        round: 当前轮次。
        deaths: 本阶段结算导致的死亡玩家 ID 列表。
        game_over: 游戏是否结束。
        winner: 胜利阵营（仅在 game_over=True 时有意义）。
        night_result: 夜晚结算结果（仅在 NIGHT_RESOLVE 后有值）。
        vote_result: 投票结算结果（仅在 DAY_VOTE/DAY_PK_VOTE 后有值）。
    """

    old_phase: GamePhase
    new_phase: GamePhase
    round: int
    deaths: list[str]
    game_over: bool = False
    winner: Optional[str] = None
    night_result: Optional[NightResolveResult] = None
    vote_result: Optional[VoteResolveResult] = None


# ============================================================================
# GameEngine
# ============================================================================

class GameEngine:
    """游戏引擎编排器 —— 纯流程调度，不做规则判定。

    作为 Game Engine 子系统的 Facade，对外暴露统一接口。
    内部持有所有子系统的引用，在 submit_action 和 advance_phase
    两个核心方法中按阶段路由到对应的 Manager。

    使用方式::

        engine = GameEngine(game_id, event_bus, roles)
        await engine.init_game()
        await engine.start_game()
        # 游戏主循环
        result = await engine.submit_action(action)
        advance = await engine.advance_phase()

    Attributes:
        game_id: 对局唯一标识（雪花 ID）。
        event_bus: 事件总线实例。
        roles: 角色映射 player_id → BaseRole。
        state_machine: 阶段状态机。
        lifecycle: 生命周期管理器。
        action_gate: 动作门控（防火墙）。
        resolver: 行动结算器。
        vote_manager: 投票管理器。
        special_action_resolver: 特殊行动结算器。
    """

    def __init__(
        self,
        game_id: str,
        event_bus: EventBus,
        roles: dict[str, BaseRole],
    ) -> None:
        """初始化游戏引擎编排器。

        Args:
            game_id: 对局唯一标识（雪花 ID）。
            event_bus: 事件总线实例。
            roles: 初始角色映射 player_id → BaseRole，
                由外部（对局初始化服务）在分配身份后传入。
        """
        self.game_id: str = game_id
        self.event_bus: EventBus = event_bus
        self.roles: dict[str, BaseRole] = roles

        # ── 子系统初始化 ──
        self.state_machine: PhaseStateMachine = PhaseStateMachine(
            game_id, event_bus
        )
        self.lifecycle: LifecycleManager = LifecycleManager(game_id, event_bus)
        self.action_gate: ActionGate = ActionGate(game_id)
        self.resolver: ActionResolver = ActionResolver(game_id, event_bus)
        self.vote_manager: VoteManager = VoteManager(game_id, event_bus)
        self.special_action_resolver: SpecialActionResolver = SpecialActionResolver(
            game_id, event_bus
        )

        self._logger = structlog.get_logger(__name__).bind(
            game_id=game_id, module="GameEngine"
        )

    # ==================================================================
    # 阶段定时器调度
    # ==================================================================

    async def schedule_phase_timer(self, phase: GamePhase) -> None:
        """为指定阶段调度 Celery 延迟任务。

        在成功进入新阶段后调用，根据阶段类型计算停留时长，
        投递延迟任务并在 Redis 中记录任务句柄。

        Args:
            phase: 刚进入的阶段（用于计算倒计时和下一步推进）。
        """
        duration = LifecycleManager.get_phase_timeout(phase)
        if duration <= 0:
            return  # GAME_OVER 等无需倒计时

        from ai_werewolf_core.tasks.game import advance_phase_task

        # 确定阶段结束后应进入的下一个阶段
        next_phase = self._determine_next_phase(
            phase,
            deaths=[],        # Celery 定时器不处理结算
            game_over=False,
            vote_result=None,
        )

        # 投递延迟任务，countdown=该阶段的停留时长
        task = advance_phase_task.apply_async(
            args=[self.game_id],
            kwargs={"expected_phase": phase.value},
            countdown=duration,
        )
        task_id = task.id
        await self.lifecycle.save_task_id(task_id)

        self._logger.info(
            "phase_timer_scheduled",
            phase=phase.value,
            next_phase=next_phase.value if next_phase else None,
            duration=duration,
            task_id=task_id,
        )

    # ==================================================================
    # 加载持久化状态（用于 Celery Worker 恢复 GameEngine）
    # ==================================================================

    @staticmethod
    async def load_roles_from_persistence(
        game_id: str,
    ) -> dict[str, BaseRole]:
        """从 Redis 加载角色数据并重建 BaseRole 实例。

        用于 Celery Worker 中重建 GameEngine。

        Args:
            game_id: 对局唯一标识。

        Returns:
            ``player_id → BaseRole`` 角色映射。
        """
        from ai_werewolf_core.core.engine.roles import create_role

        mgr = PlayerStatusManager()
        players = await mgr.get_all_players(game_id)

        roles: dict[str, BaseRole] = {}
        for pid, info in players.items():
            role_type = Role(info["role"])
            seat = info["seat"]
            role = create_role(role_type, pid)
            # 检查存活状态
            is_alive = await mgr.is_alive(game_id, seat)
            if not is_alive:
                role.die()
            roles[pid] = role
        return roles

    # ==================================================================
    # 公开接口: 对局生命周期
    # ==================================================================

    async def init_game(self) -> None:
        """初始化对局: 写入 Redis 上下文，进入 INIT 阶段。

        调用 LifecycleManager.init_game() 完成:
        1. 初始化 Redis 对局上下文 (status=INIT, phase=None, round=0)
        2. 迁移到 START 状态
        3. 初始化 PhaseStateMachine (进入 INIT 阶段)

        此方法在对局创建时由 API 层调用。
        """
        self._logger.info("engine_init_game")
        await self.lifecycle.init_game()

    async def start_game(self) -> GameStartResult:
        """启动对局: START → RUNNING，进入首轮 NIGHT_START。

        调用 LifecycleManager.start_game()，激活阶段状态机进入首轮黑夜。
        返回 GameStartResult 包含初始对局信息（玩家人数、角色分配等）。

        Returns:
            GameStartResult 实例。
        """
        self._logger.info("engine_start_game")

        await self.lifecycle.start_game()

        # 构建角色分配信息
        role_distribution: dict[str, str] = {
            pid: role.role_type.value for pid, role in self.roles.items()
        }

        return GameStartResult(
            game_id=self.game_id,
            player_count=len(self.roles),
            role_distribution=role_distribution,
            initial_phase=GamePhase.NIGHT_START,
        )

    async def end_game(self, winner_faction: str) -> None:
        """正常结束对局: RUNNING → SETTLING → FINISHED。

        Args:
            winner_faction: 胜利阵营标识，如 "VILLAGER" 或 "WEREWOLF"。
        """
        self._logger.info("engine_end_game", winner_faction=winner_faction)
        await self.lifecycle.end_game(winner_faction)

    async def abort_game(self, reason: str) -> None:
        """异常中止对局: 任意可中止状态 → ABORTED。

        Args:
            reason: 中止原因（如 "player_disconnected"）。
        """
        self._logger.warning("engine_abort_game", reason=reason)
        await self.lifecycle.abort_game(reason)

    # ==================================================================
    # 公开接口: 动作提交（Agent → Engine 的核心入口）
    # ==================================================================

    async def submit_action(self, action: AgentAction) -> SubmitResult:
        """接收 Agent 提交的动作，经门控校验后路由到对应 Manager。

        处理流程:
        1. ActionGate.admit()      —— 纯规则防火墙
        2. 按阶段路由到 Manager    —— 游戏规则校验 + 业务逻辑

        路由规则:
        - 夜晚阶段 → ActionResolver.submit_action()
        - 投票阶段 → VoteManager.submit_vote()
        - 特殊行动阶段 (HUNTER_SHOOT) → SpecialActionResolver.handle_action()
        - 发言阶段 → Engine 直接处理
        - 结算阶段 → 拒绝（不接受动作提交）

        Args:
            action: Agent 提交的动作。

        Returns:
            SubmitResult 包含接受/拒绝状态及原因。
        """
        # ── 获取行动者的角色对象 ──
        role = self.roles.get(action.actor_id)
        if role is None:
            return SubmitResult.rejected_result(
                f"未知玩家: {action.actor_id}", retry=False
            )

        # ── Step 1: ActionGate 门控校验 ──
        current_phase = await self.state_machine.get_current_phase()
        if current_phase is None:
            return SubmitResult.rejected_result(
                "游戏尚未开始", retry=False
            )

        admit_result = await self.action_gate.admit(
            action, role, self.roles, current_phase
        )
        if not admit_result.admitted:
            return SubmitResult.rejected_result(
                admit_result.reason,
                retry=self._is_retryable(admit_result),
            )

        # ── Step 2: 按阶段路由到对应 Manager ──
        try:
            if current_phase in NIGHT_ACT_PHASES:
                self.resolver.submit_action(action, self.roles, current_phase)
            elif current_phase in VOTE_PHASES:
                await self.vote_manager.submit_vote(
                    action, self.roles, current_phase
                )
            elif current_phase == GamePhase.HUNTER_SHOOT:
                await self.special_action_resolver.handle_action(
                    action, self.roles, current_phase
                )
            elif current_phase in SPEECH_PHASES:
                # 发言阶段直接接受（ActionGate 已做基础校验）
                pass
            elif current_phase in RESOLVE_PHASES:
                return SubmitResult.rejected_result(
                    f"当前阶段 {current_phase.value} 不接受动作提交",
                    retry=False,
                )
            else:
                return SubmitResult.rejected_result(
                    f"未知阶段: {current_phase.value}",
                    retry=False,
                )
        except ActionValidationError as e:
            return SubmitResult.rejected_result(str(e), retry=False)

        # ── Step 3: 检查是否满足提前结束条件 ──
        try:
            await self._check_early_termination(current_phase)
        except Exception as e:
            # 提前结束失败不影响动作提交本身
            self._logger.warning(
                "early_termination_check_failed",
                phase=current_phase.value,
                error=str(e),
            )

        return SubmitResult.accepted_result()

    # ==================================================================
    # 提前结束检测
    # ==================================================================

    async def _check_early_termination(self, current_phase: GamePhase) -> None:
        """检查当前阶段是否满足提前结束条件。

        当阶段所需的所有动作已收集完毕时，取消定时任务并立即推进阶段。

        Args:
            current_phase: 当前游戏阶段。
        """
        is_completed = False

        if current_phase in NIGHT_ACT_PHASES:
            is_completed = self.resolver.is_action_completed(self.roles, current_phase)
        elif current_phase in VOTE_PHASES:
            is_completed = await self.vote_manager.is_action_completed(self.roles)

        if not is_completed:
            return

        # 所有动作已收集完毕 → 取消定时器并立即推进
        self._logger.info(
            "early_termination_triggered",
            phase=current_phase.value,
        )

        # 取消 Celery 延迟任务
        try:
            task_id = await self.lifecycle.get_task_id()
            if task_id:
                from celery.app.control import Control
                from ai_werewolf_core.worker import celery_app

                control = Control(celery_app)
                control.revoke(task_id, terminate=False)
                await self.lifecycle.clear_task_id()
                self._logger.info("timer_revoked", task_id=task_id)
        except Exception as e:
            self._logger.warning(
                "timer_revoke_failed",
                error=str(e),
            )

        # 立即推进阶段
        await self.advance_phase()

    # ==================================================================
    # 公开接口: 阶段推进（Engine 内部自驱 or 外部触发）
    # ==================================================================

    async def advance_phase(self) -> AdvanceResult:
        """推进到下一个合法阶段。

        此方法是游戏主循环的核心驱动。Engine 根据当前阶段决定下一步:
        1. 读取当前阶段
        2. 执行当前阶段的退出逻辑（结算/胜负判定）
        3. 确定下一阶段
        4. 调用 lifecycle.advance_phase() 完成迁移

        Returns:
            AdvanceResult 包含旧阶段、新阶段、死亡名单、胜负信息等。
        """
        old_phase = await self.state_machine.get_current_phase()
        if old_phase is None:
            raise GameNotRunnableError(
                current_status=GameStatus.INIT,
                game_id=self.game_id,
            )
        round_num = await self.state_machine.get_round()

        self._logger.info(
            "advance_phase_start",
            old_phase=old_phase.value,
            round=round_num,
        )

        # ── Step 1: 执行当前阶段的退出逻辑（结算） ──
        deaths: list[str] = []
        night_result: Optional[NightResolveResult] = None
        vote_result: Optional[VoteResolveResult] = None
        game_over = False
        winner: Optional[str] = None

        if old_phase == GamePhase.NIGHT_RESOLVE:
            night_result = await self.resolver.resolve_night_actions(self.roles)
            deaths = night_result.final_deaths
            eval_result = WinEvaluator.evaluate_detailed(self.roles)
            if eval_result.is_game_over:
                game_over = True
                winner = (
                    eval_result.winner.value if eval_result.winner else None
                )

        elif old_phase in VOTE_PHASES:
            vote_result = await self.vote_manager.resolve_vote(
                self.roles, round_num
            )
            if vote_result.sole_voted_out:
                deaths = [vote_result.sole_voted_out]
            eval_result = WinEvaluator.evaluate_detailed(self.roles)
            if eval_result.is_game_over:
                game_over = True
                winner = (
                    eval_result.winner.value if eval_result.winner else None
                )

        # ── Step 2: 确定下一阶段 ──
        next_phase = self._determine_next_phase(
            old_phase, deaths, game_over, vote_result
        )

        # ── Step 3: 执行阶段迁移 ──
        if next_phase == GamePhase.NIGHT_START:
            self.resolver.begin_night()

        await self.lifecycle.advance_phase(next_phase)

        # ── Step 4: 调度阶段定时器（后台自动推进） ──
        if next_phase != GamePhase.GAME_OVER:
            await self.schedule_phase_timer(next_phase)

        # ── Step 5: 如果游戏结束，调用 end_game ──
        if next_phase == GamePhase.GAME_OVER:
            await self.lifecycle.end_game(winner or "UNKNOWN")

        self._logger.info(
            "advance_phase_complete",
            old_phase=old_phase.value,
            new_phase=next_phase.value,
            deaths=deaths,
            game_over=game_over,
        )

        return AdvanceResult(
            old_phase=old_phase,
            new_phase=next_phase,
            round=round_num,
            deaths=deaths,
            game_over=game_over,
            winner=winner,
            night_result=night_result,
            vote_result=vote_result,
        )

    def _determine_next_phase(
        self,
        current_phase: GamePhase,
        deaths: list[str],
        game_over: bool,
        vote_result: Optional[VoteResolveResult],
    ) -> GamePhase:
        """根据当前阶段和结算结果确定下一个阶段。

        **Why (独立方法)**: 将阶段决策逻辑从推进流程中分离，
        使得决策规则可被单独测试和审计。

        决策依赖:
        - PhaseStateMachine.VALID_TRANSITIONS 中定义的合法后继集合
        - 结算结果（死亡名单、平票情况、胜负判定）
        - 角色状态（猎人是否死亡/是否被毒杀）

        Args:
            current_phase: 当前阶段。
            deaths: 本阶段结算导致的死亡玩家 ID 列表。
            game_over: 胜负判定结果（is_game_over）。
            vote_result: 投票结算结果（可选）。

        Returns:
            下一个阶段。
        """
        # ── 优先检查 game_over ──
        if game_over:
            return GamePhase.GAME_OVER

        # ── 根据当前阶段决定下一阶段 ──
        if current_phase == GamePhase.INIT:
            return GamePhase.NIGHT_START

        elif current_phase == GamePhase.NIGHT_START:
            return GamePhase.NIGHT_WOLF_ACT

        elif current_phase == GamePhase.NIGHT_WOLF_ACT:
            return GamePhase.NIGHT_WITCH_ACT

        elif current_phase == GamePhase.NIGHT_WITCH_ACT:
            return GamePhase.NIGHT_SEER_ACT

        elif current_phase == GamePhase.NIGHT_SEER_ACT:
            return GamePhase.NIGHT_RESOLVE

        elif current_phase == GamePhase.NIGHT_RESOLVE:
            return GamePhase.DAY_START

        elif current_phase == GamePhase.DAY_START:
            if deaths and self._has_dead_hunter(deaths):
                return GamePhase.HUNTER_SHOOT
            if deaths:
                return GamePhase.DAY_DISCUSSION
            return GamePhase.DAY_DISCUSSION

        elif current_phase == GamePhase.DAY_DISCUSSION:
            return GamePhase.DAY_VOTE

        elif current_phase == GamePhase.DAY_VOTE:
            if vote_result and vote_result.is_tie:
                return GamePhase.DAY_PK_DISCUSSION
            return GamePhase.VOTE_RESOLVE

        elif current_phase == GamePhase.DAY_PK_DISCUSSION:
            return GamePhase.DAY_PK_VOTE

        elif current_phase == GamePhase.DAY_PK_VOTE:
            return GamePhase.VOTE_RESOLVE

        elif current_phase == GamePhase.VOTE_RESOLVE:
            if deaths and self._has_dead_hunter(deaths):
                return GamePhase.HUNTER_SHOOT
            if deaths:
                return GamePhase.LAST_WORDS
            return GamePhase.NIGHT_START

        elif current_phase == GamePhase.HUNTER_SHOOT:
            return GamePhase.DAY_DISCUSSION

        elif current_phase == GamePhase.LAST_WORDS:
            return GamePhase.NIGHT_START

        elif current_phase == GamePhase.GAME_OVER:
            return GamePhase.GAME_OVER

        # ── fallback: 使用 VALID_TRANSITIONS 的第一个合法后继 ──
        valid = PhaseStateMachine.VALID_TRANSITIONS.get(current_phase, [])
        if valid:
            return valid[0]
        return GamePhase.GAME_OVER

    # ==================================================================
    # 公开接口: 查询
    # ==================================================================

    async def get_game_state(self) -> dict:
        """获取当前对局的完整快照（供 API 和 WebSocket 使用）。

        Returns:
            包含 status, phase, round, players (含存活状态) 的字典。
        """
        status = await self.lifecycle.get_status()
        phase = await self.state_machine.get_current_phase()
        round_num = await self.state_machine.get_round()

        players_info = {}
        for pid, role in self.roles.items():
            players_info[pid] = {
                "role": role.role_type.value,
                "faction": role.faction.value,
                "is_alive": role.is_alive,
                "player_id": pid,
            }

        return {
            "game_id": self.game_id,
            "status": status.value,
            "phase": phase.value if phase else None,
            "round": round_num,
            "players": players_info,
        }

    async def get_status(self) -> GameStatus:
        """获取当前全局生命周期状态。"""
        return await self.lifecycle.get_status()

    async def get_current_phase(self) -> Optional[GamePhase]:
        """获取当前游戏阶段。"""
        return await self.state_machine.get_current_phase()

    async def get_round(self) -> int:
        """获取当前轮次。"""
        return await self.state_machine.get_round()

    # ==================================================================
    # 辅助方法
    # ==================================================================

    def _has_dead_hunter(self, deaths: list[str]) -> bool:
        """检查死亡名单中是否包含猎人。

        Args:
            deaths: 死亡玩家 ID 列表。

        Returns:
            True 如果死亡名单中有猎人。
        """
        for pid in deaths:
            role = self.roles.get(pid)
            if role is not None and role.role_type == Role.HUNTER:
                return True
        return False

    @staticmethod
    def _is_retryable(admit_result: AdmitResult) -> bool:
        """判断门控拒绝是否可重试。

        Args:
            admit_result: ActionGate 的准入结果。

        Returns:
            True 如果 Agent 可以纠正后重试。
        """
        if "冷却" in admit_result.reason or "cooldown" in admit_result.rejected_by:
            return True
        if "阶段不匹配" in admit_result.reason or "phase" in admit_result.rejected_by:
            return True
        if "survival" in admit_result.rejected_by:
            return True
        return False
