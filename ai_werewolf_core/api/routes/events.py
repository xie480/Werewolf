"""
事件查询路由 —— 查询对局事件流。

**Why**: 将事件溯源数据暴露为 RESTful API，支持按 seq_num 分页拉取。
前端和 Agent 可通过此接口获取对局的完整历史记录。

参考 [`docs/plan/Phase 3 FastAPI API.md`](../../docs/plan/Phase%203%20FastAPI%20API.md)。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ai_werewolf_core.core.event.bus import EventBus, event_bus
from ai_werewolf_core.schemas.api import EventListResponse, EventResponse
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.utils.redis_seq import RedisUnavailableException

logger = get_logger(__name__)

router = APIRouter()

# 默认每页事件数量
DEFAULT_EVENT_LIMIT: int = 100


@router.get("/{game_id}/events", response_model=EventListResponse)
async def list_events(
    game_id: str,
    since_seq: int = Query(default=0, ge=0, description="起始 seq_num（0 表示从头开始）"),
    limit: int = Query(default=DEFAULT_EVENT_LIMIT, ge=1, le=500, description="最大返回数量"),
) -> EventListResponse:
    """查询对局事件流（支持分页）。

    使用 ``since_seq`` 参数实现增量拉取：
    - 首次请求: ``since_seq=0``，返回最早的事件
    - 后续请求: ``since_seq=<上次最后一条事件的 seq_num + 1>``

    事件按 seq_num 升序排列，保证时序正确。

    Args:
        game_id: 对局 ID。
        since_seq: 起始序列号，0 表示从头开始。
        limit: 最大返回数量，默认 100，上限 500。

    Returns:
        事件列表 + 是否还有更多数据。

    Raises:
        503: Redis 不可用（返回空列表降级）。
        500: 内部错误。
    """
    try:
        # 使用全局单例 event_bus
        events = await event_bus.get_events(
            game_id=game_id,
            agent_id="",  # 空字符串 → 仅返回 PUBLIC 事件
            start_seq=since_seq,
            count=limit,
        )

        event_list = [
            EventResponse(
                event_id=e.event_id,
                seq_num=e.seq_num,
                event_type=e.event_type.value if hasattr(e.event_type, 'value') else str(e.event_type),
                visibility=e.visibility.value if hasattr(e.visibility, 'value') else str(e.visibility),
                target_agents=list(e.target_agents),
                timestamp=e.timestamp.isoformat(),
                payload=dict(e.payload),
            )
            for e in events
        ]

        has_more = len(events) >= limit

        logger.debug(
            "events_queried",
            game_id=game_id,
            since_seq=since_seq,
            count=len(events),
            has_more=has_more,
        )

        return EventListResponse(
            game_id=game_id,
            events=event_list,
            total=len(event_list),
            has_more=has_more,
        )

    except RedisUnavailableException as e:
        logger.warning(
            "list_events_redis_unavailable",
            game_id=game_id,
            error=str(e),
        )
        # 降级：返回空列表
        return EventListResponse(game_id=game_id, events=[], total=0, has_more=False)
    except Exception as e:
        logger.error(
            "list_events_failed",
            game_id=game_id,
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"查询事件流失败: {str(e)}")
