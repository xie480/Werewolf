# coding: utf-8
"""
Agent Celery 任务封装

将 LangGraph 工作流包装为 Celery 异步任务。
"""

from typing import Dict, Any

from celery import shared_task
from structlog import get_logger

from ai_werewolf_core.schemas.enums import GamePhase
from ai_werewolf_core.agents.graph import build_agent_graph, create_initial_state

logger = get_logger()


@shared_task(name="agents.run_agent_decision", bind=True, max_retries=1)
def run_agent_decision(
    self,
    game_id: str,
    player_id: str,
    current_phase: str,
    current_round: int,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """
    Celery 任务：执行 Agent 决策流程。

    Args:
        game_id: 游戏唯一标识
        player_id: 玩家唯一标识
        current_phase: 当前游戏阶段（字符串形式，如 "DAY_DISCUSSION"）
        current_round: 当前游戏轮次
        max_retries: 工作流内部最大重试次数

    Returns:
        包含 decision_action 的最终结果字典
    """
    logger.info(
        "agent_task_started",
        task_id=self.request.id,
        game_id=game_id,
        player_id=player_id,
        phase=current_phase,
        round=current_round,
    )

    # 转换阶段字符串为枚举
    try:
        phase_enum = GamePhase(current_phase)
    except ValueError:
        logger.error("invalid_phase_enum", phase=current_phase)
        return {"is_valid": False, "error": f"Invalid phase: {current_phase}"}

    # 创建初始状态
    initial_state = create_initial_state(
        game_id=game_id,
        player_id=player_id,
        current_phase=phase_enum,
        current_round=current_round,
        max_retries=max_retries,
    )

    # 构建并运行图
    graph = build_agent_graph()
    # 注意：Celery 任务是同步的，但 LangGraph 节点是异步的
    # 需要使用 asyncio 运行
    import asyncio
    final_state = asyncio.run(graph.ainvoke(initial_state))

    result = {
        "game_id": game_id,
        "player_id": player_id,
        "proposed_action": final_state.get("proposed_action"),
        "is_valid": final_state.get("is_valid", False),
        "retry_count": final_state.get("retry_count", 0),
        "internal_monologue": final_state.get("internal_monologue", ""),
    }

    logger.info(
        "agent_task_completed",
        task_id=self.request.id,
        game_id=game_id,
        player_id=player_id,
        is_valid=result["is_valid"],
    )

    return result


@shared_task(name="agents.submit_action", bind=True)
def submit_agent_action(
    self,
    game_id: str,
    player_id: str,
    action: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Celery 任务：将 Agent 生成的最终动作提交给 Engine。

    Args:
        game_id: 游戏唯一标识
        player_id: 玩家唯一标识
        action: 待提交的动作字典

    Returns:
        提交结果字典
    """
    logger.info(
        "submit_action_task",
        task_id=self.request.id,
        game_id=game_id,
        player_id=player_id,
        action_type=action.get("action_type"),
    )

    # 实际调用 Engine 提交动作
    import asyncio
    from ai_werewolf_core.schemas.models import AgentAction
    from ai_werewolf_core.api.routes.actions import submit_action_internal

    async def _submit():
        try:
            action_obj = AgentAction(**action)
            # 调用内部提交接口，复用 API 层的逻辑
            result = await submit_action_internal(game_id, action_obj)
            
            if result.accepted:
                return {
                    "game_id": game_id,
                    "player_id": player_id,
                    "submitted": True,
                    "reason": result.reason,
                    "action": action,
                }
            else:
                logger.warning("submit_action_rejected", reason=result.reason, action=action)
                # 触发兜底
                raise ValueError(f"Action rejected: {result.reason}")
                
        except Exception as e:
            logger.error("submit_action_failed_triggering_fallback", error=str(e), exc_info=True)
            
            # 终极兜底：构造安全的默认动作并强制提交(但基本不太可能)
            from ai_werewolf_core.agents.graph.nodes import generate_safe_default_action
            from ai_werewolf_core.schemas.enums import GamePhase
            
            try:
                # 尝试从原动作中提取阶段和轮次，如果失败则使用默认值
                phase_val = action.get("phase")
                phase_enum = GamePhase(phase_val) if phase_val else GamePhase.DAY_DISCUSSION
                round_num = action.get("round", 1)
                
                # 生成默认动作
                fallback_action_dict = generate_safe_default_action(phase_enum, round_num, player_id)
                # 构造对象
                fallback_action_obj = AgentAction(**fallback_action_dict)
                
                logger.info("submitting_fallback_action", fallback_action=fallback_action_dict)
                # 提交
                fallback_result = await submit_action_internal(game_id, fallback_action_obj)
                
                return {
                    "game_id": game_id,
                    "player_id": player_id,
                    "submitted": fallback_result.accepted,
                    "reason": f"Fallback submitted. Original error: {str(e)}. Fallback result: {fallback_result.reason}",
                    "action": fallback_action_dict,
                }
            except Exception as fallback_e:
                logger.error("fallback_action_failed", error=str(fallback_e), exc_info=True)
                return {
                    "game_id": game_id,
                    "player_id": player_id,
                    "submitted": False,
                    "reason": f"Original error: {str(e)}. Fallback error: {str(fallback_e)}",
                    "action": action,
                }

    return asyncio.run(_submit())
