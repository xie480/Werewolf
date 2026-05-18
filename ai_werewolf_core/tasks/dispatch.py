import asyncio
import time
from structlog import get_logger
from ai_werewolf_core.schemas.models import Event
from ai_werewolf_core.schemas.enums import EventType, GamePhase, Role
from ai_werewolf_core.tasks.agent_tasks import run_agent_decision
from ai_werewolf_core.core.engine.player_manager import PlayerStatusManager

logger = get_logger(__name__)

# ── 防重入守卫 ──
# 记录最近处理的阶段变更事件 (game_id, phase_str) → timestamp
# 用于防止同一阶段事件在短时间内被重复处理，避免多次分发 Agent 任务
_seen_phase_transitions: dict[tuple[str, str], float] = {}
_DEDUP_INTERVAL_SEC: float = 30.0  # 1 秒内相同的 (game_id, phase) 事件只处理一次


def _is_duplicate_phase_transition(game_id: str, new_phase_str: str) -> bool:
    """检查是否为重复的阶段变更事件。

    基于 (game_id, new_phase) 组合去重：
    - 如果在 _DEDUP_INTERVAL_SEC 秒内已经处理过同一个 game_id 的同一个阶段，
      则认为是重复事件，跳过处理。

    Returns:
        True 表示重复事件，应跳过；False 表示首次出现或已超间隔，应处理。
    """
    now = time.monotonic()
    key = (game_id, new_phase_str)

    last_time = _seen_phase_transitions.get(key)
    if last_time is not None and (now - last_time) < _DEDUP_INTERVAL_SEC:
        # 重复事件：在去重时间窗口内
        return True

    # 首次出现或已超时间窗口：更新记录并返回 False（非重复）
    _seen_phase_transitions[key] = now

    # 清理过期记录，防止内存泄漏（仅当记录数超过阈值时触发）
    if len(_seen_phase_transitions) > 1000:
        _prune_stale_entries(now)

    return False


def _prune_stale_entries(now: float) -> None:
    """清理过期的防重入记录。

    移除所有超过 _DEDUP_INTERVAL_SEC * 2 秒的记录。
    """
    stale_threshold = now - _DEDUP_INTERVAL_SEC * 2
    stale_keys = [
        k for k, v in _seen_phase_transitions.items()
        if v < stale_threshold
    ]
    for k in stale_keys:
        del _seen_phase_transitions[k]


