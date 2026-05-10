"""全局字典统一导出 - Phase 1 基础设施。

将 [`enums.py`](ai_werewolf_core/schemas/enums.py) 和 [`models.py`](ai_werewolf_core/schemas/models.py) 中的所有公共类型在此聚合，
其他模块只需 `from ai_werewolf_core.schemas import ...` 即可获取所需的枚举和模型。
"""

from .enums import (
    GameStatus,
    GamePhase,
    Role,
    ActionType,
    EventType,
    Visibility,
    Faction,
    Emotion,
)
from .models import (
    Player,
    AgentAction,
    SpeechContent,
    VoteContent,
    GameState,
    Event,
)

__all__ = [
    # 枚举
    "GameStatus",
    "GamePhase",
    "Role",
    "ActionType",
    "EventType",
    "Visibility",
    "Faction",
    "Emotion",
    # 模型
    "Player",
    "AgentAction",
    "SpeechContent",
    "VoteContent",
    "GameState",
    "Event",
]
