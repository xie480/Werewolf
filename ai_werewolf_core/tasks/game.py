"""
对局生命周期 Celery 任务 —— 异步结算与阶段推进。

**Why**: 遵循项目架构规范——FastAPI is ingress only，
所有重计算（夜间结算、投票结算、胜负判定、阶段推进）委托给 Celery Worker 执行。
Worker 通过 Redis 作为 Broker 接收任务，与 API 进程解耦。

参考 [`docs/plan/Celery 异步任务系统设计.md`](../../docs/plan/Celery%20异步任务系统设计.md)。
"""

from __future__ import annotations

import asyncio

from ai_werewolf_core.worker import celery_app
from ai_werewolf_core.utils.logger import get_logger

logger = get_logger(__name__)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    name="tasks.game.resolve_night",
)
def resolve_night_task(self, game_id: str) -> dict:
    """夜间结算任务 —— 结算夜晚所有动作，计算最终死亡名单。

    Args:
        game_id: 对局唯一标识。

    Returns:
        包含结算结果摘要的字典。
    """
    logger.info(
        "celery_resolve_night_start",
        game_id=game_id,
        task_id=self.request.id,
    )
    # Phase 4 完成后实现完整逻辑
    return {
        "game_id": game_id,
        "task": "resolve_night",
        "status": "placeholder",
        "final_deaths": [],
        "total_deaths": 0,
        "peaceful_night": True,
    }


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    name="tasks.game.resolve_vote",
)
def resolve_vote_task(self, game_id: str, round_num: int) -> dict:
    """投票结算任务 —— 统计票数并执行放逐。

    Args:
        game_id: 对局唯一标识。
        round_num: 投票轮次。

    Returns:
        包含投票结算结果的字典。
    """
    logger.info(
        "celery_resolve_vote_start",
        game_id=game_id,
        round_num=round_num,
        task_id=self.request.id,
    )
    return {
        "game_id": game_id,
        "task": "resolve_vote",
        "status": "placeholder",
        "round": round_num,
        "is_tie": False,
        "voted_out": "",
        "vote_count": {},
    }


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    name="tasks.game.advance_phase",
)
def advance_phase_task(self, game_id: str, expected_phase: str = "") -> dict:
    """阶段推进任务 —— 推进游戏阶段。

    在每个阶段倒计时到期后调用。执行并发防重校验：
    1. 读取 Redis 中的当前阶段
    2. 如果当前阶段与 expected_phase 不一致，说明已被提前结束机制推进，跳过
    3. 否则实例化 GameEngine 并调用 advance_phase()

    Args:
        game_id: 对局唯一标识。
        expected_phase: 预期的当前阶段值，用于并发防重校验。

    Returns:
        包含新阶段信息的字典: ``{"game_id": str, "phase": str, "round": int}``
    """
    logger.info(
        "celery_advance_phase_start",
        game_id=game_id,
        task_id=self.request.id,
        expected_phase=expected_phase,
    )

    try:
        # 使用 asyncio.run 包裹异步逻辑
        result = asyncio.run(_advance_phase_impl(
            self, game_id, expected_phase
        ))
        return result
    except Exception as e:
        logger.error(
            "celery_advance_phase_failed",
            game_id=game_id,
            task_id=self.request.id,
            error=str(e),
            exc_info=True,
        )
        return {
            "game_id": game_id,
            "task": "advance_phase",
            "status": "error",
            "error": str(e),
        }


