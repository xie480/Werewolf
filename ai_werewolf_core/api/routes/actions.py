"""
玩家操作路由 —— 投票、发言、技能使用。

**Why**: 这是 Phase 3 补全的核心模块，将狼人杀的玩家交互操作
（投票/发言/夜间技能）暴露为 RESTful API。路由层仅负责参数校验和
Engine 委托，不包含任何游戏逻辑。

参考 [`docs/plan/FastAPI API.md`](../../docs/plan/FastAPI%20API.md)。
"""

from __future__ import annotations

import uuid
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException

from ai_werewolf_core.core.engine.exceptions import (
    ActionValidationError,
    GameNotRunnableError,
)
from ai_werewolf_core.core.engine.lifecycle import LifecycleManager
from ai_werewolf_core.core.engine.player_manager import PlayerStatusManager
from ai_werewolf_core.core.engine.resolver import ActionResolver
from ai_werewolf_core.core.engine.roles.base import BaseRole
from ai_werewolf_core.core.engine.vote_manager import VoteManager
from ai_werewolf_core.core.event.bus import event_bus, EventBus
from ai_werewolf_core.schemas.api import (
    ActionResponse,
    SubmitActionRequest,
    SubmitSpeechRequest,
    SubmitVoteRequest,
    VoteStatusResponse,
)
from dataclasses import dataclass

from ai_werewolf_core.schemas.enums import (
    ActionType,
    Emotion,
    EventType,
    GamePhase,
    GameStatus,
    Visibility,
)
from ai_werewolf_core.schemas.models import AgentAction, Event
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.utils.redis_seq import RedisUnavailableException
from ai_werewolf_core.utils.time_utils import now_tz

logger = get_logger(__name__)

router = APIRouter()


# ============================================================================
# 工具函数
# ============================================================================


async def _get_current_phase(game_id: str, event_bus: EventBus) -> GamePhase:
    """获取对局当前阶段。

    Args:
        game_id: 对局 ID。
        event_bus: 事件总线实例。

    Returns:
        当前游戏阶段。

    Raises:
        HTTPException(422): 对局不在 RUNNING 状态。
        HTTPException(503): Redis 不可用。
    """
    manager = LifecycleManager(game_id, event_bus)
    status = await manager.get_status()
    if status != GameStatus.RUNNING:
        raise HTTPException(
            status_code=422,
            detail=f"对局 [{game_id}] 不在运行中，当前状态: {status.value}",
        )
    phase = await manager.state_machine.get_current_phase()
    if phase is None:
        raise HTTPException(status_code=409, detail="对局阶段未初始化")
    return phase


async def _get_round(game_id: str, event_bus: EventBus) -> int:
    """获取对局当前轮次。

    Args:
        game_id: 对局 ID。
        event_bus: 事件总线实例。

    Returns:
        当前轮次。
    """
    manager = LifecycleManager(game_id, event_bus)
    return await manager.state_machine.get_round()


@dataclass
class InternalSubmitResult:
    accepted: bool
    reason: str = ""


async def _load_roles(game_id: str) -> Dict[str, BaseRole]:
    """从持久化存储加载角色映射（供内部提交使用）。
    
    当 roles={} 被传入 submit_vote 或 submit_action 时，
    这些方法内部的存活校验需要真实的角色数据。
    此函数加载角色数据以提供正确的校验上下文。
    """
    from ai_werewolf_core.core.engine.game_engine import GameEngine
    try:
        return await GameEngine.load_roles_from_persistence(game_id)
    except Exception as e:
        logger.error("load_roles_failed", game_id=game_id, error=str(e))
        return {}


