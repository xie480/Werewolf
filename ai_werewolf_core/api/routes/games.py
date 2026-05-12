"""
对局管理路由 —— 创建、启动、推进、查询、中止对局。

**Why**: 这是 Phase 3 的核心路由模块，将对局生命周期的所有操作暴露为 RESTful API。
路由层仅负责参数校验和 Engine 调用委托，不包含任何游戏逻辑。

参考 [`docs/plan/Phase 3 FastAPI API.md`](../../docs/plan/Phase%203%20FastAPI%20API.md)。
"""

from __future__ import annotations

import random
from typing import Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, Query

from ai_werewolf_core.constant.redis_keys import RedisKeys
from ai_werewolf_core.core.engine.exceptions import (
    GameNotRunnableError,
    InvalidTransitionError,
)
from ai_werewolf_core.core.engine.lifecycle import LifecycleManager
from ai_werewolf_core.core.engine.player_manager import PlayerStatusManager
from ai_werewolf_core.core.event.bus import EventBus
from ai_werewolf_core.schemas.api import (
    CreateGameRequest,
    CreateGameResponse,
    GameDetailResponse,
    GameListResponse,
    GameStatusResponse,
)
from ai_werewolf_core.schemas.enums import GameStatus, Role
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.utils.redis_client import RedisClientManager
from ai_werewolf_core.utils.redis_seq import RedisUnavailableException
from ai_werewolf_core.utils.snowflake import get_snowflake

logger = get_logger(__name__)

router = APIRouter()

# ============================================================================
# 常量定义
# ============================================================================

# 标准 9 人局角色分配
STANDARD_9P_ROLES: list[Role] = [
    Role.WEREWOLF, Role.WEREWOLF, Role.WEREWOLF,  # 3 狼
    Role.VILLAGER, Role.VILLAGER, Role.VILLAGER,   # 3 村民
    Role.SEER,                                      # 1 预言家
    Role.WITCH,                                     # 1 女巫
    Role.HUNTER,                                    # 1 猎人
]

# ============================================================================
# 工具函数
# ============================================================================


def _assign_roles(player_count: int) -> list[Role]:
    """根据玩家人数分配角色并随机打乱。

    当前仅支持 9 人标准局，未来可扩展更多配置。

    Args:
        player_count: 玩家人数。

    Returns:
        打乱顺序后的角色列表。

    Raises:
        ValueError: 不支持的玩家人数。
    """
    if player_count == 9:
        roles = list(STANDARD_9P_ROLES)
    else:
        raise ValueError(f"暂不支持的玩家人数: {player_count}，当前仅支持 9 人局")
    random.shuffle(roles)
    return roles


def _build_players_dict(player_count: int) -> dict[str, dict]:
    """构建玩家字典——分配角色和座位。

    Args:
        player_count: 玩家人数。

    Returns:
        ``player_id → player_info`` 映射，player_info 包含 role、seat、faction。
    """
    from ai_werewolf_core.schemas.enums import Faction

    roles = _assign_roles(player_count)
    players: dict[str, dict] = {}

    for seat, role in enumerate(roles, start=1):
        player_id = f"player_{seat}"
        faction = Faction.WEREWOLF.value if role == Role.WEREWOLF else Faction.VILLAGER.value
        players[player_id] = {
            "role": role.value,
            "seat": seat,
            "faction": faction,
        }

    logger.info(
        "players_assigned",
        player_count=player_count,
        roles=[r.value for r in roles],
    )
    return players


# ============================================================================
# P0: 对局生命周期端点
# ============================================================================


