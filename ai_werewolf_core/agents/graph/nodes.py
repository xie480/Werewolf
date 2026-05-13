# coding: utf-8
"""
LangGraph 工作流核心节点实现

包含：
- memory_node: 感知与记忆加载
- reasoning_node: LLM 推理决策
- validation_node: 动作校验
- fallback_node: 安全降级
"""

import asyncio
import json
from typing import Dict, Any, List, Optional

from structlog import get_logger

from ai_werewolf_core.schemas.enums import GamePhase, ActionType
from .state import AgentState

logger = get_logger()


async def memory_node(state: AgentState) -> Dict[str, Any]:
    """
    感知与记忆节点。

    负责调用 Memory System，获取当前 Agent 的 PUBLIC/PRIVATE/FACTION 记忆快照。

    Args:
        state: 当前 AgentState

    Returns:
        包含 memory_snapshot 的状态更新字典
    """
    game_id = state["game_id"]
    player_id = state["player_id"]

    logger.debug(
        "memory_node_start",
        game_id=game_id,
        player_id=player_id,
        phase=state["current_phase"],
    )

    # TODO: 实际调用 Memory System
    # from ai_werewolf_core.agents.memory import MemoryManager
    # snapshot = await MemoryManager.build_snapshot(game_id, player_id)

    # 占位实现
    snapshot = {
        "game_id": game_id,
        "player_id": player_id,
        "phase": state["current_phase"],
        "public_memory": [],
        "private_memory": {},
        "faction_memory": [],
        "timestamp": None,
    }

    return {"memory_snapshot": snapshot}


async def reasoning_node(state: AgentState) -> Dict[str, Any]:
    """
    推理决策节点。

    负责调用 Prompt Builder 构建提示词，通过 Model Adapter 调用 LLM，
    解析响应并填充 proposed_action、internal_monologue、suspect_list。

    Args:
        state: 当前 AgentState

    Returns:
        包含推理结果的状态更新字典
    """
    game_id = state["game_id"]
    player_id = state["player_id"]
    current_phase = state["current_phase"]
    memory_snapshot = state.get("memory_snapshot")
    validation_errors = state.get("validation_errors", [])

    logger.debug(
        "reasoning_node_start",
        game_id=game_id,
        player_id=player_id,
        phase=current_phase,
        retry_count=state.get("retry_count", 0),
    )

    # TODO: 实际调用 Prompt Builder 和 Model Adapter
    # from ai_werewolf_core.agents.prompt import PromptBuilder
    # prompt = PromptBuilder.build(memory_snapshot, validation_errors)
    # response = await model_adapter.agenerate(prompt)

    # 占位实现：生成空响应
    raw_response = "I am thinking..."
    internal_monologue = "I should skip my turn."
    suspect_list = {}
    
    # 模拟解析成功，生成符合 AgentAction Schema 的动作
    current_round = state.get("current_round", 1)
    proposed_action = {
        "action_type": ActionType.PASS.value,
        "actor_id": player_id,
        "target_id": None,
        "phase": current_phase.value,
        "round": current_round,
        "reason": "Stub reasoning",
        "confidence": 1.0,
    }

    is_valid = True

    if is_valid:
        return {
            "raw_llm_response": raw_response,
            "internal_monologue": internal_monologue,
            "suspect_list": suspect_list,
            "proposed_action": proposed_action,
            "is_valid": True,
            "validation_errors": [],
        }
    else:
        # 解析失败时记录错误
        error_msg = "LLM response parsing failed"
        new_errors = validation_errors + [error_msg]
        return {
            "raw_llm_response": raw_response,
            "internal_monologue": "",
            "suspect_list": {},
            "proposed_action": None,
            "is_valid": False,
            "validation_errors": new_errors,
            "retry_count": state.get("retry_count", 0) + 1,
        }


