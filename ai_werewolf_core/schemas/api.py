"""
API 层 Pydantic Schema —— 请求/响应模型定义。

**Why**: 与核心 `models.py` 分离，API 层的契约（请求校验、响应格式）
不应污染核心数据模型。此处定义的 Schema 仅用于 FastAPI 路由的输入输出序列化。

参考 [`docs/plan/Phase 3 FastAPI API.md`](../../docs/plan/Phase%203%20FastAPI%20API.md)。
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


# ============================================================================
# 对局管理 Schema
# ============================================================================


class CreateGameRequest(BaseModel):
    """创建对局请求。

    仅需指定玩家人数，Engine 自动分配角色和座位。
    """

    player_count: int = Field(
        default=9,
        ge=6,
        le=12,
        description="玩家人数，范围 6-12",
    )


class CreateGameResponse(BaseModel):
    """创建对局成功响应。"""

    game_id: str = Field(description="雪花算法生成的对局 ID")
    status: str = Field(description="对局状态，创建后为 START")


class GameDetailResponse(BaseModel):
    """对局详情响应。"""

    game_id: str
    status: str
    phase: Optional[str] = None
    round: int
    player_count: int


class GameStatusResponse(BaseModel):
    """对局状态简要响应（用于 start/advance/abort 操作后）。"""

    game_id: str
    status: str
    phase: Optional[str] = None
    round: int


class GameListResponse(BaseModel):
    """对局列表响应（预留，尚未实现持久化列表查询）。"""

    games: List[GameDetailResponse] = Field(default_factory=list)
    total: int = 0


# ============================================================================
# 玩家查询 Schema
# ============================================================================


class PlayerResponse(BaseModel):
    """单个玩家信息响应。

    **Why**: API 层暴露角色为字符串值，不暴露内部 `Role` 枚举对象，
    确保前后端解耦。
    """

    player_id: str
    seat_number: int
    role: str  # API 层暴露角色字符串，如 "WEREWOLF"、"SEER"
    is_alive: bool


class PlayerListResponse(BaseModel):
    """玩家列表响应。"""

    game_id: str
    players: List[PlayerResponse] = Field(default_factory=list)
    total: int = 0


# ============================================================================
# 事件查询 Schema
# ============================================================================


class EventResponse(BaseModel):
    """单个事件响应（简化版，仅暴露 API 关心的字段）。"""

    event_id: str
    seq_num: int
    event_type: str
    visibility: str
    target_agents: List[str] = Field(default_factory=list)
    timestamp: str  # ISO 8601 格式
    payload: dict = Field(default_factory=dict)


class EventListResponse(BaseModel):
    """事件列表响应。"""

    game_id: str
    events: List[EventResponse] = Field(default_factory=list)
    total: int = 0
    has_more: bool = False


# ============================================================================
# 通用错误响应 Schema
# ============================================================================


class ErrorResponse(BaseModel):
    """统一错误响应格式。

    **Why**: 所有 API 错误使用统一格式，便于前端统一处理。
    """

    error: str = Field(description="错误类型/简短描述")
    detail: Optional[str] = Field(default=None, description="详细错误信息")
    game_id: Optional[str] = Field(default=None, description="相关对局 ID")
