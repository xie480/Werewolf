"""防作弊检测器 —— 检测异常行为模式并触发降级措施。

**Why (独立于 Validator 的原因)**:
校验器 (Validator) 回答"这个动作是否合法"，防作弊器 (Detector) 回答"这个 Agent 是否诚信"。
两者的关注点和响应方式不同：
- 校验失败 → 返回拒绝原因，Agent 可以纠正后重试（正常的交互流程）。
- 作弊检测触发 → 记录 WARNING 级别事件，可能降级该 Agent 的行为（如强制 PASS、限制后续提交频率）。

检测规则：
1. 幽灵玩家检测 → actor_id 不在当前对局 roles 中
2. 跨阵营越权   → 狼人在非夜间阶段提交 WOLF_KILL
3. 重放攻击防护 → 相同动作哈希在 30 秒内重复提交
4. 超频提交     → 同一玩家单阶段提交超过阈值次数
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

import structlog

from ai_werewolf_core.core.action.validator import _safe_enum_value
from ai_werewolf_core.schemas.enums import ActionType, Faction, GamePhase
from ai_werewolf_core.schemas.models import AgentAction

try:
    from ai_werewolf_core.core.engine.roles.base import BaseRole
except ImportError:
    BaseRole = None  # type: ignore[assignment]

logger = structlog.get_logger(__name__)


# ============================================================================
# 常量定义
# ============================================================================

# 单阶段最大提交次数阈值（超过则触发限速）
MAX_ACTIONS_PER_PHASE: int = 10

# 限速惩罚时长（秒）
RATE_LIMIT_DURATION_SECONDS: float = 5.0

# 重放攻击检测窗口（秒）
REPLAY_DETECTION_WINDOW_SECONDS: int = 30

# 夜间阶段集合（用于跨阵营越权检测）
# 同时存字符串值以兼容 use_enum_values=True 的 AgentAction
NIGHT_PHASES: set[GamePhase] = {
    GamePhase.NIGHT_WOLF_ACT,
    GamePhase.NIGHT_WITCH_ACT,
    GamePhase.NIGHT_SEER_ACT,
}
NIGHT_PHASE_VALUES: set[str] = {p.value for p in NIGHT_PHASES}


# ============================================================================
# 枚举定义
# ============================================================================

class PenaltyType(str, Enum):
    """惩罚类型枚举。

    Attributes:
        FORCE_PASS: 强制该玩家的后续动作为 PASS。
        RATE_LIMIT: 限速，后续动作强制延迟处理。
        MARK_SUSPICIOUS: 标记为可疑玩家（计入评测）。
    """

    FORCE_PASS = "FORCE_PASS"
    RATE_LIMIT = "RATE_LIMIT"
    MARK_SUSPICIOUS = "MARK_SUSPICIOUS"


# ============================================================================
# 数据类
# ============================================================================

@dataclass
class InspectionResult:
    """防作弊检测结果。

    Attributes:
        is_clean: 是否通过检测（True = 无问题）。
        violation_type: 违规类型描述（仅在 is_clean=False 时有意义）。
        penalty: 应施加的惩罚类型（仅在 is_clean=False 时有意义）。
    """

    is_clean: bool
    violation_type: str = ""
    penalty: PenaltyType | None = None

    @classmethod
    def passed(cls) -> "InspectionResult":
        """快速构造通过结果。"""
        return cls(is_clean=True)

    @classmethod
    def failed(
        cls, violation_type: str, penalty: PenaltyType
    ) -> "InspectionResult":
        """快速构造检测失败结果。

        Args:
            violation_type: 违规类型描述。
            penalty: 应施加的惩罚。
        """
        return cls(is_clean=False, violation_type=violation_type, penalty=penalty)


@dataclass
class ViolationRecord:
    """违规记录 —— 不可变值对象。

    Attributes:
        actor_id: 违规玩家 ID。
        violation_type: 违规类型描述。
        penalty: 施加的惩罚。
        action: 触发违规的动作（可选）。
        timestamp: 违规发生时间（UTC）。
    """

    actor_id: str
    violation_type: str
    penalty: PenaltyType
    action: AgentAction | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ============================================================================
# AntiCheatDetector
# ============================================================================

class AntiCheatDetector:
    """防作弊检测器 —— 检测异常行为模式并触发降级措施。

    检测管道（短路求值）：
    1. 幽灵玩家检测
    2. 跨阵营越权检测
    3. 重放攻击检测
    4. 超频提交检测

    使用方式::

        detector = AntiCheatDetector("game_001")
        result = detector.inspect(action, roles)
        if not result.is_clean:
            detector.apply_penalty(action.actor_id, result.penalty)

    Attributes:
        game_id: 绑定的对局 ID。
        _action_counts: actor_id → 累计提交次数的追踪字典。
        _duplicate_hashes: 重放检测哈希集合（含过期清理）。
        _violations: 违规记录列表。
        _rate_limited_until: actor_id → 限速解除时间的追踪字典。
    """

    def __init__(self, game_id: str) -> None:
        """初始化防作弊检测器。

        Args:
            game_id: 对局唯一标识。
        """
        self.game_id: str = game_id
        self._action_counts: dict[str, int] = {}
        self._duplicate_hashes: dict[str, float] = {}  # hash → timestamp
        self._violations: list[ViolationRecord] = []
        self._rate_limited_until: dict[str, float] = {}

    # ------------------------------------------------------------------
    # 主检测入口
    # ------------------------------------------------------------------

    def inspect(
        self,
        action: AgentAction,
        roles: dict[str, BaseRole],
    ) -> InspectionResult:
        """执行防作弊检测：幽灵玩家 → 越权 → 重放 → 超频。

        Args:
            action: 待检测的 AgentAction。
            roles: 当前对局的所有角色映射（actor_id → BaseRole）。

        Returns:
            InspectionResult 实例。
        """
        # 1. 幽灵玩家检测
        result = self._check_ghost_player(action, roles)
        if not result.is_clean:
            self._record_violation(action.actor_id, result.violation_type, result.penalty, action)
            return result

        # 2. 跨阵营越权检测
        result = self._check_faction_violation(action, roles)
        if not result.is_clean:
            self._record_violation(action.actor_id, result.violation_type, result.penalty, action)
            return result

        # 3. 重放攻击检测
        result = self._check_replay_attack(action)
        if not result.is_clean:
            self._record_violation(action.actor_id, result.violation_type, result.penalty, action)
            return result

        # 4. 超频提交检测
        result = self._check_rate_limit(action)
        if not result.is_clean:
            self._record_violation(action.actor_id, result.violation_type, result.penalty, action)
            return result

        return InspectionResult.passed()

    # ------------------------------------------------------------------
    # 1. 幽灵玩家检测
    # ------------------------------------------------------------------

    @staticmethod
    def _check_ghost_player(
        action: AgentAction,
        roles: dict[str, BaseRole],
    ) -> InspectionResult:
        """检测 actor_id 是否存在于当前对局的 roles 中。

        **Why**: LLM 可能因幻觉生成不存在的 actor_id，
        或是伪造请求尝试以不存在玩家的身份提交动作。

        Args:
            action: 待检测的 AgentAction。
            roles: 当前对局的所有角色映射。

        Returns:
            InspectionResult 实例。
        """
        if action.actor_id not in roles:
            return InspectionResult.failed(
                violation_type=f"幽灵玩家：actor_id '{action.actor_id}' 不存在于当前对局",
                penalty=PenaltyType.MARK_SUSPICIOUS,
            )
        return InspectionResult.passed()

    # ------------------------------------------------------------------
    # 2. 跨阵营越权检测
    # ------------------------------------------------------------------

    @staticmethod
    def _check_faction_violation(
        action: AgentAction,
        roles: dict[str, BaseRole],
    ) -> InspectionResult:
        """检测狼人是否在非夜间阶段提交 WOLF_KILL。

        **Why**: WOLF_KILL 是狼人专属技能，只能在夜间阶段使用。
        在白天或其他阶段提交视为越权行为。

        Args:
            action: 待检测的 AgentAction。
            roles: 当前对局的所有角色映射。

        Returns:
            InspectionResult 实例。
        """
        # 仅对 WOLF_KILL 动作做越权检测
        if _safe_enum_value(action.action_type) != ActionType.WOLF_KILL.value:
            return InspectionResult.passed()

        actor_role = roles.get(action.actor_id)
        if actor_role is None:
            return InspectionResult.passed()  # 幽灵玩家已在第一步检测

        # 检查是否为狼人阵营
        if actor_role.faction != Faction.WEREWOLF:
            return InspectionResult.failed(
                violation_type=(
                    f"跨阵营越权：非狼人玩家 '{action.actor_id}' "
                    f"尝试提交 WOLF_KILL"
                ),
                penalty=PenaltyType.MARK_SUSPICIOUS,
            )

        # 检查是否在夜间阶段
        if _safe_enum_value(action.phase) not in NIGHT_PHASE_VALUES:
            return InspectionResult.failed(
                violation_type=(
                    f"跨阵营越权：狼人 '{action.actor_id}' "
                    f"在非夜间阶段 {_safe_enum_value(action.phase)} 提交 WOLF_KILL"
                ),
                penalty=PenaltyType.FORCE_PASS,
            )

        return InspectionResult.passed()

    # ------------------------------------------------------------------
    # 3. 重放攻击检测
    # ------------------------------------------------------------------

    def _check_replay_attack(self, action: AgentAction) -> InspectionResult:
        """检测重复提交完全相同的动作。

        **Why**: LLM 可能在循环中重复提交完全相同的动作
        （相同 actor_id + action_type + target_id + phase + round），
        消耗系统资源。使用 SHA256 哈希进行去重。

        自动清理过期的哈希条目（超过检测窗口的条目）。

        Args:
            action: 待检测的 AgentAction。

        Returns:
            InspectionResult 实例。
        """
        # 生成动作哈希
        action_hash = self._hash_action(action)

        now = time.monotonic()

        # 清理过期的哈希条目
        self._cleanup_expired_hashes(now)

        # 检查是否有重复
        if action_hash in self._duplicate_hashes:
            return InspectionResult.failed(
                violation_type=(
                    f"重放攻击：玩家 '{action.actor_id}' "
                    f"重复提交相同的 {_safe_enum_value(action.action_type)} 动作"
                ),
                penalty=PenaltyType.FORCE_PASS,
            )

        # 记录当前哈希
        self._duplicate_hashes[action_hash] = now
        return InspectionResult.passed()

    # ------------------------------------------------------------------
    # 4. 超频提交检测
    # ------------------------------------------------------------------

    def _check_rate_limit(self, action: AgentAction) -> InspectionResult:
        """检测同一玩家单阶段提交次数是否超过阈值。

        **Why**: 防止 Agent 在短时间内洪水式提交动作，
        消耗 Redis 和 EventBus 资源。

        超频阈值：单阶段 10 次。触发后对该玩家施加 5 秒限速。

        Args:
            action: 待检测的 AgentAction。

        Returns:
            InspectionResult 实例。
        """
        actor_id = action.actor_id

        # 检查是否已被限速
        if self.is_rate_limited(actor_id):
            return InspectionResult.failed(
                violation_type=f"超频提交：玩家 '{actor_id}' 已被限速",
                penalty=PenaltyType.RATE_LIMIT,
            )

        # 累加计数
        current_count = self._action_counts.get(actor_id, 0) + 1
        self._action_counts[actor_id] = current_count

        # 检查是否超过阈值
        if current_count > MAX_ACTIONS_PER_PHASE:
            self.apply_penalty(actor_id, PenaltyType.RATE_LIMIT)
            return InspectionResult.failed(
                violation_type=(
                    f"超频提交：玩家 '{actor_id}' 单阶段提交 {current_count} 次，"
                    f"超过阈值 {MAX_ACTIONS_PER_PHASE}"
                ),
                penalty=PenaltyType.RATE_LIMIT,
            )

        return InspectionResult.passed()

    # ------------------------------------------------------------------
    # 惩罚管理
    # ------------------------------------------------------------------

    def is_rate_limited(self, actor_id: str) -> bool:
        """检查指定玩家是否已被限速。

        Args:
            actor_id: 玩家 ID。

        Returns:
            True 如果该玩家当前处于限速状态。
        """
        if actor_id not in self._rate_limited_until:
            return False

        now = time.monotonic()
        if now >= self._rate_limited_until[actor_id]:
            # 限速已过期，清理
            del self._rate_limited_until[actor_id]
            return False

        return True

    def apply_penalty(self, actor_id: str, penalty: PenaltyType) -> None:
        """对违规玩家施加惩罚。

        Args:
            actor_id: 玩家 ID。
            penalty: 惩罚类型。
        """
        if penalty == PenaltyType.RATE_LIMIT:
            self._rate_limited_until[actor_id] = (
                time.monotonic() + RATE_LIMIT_DURATION_SECONDS
            )
            logger.warning(
                "玩家已被限速",
                game_id=self.game_id,
                actor_id=actor_id,
                duration_seconds=RATE_LIMIT_DURATION_SECONDS,
            )
        elif penalty == PenaltyType.FORCE_PASS:
            logger.warning(
                "玩家动作被强制 PASS",
                game_id=self.game_id,
                actor_id=actor_id,
            )
        elif penalty == PenaltyType.MARK_SUSPICIOUS:
            logger.warning(
                "玩家被标记为可疑",
                game_id=self.game_id,
                actor_id=actor_id,
            )

    # ------------------------------------------------------------------
    # 违规记录
    # ------------------------------------------------------------------

    def _record_violation(
        self,
        actor_id: str,
        violation_type: str,
        penalty: PenaltyType,
        action: AgentAction | None = None,
    ) -> None:
        """记录一次违规事件。

        Args:
            actor_id: 违规玩家 ID。
            violation_type: 违规类型描述。
            penalty: 施加的惩罚。
            action: 触发违规的动作（可选）。
        """
        record = ViolationRecord(
            actor_id=actor_id,
            violation_type=violation_type,
            penalty=penalty,
            action=action,
        )
        self._violations.append(record)
        logger.warning(
            "检测到作弊行为",
            game_id=self.game_id,
            actor_id=actor_id,
            violation_type=violation_type,
            penalty=penalty.value,
        )

    def get_violations(self) -> list[ViolationRecord]:
        """获取所有违规记录。

        Returns:
            违规记录列表（按时间顺序）。
        """
        return list(self._violations)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_action(action: AgentAction) -> str:
        """计算动作的 SHA256 哈希值（用于重放检测）。

        哈希输入：actor_id + action_type + target_id + phase + round。

        Args:
            action: 待哈希的 AgentAction。

        Returns:
            十六进制哈希字符串。
        """
        raw = (
            f"{action.actor_id}|{_safe_enum_value(action.action_type)}|"
            f"{action.target_id or ''}|{_safe_enum_value(action.phase)}|{action.round}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cleanup_expired_hashes(self, now: float) -> None:
        """清理过期的重放检测哈希条目。

        **Why**: 防止 _duplicate_hashes 字典无限增长。
        仅在每次检测时惰性清理，避免定时器开销。

        Args:
            now: 当前时间（time.monotonic()）。
        """
        expired_keys = [
            key
            for key, ts in self._duplicate_hashes.items()
            if now - ts > REPLAY_DETECTION_WINDOW_SECONDS
        ]
        for key in expired_keys:
            del self._duplicate_hashes[key]

    # ------------------------------------------------------------------
    # 阶段重置
    # ------------------------------------------------------------------

    def reset_phase_counters(self) -> None:
        """重置阶段提交计数器（在阶段切换时调用）。

        **Why**: 超频检测按阶段统计，阶段切换后计数应清零。
        """
        self._action_counts.clear()

    # ------------------------------------------------------------------
    # 重置（测试用）
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """重置所有状态（仅用于测试）。

        **Why**: 测试环境中需要在每个测试用例前后清理状态，
        确保测试隔离性。生产代码不应调用此方法。
        """
        self._action_counts.clear()
        self._duplicate_hashes.clear()
        self._violations.clear()
        self._rate_limited_until.clear()
