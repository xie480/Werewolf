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

    负责调用 Memory System，获取当前 Agent 的 PUBLIC/PRIVATE/FACTION 记忆快照，
    并调用 Prompt Builder 构建完整的提示词。

    Args:
        state: 当前 AgentState

    Returns:
        包含 memory_snapshot 和 full_prompt 的状态更新字典
    """
    game_id = state["game_id"]
    player_id = state["player_id"]

    logger.debug(
        "memory_node_start",
        game_id=game_id,
        player_id=player_id,
        phase=state["current_phase"],
    )

    from ai_werewolf_core.agents.memory.public import PublicMemoryManager
    from ai_werewolf_core.agents.memory.private import PrivateMemoryManager
    from ai_werewolf_core.agents.prompts.builder import PromptBuilder
    from ai_werewolf_core.schemas.models import MemorySnapshot

    try:
        public_mgr = PublicMemoryManager()
        private_mgr = PrivateMemoryManager()
        
        # 获取公共记忆上下文（包含压缩记忆和近期全量记忆）
        memory_context = await public_mgr.get_memory_context(game_id)
        compressed_memories = memory_context["compressed_memories"]
        recent_memories = memory_context["recent_memories"]
        
        # 获取玩家私有状态
        private_state = await private_mgr.get_private_state(game_id, player_id, player_id)
        # 获取玩家私有轮次数据
        private_round_data = await private_mgr.get_private_round_data(game_id, player_id)
        # 获取玩家最近一次的嫌疑人列表
        last_suspect_list = await private_mgr.get_last_suspect_list(game_id, player_id)
        
        # 获取当前对局所有玩家的唯一标识ID列表（无论是否存活）
        from ai_werewolf_core.core.engine.player_manager import PlayerStatusManager
        player_mgr = PlayerStatusManager()
        all_players_raw = await player_mgr.get_all_players(game_id)
        all_player_ids = sorted(all_players_raw.keys()) if all_players_raw else []
        
        # 组装所有轮次记忆
        from ai_werewolf_core.schemas.models import RoundMemory
        round_memories_dict = {}
        
        # 1. 填入压缩记忆
        for r_num, comp_resp in compressed_memories.items():
            round_memories_dict[r_num] = RoundMemory(
                round_num=r_num,
                public_events=[],
                compressed_public=comp_resp,
                private_facts=[],
                reasoning=[]
            )
            
        # 2. 填入近期全量记忆
        for rm in recent_memories:
            round_memories_dict[rm.round_num] = rm
            
        # 3. 合并私有轮次数据
        for r_num, p_data in private_round_data.items():
            if r_num not in round_memories_dict:
                round_memories_dict[r_num] = RoundMemory(
                    round_num=r_num,
                    public_events=[],
                    private_facts=[],
                    reasoning=[]
                )
            round_memories_dict[r_num].private_facts = p_data.get("private_facts", [])
            round_memories_dict[r_num].reasoning = p_data.get("reasoning", [])
            
        round_memories = list(round_memories_dict.values())
        round_memories.sort(key=lambda x: x.round_num)
        for r_num, p_data in private_round_data.items():
            if r_num not in round_memories_dict:
                from ai_werewolf_core.schemas.models import RoundMemory
                round_memories.append(RoundMemory(
                    round_num=r_num,
                    public_events=[],
                    private_facts=p_data.get("private_facts", []),
                    reasoning=p_data.get("reasoning", [])
                ))
        round_memories.sort(key=lambda x: x.round_num)
        
        snapshot_obj = MemorySnapshot(
            agent_id=player_id,
            game_id=game_id,
            private_state=private_state,
            history=round_memories,
            experiences=[],
            last_suspect_list=last_suspect_list,
            all_player_ids=all_player_ids
        )
        
        prompt_builder = PromptBuilder()
        full_prompt = await prompt_builder.build_prompt(
            snapshot_obj,
            current_phase=state["current_phase"].value
        )

        logger.debug(
            "memory_node_end",
            game_id=game_id,
            player_id=player_id,
            phase=state["current_phase"],
            memory_snapshot=snapshot_obj.model_dump(),
            full_prompt=full_prompt
        )
        
        return {
            "memory_snapshot": snapshot_obj.model_dump(),
            "full_prompt": full_prompt
        }
    except Exception as e:
        logger.error("memory_node_error", error=str(e), exc_info=True)
        return {
            "memory_snapshot": {},
            "full_prompt": f"Error building prompt: {str(e)}"
        }


async def reasoning_node(state: AgentState) -> Dict[str, Any]:
    """
    推理决策节点。

    负责通过 Model Adapter 调用 LLM，
    解析响应并填充 proposed_action、internal_monologue、suspect_list。

    Args:
        state: 当前 AgentState

    Returns:
        包含推理结果的状态更新字典
    """
    game_id = state["game_id"]
    player_id = state["player_id"]
    current_phase = state["current_phase"]
    full_prompt = state.get("full_prompt", "")
    validation_errors = state.get("validation_errors", [])

    logger.debug(
        "reasoning_node_start",
        game_id=game_id,
        player_id=player_id,
        phase=current_phase,
        retry_count=state.get("retry_count", 0),
    )

    from ai_werewolf_core.agents.adapter.factory import AdapterFactory
    from ai_werewolf_core.schemas.models import AdapterRequest
    from pydantic import BaseModel, Field

    try:
        # 如果有之前的校验错误，追加到 Prompt 中强制纠正
        if validation_errors:
            full_prompt += f"\n\n【系统警告】你上一次的输出存在以下错误，请务必修正：\n{validation_errors[-1]}"
            
        # 调用大模型
        # 从 Redis 获取玩家的 model_id（默认 fallback 为 "deepseek-v4-flash"）
        from ai_werewolf_core.core.engine.player_manager import PlayerStatusManager
        
        player_mgr = PlayerStatusManager()
        player_info = await player_mgr.get_player_info(game_id, player_id)
        model_id = player_info.get("model_id", "deepseek-v4-flash") if player_info else "deepseek-v4-flash"
        adapter = AdapterFactory.get_adapter(model_id)
        
        # 定义期望的响应模型
        class AgentResponseSchema(BaseModel):
            internal_monologue: str = Field(..., description="内心推理过程")
            suspect_list: Dict[str, float] = Field(default_factory=dict, description="嫌疑人列表")
            action_type: str = Field(..., description="动作类型")
            action_target: Optional[str] = Field(None, description="动作目标")
            speech_content: Optional[str] = Field(None, description="发言内容")
            confidence: float = Field(1.0, description="确信度")
            
        request = AdapterRequest(
            model_id=model_id,
            agent_id=player_id,
            game_id=game_id,
            phase=current_phase,
            full_prompt=full_prompt,
            response_model=AgentResponseSchema
        )
        
        response = await adapter.agenerate(request)
        
        if response.is_success and response.parsed_data:
            parsed_data = response.parsed_data
            
            # 构造 proposed_action
            current_round = state.get("current_round", 1)
            
            # 清理 action_target，防止 LLM 输出 "null", "None", "" 等字符串
            target_id = parsed_data.action_target
            if target_id in ("null", "None", "none", "", "无"):
                target_id = None
                
            proposed_action = {
                "action_type": parsed_data.action_type,
                "actor_id": player_id,
                "target_id": target_id,
                "phase": current_phase.value,
                "round": current_round,
                "reason": parsed_data.internal_monologue,
                "inner_thought": parsed_data.internal_monologue,
                "confidence": parsed_data.confidence,
            }
            
            # 如果有发言内容，可以附加到 proposed_action 或单独处理
            if parsed_data.speech_content and parsed_data.action_type == ActionType.SPEAK.value:
                proposed_action["speech_content"] = parsed_data.speech_content
                
            # 保存内心 OS 和嫌疑人列表 (非阻塞异步写入)
            from ai_werewolf_core.agents.memory.private import PrivateMemoryManager
            import asyncio
            private_mgr = PrivateMemoryManager()
            asyncio.create_task(
                private_mgr.save_reasoning(game_id, player_id, current_round, current_phase.value, parsed_data.internal_monologue)
            )
            if parsed_data.suspect_list:
                asyncio.create_task(
                    private_mgr.save_suspect_list(game_id, player_id, parsed_data.suspect_list)
                )
                
            return {
                "raw_llm_response": response.raw_content,
                "internal_monologue": parsed_data.internal_monologue,
                "suspect_list": parsed_data.suspect_list,
                "proposed_action": proposed_action,
                "is_valid": True,
                "validation_errors": [],
            }
        else:
            error_msg = response.error_message or "LLM response parsing failed"
            new_errors = validation_errors + [error_msg]
            return {
                "raw_llm_response": response.raw_content,
                "internal_monologue": "",
                "suspect_list": {},
                "proposed_action": None,
                "is_valid": False,
                "validation_errors": new_errors,
                "retry_count": state.get("retry_count", 0) + 1,
            }
            
    except Exception as e:
        logger.error("reasoning_node_error", error=str(e), exc_info=True)
        new_errors = validation_errors + [f"Reasoning node error: {str(e)}"]
        return {
            "raw_llm_response": "",
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
