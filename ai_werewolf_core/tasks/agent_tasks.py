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
    
    async def _run_and_submit():
        """执行LangGraph工作流并提交代理操作
        
        此函数执行以下步骤：
        1. 通过ainvoke调用graph并获取状态
        2. 检查状态中的建议操作是否有效
        3. 如果操作有效，则将其提交到游戏中
        4. 如果提交失败或出现异常，则执行回退机制，提交安全默认操作
        5. 返回最终状态
        
        Returns:
            Dict[str, Any]: 包含操作结果的最终状态字典
        """
        state = await graph.ainvoke(initial_state)
        
        proposed_action = state.get("proposed_action")
        is_valid = state.get("is_valid", False)
        
        # 验证操作并尝试提交
        if is_valid and proposed_action:
            from ai_werewolf_core.schemas.models import AgentAction
            from ai_werewolf_core.api.routes.actions import submit_action_internal
            
            try:
                # 创建操作对象并提交到游戏中
                action_obj = AgentAction(**proposed_action)
                submit_result = await submit_action_internal(game_id, action_obj)
                if not submit_result.accepted:
                    logger.warning("submit_action_rejected", reason=submit_result.reason, action=proposed_action)
                    raise ValueError(f"Action rejected: {submit_result.reason}")
                else:
                    logger.info("submit_action_success", game_id=game_id, player_id=player_id, action=proposed_action)
            except Exception as e:
                # 主操作提交失败时执行回退逻辑
                logger.error("submit_action_failed_triggering_fallback", error=str(e), exc_info=True)
                from ai_werewolf_core.agents.graph.nodes import generate_safe_default_action
                from ai_werewolf_core.schemas.enums import GamePhase
                
                try:
                    # 获取操作阶段和轮数信息
                    phase_val = proposed_action.get("phase")
                    phase_enum = GamePhase(phase_val) if phase_val else GamePhase.DAY_DISCUSSION
                    round_num = proposed_action.get("round", 1)
                    
                    # 生成并提交安全的默认操作作为回退方案
                    fallback_action_dict = generate_safe_default_action(phase_enum, round_num, player_id)
                    fallback_action_obj = AgentAction(**fallback_action_dict)
                    
                    logger.info("submitting_fallback_action", fallback_action=fallback_action_dict)
                    await submit_action_internal(game_id, fallback_action_obj)
                except Exception as fallback_e:
                    logger.error("fallback_action_failed", error=str(fallback_e), exc_info=True)
                    
        return state

    from ai_werewolf_core.utils.asyncio_utils import run_async
    final_state = run_async(_run_and_submit())

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


@shared_task(name="agents.archive_memory", bind=True)
def task_archive_memory(
    self,
    game_id: str,
    agent_id: str,
    round_num: int,
) -> Dict[str, Any]:
    """
    Celery 任务：异步归档单轮记忆。
    
    执行流程：
    1. 压缩单轮推理记录
    2. 将本轮信息（公共事件摘要、私有事实、压缩推理）合并到全局摘要中
    """
    logger.info(
        "archive_memory_task_started",
        task_id=self.request.id,
        game_id=game_id,
        agent_id=agent_id,
        round_num=round_num,
    )
    
    import asyncio
    from ai_werewolf_core.agents.memory.compression import MemoryCompressionService
    from ai_werewolf_core.agents.memory.private import PrivateMemoryManager
    from ai_werewolf_core.agents.memory.public import PublicMemoryManager
    from ai_werewolf_core.utils.redis_client import RedisClientManager
    from ai_werewolf_core.constant.redis_keys import RedisKeys
    
    async def _archive():
        try:
            private_mgr = PrivateMemoryManager()
            public_mgr = PublicMemoryManager()
            
            # 1. 获取本轮推理记录并压缩
            private_round_data = await private_mgr.get_private_round_data(game_id, agent_id)
            round_data = private_round_data.get(round_num, {})
            reasoning = round_data.get("reasoning", [])
            
            compressed_reasoning = await MemoryCompressionService.compress_reasoning(
                reasoning=reasoning,
                game_id=game_id,
                agent_id=agent_id,
                round_num=round_num
            )
            
            # 2. 收集本轮所有信息用于合并
            # 获取公共记忆摘要
            redis = await RedisClientManager.get_client()
            key = RedisKeys.compressed_memory_summary(game_id)
            raw_data = await redis.hget(key, str(round_num))
            
            public_summary = ""
            if raw_data:
                import json
                data = json.loads(raw_data)
                public_summary = f"公共事件摘要：\n- 发言概括：{data.get('speech_summary', '')}\n- 关键事实：{data.get('key_facts', '')}"
            else:
                # 如果还没有压缩公共事件，则在此处触发压缩
                all_round_memories = await public_mgr.fetch_round_memories(game_id)
                target_rm = next((rm for rm in all_round_memories if rm.round_num == round_num), None)
                if target_rm and target_rm.public_events:
                    comp_resp = await MemoryCompressionService.compress(
                        events=target_rm.public_events,
                        game_id=game_id,
                        round_num=round_num
                    )
                    public_summary = f"公共事件摘要：\n- 发言概括：{comp_resp.speech_summary}\n- 关键事实：{comp_resp.key_facts}"
                
            # 获取私有事实
            private_facts = round_data.get("private_facts", [])
            private_facts_text = "私有事实：\n" + "\n".join([f"- [{f.phase.value}] {f.description}" for f in private_facts]) if private_facts else ""
            
            # 组装本轮新信息
            new_info_parts = []
            if public_summary:
                new_info_parts.append(public_summary)
            if private_facts_text:
                new_info_parts.append(private_facts_text)
            if compressed_reasoning:
                new_info_parts.append(f"我的推理：\n{compressed_reasoning}")
                
            new_info = "\n\n".join(new_info_parts)
            
            # 3. 获取当前全局摘要并合并
            global_summary_key = RedisKeys.global_summary(game_id, agent_id)
            current_summary = await redis.get(global_summary_key) or ""
            
            if new_info:
                await MemoryCompressionService.merge_global_summary(
                    current_summary=current_summary,
                    new_info=new_info,
                    game_id=game_id,
                    agent_id=agent_id,
                    round_num=round_num
                )
                
            logger.info(
                "archive_memory_task_completed",
                task_id=self.request.id,
                game_id=game_id,
                agent_id=agent_id,
                round_num=round_num,
            )
            return {"success": True}
            
        except Exception as e:
            logger.error("archive_memory_task_failed", error=str(e), exc_info=True)
            return {"success": False, "error": str(e)}
            
    from ai_werewolf_core.utils.asyncio_utils import run_async
    return run_async(_archive())


@shared_task(name="agents.submit_action", bind=True)
def submit_action(self, game_id: str, action: dict) -> Dict[str, Any]:
    """
    Placeholder task that forwards to the internal submit_action helper.
    """
    try:
        from ai_werewolf_core.schemas.models import AgentAction
        action_obj = AgentAction(**action)
        from ai_werewolf_core.api.routes.actions import submit_action_internal
        from ai_werewolf_core.utils.asyncio_utils import run_async
        result = run_async(submit_action_internal(game_id, action_obj))
        return {"accepted": result.accepted, "reason": result.reason}
    except Exception as e:
        logger.error("submit_action_task_error", error=str(e), exc_info=True)
        raise e
