# coding: utf-8
"""
LangGraph 图构建与编译

定义节点编排、条件路由和编译逻辑。
"""

from typing import Dict, Any, Optional

from langgraph.graph import StateGraph, END

from structlog import get_logger

from .state import AgentState, create_initial_state
from .nodes import memory_node, reasoning_node, validation_node, fallback_node

logger = get_logger()

# 节点名称常量
NODE_MEMORY = "memory"
NODE_REASONING = "reasoning"
NODE_VALIDATION = "validation"
NODE_FALLBACK = "fallback"

# 全局单例图实例
_compiled_graph: Optional[StateGraph] = None


def route_after_validation(state: AgentState) -> str:
    """
    根据校验结果和重试次数决定下一节点。

    Returns:
        END - 校验通过，工作流结束
        NODE_FALLBACK - 重试次数耗尽，进入降级
        NODE_REASONING - 继续重试，返回推理节点
    """
    is_valid = state.get("is_valid", False)
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 3)

    if is_valid:
        logger.debug("validation_success_ending", retry_count=retry_count)
        return END

    if retry_count >= max_retries:
        logger.warning(
            "retry_exhausted",
            retry_count=retry_count,
            max_retries=max_retries,
            errors=state.get("validation_errors", [])[-3:],
        )
        return NODE_FALLBACK

    logger.debug(
        "validation_failure_retry",
        retry_count=retry_count,
        max_retries=max_retries,
    )
    return NODE_REASONING


def build_agent_graph() -> StateGraph:
    """
    构建 Agent 工作流图。

    工作流路径：
        入口 → memory_node → reasoning_node → validation_node
                                                    ↓
                                         ┌──────────┴──────────┐
                                         ↓                     ↓
                                    validation OK          validation FAIL
                                         ↓                     ↓
                                        END              retry < max_retries?
                                                              ↓
                                                   ┌──────────┴──────────┐
                                                   ↓                     ↓
                                                retry OK             retry exhausted
                                                   ↓                     ↓
                                              reasoning_node        fallback_node
                                                                        ↓
                                                                       END

    Returns:
        编译后的 StateGraph 实例
    """
    # 创建状态图
    workflow = StateGraph(AgentState)

    # 添加节点
    workflow.add_node(NODE_MEMORY, memory_node)
    workflow.add_node(NODE_REASONING, reasoning_node)
    workflow.add_node(NODE_VALIDATION, validation_node)
    workflow.add_node(NODE_FALLBACK, fallback_node)

    # 设置入口点
    workflow.set_entry_point(NODE_MEMORY)

    # 定义主流程边
    workflow.add_edge(NODE_MEMORY, NODE_REASONING)
    workflow.add_edge(NODE_REASONING, NODE_VALIDATION)

    # 添加条件边
    workflow.add_conditional_edges(
        NODE_VALIDATION,
        route_after_validation,
        {
            END: END,
            NODE_FALLBACK: NODE_FALLBACK,
            NODE_REASONING: NODE_REASONING,
        },
    )

    # fallback 节点完成后直接结束
    workflow.add_edge(NODE_FALLBACK, END)

    logger.info("agent_graph_built")
    return workflow.compile()


def get_agent_graph() -> StateGraph:
    """
    获取全局单例 Agent 工作流图。

    懒加载构建，确保只编译一次。

    Returns:
        编译后的 StateGraph 实例
    """
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_agent_graph()
    return _compiled_graph


async def run_agent_workflow(
    game_id: str,
    player_id: str,
    current_phase: Any,  # GamePhase
    current_round: int,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """
    运行 Agent 工作流的便捷入口。

    Args:
        game_id: 游戏唯一标识
        player_id: 玩家唯一标识
        current_phase: 当前游戏阶段
        current_round: 当前游戏轮次
        max_retries: 最大重试次数

    Returns:
        最终状态字典，包含 proposed_action 等字段
    """
    # 创建初始状态
    initial_state = create_initial_state(
        game_id=game_id,
        player_id=player_id,
        current_phase=current_phase,
        current_round=current_round,
        max_retries=max_retries,
    )

    # 获取工作流图
    graph = get_agent_graph()
    # 运行工作流
    final_state = await graph.ainvoke(initial_state)

    logger.info(
        "agent_workflow_completed",
        game_id=game_id,
        player_id=player_id,
        is_valid=final_state.get("is_valid", False),
        retry_count=final_state.get("retry_count", 0),
    )

    return final_state
