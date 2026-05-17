# coding: utf-8
"""
AgentState 状态定义

定义节点间传递的契约数据结构。
"""

from typing import TypedDict, List, Dict, Optional, Any

from ai_werewolf_core.schemas.enums import GamePhase


class AgentState(TypedDict, total=False):
    """
    LangGraph 工作流节点间传递的状态契约。

    字段说明：
    - game_id / player_id: 游戏标识和玩家标识，由 Engine 传入
    - current_phase: 当前游戏阶段（DAY/NIGHT/VOTE 等）
    - memory_snapshot: Memory System 构建的快照，包含 PUBLIC/PRIVATE/FACTION 记忆
    - raw_llm_response: LLM 原始返回的文本内容
    - internal_monologue: 解析出的内心独白/思考过程
    - suspect_list: 玩家 ID 到嫌疑值的映射热力图
    - proposed_action: 拟提交的合法动作字典
    - retry_count: 当前重试次数
    - max_retries: 最大允许重试次数（默认 3）
    - validation_errors: 校验失败时记录的错误列表，用于反馈给 LLM
    - is_valid: 当前动作是否通过校验
    """

    # 基础上下文（由 Engine 传入）
    game_id: str
    player_id: str
    current_phase: GamePhase
    current_round: int

    # 记忆与感知（由 memory_node 填充）
    memory_snapshot: Optional[Any]
    full_prompt: str

    # 推理与决策（由 reasoning_node 填充）
    raw_llm_response: str
    internal_monologue: str
    suspect_list: Dict[str, float]

    # 最终输出（由 reasoning_node/fallback_node 填充）
    proposed_action: Optional[Dict]

    # 控制流与重试状态（由 validation_node 维护）
    retry_count: int
    max_retries: int
    validation_errors: List[str]
    is_valid: bool


def create_initial_state(
    game_id: str,
    player_id: str,
    current_phase: GamePhase,
    current_round: int,
    max_retries: int = 3
) -> AgentState:
    """
    创建 AgentState 初始状态。

    Args:
        game_id: 游戏唯一标识
        player_id: 玩家唯一标识
        current_phase: 当前游戏阶段
        current_round: 当前游戏轮次
        max_retries: 最大重试次数，默认 3

    Returns:
        初始化后的 AgentState 字典
    """
    return AgentState(
        game_id=game_id,
        player_id=player_id,
        current_phase=current_phase,
        current_round=current_round,
        memory_snapshot=None,
        full_prompt="",
        raw_llm_response="",
        internal_monologue="",
        suspect_list={},
        proposed_action=None,
        retry_count=0,
        max_retries=max_retries,
        validation_errors=[],
        is_valid=False,
    )
