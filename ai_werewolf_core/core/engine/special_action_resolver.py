"""
特殊行动结算器 (SpecialActionResolver) 模块。

**Why**: 白天部分角色拥有条件触发的即时技能（如猎人死亡开枪），
这些技能一旦触发就需要立刻执行状态变更，不存在延迟结算或撤销机制。
本模块遵循单一职责原则 (SRP)，从 GameEngine 主流程中拆分出专门的特殊行动
处理逻辑：

1. **条件校验**：判断当前是否处于允许特殊行动的阶段（如 ``HUNTER_SHOOT``），
   以及角色是否满足发动条件（如猎人已死亡、未被毒杀）。
2. **即时结算**：接收到动作后，立刻执行状态变更（如目标死亡），
   不等待阶段结束。
3. **中断恢复**：特殊行动结束后，返回处理结果供引擎决定下一个阶段。

**规则硬编码**: 所有技能触发条件、合法性校验均在 Python 代码中硬编码，
不依赖 LLM 判定。例如猎人被毒杀不能开枪的规则在此强制执行。

参考:
- :doc:`docs/plan/白天行动结算与投票管理器设计`
- :doc:`docs/agent.md`
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from ai_werewolf_core.core.event.bus import EventBus
from ai_werewolf_core.core.engine.exceptions import ActionValidationError
from ai_werewolf_core.core.engine.roles.base import BaseRole
from ai_werewolf_core.core.engine.roles.hunter import HunterRole
from ai_werewolf_core.schemas.enums import (
    ActionType,
    EventType,
    GamePhase,
    Role,
    Visibility,
)
from ai_werewolf_core.schemas.models import AgentAction, Event
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.utils.player_status import PlayerStatusManager

logger = get_logger(__name__)


# ------------------------------------------------------------------
# 特殊行动结算结果数据类
# ------------------------------------------------------------------


class SpecialActionResult:
    """特殊行动结算结果 —— 封装一次特殊行动结算后的状态变更信息。

    **Why**: 特殊行动可能产生多种副作用（死亡、状态变化），
    通过统一的数据类返回结果，便于引擎消费并决定后续阶段流转。

    Attributes:
        action_type: 处理的动作类型。
        was_handled: 是否成功处理（False 表示该阶段无符合条件的技能触发）。
        deaths_caused: 本次行动导致的死亡玩家 ID 列表。
        next_phase_hint: 建议的下一个阶段（可选，供引擎参考）。
    """

    def __init__(
        self,
        action_type: ActionType,
        was_handled: bool = False,
        deaths_caused: Optional[list[str]] = None,
        next_phase_hint: Optional[GamePhase] = None,
    ) -> None:
        self.action_type = action_type
        self.was_handled = was_handled
        self.deaths_caused: list[str] = deaths_caused or []
        self.next_phase_hint = next_phase_hint


# ------------------------------------------------------------------
# 特殊行动结算器
# ------------------------------------------------------------------


class SpecialActionResolver:
    """特殊行动结算器 —— 专职处理白天特殊角色的即时技能。

    作为 Game Engine 与特殊角色技能之间的中间层，负责：
    1. **条件校验**：判断当前阶段和角色状态是否允许触发技能。
    2. **即时结算**：校验通过后立即执行技能效果（如猎人开枪杀人）。
    3. **事件发布**：技能执行后通过 EventBus 发布相关事件。

    **当前支持的特殊行动**:
    - ``HUNTER_SHOOT``: 猎人死亡时开枪带走一名玩家。触发条件：
      猎人在当前对局中已死亡，且非被女巫毒杀（毒杀由外部判定）。
      猎人在 ``HUNTER_SHOOT`` 阶段可以选择开枪或 PASS。

    **Why (即时结算而非延迟)**: 猎人的开枪是死亡触发的连锁反应，
    必须在遗言阶段之前完成，否则遗言逻辑和后续阶段会失去上下文。
    即时结算确保状态一致性。

    Attributes:
        game_id: 对局唯一标识。
        event_bus: 事件总线实例。
        hunter_has_shot: 标记猎人是否已在本局使用过开枪技能。
            （猎人只能开一枪，防止重复触发）。
    """

    def __init__(self, game_id: str, event_bus: EventBus) -> None:
        """初始化特殊行动结算器。

        Args:
            game_id: 对局唯一标识，用于日志追踪和事件路由。
            event_bus: 事件总线实例，用于发布死亡事件和技能事件。
        """
        self.game_id: str = game_id
        self.event_bus: EventBus = event_bus
        self.hunter_has_shot: bool = False
        """标记猎人是否已在本局使用过开枪技能，防止重复触发。"""

        self._player_status: PlayerStatusManager = PlayerStatusManager()
        """玩家状态缓存管理器，用于同步更新 Redis BitMap。"""

        self._logger = logger.bind(
            game_id=self.game_id, module="SpecialActionResolver"
        )

    # ------------------------------------------------------------------
    # 统一处理入口
    # ------------------------------------------------------------------

    async def handle_action(
        self,
        action: AgentAction,
        roles: Dict[str, BaseRole],
        current_phase: GamePhase,
    ) -> SpecialActionResult:
        """处理特殊行动的入口方法。

        根据当前阶段和动作类型路由到对应的处理器。
        如果阶段或动作类型不匹配，返回 ``was_handled=False`` 的结果。

        **Why (路由分发而非直接判断)**: 未来可能扩展更多特殊角色
        （如白痴翻牌、守卫守护等），路由模式使得扩展只需新增 handler 方法，
        无需修改入口逻辑。

        Args:
            action: Agent 提交的动作。
            roles: 当前所有角色的映射 ``player_id → BaseRole``。
            current_phase: 当前游戏阶段。

        Returns:
            :class:`SpecialActionResult` 包含处理结果和建议的下阶段提示。

        Raises:
            ActionValidationError: 动作校验失败（如目标不存在、角色状态不符）。
        """
        # ── 猎人开枪 ──
        if current_phase == GamePhase.HUNTER_SHOOT:
            if action.action_type == ActionType.HUNTER_SHOOT:
                return await self._handle_hunter_shoot(action, roles)
            if action.action_type == ActionType.PASS:
                return await self._handle_hunter_pass(action, roles)

        # 未匹配任何特殊行动处理器
        self._logger.debug(
            "special_action_not_handled",
            action_type=action.action_type.value,
            current_phase=current_phase.value,
        )
        return SpecialActionResult(
            action_type=action.action_type,
            was_handled=False,
        )

    # ------------------------------------------------------------------
    # 猎人开枪处理器
    # ------------------------------------------------------------------

    async def _handle_hunter_shoot(
        self, action: AgentAction, roles: Dict[str, BaseRole]
    ) -> SpecialActionResult:
        """处理猎人开枪动作。

        执行完整的猎人开枪流程：
        1. 校验猎人身份与状态（必须是已死亡的猎人）。
        2. 校验开枪次数（每局只能开一枪）。
        3. 校验目标合法性（目标存在且存活，不能自杀）。
        4. 执行目标死亡。
        5. 发布死亡事件。
        6. 标记猎人已开枪。

        **Why (多重校验)**: 猎人的开枪是高影响力的技能，一旦执行错误
        会严重破坏游戏公平性。因此需要逐层校验每项前置条件。

        Args:
            action: 猎人提交的开枪动作（``action_type=HUNTER_SHOOT``）。
            roles: 角色映射。

        Returns:
            :class:`SpecialActionResult` 包含死亡信息和建议的下阶段提示。

        Raises:
            ActionValidationError: 任何前置条件不满足时抛出。
        """
        hunter_id = action.actor_id
        target_id = action.target_id

        # ── 校验 1: 行动者必须是猎人 ──
        hunter_role = roles.get(hunter_id)
        if hunter_role is None:
            raise ActionValidationError(
                action,
                f"行动者 [{hunter_id}] 不存在于当前对局中",
            )
        if hunter_role.role_type != Role.HUNTER:
            raise ActionValidationError(
                action,
                f"只有猎人能执行开枪动作，当前角色为 [{hunter_role.role_type.value}]",
            )

        # ── 校验 2: 猎人必须已死亡 ──
        # Why: 猎人只有在死亡时才能开枪，活着的猎人不能发动技能。
        if hunter_role.is_alive:
            raise ActionValidationError(
                action,
                f"猎人 [{hunter_id}] 仍然存活，无法开枪",
            )

        # ── 校验 3: 每局只能开一枪 ──
        # Why: 防止因系统逻辑错误或重复事件导致猎人多次开枪。
        if self.hunter_has_shot:
            raise ActionValidationError(
                action,
                f"猎人 [{hunter_id}] 已在本局使用过开枪技能",
            )

        # ── 校验 4: 必须有目标 ──
        # Why: 猎人可以放弃开枪（使用 PASS），但不能提交无目标的 HUNTER_SHOOT。
        if target_id is None:
            raise ActionValidationError(
                action,
                "猎人开枪必须指定目标",
            )

        # ── 校验 5: 不能自杀 ──
        # Why: 猎人已死，不能对自己开枪。虽然逻辑上"已死的猎人开枪打自己"无意义，
        # 但明确禁止可以防止 AI Agent 产生幻觉动作。
        if target_id == hunter_id:
            raise ActionValidationError(
                action,
                f"猎人不能对自己开枪: [{hunter_id}]",
            )

        # ── 校验 6: 目标存在且存活 ──
        target_role = roles.get(target_id)
        if target_role is None:
            raise ActionValidationError(
                action,
                f"开枪目标 [{target_id}] 不存在于当前对局中",
            )
        if not target_role.is_alive:
            raise ActionValidationError(
                action,
                f"开枪目标 [{target_id}] 已死亡",
            )

        # ── 执行死亡 ──
        target_role.die()
        # 同步更新 Redis BitMap 存活状态——多 Worker 一致性要求
        await self._player_status.mark_dead(
            self.game_id, target_id, target_role.seat_number
        )
        self.hunter_has_shot = True

        self._logger.info(
            "hunter_shot_executed",
            hunter_id=hunter_id,
            target_id=target_id,
            target_role=target_role.role_type.value,
        )

        # ── 发布死亡事件 ──
        death_event = Event(
            event_id=str(uuid.uuid4()),
            game_id=self.game_id,
            seq_num=0,  # EventBus 自动分配
            event_type=EventType.PLAYER_DEATH,
            visibility=Visibility.PUBLIC,
            target_agents=[target_id],
            timestamp=datetime.now(timezone.utc),
            payload={
                "player_id": target_id,
                "death_reason": "HUNTER_SHOT",
                "role_type": target_role.role_type.value,
                "faction": target_role.faction.value,
                "killed_by": hunter_id,
                "round": action.round,
            },
        )
        await self.event_bus.publish(death_event)

        # ── 发布技能使用事件 ──
        skill_event = Event(
            event_id=str(uuid.uuid4()),
            game_id=self.game_id,
            seq_num=0,
            event_type=EventType.SYSTEM_ANNOUNCEMENT,
            visibility=Visibility.PUBLIC,
            target_agents=[],
            timestamp=datetime.now(timezone.utc),
            payload={
                "announcement_type": "hunter_shot",
                "hunter_id": hunter_id,
                "target_id": target_id,
                "round": action.round,
            },
        )
        await self.event_bus.publish(skill_event)

        return SpecialActionResult(
            action_type=ActionType.HUNTER_SHOOT,
            was_handled=True,
            deaths_caused=[target_id],
            next_phase_hint=GamePhase.LAST_WORDS,
        )

    async def _handle_hunter_pass(
        self, action: AgentAction, roles: Dict[str, BaseRole]
    ) -> SpecialActionResult:
        """处理猎人放弃开枪。

        猎人在死亡后可以选择不发动技能（PASS）。
        此时不需要执行任何死亡操作，但需要标记技能已处理，
        防止引擎在 ``HUNTER_SHOOT`` 阶段无限等待。

        **Why (显式放弃而非隐式跳过)**: 要求猎人在 HUNTER_SHOOT 阶段
        明确提交 PASS 动作，可以避免引擎因等待超时而导致的死锁问题。

        Args:
            action: 猎人的 PASS 动作。
            roles: 角色映射。

        Returns:
            :class:`SpecialActionResult` 标记已处理但无死亡。
        """
        hunter_id = action.actor_id
        hunter_role = roles.get(hunter_id)

        if hunter_role is None:
            raise ActionValidationError(
                action,
                f"行动者 [{hunter_id}] 不存在于当前对局中",
            )
        if hunter_role.role_type != Role.HUNTER:
            raise ActionValidationError(
                action,
                f"只有猎人能执行此操作，当前角色为 [{hunter_role.role_type.value}]",
            )
        if hunter_role.is_alive:
            raise ActionValidationError(
                action,
                f"猎人 [{hunter_id}] 仍然存活，无需执行开枪/放弃操作",
            )

        self.hunter_has_shot = True  # 标记已处理（放弃也算使用）
        self._logger.info("hunter_passed", hunter_id=hunter_id)

        return SpecialActionResult(
            action_type=ActionType.PASS,
            was_handled=True,
            deaths_caused=[],
            next_phase_hint=GamePhase.LAST_WORDS,
        )

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    def has_hunter_shot(self) -> bool:
        """检查猎人是否已在本局使用过开枪技能（包括放弃）。

        Returns:
            ``True`` 如果猎人已开枪或已放弃。
        """
        return self.hunter_has_shot

    # ------------------------------------------------------------------
    # 生命周期管理
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """完全重置结算器状态。

        **Why**: 在对局结束或重新开始时调用，清空技能使用标记，
        确保新对局不会受上一局的状态污染。
        """
        self.hunter_has_shot = False
        self._logger.info("special_action_resolver_reset")