async def validation_node(state: AgentState) -> Dict[str, Any]:
    """
    动作校验节点。

    负责对 proposed_action 进行 Schema 校验和基础业务规则校验。

    Args:
        state: 当前 AgentState

    Returns:
        包含校验结果的状态更新字典
    """
    game_id = state["game_id"]
    player_id = state["player_id"]
    proposed_action = state.get("proposed_action")
    current_errors = state.get("validation_errors", [])
    retry_count = state.get("retry_count", 0)

    logger.debug(
        "validation_node_start",
        game_id=game_id,
        player_id=player_id,
        has_action=proposed_action is not None,
        retry_count=retry_count,
    )

    if not proposed_action:
        return {
            "is_valid": False,
            "validation_errors": current_errors + ["No proposed action provided"],
            "retry_count": retry_count + 1,
        }

    errors = []

    from ai_werewolf_core.schemas.models import AgentAction
    from ai_werewolf_core.core.action.validator import ActionValidator

    # 1. Schema 校验
    action_obj = None
    try:
        # 尝试将字典转换为 AgentAction 模型
        action_obj = AgentAction(**proposed_action)
    except Exception as e:
        errors.append(f"Schema validation error: {str(e)}")

    # 2. 基础业务规则校验
    if not errors and action_obj:
        try:
            result = await ActionValidator.validate_basic(action_obj, game_id)
            if not result.is_valid:
                errors.append(f"Business validation error: {result.reason}")
        except Exception as e:
            errors.append(f"Business validation error: {str(e)}")

    if errors:
        return {
            "is_valid": False,
            "validation_errors": current_errors + errors,
            "retry_count": retry_count + 1,
        }

    logger.info(
        "validation_passed",
        game_id=game_id,
        player_id=player_id,
        action_type=proposed_action.get("type"),
    )

    return {
        "is_valid": True,
        "validation_errors": [],
        "retry_count": retry_count,  # 校验成功时不增加重试计数
    }


async def fallback_node(state: AgentState) -> Dict[str, Any]:
    """
    安全降级节点。

    当重试次数耗尽时执行，生成安全的默认动作防止游戏阻塞。

    Args:
        state: 当前 AgentState

    Returns:
        包含默认动作的状态更新字典
    """
    game_id = state["game_id"]
    player_id = state["player_id"]
    current_phase = state["current_phase"]
    current_round = state.get("current_round", 1)
    errors = state.get("validation_errors", [])

    logger.error(
        "fallback_triggered",
        game_id=game_id,
        player_id=player_id,
        phase=current_phase,
        errors=errors,
    )

    # 根据当前阶段生成安全默认动作
    default_action = generate_safe_default_action(current_phase, current_round, player_id)

    return {
        "proposed_action": default_action,
        "is_valid": True,
        "internal_monologue": "系统强制接管：重试次数耗尽，执行默认动作。",
        "validation_errors": [],
    }


def generate_safe_default_action(phase: GamePhase, round_num: int, player_id: str) -> Dict:
    """
    生成安全的默认动作。

    Args:
        phase: 当前游戏阶段
        round_num: 当前游戏轮次
        player_id: 玩家 ID

    Returns:
        默认动作字典
    """
    from ai_werewolf_core.schemas.enums import GamePhase, ActionType

    base_action = {
        "actor_id": player_id,
        "target_id": None,
        "phase": phase.value,
        "round": round_num,
        "reason": "系统强制接管：重试次数耗尽，执行默认动作。",
        "confidence": 1.0,
    }

    # 根据阶段类型生成默认动作
    # 发言阶段
    if phase in (GamePhase.DAY_START, GamePhase.DAY_DISCUSSION, GamePhase.DAY_PK_DISCUSSION, GamePhase.LAST_WORDS):
        base_action["action_type"] = ActionType.SPEAK.value
    # 投票阶段
    elif phase in (GamePhase.DAY_VOTE, GamePhase.DAY_PK_VOTE):
        base_action["action_type"] = ActionType.VOTE.value
    # 夜间行动阶段
    elif phase in (GamePhase.NIGHT_WOLF_ACT, GamePhase.NIGHT_WITCH_ACT, GamePhase.NIGHT_SEER_ACT):
        base_action["action_type"] = ActionType.PASS.value
    # 其他阶段默认跳过
    else:
        base_action["action_type"] = ActionType.PASS.value

    return base_action
