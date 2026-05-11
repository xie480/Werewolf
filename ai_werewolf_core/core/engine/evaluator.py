"""
胜负判定器 (Win Condition Evaluator) 模块。

**Why**: 本模块是 Game Engine 中决定对局终点的裁判系统。它独立于任何
特定游戏阶段，在每个关键结算点（夜晚结算后、投票出局后、猎人开枪后）
由 Game Engine 调用，基于当前存活玩家的阵营分布判定是否满足游戏结束条件。

**核心设计**:
- 独立无状态：判定器不保存任何游戏状态，每次调用时传入当前的存活玩家列表
  和角色信息，确保纯函数式、可测试、无副作用。
- 屠边规则：判断好人阵营（神职全死或平民全死）或狼人阵营（狼人全死）
  是否满足失败条件。
- 屠城规则（可选）：当狼人存活数量 >= 好人存活数量时，狼人直接获胜。

参考: :doc:`docs/plan/行动结算与胜负判定设计`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Optional

from ai_werewolf_core.core.engine.roles.base import BaseRole
from ai_werewolf_core.schemas.enums import Faction, Role
from ai_werewolf_core.utils.logger import get_logger

logger = get_logger(__name__)

# ------------------------------------------------------------------
# 神职角色集合 —— 用于屠边判定
# ------------------------------------------------------------------

GOD_ROLES: FrozenSet[Role] = frozenset({
    Role.SEER,
    Role.WITCH,
    Role.HUNTER,
})
"""
神职角色集合。

**Why (使用 frozenset 而非 list/tuple)**:
1. 不可变 —— 防止运行时被意外修改，保证判定逻辑的稳定性。
2. 哈希化 —— 支持快速 ``in`` 查找（O(1) vs O(n)）。
3. 语义明确 —— 表达"这是一个常量集合，不应被修改"的意图。

