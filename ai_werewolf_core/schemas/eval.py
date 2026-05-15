"""
评测系统相关的数据模型定义。
"""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from ai_werewolf_core.schemas.enums import Role, Faction

class AgentActionLog(BaseModel):
    """玩家单次行为日志"""
    round_num: int
    phase: str
    action_type: str
    target: Optional[str] = None
    speech: Optional[str] = None
    timestamp: str

class AgentInternalMonologue(BaseModel):
    """玩家单轮内部思维链"""
    round_num: int
    reasoning: List[str]
    suspect_heatmap: Dict[str, float]

class ExtractedGameData(BaseModel):
    """从对局中抽取出的完整评测所需数据"""
    game_id: str
    duration_seconds: int
    winner: Faction
    global_roles: Dict[str, Role]
    global_factions: Dict[str, Faction]
    
    # 玩家行为日志: player_id -> list[AgentActionLog]
    agent_action_logs: Dict[str, List[AgentActionLog]]
    
    # 玩家内部思维: player_id -> list[AgentInternalMonologue]
    agent_internal_monologues: Dict[str, List[AgentInternalMonologue]]
    
    # 投票记录: round_num -> {voter_id -> target_id}
    vote_records: Dict[int, Dict[str, str]]
    
    # 动作校验失败次数: player_id -> int
    action_validation_failures: Dict[str, int]
    
    # 夜晚击杀记录: round_num -> target_id
    night_kills: Dict[int, str]

class LLMJudgeResult(BaseModel):
    """LLM 裁判的评分结果"""
    roleplay_score: int = Field(..., ge=0, le=10, description="角色扮演得分")
    deception_score: Optional[int] = Field(None, ge=0, le=10, description="伪装与欺骗得分 (狼人专属)")
    god_deduction_score: Optional[int] = Field(None, ge=0, le=10, description="找神能力得分 (狼人专属)")
    leadership_score: Optional[int] = Field(None, ge=0, le=10, description="统帅与引导得分 (好人专属)")
    strengths: str = Field(..., description="高光时刻总结")
    weaknesses: str = Field(..., description="致命失误总结")
    overall_review: str = Field(..., description="综合评价")