async def submit_action_internal(game_id: str, action: AgentAction) -> InternalSubmitResult:
    """
    内部提交动作接口，供 Agent 任务调用。
    
    该函数根据当前游戏阶段处理不同类型的动作，包括投票、发言和夜间技能等。
    对于投票和发言，会验证当前阶段是否允许执行此类操作；
    对于夜间技能，使用ActionResolver来处理。
    
    参数:
        game_id (str): 游戏实例的唯一标识符
        action (AgentAction): 代理执行的动作对象，包含动作类型、执行者ID、目标等信息
        
    返回:
        InternalSubmitResult: 包含执行结果状态和消息的对象
            - success (bool): 操作是否成功
            - message (str): 执行结果的描述信息
    """
    # use global event_bus
    try:
        # 获取当前游戏阶段
        current_phase = await _get_current_phase(game_id, event_bus)
        
        # 加载角色映射用于校验（不再传入空字典 roles={}）
        roles = await _load_roles(game_id)
        
        if action.action_type == ActionType.VOTE:
            # 验证当前阶段是否允许投票
            if current_phase not in (GamePhase.DAY_VOTE, GamePhase.DAY_PK_VOTE):
                return InternalSubmitResult(False, f"当前阶段 [{current_phase.value}] 不允许投票")
            # 提交投票动作（传入正确的 roles 映射）
            vote_mgr = VoteManager(game_id, event_bus)
            vote_mgr.begin_vote(action.round)
            await vote_mgr.submit_vote(action, roles=roles, current_phase=current_phase)
            return InternalSubmitResult(True, "投票提交成功")
            
        elif action.action_type == ActionType.SPEAK:
            # 验证当前阶段是否允许发言
            speech_phases = (GamePhase.DAY_DISCUSSION, GamePhase.DAY_PK_DISCUSSION, GamePhase.LAST_WORDS)
            if current_phase not in speech_phases:
                return InternalSubmitResult(False, f"当前阶段 [{current_phase.value}] 不允许发言")
            
            # 创建并发布发言事件
            speech_event = Event(
                event_id=str(uuid.uuid4()),
                game_id=game_id,
                seq_num=0,
                event_type=EventType.SPEECH_EVENT,
                visibility=Visibility.PUBLIC,
                target_agents=[],
                timestamp=now_tz(),
                payload={
                    "actor_id": action.actor_id,
                    "content": action.speech_content or action.reason,
                    "inner_thought": action.inner_thought,
                    "emotion": Emotion.NEUTRAL.value,
                    "phase": current_phase.value,
                    "round": action.round,
                },
            )
            await event_bus.publish(speech_event)
            return InternalSubmitResult(True, "发言提交成功")
            
        else:
            # 处理夜间技能动作（传入正确的 roles 映射）
            resolver = ActionResolver(game_id, event_bus)
            resolver.submit_action(action, roles=roles, current_phase=current_phase)
            
            # 检查是否满足提前结束条件
            from ai_werewolf_core.core.engine.game_engine import GameEngine
            reloaded_roles = await GameEngine.load_roles_from_persistence(game_id)
            engine = GameEngine(game_id, event_bus, reloaded_roles)
            try:
                await engine._check_early_termination(current_phase)
            except Exception as e:
                logger.warning("early_termination_check_failed", phase=current_phase.value, error=str(e))
                
            return InternalSubmitResult(True, "技能提交成功")
            
    except ActionValidationError as e:
        return InternalSubmitResult(False, str(e))
    except Exception as e:
        logger.error("submit_action_internal_failed", game_id=game_id, error=str(e), exc_info=True)
        return InternalSubmitResult(False, f"内部错误: {str(e)}")


# ============================================================================
# 投票端点
# ============================================================================


@router.post("/{game_id}/vote", response_model=ActionResponse)
async def submit_vote(game_id: str, request: SubmitVoteRequest) -> ActionResponse:
    """提交投票。

    投票人在当前投票阶段提交对目标玩家的投票意图。
    target_id 为 None 表示弃权。

    执行流程:
    1. 校验对局处于 RUNNING 状态且当前为投票阶段
    2. 构建 AgentAction 交给 VoteManager.submit_vote 完成校验和写入
    3. 返回操作结果

    Raises:
        409: 非法动作（阶段不匹配 / 投票人不存在或已死亡）。
        422: 对局不在 RUNNING 状态。
        503: Redis 不可用。
    """
    # use global event_bus
    try:
        # 校验对局状态和阶段
        current_phase = await _get_current_phase(game_id, event_bus)
        if current_phase not in (GamePhase.DAY_VOTE, GamePhase.DAY_PK_VOTE):
            raise HTTPException(
                status_code=409,
                detail=f"当前阶段 [{current_phase.value}] 不允许投票",
            )

        current_round = await _get_round(game_id, event_bus)

        # 构建 AgentAction
        action = AgentAction(
            action_type=ActionType.VOTE,
            actor_id=request.actor_id,
            target_id=request.target_id,
            phase=current_phase,
            round=current_round,
            reason="API 投票提交",
        )

        # 委托 VoteManager 校验并记录
        vote_mgr = VoteManager(game_id, event_bus)
        vote_mgr.begin_vote(current_round)
        await vote_mgr.submit_vote(action, roles={}, current_phase=current_phase)

        logger.info(
            "vote_submitted",
            game_id=game_id,
            actor_id=request.actor_id,
            target_id=request.target_id,
        )
        return ActionResponse(
            success=True,
            action_type=ActionType.VOTE.value,
            actor_id=request.actor_id,
            target_id=request.target_id,
        )

    except ActionValidationError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RedisUnavailableException as e:
        logger.error("submit_vote_redis_unavailable", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=503, detail="Redis 服务不可用，请稍后重试")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("submit_vote_failed", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"投票提交失败: {str(e)}")


@router.get("/{game_id}/vote/status", response_model=VoteStatusResponse)
async def get_vote_status(game_id: str, round_num: Optional[int] = None) -> VoteStatusResponse:
    """查询当前投票状态。

    返回当前轮次的全量选票映射和已投票人数。

    Args:
        game_id: 对局 ID。
        round_num: 轮次号（可选，默认为当前轮次）。

    Raises:
        503: Redis 不可用。
    """
    try:
        # use global event_bus
        if round_num is None:
            round_num = await _get_round(game_id, event_bus)

        vote_mgr = VoteManager(game_id, event_bus)

        current_votes = await vote_mgr.get_current_votes()
        voter_count = await vote_mgr.get_voter_count()
        is_pk = vote_mgr.is_pk_vote()

        return VoteStatusResponse(
            game_id=game_id,
            votes=current_votes,
            voter_count=voter_count,
            is_pk_vote=is_pk,
        )

    except RedisUnavailableException as e:
        logger.error("get_vote_status_redis_unavailable", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=503, detail="Redis 服务不可用，请稍后重试")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_vote_status_failed", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询投票状态失败: {str(e)}")


