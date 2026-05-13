"""Pydantic 数据模型定义 - Phase 1 基础设施。

**Why**: 所有跨模块的数据传递必须使用 Pydantic `BaseModel` 进行强类型校验。
这既保证了数据格式的正确性，也防止非法数据流入游戏引擎或记忆系统。
"""

from datetime import datetime
from typing import Optional, Dict, List, Any
from pydantic import BaseModel, Field, validator

from .enums import (
    GamePhase,
    Role,
    ActionType,
    Visibility,
    GameStatus,
    EventType,
    Emotion,
    Faction,
)


class Player(BaseModel):
    """玩家基础信息模型。
    
    代表一个参与对局的玩家（AI Agent 或人类）。
    """

    player_id: str
    """唯一标识，格式 `player_{序号}`，如 `player_1`。"""

    seat_number: int
    """座位号，用于发言顺序和 UI 排列。"""

    role: Role
    """该玩家的身份（由 Game Engine 在开局时分配）。"""

    is_alive: bool = True
    """存活状态。引擎在结算死亡后更新此字段。"""

    class Config:
        use_enum_values = True


class AgentAction(BaseModel):
    """统一行动协议（Action Schema）。

    **Why**: AI Agent 向 Game Engine 提交的所有意图必须封装为此结构，
    以便 Action System 进行语法、时序和业务三个层次的校验。
    参考 [`Game Engine.md`](docs/system/Game%20Engine.md) 和
    [`Action System.md`](docs/system/Action%20System.md)。

    注意：`phase` 字段的存在是为了日志和复盘，但引擎以当前实际阶段为准；
    如果 Agent 提交的 `phase` 与实际阶段不一致，Action System 会拦截。
    """

    action_type: ActionType # 行动类型
    actor_id: str # 行动者
    target_id: Optional[str] = None # 目标
    phase: GamePhase # 当前游戏阶段
    round: int # 当前轮次
    reason: str = "" # 行动理由
    confidence: float = Field(0.0, ge=0.0, le=1.0) # 行动可信度
    timestamp: Optional[datetime] = None # 行动时间

    @validator("target_id")
    def target_id_format(cls, v):
        """确保 target_id 要么为 None，要么符合 `player_X` 格式。"""
        if v is not None and not v.startswith("player_"):
            raise ValueError("target_id 必须以 'player_' 开头")
        return v

    class Config:
        use_enum_values = True


class PublicEventLog(BaseModel):
    """单条公共事件日志"""
    seq_num: int = Field(..., description="全局事件序号，保证严格时序")
    phase: GamePhase = Field(..., description="事件发生的游戏阶段")
    description: str = Field(..., description="自然语言描述，如'玩家3发言：我是预言家'")


class PrivateEventLog(BaseModel):
    """单条私有事件日志"""
    seq_num: int = Field(..., description="全局事件序号，保证严格时序")
    phase: GamePhase = Field(..., description="事件发生的游戏阶段")
    description: str = Field(..., description="自然语言描述，如'昨晚你查验了3号，他是狼人'")


class PrivateState(BaseModel):
    """Agent 私有状态"""
    role: Role = Field(..., description="真实底牌身份")
    faction: Faction = Field(..., description="所属阵营")
    teammates: List[str] = Field(default_factory=list, description="已知队友ID列表（如狼人队友）")
    skill_status: Dict[str, Any] = Field(default_factory=dict, description="技能状态（如女巫解药是否可用）")
    system_feedbacks: List[PrivateEventLog] = Field(default_factory=list, description="系统私密反馈（如昨晚验人结果）")


class MemorySnapshot(BaseModel):
    """传递给 LangGraph 的完整记忆快照"""
    agent_id: str
    game_id: str
    public_timeline: List[PublicEventLog] = Field(..., description="裁剪后的公共时间线")
    private_state: PrivateState = Field(..., description="当前私有状态")
    historical_reasoning: List[str] = Field(default_factory=list, description="历史内心OS摘要")


class SpeechContent(BaseModel):
    """发言内容协议（Speech Schema）。

    采用方案 A（List 拆分）：将原本模糊的自然语言立场拆分为机器可读的图谱关系——
    `suspected_player_ids`（怀疑列表）和 `trusted_player_ids`（信任列表）。
    `emotion` 从自由字符串升级为 [`Emotion`](ai_werewolf_core/schemas/enums.py) 枚举，
    用于前端驱动虚拟角色表情动画和复盘分析。

    参考 [`Game Engine.md`](docs/system/Game%20Engine.md)。
    """

    speech: str = ""
    """发言的具体文本内容。"""

    suspected_player_ids: List[str] = Field(default_factory=list)
    """发言中明确怀疑或踩的玩家 ID 列表，如 `["player_3", "player_7"]`。"""

    trusted_player_ids: List[str] = Field(default_factory=list)
    """发言中明确信任或保护的玩家 ID 列表，如 `["player_5"]`。"""

    emotion: Emotion = Emotion.NEUTRAL
    """发言时的主要情绪表现，严格限定枚举值，禁止 LLM 自由输出。"""

    confidence: float = Field(0.0, ge=0.0, le=1.0)
    """发言内容的整体确信度。"""


class VoteContent(BaseModel):
    """投票内容协议（Vote Schema）。

    记录为什么投给目标，而不仅仅是“投给谁”。
    包含投票理由、确信度以及备选目标列表。
    参考 [`Game Engine.md`](docs/system/Game%20Engine.md)。
    """

    vote_target: Optional[int] = None   # 座位号；None 表示弃权
    reason: str = ""
    certainty: float = Field(0.0, ge=0.0, le=1.0)
    alternative_targets: List[int] = Field(default_factory=list) # 备选目标


class GameState(BaseModel):
    """游戏状态快照。

    在内存中维护的当前对局状态，包含所有玩家的最新信息。
    注意：真正的权威状态来源于事件溯源（Event Sourcing），
    此模型仅作为运行时缓存。
    """

    game_id: str
    status: GameStatus = GameStatus.INIT
    phase: GamePhase = GamePhase.INIT
    round: int = 1
    players: Dict[str, Player] = Field(default_factory=dict)

    class Config:
        use_enum_values = True


class Event(BaseModel):
    """事件基类 —— Event Sourcing 的核心单元。

    所有对局中发生的事实（Fact，非 Intent）都通过此模型记录和传递。
    参考 [`Event System.md`](docs/system/Event%20System.md)。
    """

    event_id: str
    game_id: str
    seq_num: int
    """全局递增序列号——严格保证时序正确，防止 Agent 产生幻觉。"""

    event_type: EventType
    visibility: Visibility
    target_agents: List[str] = Field(default_factory=list)  # 目标玩家id
    timestamp: datetime
    payload: dict = Field(default_factory=dict) # 事件内容

    class Config:
        use_enum_values = True


class AdapterRequest(BaseModel):
    """业务层已经组装好的完整 Prompt"""
    model_id: str = Field(..., description="使用的模型唯一标识")
    agent_id: str
    game_id: str
    phase: GamePhase
    full_prompt: str = Field(..., description="已组装好的完整 Prompt 文本")
    temperature: float = 0.7
    max_tokens: int = 1024
    response_model: Any = Field(..., description="期望解析的 Pydantic Schema")


class AdapterResponse(BaseModel):
    raw_content: str
    parsed_data: Optional[BaseModel] = None
    is_success: bool
    error_message: Optional[str] = None
    retry_count: int = 0
    usage: Dict[str, int] = Field(default_factory=dict)
