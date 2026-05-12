"""
评测统计 Celery 任务 —— Phase 5 预留。

**Why**: 赛后复盘评测是重计算任务（五维评分、数据分析），
必须委托给 Celery Worker 异步执行。当前 Phase 3 仅定义任务签名，
Phase 5 完成后补充完整实现。

参考:
- [`docs/plan/Celery 异步任务系统设计.md`](../docs/plan/Celery%20异步任务系统设计.md)
- [`docs/system/Evaluation System.md`](../docs/system/Evaluation%20System.md)
"""

from __future__ import annotations

from ai_werewolf_core.worker import celery_app
from ai_werewolf_core.utils.logger import get_logger

logger = get_logger(__name__)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    name="tasks.eval.evaluate_game",
)
def evaluate_game_task(self, game_id: str) -> dict:
    """赛后评测任务 —— Phase 5 占位。

    对已结束的对局执行五维评分（推理能力、阵营协作、
    信息利用、策略深度、情绪表达），生成复盘报告。

    Args:
        game_id: 对局唯一标识。

    Returns:
        占位结果: ``{"game_id": str, "status": "placeholder"}``
    """
    logger.info(
        "celery_evaluate_game_placeholder",
        game_id=game_id,
        task_id=self.request.id,
    )
    # Phase 5 Evaluation System 完成后实现完整评测逻辑
    return {
        "game_id": game_id,
        "task": "evaluate_game",
        "status": "placeholder",
        "scores": {
            "reasoning": 0,
            "teamwork": 0,
            "information": 0,
            "strategy": 0,
            "expression": 0,
        },
        "message": "Phase 5 Evaluation System 完成后实现",
    }
