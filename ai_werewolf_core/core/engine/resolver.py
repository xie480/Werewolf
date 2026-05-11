"""
行动解析与结算器 (Action Resolver) 模块。

**Why**: 本模块是 Game Engine 中最复杂的逻辑处理中心，负责：
1. 接收并暂存 Agent 提交的动作意图（Intent），通过 Role System 校验合法性。
2. 在夜晚串行执行架构下，作为"夜间状态暂存器 (Draft State Manager)"，
   维护预期死亡名单（pending_deaths），并在夜晚结束时统一结算。

**核心设计**:
- 串行状态暂存：夜晚阶段严格串行执行（狼人 → 女巫 → 预言家）。
- 状态快照与应用：结算过程中，先在内存中维护"预期死亡名单"。
  狼人刀人加入名单，女巫救人移出名单，女巫毒人加入名单。
  待所有夜间动作结算完毕后，统一应用状态变更并生成天亮信息。

参考: :doc:`docs/plan/行动结算与胜负判定设计`
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from ai_werewolf_core.core.engine.exceptions import (
    ActionValidationError,
    ResolverError,
)
from ai_werewolf_core.core.engine.roles.base import BaseRole
from ai_werewolf_core.core.engine.roles.witch import WitchRole
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
from ai_werewolf_core.core.engine.player_manager import PlayerStatusManager

logger = get_logger(__name__)


# ------------------------------------------------------------------
# 结算结果数据类
# ------------------------------------------------------------------


class NightResolveResult:
    """夜晚结算结果 —— 封装一夜结算后的完整状态变更信息。

    **Why**: 将结算结果封装为不可变的数据类，便于 Game Engine 和
    EventBus 下游模块统一消费，避免通过多个返回值传递分散信息。

    Attributes:
        final_deaths: 最终死亡玩家 ID 列表（按死亡原因排序）。
        death_reasons: ``player_id → 死亡原因`` 映射（WOLF_KILL / WITCH_POISON）。
        saved_players: 被女巫解药救活的玩家 ID 集合。
        poisoned_players: 被女巫毒药毒杀的玩家 ID 集合。
        wolf_target: 狼人原始刀人目标（可能被救活）。
        processed_actions: 本夜成功处理的动作数量。
    """

    def __init__(
        self,
        final_deaths: List[str],
        death_reasons: Dict[str, ActionType],
        saved_players: Set[str],
        poisoned_players: Set[str],
        wolf_target: Optional[str],
        processed_actions: int,
    ) -> None:
        self.final_deaths = final_deaths
        self.death_reasons = death_reasons
        self.saved_players = saved_players
        self.poisoned_players = poisoned_players
        self.wolf_target = wolf_target
        self.processed_actions = processed_actions

    @property
    def total_deaths(self) -> int:
        """本夜总死亡人数。"""
        return len(self.final_deaths)

    @property
    def is_peaceful_night(self) -> bool:
        """是否平安夜（无人死亡）。"""
        return self.total_deaths == 0


# ------------------------------------------------------------------
# Action Resolver
# ------------------------------------------------------------------


class ActionResolver:
    """行动解析与结算器。

    作为 Game Engine 与 Agent 之间的中间层，负责：
    1. **接收与暂存**：在夜晚各阶段（NIGHT_WOLF_ACT / NIGHT_WITCH_ACT /
       NIGHT_SEER_ACT）接收 Agent 提交的动作，校验合法性后暂存。
    2. **夜晚统一结算**：在 NIGHT_RESOLVE 阶段汇总理死亡名单，
       按"狼刀 → 女巫救 → 女巫毒"的规则计算最终死亡玩家列表。
    3. **事件发布**：结算完成后通过 EventBus 发布 PLAYER_DEATH 事件。

    **Why (串行暂存而非实时结算)**: 狼人杀夜晚有严格的因果链——
    女巫的解药/毒药必须在知道狼人目标的前提下才能发挥作用。
    因此，在内存中维护 ``pending_deaths`` 草稿状态，
    到 NIGHT_RESOLVE 阶段一次性结算，确保逻辑正确且可审计。

    Attributes:
        game_id: 对局唯一标识。
        event_bus: 事件总线实例。
        pending_deaths: 预期死亡名单草稿 (``player_id → 杀害原因``)。
            - 狼人刀人 → 加入此映射。
            - 女巫救人 → 从此映射移除（仅能救狼刀目标）。
            - 女巫毒人 → 加入此映射。
        pending_actions: 当前夜晚已收集但尚未结算的动作列表。
        _current_night_wolf_target: 当前夜晚狼人的原始刀人目标（用于女巫救人的合法性校验）。
        _night_resolved: 标记当前夜晚是否已完成结算，防重复结算。
    """

    def __init__(self, game_id: str, event_bus: EventBus) -> None:
        """初始化行动结算器。

        Args:
            game_id: 对局唯一标识。
            event_bus: 事件总线实例，用于发布结算事件。
        """
        self.game_id: str = game_id
        self.event_bus: EventBus = event_bus

        # 夜晚草稿状态
        self.pending_deaths: Dict[str, ActionType] = {}
        """预期死亡名单: player_id → 死亡原因 (WOLF_KILL | WITCH_POISON)"""

        self.pending_actions: List[AgentAction] = []
        """当前夜晚已收集但尚未结算的动作列表。"""

        self._current_night_wolf_target: Optional[str] = None
        """当前夜晚狼人的原始刀人目标（用于女巫救人校验）。"""

        self._night_resolved: bool = False
        """标记当前夜晚是否已完成结算（防止在同一夜多次调用 resolve）。"""

        self._player_status: PlayerStatusManager = PlayerStatusManager()
        """玩家状态缓存管理器，用于同步更新 Redis BitMap。"""

        self._logger = logger.bind(game_id=self.game_id, module="ActionResolver")

    # ------------------------------------------------------------------
    # 夜晚周期管理
    # ------------------------------------------------------------------

    def begin_night(self) -> None:
        """开启新的夜晚结算周期。

        **Why**: 每轮夜晚开始前必须调用此方法，清空上一轮的草稿状态。
        这确保跨轮次的状态不会污染（例如上一轮的 pending_deaths 残留导致误判）。
        通常在状态机进入 NIGHT_START 阶段时由 Game Engine 调用。
        """
        self.pending_deaths.clear()
        self.pending_actions.clear()
        self._current_night_wolf_target = None
        self._night_resolved = False
        self._logger.info("night_cycle_begin")

    # ------------------------------------------------------------------
    # 动作接收与校验
    # ------------------------------------------------------------------

    def submit_action(
        self,
        action: AgentAction,
        roles: Dict[str, BaseRole],
        current_phase: GamePhase,
    ) -> bool:
        """接收并暂存 Agent 提交的动作。

        此方法执行两阶段校验：
        1. **阶段校验**：动作的预期阶段是否与当前阶段一致。
        2. **角色校验**：通过 Role System 的 :meth:`BaseRole.validate_action`
           判定该角色在当前阶段是否有权执行此动作。

        **Why (校验与暂存分离)**: 校验成功不代表立即生效——夜晚动作
        需要在 NIGHT_RESOLVE 阶段统一结算。因此通过校验的动作暂存于
        ``pending_actions``，校验失败的则立即抛出异常（无副作用）。

        Args:
            action: Agent 提交的动作（AgentAction 模型）。
            roles: 当前所有角色的映射 ``player_id → BaseRole``。
            current_phase: 当前游戏阶段。

        Returns:
            ``True`` 表示动作已通过校验并暂存成功。

        Raises:
            ActionValidationError: 动作非法（阶段不匹配 / 角色无权限 / 目标不存在等）。
        """
        # ── 校验 1: 阶段匹配 ──
        if action.phase != current_phase:
            raise ActionValidationError(
                action,
                f"阶段不匹配: 动作声明 [{action.phase.value}]，"
                f"实际当前阶段 [{current_phase.value}]",
            )

        # ── 校验 2: 行动者存在且存活 ──
        actor_role = roles.get(action.actor_id)
        if actor_role is None:
            raise ActionValidationError(
                action,
                f"行动者 [{action.actor_id}] 不存在于当前对局中",
            )
        if not actor_role.is_alive:
            raise ActionValidationError(
                action,
                f"行动者 [{action.actor_id}] 已死亡，无法执行动作",
            )

        # ── 校验 3: Role System 合法性判定 ──
        if not actor_role.validate_action(current_phase, action.action_type):
            raise ActionValidationError(
                action,
                f"角色 [{actor_role.role_type.value}] 在阶段 [{current_phase.value}] "
                f"无权执行动作 [{action.action_type.value}]",
            )

        # ── 校验 4: 目标有效性（仅针对需要目标的操作） ──
        self._validate_target(action, roles, current_phase)

        # ── 暂存动作 ──
        self.pending_actions.append(action)
        self._logger.info(
            "action_submitted",
            actor_id=action.actor_id,
            action_type=action.action_type.value,
            target_id=action.target_id,
            phase=current_phase.value,
            total_pending=len(self.pending_actions),
        )

        # ── 更新草稿死亡名单（仅夜晚致命动作） ──
        self._update_draft_deaths(action, actor_role)

        return True

    def _validate_target(
        self,
        action: AgentAction,
        roles: Dict[str, BaseRole],
        current_phase: GamePhase,
    ) -> None:
        """校验动作目标的合法性。

        **Why**: 不同类型的动作对目标有不同的要求：
        - WOLF_KILL / WITCH_POISON / SEER_CHECK 必须有目标。
        - WITCH_SAVE 必须有目标，且仅能救狼人的原始刀人目标。
        - PASS / SPEAK / VOTE 可以无目标。

        Args:
            action: 待校验的动作。
            roles: 角色映射。
            current_phase: 当前阶段。

        Raises:
            ActionValidationError: 目标不合法。
        """
        requires_target = action.action_type in (
            ActionType.WOLF_KILL,
            ActionType.WITCH_SAVE,
            ActionType.WITCH_POISON,
            ActionType.SEER_CHECK,
            ActionType.HUNTER_SHOOT,
            ActionType.VOTE,
        )

        if requires_target and action.target_id is None:
            raise ActionValidationError(
                action,
                f"动作 [{action.action_type.value}] 必须指定目标",
            )

        if action.target_id is not None:
            # 目标必须存在于对局中
            target_role = roles.get(action.target_id)
            if target_role is None:
                raise ActionValidationError(
                    action,
                    f"目标 [{action.target_id}] 不存在于当前对局中",
                )

            # WITCH_SAVE 仅能救狼人原始刀人目标
            # Why: 标准狼人杀规则——女巫的解药只能救当晚被狼人杀害的玩家，
            # 不能凭空复活已死的玩家或救毒杀目标。
            if action.action_type == ActionType.WITCH_SAVE:
                if self._current_night_wolf_target is None:
                    raise ActionValidationError(
                        action,
                        "狼人尚未行动，女巫无法使用解药（无刀口目标）",
                    )
                if action.target_id != self._current_night_wolf_target:
                    raise ActionValidationError(
                        action,
                        f"解药只能救狼人今晚的刀口目标 [{self._current_night_wolf_target}]，"
                        f"不能救 [{action.target_id}]",
                    )

    def _update_draft_deaths(
        self, action: AgentAction, actor_role: BaseRole
    ) -> None:
        """根据动作类型更新草稿死亡名单。

        **Why (草稿更新而非实时生效)**: 这是整个 Resolver 的核心设计——
        狼刀和女巫毒药只是"预期"死亡，女巫的解药可以在后续步骤中反转。
        因此先在内存中维护 pending_deaths 草稿，最终在 resolve_night_actions
        中统一应用。

        **规则**:
        - ``WOLF_KILL`` → 将目标加入 pending_deaths，记录狼人原始目标。
        - ``WITCH_SAVE`` → 从 pending_deaths 移除被救目标（仅移除以 WOLF_KILL 为原因的条目）。
        - ``WITCH_POISON`` → 将目标加入 pending_deaths（reason=WITCH_POISON）。

        Args:
            action: 已通过校验的动作。
            actor_role: 行动者的角色实例。
        """
        if action.action_type == ActionType.WOLF_KILL and action.target_id is not None:
            self.pending_deaths[action.target_id] = ActionType.WOLF_KILL
            self._current_night_wolf_target = action.target_id
            self._logger.info(
                "draft_wolf_kill",
                target_id=action.target_id,
            )

        elif action.action_type == ActionType.WITCH_SAVE and action.target_id is not None:
            # 仅当目标在 pending_deaths 中且原因为 WOLF_KILL 时，才能移除
            # Why: 解药只能反制狼刀，不能反解毒药或之前的死亡
            if (
                action.target_id in self.pending_deaths
                and self.pending_deaths[action.target_id] == ActionType.WOLF_KILL
            ):
                del self.pending_deaths[action.target_id]
                self._logger.info(
                    "draft_witch_save",
                    target_id=action.target_id,
                )
            else:
                self._logger.warning(
                    "witch_save_no_effect",
                    target_id=action.target_id,
                    reason="目标不在狼刀死亡名单中",
                )

        elif action.action_type == ActionType.WITCH_POISON and action.target_id is not None:
            self.pending_deaths[action.target_id] = ActionType.WITCH_POISON
            self._logger.info(
                "draft_witch_poison",
                target_id=action.target_id,
            )

    # ------------------------------------------------------------------
    # 夜晚结算
    # ------------------------------------------------------------------

    async def resolve_night_actions(
        self, roles: Dict[str, BaseRole]
    ) -> NightResolveResult:
        """结算夜晚所有动作，计算最终死亡名单并应用状态变更。

        此方法在 NIGHT_RESOLVE 阶段由 Game Engine 调用，执行以下步骤：

        1. **消费物品**: 对使用了 WITCH_SAVE / WITCH_POISON 的女巫，调用其
           :meth:`WitchRole.use_antidote` / :meth:`WitchRole.use_poison` 消费药品。
        2. **确定最终死亡**: ``pending_deaths`` 即为最终死亡名单。
        3. **应用状态变更**: 调用 :meth:`BaseRole.die` 标记死亡角色。
        4. **发布死亡事件**: 为每个死亡玩家发布 ``PLAYER_DEATH`` 事件。

        **Why (此处分步执行而非合并)**: 消费药品是业务副作用，
        死亡状态变更是游戏状态副作用，死亡事件发布是通知副作用——
        三者职责不同，分步执行有助于故障定位和测试隔离。

        Args:
            roles: 当前所有角色的映射 ``player_id → BaseRole``。
                结算过程中会对角色的 ``is_alive`` 状态和女巫药品状态进行修改。

        Returns:
            :class:`NightResolveResult` 包含最终死亡名单、死亡原因、
            救活玩家列表等完整结算信息。

        Raises:
            ResolverError: 同一夜晚重复结算。
        """
        if self._night_resolved:
            raise ResolverError(
                self.game_id,
                "当前夜晚已完成结算，不允许重复调用 resolve_night_actions。"
                "请先调用 begin_night() 开启新的夜晚周期。",
            )

        self._logger.info(
            "night_resolve_start",
            pending_actions_count=len(self.pending_actions),
            pending_deaths_count=len(self.pending_deaths),
        )

        # ── Step 1: 消费女巫药品 ──
        saved_players, poisoned_players = self._consume_witch_items(roles)

        # ── Step 2: 确定最终死亡名单 ──
        # Why: pending_deaths 已经过"狼刀 → 女巫救 → 女巫毒"的草稿更新，
        # 此时的内容即为最终死亡名单。
        final_deaths: List[str] = list(self.pending_deaths.keys())
        death_reasons: Dict[str, ActionType] = dict(self.pending_deaths)

        # ── Step 3: 应用死亡状态变更 ──
        for player_id in final_deaths:
            role = roles.get(player_id)
            if role is not None and role.is_alive:
                role.die()
                # 同步更新 Redis BitMap 存活状态——多 Worker 一致性要求
                await self._player_status.mark_dead(
                    self.game_id, player_id, role.seat_number
                )
                self._logger.info(
                    "player_died",
                    player_id=player_id,
                    cause=death_reasons[player_id].value,
                )

        # ── Step 4: 发布死亡事件 ──
        await self._publish_death_events(final_deaths, death_reasons, roles)

        # ── Step 5: 构建结算结果 ──
        result = NightResolveResult(
            final_deaths=final_deaths,
            death_reasons=death_reasons,
            saved_players=saved_players,
            poisoned_players=poisoned_players,
            wolf_target=self._current_night_wolf_target,
            processed_actions=len(self.pending_actions),
        )

        self._night_resolved = True
        self._logger.info(
            "night_resolve_complete",
            final_deaths_count=result.total_deaths,
            saved_count=len(saved_players),
            poisoned_count=len(poisoned_players),
            is_peaceful_night=result.is_peaceful_night,
        )

        return result

    def _consume_witch_items(
        self, roles: Dict[str, BaseRole]
    ) -> Tuple[Set[str], Set[str]]:
        """消费女巫的药品（解药 / 毒药）。

        **Why (结算时消费而非 submit 时消费)**:
        如果 Agent 提交了 WITCH_SAVE 但后来因为某种原因结算失败，
        药品不应被消费。将消费时机推迟到结算成功确认后，
        避免状态污染。

        遍历 pending_actions 中的女巫动作，对 WITCH_SAVE 消费解药，
        对 WITCH_POISON 消费毒药。

        Args:
            roles: 角色映射，用于定位女巫实例。

        Returns:
            ``(saved_players, poisoned_players)`` 二元组：
            - saved_players: 被解药救活的玩家 ID 集合。
            - poisoned_players: 被毒药毒杀的玩家 ID 集合。

        Raises:
            ResolverError: 如果同一药品被多次消费（逻辑错误）。
        """
        saved_players: Set[str] = set()
        poisoned_players: Set[str] = set()

        consumed_antidote: Set[str] = set()
        """记录已消费解药的女巫 ID，防止同一位女巫的多个 WITCH_SAVE 动作重复消费。"""

        consumed_poison: Set[str] = set()
        """记录已消费毒药的女巫 ID，防止重复消费。"""

        for action in self.pending_actions:
            if action.action_type == ActionType.WITCH_SAVE and action.target_id is not None:
                witch = roles.get(action.actor_id)
                if isinstance(witch, WitchRole):
                    if action.actor_id in consumed_antidote:
                        continue  # 同一位女巫的重复动作，跳过
                    witch.use_antidote()
                    consumed_antidote.add(action.actor_id)
                    saved_players.add(action.target_id)
                    self._logger.info(
                        "antidote_consumed",
                        witch_id=action.actor_id,
                        target_id=action.target_id,
                    )

            elif action.action_type == ActionType.WITCH_POISON and action.target_id is not None:
                witch = roles.get(action.actor_id)
                if isinstance(witch, WitchRole):
                    if action.actor_id in consumed_poison:
                        continue
                    witch.use_poison()
                    consumed_poison.add(action.actor_id)
                    poisoned_players.add(action.target_id)
                    self._logger.info(
                        "poison_consumed",
                        witch_id=action.actor_id,
                        target_id=action.target_id,
                    )

        return saved_players, poisoned_players

    async def _publish_death_events(
        self,
        death_player_ids: List[str],
        death_reasons: Dict[str, ActionType],
        roles: Dict[str, BaseRole],
    ) -> None:
        """为每个死亡玩家发布 PLAYER_DEATH 事件。

        **Why (独立事件而非批量事件)**: 每个玩家的死亡是独立的事实。
        发布独立事件使得下游模块（如猎人开枪触发器、遗言系统）能够
        精确订阅并处理单个死亡事件，避免解析批量事件的复杂性。

        事件可见性为 PUBLIC，所有玩家和观战者都能看到死亡公告。

        Args:
            death_player_ids: 死亡玩家 ID 列表。
            death_reasons: 死亡原因映射。
            roles: 角色映射（用于获取死亡玩家的角色信息）。
        """
        round = self._infer_round()
        for player_id in death_player_ids:
            role = roles.get(player_id)
            role_type_value = role.role_type.value if role else "UNKNOWN"
            faction_value = role.faction.value if role else "UNKNOWN"

            event = Event(
                event_id=str(uuid.uuid4()),
                game_id=self.game_id,
                seq_num=0,  # EventBus 自动分配
                event_type=EventType.PLAYER_DEATH,
                visibility=Visibility.PUBLIC,
                target_agents=[player_id],  # 死亡玩家本人——用于触发遗言等逻辑
                timestamp=datetime.now(timezone.utc),
                payload={
                    "player_id": player_id,
                    "death_reason": death_reasons[player_id].value,
                    "role_type": role_type_value,
                    "faction": faction_value,
                    "round": round,
                },
            )
            await self.event_bus.publish(event)
            self._logger.info(
                "death_event_published",
                player_id=player_id,
                reason=death_reasons[player_id].value,
                event_id=event.event_id,
            )

    def _infer_round(self) -> int:
        """从 pending_actions 中推断当前轮次。

        **HACK**: Resolver 本身不持有轮次信息。此方法尝试从
        pending_actions 中提取 action.round，取最大值作为当前轮次。
        如果 pending_actions 为空，返回 0 表示无法推断。

        Returns:
            推断的轮次数，第一个动作为空时返回 0。
        """
        if not self.pending_actions:
            return 0
        return max(action.round for action in self.pending_actions)

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    def get_pending_deaths(self) -> Dict[str, ActionType]:
        """获取当前草稿死亡名单的只读副本。

        Returns:
            ``player_id → 死亡原因`` 的映射副本。
        """
        return dict(self.pending_deaths)

    def get_pending_actions(self) -> List[AgentAction]:
        """获取当前已暂存但未结算的动作列表副本。

        Returns:
            动作列表副本。
        """
        return list(self.pending_actions)

    def get_wolf_target(self) -> Optional[str]:
        """获取当前夜晚狼人的原始刀人目标。

        Returns:
            狼人刀人目标的 player_id，若狼人尚未行动则返回 None。
        """
        return self._current_night_wolf_target

    def is_night_resolved(self) -> bool:
        """检查当前夜晚是否已完成结算。

        Returns:
            ``True`` 如果已调用 :meth:`resolve_night_actions` 完成结算。
        """
        return self._night_resolved
