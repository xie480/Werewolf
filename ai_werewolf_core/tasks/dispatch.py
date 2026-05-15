import asyncio
from structlog import get_logger
from ai_werewolf_core.schemas.models import Event
from ai_werewolf_core.schemas.enums import EventType, GamePhase, Role
from ai_werewolf_core.tasks.agent_tasks import run_agent_decision
from ai_werewolf_core.core.engine.player_manager import PlayerStatusManager

logger = get_logger(__name__)

async def on_phase_transition(event: Event):
    """监听阶段变更事件，为 AI 玩家派发决策任务。"""
    if event.event_type != EventType.PHASE_TRANSITION_EVENT:
        return

    game_id = event.game_id
    payload = event.payload
    new_phase_str = payload.get("new_phase")
    round_num = payload.get("round", 1)

    if not new_phase_str:
        return

    try:
        # 获取当前阶段
        new_phase = GamePhase(new_phase_str)
    except ValueError:
        return

    # 获取当前存活玩家
    player_mgr = PlayerStatusManager()
    alive_seats = await player_mgr.get_alive_players(game_id)
    players_info = await player_mgr.get_all_players(game_id)
    
    for player_id, info in players_info.items():
        seat = info.get("seat")
        role = info.get("role")
        is_alive = seat in alive_seats
        
        # 简单过滤：判断该玩家在当前阶段是否可能需要行动
        can_act = False
        if new_phase == GamePhase.NIGHT_WOLF_ACT and role == Role.WEREWOLF.value and is_alive:
            can_act = True
        elif new_phase == GamePhase.NIGHT_WITCH_ACT and role == Role.WITCH.value and is_alive:
            can_act = True
        elif new_phase == GamePhase.NIGHT_SEER_ACT and role == Role.SEER.value and is_alive:
            can_act = True
        elif new_phase in (GamePhase.DAY_DISCUSSION, GamePhase.DAY_VOTE, GamePhase.DAY_PK_DISCUSSION, GamePhase.DAY_PK_VOTE) and is_alive:
            can_act = True
        elif new_phase == GamePhase.HUNTER_SHOOT and role == Role.HUNTER.value and not is_alive:
            can_act = True
        elif new_phase == GamePhase.LAST_WORDS and not is_alive:
            can_act = True
            
        if not can_act:
            continue
            
        # 区分真实玩家和 AI
        is_ai = info.get("ai_profile_id") is not None
        
        if not is_ai:
            continue
            
        logger.info("dispatching_agent_task", game_id=game_id, player_id=player_id, phase=new_phase.value)
        run_agent_decision.apply_async(
            kwargs={
                "game_id": game_id,
                "player_id": player_id,
                "current_phase": new_phase.value,
                "current_round": round_num,
            }
        )

def register_dispatchers(event_bus):
    """注册事件分发器"""
    event_bus.subscribe(EventType.PHASE_TRANSITION_EVENT, on_phase_transition)
