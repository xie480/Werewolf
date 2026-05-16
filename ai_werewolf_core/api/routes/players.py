"""
玩家查询路由 —— 查询对局中的玩家信息。

**Why**: 将玩家状态查询暴露为 RESTful API，供前端展示玩家列表和单个玩家详情。
路由层仅负责从 PlayerStatusManager 读取数据并转换为 API 响应格式。

参考 [`docs/plan/Phase 3 FastAPI API.md`](../../docs/plan/Phase%203%20FastAPI%20API.md)。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ai_werewolf_core.core.engine.player_manager import PlayerStatusManager
from ai_werewolf_core.schemas.api import PlayerListResponse, PlayerResponse
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.utils.redis_seq import RedisUnavailableException

logger = get_logger(__name__)

router = APIRouter()


@router.get("/{game_id}/players", response_model=PlayerListResponse)
async def list_players(game_id: str) -> PlayerListResponse:
    """查询指定对局的所有玩家信息。

    从 Redis Hash 读取玩家身份和状态数据。
    Redis 不可用时返回空列表。

    Raises:
        503: Redis 不可用（但返回空列表而非错误）。
    """
    try:
        player_mgr = PlayerStatusManager()
        all_players = await player_mgr.get_all_players(game_id)

        player_list = [
            PlayerResponse(
                player_id=pid,
                seat_number=info.get("seat", 0),
                role=info.get("role", "UNKNOWN"),
                is_alive=True,  # 从 get_all_players 无法获取存活状态，默认 True
                name=info.get("name", pid),  # 优先使用存储的名称，无则回退 player_id
            )
            for pid, info in all_players.items()
        ]
        # 按座位号排序
        player_list.sort(key=lambda p: p.seat_number)

        return PlayerListResponse(
            game_id=game_id,
            players=player_list,
            total=len(player_list),
        )

    except RedisUnavailableException as e:
        logger.warning(
            "list_players_redis_unavailable",
            game_id=game_id,
            error=str(e),
        )
        # 降级：返回空列表，不抛出 HTTP 错误
        return PlayerListResponse(game_id=game_id, players=[], total=0)
    except Exception as e:
        logger.error("list_players_failed", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询玩家列表失败: {str(e)}")


@router.get("/{game_id}/players/{player_id}", response_model=PlayerResponse)
async def get_player(game_id: str, player_id: str) -> PlayerResponse:
    """查询单个玩家信息。

    Raises:
        404: 玩家不存在。
        500: 内部错误。
    """
    try:
        player_mgr = PlayerStatusManager()
        info = await player_mgr.get_player_info(game_id, player_id)

        if info is None:
            raise HTTPException(
                status_code=404,
                detail=f"玩家 [{player_id}] 在对局 [{game_id}] 中不存在",
            )

        # 同时查询存活状态
        seat = info.get("seat", 0)
        is_alive = await player_mgr.is_alive(game_id, seat)

        return PlayerResponse(
            player_id=player_id,
            seat_number=seat,
            role=info.get("role", "UNKNOWN"),
            is_alive=is_alive,
            name=info.get("name", player_id),  # 优先使用存储的名称，无则回退 player_id
        )

    except HTTPException:
        raise
    except RedisUnavailableException as e:
        logger.warning(
            "get_player_redis_unavailable",
            game_id=game_id,
            player_id=player_id,
            error=str(e),
        )
        raise HTTPException(status_code=503, detail="Redis 服务不可用，请稍后重试")
    except Exception as e:
        logger.error(
            "get_player_failed",
            game_id=game_id,
            player_id=player_id,
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"查询玩家失败: {str(e)}")
