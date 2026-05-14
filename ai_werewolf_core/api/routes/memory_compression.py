"""Memory compression API route (placeholder)."""

from pydantic import BaseModel
from typing import List
from ai_werewolf_core.schemas.models import PublicEventLog

class CompressRequest(BaseModel):
    """请求体用于压缩记忆的 API（仅用于单元测试占位）"""
    game_id: str
    round_num: int
    events: List[PublicEventLog]
