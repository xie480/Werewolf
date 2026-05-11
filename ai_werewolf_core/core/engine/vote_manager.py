"""
投票管理器 (VoteManager) 模块。

**Why**: 狼人杀白天投票（放逐投票、PK 投票）是群体性的即时结算行为，
与夜晚的串行暂存-统一结算模式有本质区别。本模块遵循单一职责原则 (SRP)，
从 GameEngine 主流程中拆分出专门的投票管理逻辑：

1. **选票收集与校验**：记录投票意图，校验投票人存活状态、阶段合法性、
   以及 PK 阶段的候选人限制。
2. **计票与平票处理**：统计最高票，若出现平票则返回平票名单供引擎触发 PK 流程。
3. **即时结算**：若产生唯一最高票，直接调用目标角色的 ``die()`` 方法，
   并通过 EventBus 发布 ``PLAYER_DEATH`` 事件。

**规则硬编码**: 所有投票平票逻辑、票数统计均在 Python 代码中硬编码，
不依赖 LLM 判定，确保游戏逻辑的确定性和可审计性。

参考:
- :doc:`docs/plan/白天行动结算与投票管理器设计`
- :doc:`docs/agent.md`
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ai_werewolf_core.core.event.bus import EventBus
from ai_werewolf_core.core.engine.exceptions import ActionValidationError
from ai_werewolf_core.core.engine.roles.base import BaseRole
from ai_werewolf_core.schemas.enums import ActionType, EventType, GamePhase, Visibility
from ai_werewolf_core.schemas.models import AgentAction, Event
from ai_werewolf_core.utils.logger import get_logger

logger = get_logger(__name__)


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
    1. **开启投票回合**：清空历史选票，设置可选的 PK 候选人名单。
    2. **接收与校验选票**：验证投票人存活、阶段合法性、PK 候选人限制。
    3. **结算投票**：统计票数，若存在唯一最高票则执行即时死亡结算，
       若平票则返回平票名单供引擎处理。

    **Why (即时结算而非延迟结算)**: 白天投票的结果立即可知且不可逆转，
    不存在像夜晚那样"狼刀 → 女巫救 → 女巫毒"的因果链反转。
    因此采用即时结算模式，简化设计并降低状态管理复杂度。

    Attributes:
        game_id: 对局唯一标识。
        event_bus: 事件总线实例。
        current_votes: 当前投票回合的选票映射 ``voter_id → target_id``。
        pk_candidates: 当前 PK 候选人名单（None 表示普通投票，无候选人限制）。
    """

    def __init__(self, game_id: str, event_bus: EventBus) -> None:
        """初始化投票管理器。

        Args:
            game_id: 对局唯一标识，用于日志追踪和事件路由。
            event_bus: 事件总线实例，用于发布投票事件和死亡事件。
        """
        self.game_id: str = game_id
        self.event_bus: EventBus = event_bus
        self.current_votes: Dict[str, str] = {}
        """当前投票回合的选票映射: voter_id → target_id"""

        self.pk_candidates: Optional[List[str]] = None
        """当前 PK 候选人名单。None 表示普通投票（无候选人限制）。"""

        self._vote_history: List[Dict[str, str]] = []
        """历史投票记录，用于复盘审计。"""

        self._logger = logger.bind(game_id=self.game_id, module="VoteManager")

    # ------------------------------------------------------------------
    # 投票回合管理
    # ------------------------------------------------------------------

    def begin_vote(self, pk_candidates: Optional[List[str]] = None) -> None:
        """开启新一轮投票，清空历史选票。

        **Why**: 每轮投票开始前必须调用此方法，确保上一轮的选票残留
        不会污染新的投票回合。若为 PK 投票，需传入 PK 候选人名单以限制
        投票目标。

        通常在状态机进入 ``DAY_VOTE`` 或 ``DAY_PK_VOTE`` 阶段时
        由 Game Engine 调用。

        Args:
            pk_candidates: PK 候选人名单。若为普通放逐投票，传 None。
        """
        # 保存上一轮投票到历史记录（用于复盘）
        if self.current_votes:
            self._vote_history.append(dict(self.current_votes))

        self.current_votes.clear()
        self.pk_candidates = pk_candidates
        self._logger.info(
            "vote_begin",
            is_pk_vote=pk_candidates is not None,
            pk_candidates=pk_candidates,
        )

    # ------------------------------------------------------------------
    # 选票提交与校验
    # ------------------------------------------------------------------

    def submit_vote(
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
        4. **重复投票覆盖**：同一投票人多次提交会覆盖前一次投票（以最后一次为准）。

        **Why (允许覆盖而非拒绝)**: AI Agent 可能在投票阶段多次调用 LLM
        并提交不同投票，采用"最后一次为准"的策略可以避免因中间态的决策变更
        导致的拒绝处理复杂度。

        Args:
            action: Agent 提交的投票动作。
            roles: 当前所有角色的映射 ``player_id → BaseRole``。
            current_phase: 当前游戏阶段。

        Returns:
            ``True`` 表示选票已记录。

        Raises:
            ActionValidationError: 选票非法（类型错误 / 投票人不存在或已死亡 /
                PK 投票目标不在候选人名单中）。
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

        # ── 记录选票（覆盖模式） ──
        previous_vote = self.current_votes.get(voter_id)
        self.current_votes[voter_id] = action.target_id or ""  # 空字符串表示弃权

        if previous_vote is not None:
            self._logger.info(
                "vote_updated",
                voter_id=voter_id,
                previous_target=previous_vote or "(弃权)",
                new_target=action.target_id or "(弃权)",
            )
        else:
            self._logger.info(
                "vote_submitted",
                voter_id=voter_id,
                target_id=action.target_id or "(弃权)",
            )

        return True

    # ------------------------------------------------------------------
    # 投票结算
    # ------------------------------------------------------------------

    async def resolve_vote(
        self, roles: Dict[str, BaseRole], current_round: int = 0
    ) -> VoteResolveResult:
        """结算当前投票回合，统计票数并处理结果。

        结算逻辑：
        1. **统计投票分布**：使用 ``Counter`` 统计每个目标获得的票数。
        2. **确定最高票**：找出得票最高的目标（可能为多人并列）。
        3. **平票判断**：
           - 若最高票有多人并列 → 返回平票结果，由引擎决定是否进入 PK。
           - 若唯一最高票 → 执行即时死亡结算（调用 ``die()`` + 发布事件）。
        4. **无人得票处理**：若所有选票均为弃权，``highest_voted`` 为空列表。

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
        """
        self._logger.info(
            "vote_resolve_start",
            total_votes=len(self.current_votes),
            pk_candidates=self.pk_candidates,
        )

        # ── Step 1: 统计投票分布 ──
        # Why: 使用 Counter 而非手动累加，代码更简洁且不易出错。
        # 过滤掉空字符串（弃权票），不计入得票统计。
        vote_targets = [
            target for target in self.current_votes.values() if target
        ]
        vote_count: Dict[str, int] = dict(Counter(vote_targets))

        total_voters = len(self.current_votes)

        # ── Step 2: 确定最高票 ──
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

        # ── Step 3: 处理唯一最高票 —— 即时死亡结算 ──
        if not is_tie and len(highest_voted) == 1:
            voted_out_id = highest_voted[0]
            await self._execute_elimination(voted_out_id, roles, vote_count, current_round)

        # ── Step 4: 发布投票结算事件 ──
        await self._publish_vote_resolve_event(
            is_tie=is_tie,
            highest_voted=highest_voted,
            vote_count=vote_count,
            total_voters=total_voters,
        )

        # ── Step 5: 构建结算结果 ──
        result = VoteResolveResult(
            is_tie=is_tie,
            highest_voted=highest_voted,
            vote_details=dict(self.current_votes),
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
            timestamp=datetime.now(timezone.utc),
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
            timestamp=datetime.now(timezone.utc),
            payload={
                "announcement_type": "vote_result",
                "is_tie": is_tie,
                "highest_voted": highest_voted,
                "vote_count": vote_count,
                "total_voters": total_voters,
                "is_pk_vote": self.pk_candidates is not None,
            },
        )
        await self.event_bus.publish(event)

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    def get_current_votes(self) -> Dict[str, str]:
        """获取当前选票的只读副本。

        Returns:
            ``voter_id → target_id`` 的映射副本（空字符串表示弃权）。
        """
        return dict(self.current_votes)

    def get_vote_history(self) -> List[Dict[str, str]]:
        """获取历史投票记录。

        Returns:
            历史投票列表，每个元素为一个投票回合的选票映射副本。
        """
        return [dict(votes) for votes in self._vote_history]

    def get_voter_count(self) -> int:
        """获取已投票人数。

        Returns:
            当前回合已投票的玩家数量。
        """
        return len(self.current_votes)

    def has_voted(self, voter_id: str) -> bool:
        """检查指定玩家是否已投票。

        Args:
            voter_id: 玩家 ID。

        Returns:
            ``True`` 如果该玩家已投票。
        """
        return voter_id in self.current_votes

    def is_pk_vote(self) -> bool:
        """检查当前是否为 PK 投票回合。

        Returns:
            ``True`` 如果是 PK 投票（存在候选人限制）。
        """
        return self.pk_candidates is not None

    # ------------------------------------------------------------------
    # 生命周期管理
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """完全重置投票管理器状态。

        **Why**: 在对局结束或重新开始时调用，确保所有状态被清理，
        避免跨对局的状态污染。
        """
        self.current_votes.clear()
        self.pk_candidates = None
        self._vote_history.clear()
        self._logger.info("vote_manager_reset")