def _advance_phase_impl(self, game_id: str, expected_phase: str) -> dict:
    """advance_phase_task 的异步实现。"""
    import asyncio

    async def _run() -> dict:
        # ── Step 1: 并发防重校验 ──
        from ai_werewolf_core.core.engine.game_engine import GameEngine
        from ai_werewolf_core.core.event.bus import EventBus
        from ai_werewolf_core.schemas.enums import GamePhase

        event_bus = EventBus()

        # 检查当前阶段是否与预期一致
        from ai_werewolf_core.core.engine.state_machine import PhaseStateMachine
        sm = PhaseStateMachine(game_id, event_bus)
        current_phase = await sm.get_current_phase()

        if current_phase is None:
            logger.warning(
                "advance_phase_skip_no_phase",
                game_id=game_id,
                task_id=self.request.id,
            )
            return {
                "game_id": game_id,
                "task": "advance_phase",
                "status": "skipped",
                "reason": "对局尚未初始化或无当前阶段",
            }

        if expected_phase and current_phase.value != expected_phase:
            logger.info(
                "advance_phase_skip_concurrency",
                game_id=game_id,
                task_id=self.request.id,
                expected=expected_phase,
                actual=current_phase.value,
                reason="阶段已被提前结束机制推进",
            )
            return {
                "game_id": game_id,
                "task": "advance_phase",
                "status": "skipped",
                "reason": f"当前阶段 {current_phase.value} 与预期 {expected_phase} 不一致",
            }

        # 再次校验：检查 task_id 是否仍有效
        from ai_werewolf_core.core.engine.lifecycle import LifecycleManager
        lcm = LifecycleManager(game_id, event_bus)
        saved_task_id = await lcm.get_task_id()
        if saved_task_id and saved_task_id != self.request.id:
            logger.info(
                "advance_phase_skip_task_mismatch",
                game_id=game_id,
                task_id=self.request.id,
                saved_task_id=saved_task_id,
                reason="任务 ID 不匹配，定时器已被替换",
            )
            return {
                "game_id": game_id,
                "task": "advance_phase",
                "status": "skipped",
                "reason": "任务 ID 不匹配，已有新定时器",
            }

        # ── Step 2: 构建 GameEngine 并推进 ──
        logger.info(
            "advance_phase_executing",
            game_id=game_id,
            task_id=self.request.id,
            current_phase=current_phase.value,
        )

        roles = await GameEngine.load_roles_from_persistence(game_id)
        engine = GameEngine(game_id, event_bus, roles)

        # 加载对局状态（从进程优化，实际已通过 get_current_phase 加载）
        advance_result = await engine.advance_phase()

        logger.info(
            "celery_advance_phase_complete",
            game_id=game_id,
            task_id=self.request.id,
            old_phase=advance_result.old_phase.value,
            new_phase=advance_result.new_phase.value,
            round=advance_result.round,
        )

        return {
            "game_id": game_id,
            "task": "advance_phase",
            "status": "success",
            "old_phase": advance_result.old_phase.value,
            "new_phase": advance_result.new_phase.value,
            "round": advance_result.round,
            "game_over": advance_result.game_over,
            "winner": advance_result.winner,
        }

    return asyncio.new_event_loop().run_until_complete(_run())


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    name="tasks.game.evaluate_winner",
)
def evaluate_winner_task(self, game_id: str) -> dict:
    """胜负判定任务 —— 检查对局是否满足结束条件。

    在关键阶段（NIGHT_RESOLVE、VOTE_RESOLVE、HUNTER_SHOOT）后调用。
    委托 Evaluator（Phase 2 已有）检查胜负条件。

    Args:
        game_id: 对局唯一标识。

    Returns:
        包含胜负判定结果的字典: ``{"game_over": bool, "winner": str}``
    """
    logger.info(
        "celery_evaluate_winner_start",
        game_id=game_id,
        task_id=self.request.id,
    )

    try:
        result = asyncio.run(_evaluate_winner_impl(self, game_id))
        return result
    except Exception as e:
        logger.error(
            "celery_evaluate_winner_failed",
            game_id=game_id,
            error=str(e),
            exc_info=True,
        )
        return {
            "game_id": game_id,
            "task": "evaluate_winner",
            "status": "error",
            "game_over": False,
            "winner": "",
        }


def _evaluate_winner_impl(self, game_id: str) -> dict:
    """evaluate_winner_task 的异步实现。"""
    async def _run() -> dict:
        from ai_werewolf_core.core.engine.game_engine import GameEngine
        from ai_werewolf_core.core.engine.evaluator import WinEvaluator
        from ai_werewolf_core.core.event.bus import EventBus

        event_bus = EventBus()
        roles = await GameEngine.load_roles_from_persistence(game_id)
        eval_result = WinEvaluator.evaluate_detailed(roles)

        logger.info(
            "celery_evaluate_winner_complete",
            game_id=game_id,
            task_id=self.request.id,
            game_over=eval_result.is_game_over,
            winner=eval_result.winner.value if eval_result.winner else None,
        )

        return {
            "game_id": game_id,
            "task": "evaluate_winner",
            "status": "success",
            "game_over": eval_result.is_game_over,
            "winner": eval_result.winner.value if eval_result.winner else None,
        }

    return asyncio.new_event_loop().run_until_complete(_run())
