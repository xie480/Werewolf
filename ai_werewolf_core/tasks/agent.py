"""
Agent 推理 Celery 任务 —— Phase 4 预留。

**Why**: Agent Runtime（LangGraph 工作流 + LLM 推理）是重计算任务，
必须委托给 Celery Worker 异步执行。当前 Phase 3 仅定义任务签名，
Phase 4 完成后补充完整实现。

参考:
- [`docs/plan/Celery 异步任务系统设计.md`](../docs/plan/Celery%20异步任务系统设计.md)
- [`docs/system/Agent Runtime.md`](../docs/system/Agent%20Runtime.md)
"""

from __future__ import annotations

from ai_werewolf_core.worker import celery_app
from ai_werewolf_core.utils.logger import get_logger

logger = get_logger(__name__)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    name="tasks.agent.agent_infer",
)
def agent_infer_task(self, game_id: str, player_id: str) -> dict:
    """Agent 推理任务 —— Phase 4 占位。

    委托 LangGraph 工作流为指定玩家执行一次推理，
    返回该玩家的决策结果（发言/投票/技能使用）。

    Args:
        game_id: 对局唯一标识。
        player_id: 执行推理的玩家 ID。

    Returns:
        占位结果: ``{"game_id": str, "player_id": str, "status": "placeholder"}``
    """
    logger.info(
        "celery_agent_infer_placeholder",
        game_id=game_id,
        player_id=player_id,
        task_id=self.request.id,
    )
    # Phase 4 Agent Runtime 完成后实现完整 LangGraph 推理逻辑
    return {
        "game_id": game_id,
        "player_id": player_id,
        "task": "agent_infer",
        "status": "placeholder",
        "action": None,
        "message": "Phase 4 Agent Runtime 完成后实现",
    }


# 批量推理任务（Phase 4 预留）
@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    name="tasks.agent.batch_infer",
)
def batch_infer_task(self, game_id: str, player_ids: list[str]) -> dict:
    """批量 Agent 推理任务 —— Phase 4 占位。

    Args:
        game_id: 对局唯一标识。
        player_ids: 需要推理的玩家 ID 列表。

    Returns:
        占位结果。
    """
    logger.info(
        "celery_batch_infer_placeholder",
        game_id=game_id,
        player_count=len(player_ids),
        task_id=self.request.id,
    )
    return {
        "game_id": game_id,
        "task": "batch_infer",
        "status": "placeholder",
        "player_count": len(player_ids),
        "results": [],
        "message": "Phase 4 Agent Runtime 完成后实现",
    }
