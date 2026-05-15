"""
Replay 回放系统路由 —— 提供按天数/阶段打包的事件流。

**Why**: 回放系统需要将扁平的事件流组装成结构化的时间轴，
并支持上帝视角（GOD）和第一人称视角（POV）的权限过滤。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ai_werewolf_core.core.event.bus import event_bus
from ai_werewolf_core.core.engine.player_manager import PlayerStatusManager
from ai_werewolf_core.schemas.api import (
    ReplayResponse,
    ReplayInitialState,
    ReplayPlayerInfo,
    ReplayDayChunk,
    ReplayPhaseChunk,
    EventResponse,
)
from ai_werewolf_core.schemas.enums import EventType
from ai_werewolf_core.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get("/{game_id}/replay", response_model=ReplayResponse)
async def get_game_replay(
    game_id: str,
    perspective: str = Query(..., description="视角模式: GOD 或 POV"),
    agent_id: Optional[str] = Query(None, description="POV 模式下必填，指定第一人称视角的玩家 ID"),
) -> ReplayResponse:
    """获取对局回放数据。

    支持上帝视角（GOD）和第一人称视角（POV）。
    返回按天数和阶段打包（Chunking）的结构化事件流。

    Args:
        game_id: 对局 ID。
        perspective: 视角模式，GOD 或 POV。
        agent_id: POV 模式下的玩家 ID。

    Returns:
        ReplayResponse: 包含初始状态和时间轴的回放数据。

    Raises:
        HTTPException: 参数校验失败或内部错误。
    """
    perspective = perspective.upper()
    if perspective not in ("GOD", "POV"):
        raise HTTPException(status_code=400, detail="perspective 必须为 GOD 或 POV")

    if perspective == "POV" and not agent_id:
        raise HTTPException(status_code=400, detail="POV 模式下必须提供 agent_id")

    is_god_mode = perspective == "GOD"
    query_agent_id = agent_id if agent_id else ""

    try:
        # 1. 获取初始状态 (玩家列表)
        player_mgr = PlayerStatusManager()
        all_players = await player_mgr.get_all_players(game_id)
        
        if not all_players:
            raise HTTPException(status_code=404, detail=f"对局 {game_id} 不存在或无玩家数据")

        replay_players = []
        for p in all_players:
            # POV 模式下，掩码非己方玩家的角色
            role = p.role.value
            if perspective == "POV" and p.player_id != agent_id:
                role = "UNKNOWN"
            
            replay_players.append(
                ReplayPlayerInfo(
                    agent_id=p.player_id,
                    seat_number=p.seat_number,
                    role=role,
                )
            )
        
        initial_state = ReplayInitialState(players=replay_players)

        # 2. 获取事件流（使用全局单例）
        # 拉取全量事件，count 设为一个足够大的值（如 10000）
        events = await event_bus.get_events(
            game_id=game_id,
            agent_id=query_agent_id,
            start_seq=0,
            count=10000,
            is_god_mode=is_god_mode,
        )

        # 3. Chunking 组装逻辑
        timeline: list[ReplayDayChunk] = []
        
        current_day_num = 0
        current_phase_name = "INIT"
        
        current_day_chunk: Optional[ReplayDayChunk] = None
        current_phase_chunk: Optional[ReplayPhaseChunk] = None

        for event in events:
            # 处理内部 OS 透视 (inner_thought)
            payload = dict(event.payload)
            if perspective == "POV":
                # 如果不是当前视角的玩家发出的动作，剔除 inner_thought
                actor = payload.get("actor_id") or payload.get("actor")
                if actor != agent_id and "inner_thought" in payload:
                    del payload["inner_thought"]

            event_resp = EventResponse(
                event_id=event.event_id,
                seq_num=event.seq_num,
                event_type=event.event_type.value if hasattr(event.event_type, 'value') else str(event.event_type),
                visibility=event.visibility.value if hasattr(event.visibility, 'value') else str(event.visibility),
                target_agents=list(event.target_agents),
                timestamp=event.timestamp.isoformat(),
                payload=payload,
            )

            if event.event_type == EventType.PHASE_TRANSITION_EVENT:
                new_day = payload.get("round", current_day_num)
                new_phase = payload.get("new_phase", current_phase_name)
                
                # 如果天数发生变化，或者还没有 DayChunk，创建新的 DayChunk
                if new_day != current_day_num or current_day_chunk is None:
                    current_day_num = new_day
                    current_day_chunk = ReplayDayChunk(day_num=current_day_num, phases=[])
                    timeline.append(current_day_chunk)
                
                # 阶段发生变化，创建新的 PhaseChunk
                current_phase_name = new_phase
                current_phase_chunk = ReplayPhaseChunk(phase_name=current_phase_name, events=[])
                current_day_chunk.phases.append(current_phase_chunk)
                
                # 将阶段切换事件本身也放入该阶段
                current_phase_chunk.events.append(event_resp)
            else:
                # 非阶段切换事件
                if current_day_chunk is None:
                    # 处理 INIT 阶段没有 PHASE_TRANSITION_EVENT 的情况
                    current_day_chunk = ReplayDayChunk(day_num=current_day_num, phases=[])
                    timeline.append(current_day_chunk)
                
                if current_phase_chunk is None:
                    current_phase_chunk = ReplayPhaseChunk(phase_name=current_phase_name, events=[])
                    current_day_chunk.phases.append(current_phase_chunk)
                
                current_phase_chunk.events.append(event_resp)

        logger.info(
            "replay_generated",
            game_id=game_id,
            perspective=perspective,
            agent_id=agent_id,
            total_events=len(events),
            days=len(timeline),
        )

        return ReplayResponse(
            game_id=game_id,
            perspective=perspective,
            agent_id=agent_id,
            initial_state=initial_state,
            timeline=timeline,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_game_replay_failed", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取回放数据失败: {str(e)}")