async def on_phase_transition(event: Event):
    """监听阶段变更事件，为 AI 玩家派发决策任务。

    记录审计时间戳：
    - NIGHT_WOLF_ACT 进入时记录 T1（phase_entered）到 Redis
    - 所有狼人分发完成后记录 T2（wolf_dispatched）
    """
    if event.event_type != EventType.PHASE_TRANSITION_EVENT:
        return

    game_id = event.game_id
    payload = event.payload
    new_phase_str = payload.get("new_phase")
    round_num = payload.get("round", 1)

    import os
    logger.info("on_phase_transition_called", game_id=game_id, new_phase=new_phase_str, pid=os.getpid())

    if not new_phase_str:
        return

    # ── 防重入检测：同一个 game_id + phase 在短时间内只处理一次 ──
    if _is_duplicate_phase_transition(game_id, new_phase_str):
        logger.info(
            "duplicate_phase_transition_skipped",
            game_id=game_id,
            new_phase=new_phase_str,
            reason="去重窗口内已处理过相同的阶段变更事件",
        )
        return

    try:
        new_phase = GamePhase(new_phase_str)
    except ValueError:
        return

    # 获取当前存活玩家
    player_mgr = PlayerStatusManager()
    alive_seats = await player_mgr.get_alive_players(game_id)
    players_info = await player_mgr.get_all_players(game_id)

    # ── 记录阶段进入时间戳 T1（仅 NIGHT_WOLF_ACT 需要审计） ──
    if new_phase == GamePhase.NIGHT_WOLF_ACT:
        try:
            from ai_werewolf_core.utils.redis_client import RedisClientManager
            from ai_werewolf_core.constant.redis_keys import RedisKeys
            redis = await RedisClientManager.get_client()
            vote_key = RedisKeys.wolf_vote_hash(game_id, round_num)
            from ai_werewolf_core.utils.time_utils import now_tz
            await redis.hset(vote_key, "audit:phase_entered_at", now_tz().isoformat())
            logger.info(
                "wolf_audit_timestamp_recorded",
                game_id=game_id,
                round=round_num,
                label="phase_entered_at",
            )
        except Exception as e:
            logger.warning(
                "wolf_audit_timestamp_failed",
                game_id=game_id,
                label="phase_entered_at",
                error=str(e),
            )

    # ── 构建需要派发任务的玩家列表 ──
    dispatch_targets = []
    for player_id, info in players_info.items():
        seat = info.get("seat")
        role = info.get("role")
        is_alive = seat in alive_seats

        # 判断该玩家在当前阶段是否可能需要行动
        can_act = False
        if new_phase == GamePhase.NIGHT_WOLF_ACT and role == Role.WEREWOLF.value and is_alive:
            can_act = True
        elif new_phase == GamePhase.NIGHT_WITCH_ACT and role == Role.WITCH.value and is_alive:
            can_act = True
        elif new_phase == GamePhase.NIGHT_SEER_ACT and role == Role.SEER.value and is_alive:
            can_act = True
        elif new_phase in (GamePhase.DAY_VOTE, GamePhase.DAY_PK_VOTE) and is_alive:
            can_act = True
        elif new_phase == GamePhase.HUNTER_SHOOT and role == Role.HUNTER.value and not is_alive:
            can_act = True
        elif new_phase == GamePhase.LAST_WORDS and not is_alive:
            can_act = True

        if not can_act:
            continue

        # 区分真实玩家和 AI (目前全为 AI)
        is_human = info.get("is_human", False)
        if is_human:
            continue

        dispatch_targets.append(player_id)

    if not dispatch_targets:
        logger.info("no_agents_to_dispatch", game_id=game_id, phase=new_phase.value)
        return

    # ── 并行分发 Agent 任务（尤其对 NIGHT_WOLF_ACT 阶段的多狼人并行） ──
    import asyncio
    loop = asyncio.get_running_loop()
    dispatch_count = len(dispatch_targets)

    logger.info(
        "dispatching_agent_tasks_parallel",
        game_id=game_id,
        phase=new_phase.value,
        dispatch_count=dispatch_count,
        targets=dispatch_targets,
        pid=os.getpid(),
    )

    for player_id in dispatch_targets:
        await loop.run_in_executor(
            None,
            lambda pid=player_id: run_agent_decision.apply_async(
                kwargs={
                    "game_id": game_id,
                    "player_id": pid,
                    "current_phase": new_phase.value,
                    "current_round": round_num,
                }
            )
        )

    # ── 记录狼人分发完成时间戳 T2 ──
    if new_phase == GamePhase.NIGHT_WOLF_ACT:
        try:
            from ai_werewolf_core.utils.redis_client import RedisClientManager
            from ai_werewolf_core.constant.redis_keys import RedisKeys
            redis = await RedisClientManager.get_client()
            vote_key = RedisKeys.wolf_vote_hash(game_id, round_num)
            from ai_werewolf_core.utils.time_utils import now_tz
            await redis.hset(vote_key, "audit:wolf_dispatched_at", now_tz().isoformat())
            logger.info(
                "wolf_audit_timestamp_recorded",
                game_id=game_id,
                round=round_num,
                label="wolf_dispatched_at",
            )
        except Exception as e:
            logger.warning(
                "wolf_audit_timestamp_failed",
                game_id=game_id,
                label="wolf_dispatched_at",
                error=str(e),
            )

    logger.info(
        "agent_dispatch_complete",
        game_id=game_id,
        phase=new_phase.value,
        dispatched_count=dispatch_count,
    )


async def on_speech_turn(event: Event):
    """监听发言轮次事件，仅为当前轮到发言的玩家派发决策任务。

    **Why**: 发言阶段采用顺序发言机制，每次 SPEECH_TURN_EVENT 触发时，
    只唤醒当前应该发言的玩家，其余玩家继续等待。
    """
    if event.event_type != EventType.SPEECH_TURN_EVENT:
        return

    game_id = event.game_id
    payload = event.payload
    player_id = payload.get("player_id")
    phase = payload.get("phase")
    round_num = payload.get("round", 1)

    if not player_id:
        return

    import os
    logger.info(
        "on_speech_turn_called",
        game_id=game_id,
        player_id=player_id,
        pid=os.getpid(),
    )

    # 获取玩家信息，检查是否为 AI 玩家
    player_mgr = PlayerStatusManager()
    players_info = await player_mgr.get_all_players(game_id)
    player_info = players_info.get(player_id)
    if not player_info:
        logger.warning("speech_turn_player_not_found", player_id=player_id)
        return

    is_human = player_info.get("is_human", False)
    if is_human:
        # 人类玩家不需要派发 AI 任务
        logger.info("speech_turn_human_skip", player_id=player_id)
        return

    # 为当前玩家派发发言任务
    loop = asyncio.get_running_loop()
    logger.info(
        "dispatching_speech_task",
        game_id=game_id,
        player_id=player_id,
        phase=phase,
        round=round_num,
    )
    await loop.run_in_executor(
        None,
        lambda pid=player_id: run_agent_decision.apply_async(
            kwargs={
                "game_id": game_id,
                "player_id": pid,
                "current_phase": phase,
                "current_round": round_num,
            }
        ),
    )


def register_dispatchers(event_bus):
    """注册事件分发器"""
    event_bus.subscribe(EventType.PHASE_TRANSITION_EVENT, on_phase_transition, local_only=True)
    event_bus.subscribe(EventType.SPEECH_TURN_EVENT, on_speech_turn, local_only=True)