扩展性: 若后续新增神职角色（如守卫、白痴），只需在此集合中添加对应的
:class:`Role` 枚举成员即可，无需修改 :class:`WinEvaluator` 的判定逻辑。
"""

# ------------------------------------------------------------------
# 胜负判定结果数据类
# ------------------------------------------------------------------


@dataclass(frozen=True)
class WinEvaluationResult:
    """胜负判定结果 —— 封装一次胜负评估的完整数据。

    **Why (frozen dataclass)**: 判定结果应为不可变值对象。
    一旦生成就不应被修改，避免下游模块意外篡改导致逻辑错误。

    Attributes:
        winner: 胜利阵营。``None`` 表示游戏继续。
        is_game_over: 游戏是否结束。
        alive_wolves: 当前存活狼人数量。
        alive_gods: 当前存活神职数量。
        alive_villagers: 当前存活平民数量。
        total_alive: 当前存活玩家总数。
        reason: 游戏结束原因描述（若游戏继续则为空字符串）。
    """

    winner: Optional[Faction]
    is_game_over: bool
    alive_wolves: int
    alive_gods: int
    alive_villagers: int
    total_alive: int
    reason: str = ""

    @property
    def alive_good(self) -> int:
        """存活好人总数 = 神职 + 平民。"""
        return self.alive_gods + self.alive_villagers


# ------------------------------------------------------------------
# WinEvaluator
# ------------------------------------------------------------------


class WinEvaluator:
    """胜负条件判定器。

    提供静态方法用于评估当前对局是否满足胜负条件。
    支持以下两种判定模式：

    1. **屠边模式 (默认)**:
       - 狼人全死 → 好人阵营 (:attr:`Faction.VILLAGER`) 获胜。
       - 神职全死 或 平民全死 → 狼人阵营 (:attr:`Faction.WEREWOLF`) 获胜。

    2. **屠城模式 (enable_massacre)**:
       在屠边判定基础上，额外增加一条规则：
       - 存活狼人数量 >= 存活好人数量 → 狼人阵营立即获胜。
       **Why**: 屠城模式适用于快速局或自定义规则，当狼人占据人数优势时
       无需等待神/民归边即可结束游戏。

    **Why (纯静态方法)**: 胜负判定是纯计算逻辑，不依赖任何外部状态。
    保持静态方法设计使得判定器天然线程安全、易于单元测试，
    且不会被误用为有状态的单例。
    """

    @staticmethod
    def evaluate(
        roles: Dict[str, BaseRole],
        enable_massacre: bool = False,
    ) -> Optional[Faction]:
        """评估当前对局的胜负条件。

        遍历所有角色，统计存活神职、平民和狼人的数量，
        然后按屠边（或屠城）规则判定胜负。

        **判定优先级** (Why: 两个阵营同时满足条件时，好人优先):
        1. 狼人全死 → 好人胜（优先级最高，避免平局歧义）。
        2. 神职全死 或 平民全死 → 狼人胜。
        3. (屠城模式) 狼人数量 >= 好人数量 → 狼人胜。
        4. 否则 → 游戏继续。

        Args:
            roles: 当前所有角色的映射 ``player_id → BaseRole``。
                判定器会读取每个角色的 ``is_alive``、``role_type`` 和 ``faction``。
            enable_massacre: 是否启用屠城规则。默认 ``False``（仅屠边）。

        Returns:
            - :attr:`Faction.VILLAGER` 如果好人阵营获胜。
            - :attr:`Faction.WEREWOLF` 如果狼人阵营获胜。
            - ``None`` 如果游戏继续（不满足任何胜负条件）。

        Raises:
            ValueError: 如果 ``roles`` 为空字典（无法判定）。
        """
        if not roles:
            raise ValueError("roles 不能为空 —— 无法对空对局进行胜负判定")

        # ── Step 1: 统计数据 ──
        stats = WinEvaluator._count_alive_by_role(roles)

        logger.info(
            "win_evaluation",
            alive_wolves=stats["wolves"],
            alive_gods=stats["gods"],
            alive_villagers=stats["villagers"],
            total_alive=stats["total"],
            enable_massacre=enable_massacre,
        )

        # ── Step 2: 屠边规则判定 ──
        return WinEvaluator._apply_rules(
            alive_wolves=stats["wolves"],
            alive_gods=stats["gods"],
            alive_villagers=stats["villagers"],
            enable_massacre=enable_massacre,
        )

    @staticmethod
    def evaluate_detailed(
        roles: Dict[str, BaseRole],
        enable_massacre: bool = False,
    ) -> WinEvaluationResult:
        """评估胜负条件并返回详细结果。

        与 :meth:`evaluate` 功能相同，但返回包含完整统计数据的
        :class:`WinEvaluationResult` 对象，便于日志、复盘和前端展示。

        Args:
            roles: 当前所有角色的映射 ``player_id → BaseRole``。
            enable_massacre: 是否启用屠城规则。

        Returns:
            包含胜负结果和详细统计的 :class:`WinEvaluationResult`。

        Raises:
            ValueError: 如果 ``roles`` 为空字典。
        """
        if not roles:
            raise ValueError("roles 不能为空 —— 无法对空对局进行胜负判定")

        stats = WinEvaluator._count_alive_by_role(roles)
        winner = WinEvaluator._apply_rules(
            alive_wolves=stats["wolves"],
            alive_gods=stats["gods"],
            alive_villagers=stats["villagers"],
            enable_massacre=enable_massacre,
        )

        reason = WinEvaluator._build_reason(
            winner=winner,
            alive_wolves=stats["wolves"],
            alive_gods=stats["gods"],
            alive_villagers=stats["villagers"],
            enable_massacre=enable_massacre,
        )

        return WinEvaluationResult(
            winner=winner,
            is_game_over=winner is not None,
            alive_wolves=stats["wolves"],
            alive_gods=stats["gods"],
            alive_villagers=stats["villagers"],
            total_alive=stats["total"],
            reason=reason,
        )

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _count_alive_by_role(roles: Dict[str, BaseRole]) -> Dict[str, int]:
        """统计存活角色数量。

        **Why (独立统计方法)**: 将"数据采集"与"规则判定"分离，
        使得统计逻辑可被复用（如用于生成天亮播报、前端状态面板等），
        同时规则判定保持纯粹的条件判断。

        遍历所有角色，按 ``role_type`` 分类统计存活者的数量：
        - ``wolves``: 存活狼人数量（通过 ``faction == Faction.WEREWOLF`` 判定）。
        - ``gods``: 存活神职数量（通过 ``role_type in GOD_ROLES`` 判定）。
        - ``villagers``: 存活平民数量（通过 ``role_type == Role.VILLAGER`` 判定）。
        - ``total``: 总存活数。

        **HACK**: 使用 ``faction`` 而非 ``role_type`` 判定狼人数量。
        这是为了兼容未来可能新增的狼人阵营角色（如白狼王、狼美人等）。

        Args:
            roles: 角色映射。

        Returns:
            包含 ``wolves``、``gods``、``villagers``、``total`` 四个键的字典。
        """
        wolves = 0
        gods = 0
        villagers = 0

        for role in roles.values():
            if not role.is_alive:
                continue

            # 阵营判定狼人 —— 兼容未来狼人阵营扩展角色
            if role.faction == Faction.WEREWOLF:
                wolves += 1
            # 角色类型判定神职
            elif role.role_type in GOD_ROLES:
                gods += 1
            # 角色类型判定平民
            elif role.role_type == Role.VILLAGER:
                villagers += 1

        return {
            "wolves": wolves,
            "gods": gods,
            "villagers": villagers,
            "total": wolves + gods + villagers,
        }

    @staticmethod
    def _apply_rules(
        alive_wolves: int,
        alive_gods: int,
        alive_villagers: int,
        enable_massacre: bool,
    ) -> Optional[Faction]:
        """应用胜负规则进行判定。

        **Why (独立规则方法)**: 将规则判定与数据采集分离，使得规则逻辑
        可以被 :meth:`evaluate` 和 :meth:`evaluate_detailed` 共同调用，
        避免代码重复。

        **判定优先级**:
        1. 狼人全死 (alive_wolves == 0) → 好人胜。
           即使此时神职或平民也已全死（极端情况不应出现，但防御性编程
           仍以好人优先）。
        2. 神职全死 (alive_gods == 0) 或平民全死 (alive_villagers == 0)
           → 狼人胜（屠边）。
        3. (屠城模式) 狼人数量 >= 好人数量 → 狼人胜。
        4. 否则 → 游戏继续 (None)。

        Args:
            alive_wolves: 存活狼人数量。
            alive_gods: 存活神职数量。
            alive_villagers: 存活平民数量。
            enable_massacre: 是否启用屠城规则。

        Returns:
            胜利阵营或 ``None``。
        """
        # 规则 1: 狼人全灭 → 好人阵营胜利
        if alive_wolves == 0:
            return Faction.VILLAGER

        # 规则 2: 屠边 —— 神职全死 或 平民全死 → 狼人阵营胜利
        if alive_gods == 0 or alive_villagers == 0:
            return Faction.WEREWOLF

        # 规则 3: 屠城模式 —— 狼人数量 >= 好人数量 → 狼人阵营胜利
        if enable_massacre:
            alive_good = alive_gods + alive_villagers
            if alive_wolves >= alive_good:
                return Faction.WEREWOLF

        # 游戏继续
        return None

    @staticmethod
    def _build_reason(
        winner: Optional[Faction],
        alive_wolves: int,
        alive_gods: int,
        alive_villagers: int,
        enable_massacre: bool,
    ) -> str:
        """构建胜负原因描述字符串。

        Args:
            winner: 胜利阵营。
            alive_wolves: 存活狼人数量。
            alive_gods: 存活神职数量。
            alive_villagers: 存活平民数量。
            enable_massacre: 是否启用屠城规则。

        Returns:
            人类可读的胜负原因描述。
        """
        if winner is None:
            return f"游戏继续: 狼人[{alive_wolves}] 神职[{alive_gods}] 平民[{alive_villagers}]"

        if winner == Faction.VILLAGER:
            return f"好人阵营胜利: 全部狼人已被淘汰 (存活: 神职[{alive_gods}] 平民[{alive_villagers}])"

        if winner == Faction.WEREWOLF:
            if alive_gods == 0 and alive_villagers == 0:
                return f"狼人阵营胜利: 神职和平民全部被淘汰 (存活狼人[{alive_wolves}])"
            if alive_gods == 0:
                return f"狼人阵营胜利 (屠边): 全部神职已被淘汰 (存活狼人[{alive_wolves}] 平民[{alive_villagers}])"
            if alive_villagers == 0:
                return f"狼人阵营胜利 (屠边): 全部平民已被淘汰 (存活狼人[{alive_wolves}] 神职[{alive_gods}])"
            if enable_massacre:
                alive_good = alive_gods + alive_villagers
                return f"狼人阵营胜利 (屠城): 狼人[{alive_wolves}] >= 好人[{alive_good}]"

        return f"未知结果: winner={winner}"
