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


import asyncio

@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    name="tasks.eval.evaluate_game",
)
def evaluate_game_task(self, game_id: str) -> dict:
    """赛后评测任务 —— 执行五维评分并生成复盘报告。

    对已结束的对局执行五维评分，生成复盘报告。

    Args:
        game_id: 对局唯一标识。

    Returns:
        包含评测结果摘要的字典。
    """
    logger.info(
        "celery_evaluate_game_start",
        game_id=game_id,
        task_id=self.request.id,
    )
    
    try:
        from ai_werewolf_core.utils.asyncio_utils import run_async
        result = run_async(_evaluate_game_impl(self, game_id))
        return result
    except Exception as e:
        logger.error(
            "celery_evaluate_game_failed",
            game_id=game_id,
            error=str(e),
            exc_info=True,
        )
        return {
            "game_id": game_id,
            "task": "evaluate_game",
            "status": "error",
            "error": str(e),
        }

def _evaluate_game_impl(self, game_id: str) -> dict:
    """evaluate_game_task 的异步实现。"""
    async def _run() -> dict:
        from ai_werewolf_core.db.session import async_session_factory
        from ai_werewolf_core.core.eval.pipeline import EvaluationPipeline
        from ai_werewolf_core.config import settings
        
        async with async_session_factory() as session:
            model_config = {
                "provider": "openai",
                "model_name": settings.eval_model_name,
                "api_key": settings.eval_model_key,
                "base_url": settings.eval_model_url
            }
                
            pipeline = EvaluationPipeline(session, model_config)
            report = await pipeline.run(game_id)
            
            return {
                "game_id": game_id,
                "task": "evaluate_game",
                "status": "success",
                "report_id": report.id
            }
            
    from ai_werewolf_core.utils.asyncio_utils import run_async
    return run_async(_run())