# ============================================================================
# 发言端点
# ============================================================================


@router.post("/{game_id}/speak", response_model=ActionResponse)
async def submit_speech(game_id: str, request: SubmitSpeechRequest) -> ActionResponse:
    """提交发言。

    将发言内容作为 PUBLIC SPEECH_EVENT 发布到 EventBus，
    所有玩家和观战者均可接收。

    执行流程:
    1. 校验对局处于 RUNNING 状态且当前为发言阶段
    2. 构建发言 Event 发布到 EventBus
    3. 返回操作结果

    Raises:
        409: 当前阶段不允许发言。
        422: 对局不在 RUNNING 状态。
        503: Redis 不可用。
    """
    # use global event_bus
    try:
        # 校验对局状态和阶段
        current_phase = await _get_current_phase(game_id, event_bus)
        speech_phases = (GamePhase.DAY_DISCUSSION, GamePhase.DAY_PK_DISCUSSION, GamePhase.LAST_WORDS)
        if current_phase not in speech_phases:
            raise HTTPException(
                status_code=409,
                detail=f"当前阶段 [{current_phase.value}] 不允许发言",
            )

        current_round = await _get_round(game_id, event_bus)

        # 构建发言事件
        speech_event = Event(
            event_id=str(uuid.uuid4()),
            game_id=game_id,
            seq_num=0,  # EventBus 自动分配
            event_type=EventType.SPEECH_EVENT,
            visibility=Visibility.PUBLIC,
            target_agents=[],
            timestamp=now_tz(),
            payload={
                "actor_id": request.actor_id,
                "content": request.content,
                "emotion": request.emotion,
                "phase": current_phase.value,
                "round": current_round,
            },
        )
        await event_bus.publish(speech_event)

        logger.info(
            "speech_submitted",
            game_id=game_id,
            actor_id=request.actor_id,
            phase=current_phase.value,
        )
        return ActionResponse(
            success=True,
            action_type=ActionType.SPEAK.value,
            actor_id=request.actor_id,
        )

    except RedisUnavailableException as e:
        logger.error("submit_speech_redis_unavailable", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=503, detail="Redis 服务不可用，请稍后重试")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("submit_speech_failed", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"发言提交失败: {str(e)}")


# ============================================================================
# 夜间技能端点
# ============================================================================


@router.post("/{game_id}/action", response_model=ActionResponse)
async def submit_action(game_id: str, request: SubmitActionRequest) -> ActionResponse:
    """提交夜间技能动作。

    支持的动作类型（由 ActionType 枚举定义）:
    - WOLF_KILL: 狼人刀人
    - WITCH_SAVE: 女巫使用解药
    - WITCH_POISON: 女巫使用毒药
    - SEER_CHECK: 预言家验人
    - HUNTER_SHOOT: 猎人死亡开枪
    - PASS: 空过/不发动技能

    执行流程:
    1. 校验 action_type 是否为合法 ActionType 枚举值
    2. 校验对局处于 RUNNING 状态且当前阶段合法
    3. 构建 AgentAction 交给 ActionResolver.submit_action 校验并暂存
    4. 返回操作结果

    注意: 夜间动作在提交时仅暂存，真正的结算在 NIGHT_RESOLVE 阶段执行。

    Raises:
        400: 无效的 action_type。
        409: 阶段不匹配 / 动作非法。
        422: 对局不在 RUNNING 状态。
        503: Redis 不可用。
    """
    # use global event_bus
    try:
        # 校验 action_type 合法性
        try:
            action_type = ActionType(request.action_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"无效的动作类型: {request.action_type}，"
                       f"有效值: {[at.value for at in ActionType]}",
            )

        # 校验对局状态和阶段
        current_phase = await _get_current_phase(game_id, event_bus)
        current_round = await _get_round(game_id, event_bus)

        # 构建 AgentAction
        action = AgentAction(
            action_type=action_type,
            actor_id=request.actor_id,
            target_id=request.target_id,
            phase=current_phase,
            round=current_round,
            reason="API 技能提交",
        )

        # 委托 ActionResolver 校验并暂存
        resolver = ActionResolver(game_id, event_bus)
        await resolver.submit_action(action, roles={}, current_phase=current_phase)

        logger.info(
            "night_action_submitted",
            game_id=game_id,
            actor_id=request.actor_id,
            action_type=action_type.value,
            target_id=request.target_id,
            phase=current_phase.value,
        )
        return ActionResponse(
            success=True,
            action_type=action_type.value,
            actor_id=request.actor_id,
            target_id=request.target_id,
        )

    except ActionValidationError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RedisUnavailableException as e:
        logger.error("submit_action_redis_unavailable", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=503, detail="Redis 服务不可用，请稍后重试")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("submit_action_failed", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"技能提交失败: {str(e)}")
