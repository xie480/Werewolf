"""
对局生命周期 Celery 任务 —— 异步结算与阶段推进。

**Why**: 遵循项目架构规范——FastAPI is ingress only，
所有重计算（夜间结算、投票结算、胜负判定、阶段推进）委托给 Celery Worker 执行。
Worker 通过 Redis 作为 Broker 接收任务，与 API 进程解耦。

参考 [`docs/plan/Celery 异步任务系统设计.md`](../../docs/plan/Celery%20异步任务系统设计.md)。
"""

from __future__ import annotations

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

    在 NIGHT_RESOLVE 阶段调用，执行以下步骤：
    1. 初始化 ActionResolver 并加载当前角色状态
    2. 调用 resolve_night_actions() 计算最终死亡名单
    3. 发布 PLAYER_DEATH 事件
    4. 返回结算结果摘要

    Args:
        game_id: 对局唯一标识。

    Returns:
        包含结算结果摘要的字典: ``{"final_deaths": [...], "total_deaths": N, "peaceful_night": bool}``
    """
    logger.info(
        "celery_resolve_night_start",
        game_id=game_id,
        task_id=self.request.id,
    )
    # Phase 4 Agent Runtime 完成后实现完整逻辑
    # 当前占位：返回空结算结果
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

    在 VOTE_RESOLVE 阶段调用，执行以下步骤：
    1. 初始化 VoteManager 并加载当前轮次投票数据
    2. 调用 resolve_vote() 统计票数
    3. 若非平票，执行放逐死亡结算
    4. 返回结算结果

    Args:
        game_id: 对局唯一标识。
        round_num: 投票轮次。

    Returns:
        包含投票结算结果的字典: ``{"is_tie": bool, "voted_out": str, "vote_count": dict}``
    """
    logger.info(
        "celery_resolve_vote_start",
        game_id=game_id,
        round_num=round_num,
        task_id=self.request.id,
    )
    # Phase 4 Agent Runtime 完成后实现完整逻辑
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
def advance_phase_task(self, game_id: str, next_phase: str) -> dict:
    """阶段推进任务 —— 推进游戏阶段。

    在每个阶段结束后调用，执行以下步骤：
    1. 初始化 LifecycleManager
    2. 调用 advance_phase() 推进到目标阶段
    3. 返回新阶段信息

    Args:
        game_id: 对局唯一标识。
        next_phase: 目标阶段（GamePhase 枚举值）。

    Returns:
        包含新阶段信息的字典: ``{"game_id": str, "phase": str, "round": int}``
    """
    logger.info(
        "celery_advance_phase_start",
        game_id=game_id,
        next_phase=next_phase,
        task_id=self.request.id,
    )
    # Phase 4 Agent Runtime 完成后实现完整逻辑
    return {
        "game_id": game_id,
        "task": "advance_phase",
        "status": "placeholder",
        "next_phase": next_phase,
        "round": 0,
    }


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
    # Phase 4 Agent Runtime 完成后实现完整逻辑
    return {
        "game_id": game_id,
        "task": "evaluate_winner",
        "status": "placeholder",
        "game_over": False,
        "winner": "",
    }
