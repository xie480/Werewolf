"""动作校验器 —— 纯规则防火墙，不依赖 LLM 判定，不做游戏规则判定。

**Why**: 独立于 Manager 的集中式校验层，将存活检查、阶段检查、冷却检查
从 ActionResolver、VoteManager、SpecialActionResolver 中抽取出来，统一入口。

校验链（短路求值）:
1. 结构化校验 → 最快失败（字段缺失/类型错误），无需访问 Redis
2. 阶段校验   → 对比动作声明阶段与当前阶段
3. 生存状态校验 → 查询角色声明，按声明去 Redis BitMap 验证实际状态
4. 冷却校验   → 检查内存字典，防止 LLM loop 重复提交
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import structlog
from redis.asyncio import Redis

from ai_werewolf_core.constant.redis_keys import RedisKeys
from ai_werewolf_core.core.engine.roles.base import BaseRole
from ai_werewolf_core.schemas.enums import GamePhase, SurvivalRequirement
from ai_werewolf_core.schemas.models import AgentAction
from ai_werewolf_core.utils.redis_client import RedisClientManager

logger = structlog.get_logger(__name__)


# ============================================================================
# 工具函数
# ============================================================================

def _safe_enum_value(obj) -> str:
    """安全获取枚举或字符串的值。

    **Why**: AgentAction 的 Config 设置了 ``use_enum_values = True``，
    导致枚举字段（phase、action_type）被序列化为字符串。
    此函数兼容两种类型，确保代码在 Pydantic 模型内部和外部都能正常工作。

    Args:
        obj: 枚举值或字符串。

    Returns:
        字符串表示。
    """
    if hasattr(obj, 'value'):
        return obj.value
    return str(obj) if obj is not None else ""


# ============================================================================
# 常量定义
# ============================================================================

# 默认冷却窗口（秒）：同一玩家同一阶段同一动作类型在此时间内不可重复提交
DEFAULT_COOLDOWN_SECONDS: float = 2.0

# 座位号到 BitMap 偏移的映射由 PlayerStatusManager 管理，
# 本模块仅通过 BitMap 的 GETBIT/SETBIT 检查存活状态。


# ============================================================================
# 数据类
# ============================================================================

@dataclass
class ValidationResult:
    """校验结果 —— 通过或拒绝。

    Attributes:
        is_valid: 是否通过校验。
        reason: 拒绝原因（仅在 is_valid=False 时有意义）。
        rejected_by: 拒绝来源（"structural"/"phase"/"survival"/"cooldown"）。
    """

    is_valid: bool
    reason: str = ""
    rejected_by: str = ""

    @classmethod
    def passed(cls) -> "ValidationResult":
        """快速构造通过结果。"""
        return cls(is_valid=True)

    @classmethod
    def rejected(cls, reason: str, rejected_by: str) -> "ValidationResult":
        """快速构造拒绝结果。

        Args:
            reason: 人类可读的拒绝原因。
            rejected_by: 校验环节标识。
        """
        return cls(is_valid=False, reason=reason, rejected_by=rejected_by)


# ============================================================================
# ActionValidator
# ============================================================================

class ActionValidator:
    """动作校验器 —— 纯规则防火墙，不依赖 LLM 判定，不做游戏规则判定。

    对每个提交的 AgentAction 执行四层校验：
    1. 结构化校验（Pydantic 字段完整性）
    2. 阶段校验（声明的 phase 与当前阶段一致）
    3. 生存状态校验（根据角色声明的 SurvivalRequirement 验证 BitMap）
    4. 冷却校验（防止 LLM 循环重复提交）

    使用方式::

        validator = ActionValidator("game_001")
        result = await validator.validate(action, role, current_phase)

    Attributes:
        game_id: 绑定的对局 ID。
        _recent_actions: 冷却追踪字典，key 格式 "actor_id:phase:action_type"。
    """

    def __init__(self, game_id: str) -> None:
        """初始化动作校验器。

        Args:
            game_id: 对局唯一标识。
        """
        self.game_id: str = game_id
        self._recent_actions: dict[str, float] = {}
        self._redis: Optional[Redis] = None

    # ------------------------------------------------------------------
    # Redis 懒初始化
    # ------------------------------------------------------------------

    async def _get_redis(self) -> Redis:
        """获取 Redis 客户端（懒初始化，共享连接池）。

        Returns:
            Redis 异步客户端实例。
        """
        if self._redis is None:
            self._redis = await RedisClientManager.get_client()
        return self._redis

    # ------------------------------------------------------------------
    # 主校验入口
    # ------------------------------------------------------------------

    async def validate(
        self,
        action: AgentAction,
        role: BaseRole,
        current_phase: GamePhase,
    ) -> ValidationResult:
        """执行完整校验链：结构化 → 阶段 → 生存状态 → 冷却。

        全部通过返回 ValidationResult.passed()，
        任一步失败返回 ValidationResult.rejected(reason)。

        Args:
            action: 待校验的 AgentAction。
            role: 行动者的角色对象，用于获取生存状态声明。
            current_phase: PhaseStateMachine 的当前阶段。

        Returns:
            ValidationResult 实例。
        """
        # 1. 结构化校验
        result = self._validate_structure(action)
        if not result.is_valid:
            return result

        # 2. 阶段校验
        result = self._validate_phase(action, current_phase)
        if not result.is_valid:
            return result

        # 3. 冷却校验（在生存状态之前 — 无需 Redis）
        result = self._validate_cooldown(action)
        if not result.is_valid:
            return result

        # 4. 生存状态校验（根据角色声明 — 需要 Redis）
        result = await self._validate_survival(action, role)
        if not result.is_valid:
            return result

        return ValidationResult.passed()

    # ------------------------------------------------------------------
    # 1. 结构化校验
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_structure(action: AgentAction) -> ValidationResult:
        """校验动作的字段完整性。

        **Why**: 最快失败——字段缺失或类型错误可直接在入口拒绝，
        无需访问 Redis。Pydantic 模型本身已做基础校验，
        此处补充业务级别的字段检查。

        Args:
            action: 待校验的 AgentAction。

        Returns:
            ValidationResult 实例。
        """
        # actor_id 不能为空
        if not action.actor_id or not action.actor_id.strip():
            return ValidationResult.rejected(
                reason="actor_id 不能为空",
                rejected_by="structural",
            )

        # actor_id 格式校验：必须以 "player_" 开头
        if not action.actor_id.startswith("player_"):
            return ValidationResult.rejected(
                reason=f"actor_id 格式无效: {action.actor_id}，必须以 'player_' 开头",
                rejected_by="structural",
            )

        # action_type 不能为 None（Pydantic 已保证，但做防御性检查）
        if action.action_type is None:
            return ValidationResult.rejected(
                reason="action_type 不能为空",
                rejected_by="structural",
            )

        # 如果有 target_id，校验格式
        if action.target_id is not None and not action.target_id.startswith("player_"):
            return ValidationResult.rejected(
                reason=f"target_id 格式无效: {action.target_id}，必须以 'player_' 开头",
                rejected_by="structural",
            )

        return ValidationResult.passed()

    # ------------------------------------------------------------------
    # 2. 阶段校验
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_phase(
        action: AgentAction, current_phase: GamePhase
    ) -> ValidationResult:
        """校验动作声明的 phase 与当前阶段一致。

        **Why**: LLM 可能因幻觉提交错误阶段的动作
        （如黑夜提交发言、白天提交刀人）。防火墙在此拦截，
        拒绝原因返回给 Agent 供其自我纠正。

        Args:
            action: 待校验的 AgentAction。
            current_phase: 状态机的当前阶段。

        Returns:
            ValidationResult 实例。
        """
        if _safe_enum_value(action.phase) != _safe_enum_value(current_phase):
            return ValidationResult.rejected(
                reason=(
                    f"阶段不匹配：动作声明的阶段为 {_safe_enum_value(action.phase)}，"
                    f"但当前游戏阶段为 {_safe_enum_value(current_phase)}"
                ),
                rejected_by="phase",
            )
        return ValidationResult.passed()

    # ------------------------------------------------------------------
    # 3. 生存状态校验
    # ------------------------------------------------------------------

    async def _validate_survival(
        self, action: AgentAction, role: BaseRole
    ) -> ValidationResult:
        """根据角色声明的 SurvivalRequirement 验证 Redis BitMap。

        **Why**: 生存状态约束不是通用规则，而是角色相关的。
        本方法从角色对象获取 SurvivalRequirement 声明，
        然后去 Redis BitMap 验证实际状态，而非硬编码"必须存活"。

        校验逻辑：
        - ANY → 跳过
        - MUST_BE_ALIVE → 拒绝已死亡的玩家
        - MUST_BE_DEAD  → 拒绝仍存活的玩家

        Args:
            action: 待校验的 AgentAction。
            role: 行动者的角色对象。

        Returns:
            ValidationResult 实例。
        """
        # 获取角色声明的生存状态要求
        requirement = role.get_survival_requirement(action.action_type)

        # ANY：不校验生存状态
        if requirement == SurvivalRequirement.ANY:
            return ValidationResult.passed()

        # 查询 Redis BitMap 中的实际存活状态
        try:
            redis = await self._get_redis()
            bitmap_key = RedisKeys.alive_bitmap(self.game_id)

            # 从 actor_id 提取座位号（格式 player_N → N）
            seat_number = self._extract_seat_number(action.actor_id)
            # BitMap 偏移 = 座位号 - 1（座位号从 1 开始，BitMap 偏移从 0 开始）
            bitmap_offset = seat_number - 1

            is_alive_raw = await redis.getbit(bitmap_key, bitmap_offset)
            is_alive = bool(is_alive_raw)
        except Exception as e:
            logger.error(
                "Redis BitMap 查询失败，无法完成生存状态校验",
                game_id=self.game_id,
                actor_id=action.actor_id,
                error=str(e),
                exc_info=True,
            )
            # Redis 不可用时保守拒绝
            return ValidationResult.rejected(
                reason=f"无法验证玩家存活状态（Redis 不可用）: {e}",
                rejected_by="survival",
            )

        if requirement == SurvivalRequirement.MUST_BE_ALIVE:
            if not is_alive:
                return ValidationResult.rejected(
                    reason=(
                        f"角色 {_safe_enum_value(role.role_type)} 要求 {_safe_enum_value(action.action_type)} "
                        f"在存活状态下执行，但玩家 {action.actor_id} 已死亡"
                    ),
                    rejected_by="survival",
                )
        elif requirement == SurvivalRequirement.MUST_BE_DEAD:
            if is_alive:
                return ValidationResult.rejected(
                    reason=(
                        f"角色 {_safe_enum_value(role.role_type)} 要求 {_safe_enum_value(action.action_type)} "
                        f"在死亡状态下执行，但玩家 {action.actor_id} 仍存活"
                    ),
                    rejected_by="survival",
                )

        return ValidationResult.passed()

    # ------------------------------------------------------------------
    # 4. 冷却校验
    # ------------------------------------------------------------------

    def _validate_cooldown(self, action: AgentAction) -> ValidationResult:
        """检查同一玩家在同一阶段是否重复提交同类型动作。

        **Why**: 防止 LLM 在 while 循环中重复调用提交工具
        （如 LangGraph 的 tool calling 循环），打爆 Redis 和 EventBus。

        使用内存字典追踪，key 格式为 ``actor_id:phase:action_type``。

        Args:
            action: 待校验的 AgentAction。

        Returns:
            ValidationResult 实例。
        """
        cooldown_key = (
            f"{action.actor_id}:{_safe_enum_value(action.phase)}:{_safe_enum_value(action.action_type)}"
        )
        now = time.monotonic()

        last_submit_time = self._recent_actions.get(cooldown_key)
        if last_submit_time is not None:
            elapsed = now - last_submit_time
            if elapsed < DEFAULT_COOLDOWN_SECONDS:
                return ValidationResult.rejected(
                    reason=(
                        f"冷却中：玩家 {action.actor_id} 在阶段 {_safe_enum_value(action.phase)} "
                        f"的 {_safe_enum_value(action.action_type)} 动作需要冷却 "
                        f"{DEFAULT_COOLDOWN_SECONDS} 秒，已过 {elapsed:.1f} 秒"
                    ),
                    rejected_by="cooldown",
                )

        # 更新冷却时间戳
        self._recent_actions[cooldown_key] = now
        return ValidationResult.passed()

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_seat_number(actor_id: str) -> int:
        """从 actor_id 提取座位号。

        格式：player_N → N

        Args:
            actor_id: 玩家 ID。

        Returns:
            座位号（从 1 开始）。

        Raises:
            ValueError: actor_id 格式无效。
        """
        try:
            return int(actor_id.split("_")[1])
        except (IndexError, ValueError) as e:
            raise ValueError(f"无法从 actor_id 提取座位号: {actor_id}") from e

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    def reset_cooldowns(self) -> None:
        """重置冷却追踪字典（用于阶段切换或测试）。

        **Why**: 阶段切换后冷却状态应重置，
        因为不同阶段的动作类型互不干扰。
        """
        self._recent_actions.clear()