@router.post("", response_model=CreateGameResponse, status_code=201)
async def create_game(request: CreateGameRequest = CreateGameRequest()) -> CreateGameResponse:
    """创建新对局。

    执行流程:
    1. 生成 Snowflake game_id
    2. 创建 LifecycleManager 并调用 init_game()
       - 内部自动 INSERT GameRecord 到 PostgreSQL
       - 内部自动写入 Redis Hash 上下文
    3. 分配角色并初始化玩家数据
       - 写入 Redis Hash + BitMap
       - 同步 INSERT PlayerRecord 到 PostgreSQL
    4. 返回 game_id 和 status

    Raises:
        503: Redis 不可用。
        500: 未知内部错误。
    """
    try:
        game_id = get_snowflake().next_id()
        event_bus = EventBus()
        manager = LifecycleManager(game_id, event_bus)
        await manager.init_game()

        # 分配角色并初始化玩家到 Redis + DB
        player_count = request.player_count
        players = _build_players_dict(player_count)
        player_mgr = PlayerStatusManager()
        await player_mgr.init_players(game_id, players)

        logger.info("game_created", game_id=game_id, player_count=player_count)
        return CreateGameResponse(game_id=game_id, status="START")

    except RedisUnavailableException as e:
        logger.error("create_game_redis_unavailable", error=str(e), exc_info=True)
        raise HTTPException(status_code=503, detail="Redis 服务不可用，请稍后重试")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("create_game_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"创建对局失败: {str(e)}")


@router.post("/{game_id}/start", response_model=GameStatusResponse)
async def start_game(game_id: str) -> GameStatusResponse:
    """启动对局: START → RUNNING，进入首轮 NIGHT_START。

    执行流程:
    1. 创建 LifecycleManager 并调用 start_game()
    2. 查询启动后的状态和阶段
    3. 返回状态快照

    Raises:
        409: 状态迁移非法（当前状态不是 START）。
        503: Redis 不可用。
        500: 未知内部错误。
    """
    try:
        event_bus = EventBus()
        manager = LifecycleManager(game_id, event_bus)
        await manager.start_game()

        status = await manager.get_status()
        phase = await manager.state_machine.get_current_phase()
        round_num = await manager.state_machine.get_round()

        logger.info("game_started", game_id=game_id)
        return GameStatusResponse(
            game_id=game_id,
            status=status.value,
            phase=phase.value if phase else None,
            round=round_num,
        )

    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RedisUnavailableException as e:
        logger.error("start_game_redis_unavailable", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=503, detail="Redis 服务不可用，请稍后重试")
    except Exception as e:
        logger.error("start_game_failed", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"启动对局失败: {str(e)}")


@router.get("/{game_id}", response_model=GameDetailResponse)
async def get_game(game_id: str) -> GameDetailResponse:
    """查询对局当前状态。

    返回对局的 status、phase、round 和 player_count。
    player_count 通过查询玩家列表长度获得。

    Raises:
        503: Redis 不可用。
        500: 未知内部错误。
    """
    try:
        event_bus = EventBus()
        manager = LifecycleManager(game_id, event_bus)

        status = await manager.get_status()
        phase = await manager.state_machine.get_current_phase()
        round_num = await manager.state_machine.get_round()

        # 通过玩家状态管理器获取玩家数量
        player_mgr = PlayerStatusManager()
        all_players = await player_mgr.get_all_players(game_id)
        player_count = len(all_players)

        return GameDetailResponse(
            game_id=game_id,
            status=status.value,
            phase=phase.value if phase else None,
            round=round_num,
            player_count=player_count,
        )

    except RedisUnavailableException as e:
        logger.error("get_game_redis_unavailable", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=503, detail="Redis 服务不可用，请稍后重试")
    except Exception as e:
        logger.error("get_game_failed", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询对局失败: {str(e)}")


# ============================================================================
# P1: 阶段推进
# ============================================================================


@router.post("/{game_id}/advance", response_model=GameStatusResponse)
async def advance_phase(
    game_id: str,
    next_phase: Optional[str] = Query(
        default=None,
        description="目标阶段。不传则自动推导下一阶段（推荐）。",
    ),
) -> GameStatusResponse:
    """推进游戏阶段。

    执行流程:
    1. 如果未指定 next_phase，自动读取当前阶段并推导下一阶段
    2. 调用 LifecycleManager.advance_phase() 执行迁移
    3. 返回最新状态快照

    自动推导规则基于 PhaseStateMachine.VALID_TRANSITIONS 有向图，
    选取当前阶段的后继列表中第一个合法阶段。

    Raises:
        409: 阶段迁移非法。
        422: 对局不在 RUNNING 状态。
        503: Redis 不可用。
        500: 未知内部错误。
    """
    from ai_werewolf_core.schemas.enums import GamePhase

    try:
        event_bus = EventBus()
        manager = LifecycleManager(game_id, event_bus)

        # 自动推导下一阶段
        if next_phase is None:
            current_phase = await manager.state_machine.get_current_phase()
            if current_phase is None:
                raise HTTPException(status_code=409, detail="对局尚未初始化，无法推进阶段")

            valid_next = manager.state_machine.VALID_TRANSITIONS.get(current_phase, [])
            if not valid_next:
                raise HTTPException(status_code=409, detail=f"当前阶段 {current_phase.value} 无合法后继阶段")

            target_phase = valid_next[0]
        else:
            try:
                target_phase = GamePhase(next_phase)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"无效的阶段名称: {next_phase}")

        await manager.advance_phase(target_phase)

        status = await manager.get_status()
        phase = await manager.state_machine.get_current_phase()
        round_num = await manager.state_machine.get_round()

        logger.info(
            "phase_advanced",
            game_id=game_id,
            target_phase=target_phase.value,
        )
        return GameStatusResponse(
            game_id=game_id,
            status=status.value,
            phase=phase.value if phase else None,
            round=round_num,
        )

    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except GameNotRunnableError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RedisUnavailableException as e:
        logger.error("advance_phase_redis_unavailable", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=503, detail="Redis 服务不可用，请稍后重试")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("advance_phase_failed", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"推进阶段失败: {str(e)}")


# ============================================================================
# P2: 对局中止
# ============================================================================


@router.post("/{game_id}/abort", response_model=GameStatusResponse)
async def abort_game(game_id: str, reason: str = Query(default="unknown", description="中止原因")) -> GameStatusResponse:
    """中止对局。

    将状态从 START/RUNNING/SETTLING 迁移到 ABORTED。
    设置 Redis Key TTL 为 1 小时，允许短暂保留用于复盘查询。

    Raises:
        409: 当前状态不可中止。
        503: Redis 不可用。
        500: 未知内部错误。
    """
    try:
        event_bus = EventBus()
        manager = LifecycleManager(game_id, event_bus)
        await manager.abort_game(reason)

        status = await manager.get_status()

        logger.info("game_aborted", game_id=game_id, reason=reason)
        return GameStatusResponse(
            game_id=game_id,
            status=status.value,
            phase=None,
            round=0,
        )

    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RedisUnavailableException as e:
        logger.error("abort_game_redis_unavailable", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=503, detail="Redis 服务不可用，请稍后重试")
    except Exception as e:
        logger.error("abort_game_failed", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"中止对局失败: {str(e)}")


# ============================================================================
# P3: 加入游戏 + 对局列表
# ============================================================================


@router.post("/{game_id}/join", response_model=GameStatusResponse)
async def join_game(game_id: str) -> GameStatusResponse:
    """加入已有对局。

    当前版本中，创建对局时已自动分配 9 个 player_N 并完成角色分配，
    join 端点主要用于前端联调和未来扩展（允许多波次加入）。

    执行流程:
    1. 校验对局存在且处于 START 状态
    2. 返回对局当前状态快照

    Raises:
        409: 对局不存在或状态不允许加入。
        503: Redis 不可用。
    """
    try:
        event_bus = EventBus()
        manager = LifecycleManager(game_id, event_bus)

        status = await manager.get_status()
        if status not in (GameStatus.START,):
            raise HTTPException(
                status_code=409,
                detail=f"对局 [{game_id}] 当前状态 [{status.value}] 不允许加入，"
                       f"仅 START 状态可加入",
            )

        phase = await manager.state_machine.get_current_phase()
        round_num = await manager.state_machine.get_round()

        logger.info("player_joined", game_id=game_id)
        return GameStatusResponse(
            game_id=game_id,
            status=status.value,
            phase=phase.value if phase else None,
            round=round_num,
        )

    except RedisUnavailableException as e:
        logger.error("join_game_redis_unavailable", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=503, detail="Redis 服务不可用，请稍后重试")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("join_game_failed", game_id=game_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"加入对局失败: {str(e)}")


@router.get("", response_model=GameListResponse)
async def list_games() -> GameListResponse:
    """获取活跃对局列表。

    通过扫描 Redis 中存在的对局上下文 Key，返回当前所有活跃对局的概要信息。
    已结束的对局（FINISHED/ABORTED）在 Redis TTL 过期后自动从列表中消失。

    注意: 此接口扫描 Redis KEYS，仅用于开发调试和前端列表展示，
    生产环境大规模部署时建议改用独立的对局索引表。

    Raises:
        503: Redis 不可用。
    """
    try:
        redis_client = await RedisClientManager.get_client()
        pattern = f"{RedisKeys.GAME_CONTEXT_PREFIX}:*:context"
        games: list[GameDetailResponse] = []

        cursor = 0
        while True:
            cursor, keys = await redis_client.scan(cursor, match=pattern, count=100)
            for key in keys:
                # 从 Key 中提取 game_id: werewolf:game:{game_id}:context
                key_parts = key.split(":")
                if len(key_parts) >= 3:
                    game_id = key_parts[2]
                    try:
                        event_bus = EventBus()
                        manager = LifecycleManager(game_id, event_bus)
                        status = await manager.get_status()
                        phase = await manager.state_machine.get_current_phase()
                        round_num = await manager.state_machine.get_round()

                        player_mgr = PlayerStatusManager()
                        all_players = await player_mgr.get_all_players(game_id)

                        games.append(GameDetailResponse(
                            game_id=game_id,
                            status=status.value,
                            phase=phase.value if phase else None,
                            round=round_num,
                            player_count=len(all_players),
                        ))
                    except Exception:
                        # 跳过无法查询的对局（可能 Key 在扫描过程中被删除）
                        continue

            if cursor == 0:
                break

        logger.info("games_listed", total=len(games))
        return GameListResponse(games=games, total=len(games))

    except RedisUnavailableException as e:
        logger.error("list_games_redis_unavailable", error=str(e), exc_info=True)
        raise HTTPException(status_code=503, detail="Redis 服务不可用，请稍后重试")
    except Exception as e:
        logger.error("list_games_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询对局列表失败: {str(e)}")
