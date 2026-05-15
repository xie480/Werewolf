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


class PlayerSetupConfig(BaseModel):
    type: str = Field(..., description="'existing' 或 'dynamic'")
    player_id: Optional[str] = Field(default=None, description="现有 AI 玩家的 ID")
    config: Optional[dict] = Field(default=None, description="动态创建时的配置 (如 model_name, temperature)")

class CreateGameRequest(BaseModel):
    """创建对局请求。

    支持指定玩家人数、角色配置以及 AI 玩家配置。
    """

    player_count: int = Field(
        default=9,
        ge=6,
        le=12,
        description="玩家人数，范围 6-12",
    )
    role_setup: Optional[List[str]] = Field(default=None, description="角色配置列表")
    players: Optional[List[PlayerSetupConfig]] = Field(default=None, description="玩家配置列表")


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


# ============================================================================
# 投票/发言/技能操作 Schema
# ============================================================================


class SubmitVoteRequest(BaseModel):
    """提交投票请求。

    **Why**: 投票是白天阶段的核心操作。target_id 为 None 表示弃权。
    """

    actor_id: str = Field(description="投票人 ID")
    target_id: Optional[str] = Field(default=None, description="被投人 ID，None 表示弃权")


class VoteStatusResponse(BaseModel):
    """投票状态响应 —— 当前选票快照。"""

    game_id: str
    votes: dict = Field(default_factory=dict, description="voter_id → target_id 映射")
    voter_count: int = Field(default=0, description="已投票人数")
    is_pk_vote: bool = Field(default=False, description="是否为 PK 投票")


class SubmitSpeechRequest(BaseModel):
    """提交发言请求。

    **Why**: 发言是白天讨论阶段的核心操作。emotion 可选，由 Agent 自行选择情绪。
    """

    actor_id: str = Field(description="发言人 ID")
    content: str = Field(min_length=1, max_length=2000, description="发言内容")
    emotion: Optional[str] = Field(default=None, description="情绪标签，如 CONFIDENT、ANXIOUS 等")


class SubmitActionRequest(BaseModel):
    """提交夜间技能请求。

    **Why**: 夜间技能（狼刀/女巫救毒/预言家查验/猎人开枪）通过统一接口提交，
    action_type 使用 ActionType 枚举值校验。
    """

    actor_id: str = Field(description="行动者 ID")
    action_type: str = Field(description="动作类型，如 WOLF_KILL、WITCH_SAVE、SEER_CHECK")
    target_id: Optional[str] = Field(default=None, description="目标玩家 ID，PASS 时为 None")


class ActionResponse(BaseModel):
    """技能操作响应。"""

    success: bool = Field(default=True, description="操作是否成功")
    action_type: str = Field(description="执行的动作类型")
    actor_id: str = Field(description="行动者 ID")
    target_id: Optional[str] = Field(default=None, description="目标玩家 ID")


# ============================================================================
# 评测复盘 Schema
# ============================================================================

class AgentEvaluationResponse(BaseModel):
    """单个玩家的评测结果响应"""
    player_id: str
    role: str
    rule_compliance_score: int
    logical_consistency_score: int
    roleplay_score: int
    deception_score: Optional[int] = None
    god_deduction_score: Optional[int] = None
    situational_awareness_score: Optional[int] = None
    leadership_score: Optional[int] = None
    strengths: Optional[str] = None
    weaknesses: Optional[str] = None
    overall_review: Optional[str] = None

class MatchReportResponse(BaseModel):
    """对局复盘报告响应"""
    report_id: str
    game_id: str
    duration_seconds: int
    winner: str
    mvp_agent_id: str
    evaluations: List[AgentEvaluationResponse]


# ============================================================================
# Replay 回放系统 Schema
# ============================================================================

class ReplayPlayerInfo(BaseModel):
    agent_id: str
    seat_number: int
    role: str  # 初始角色，POV视角下非己方角色可能为 "UNKNOWN"

class ReplayInitialState(BaseModel):
    players: List[ReplayPlayerInfo]

class ReplayPhaseChunk(BaseModel):
    phase_name: str  # 如 "NIGHT_WOLF_ACT", "DAY_DISCUSSION"
    events: List[EventResponse]

class ReplayDayChunk(BaseModel):
    day_num: int
    phases: List[ReplayPhaseChunk]

class ReplayResponse(BaseModel):
    game_id: str
    perspective: str
    agent_id: Optional[str] = None
    initial_state: ReplayInitialState
    timeline: List[ReplayDayChunk]
