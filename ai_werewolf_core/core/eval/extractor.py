"""
数据抽取模块 (Data Extractor)

负责从 EventRecord 和 Redis (Private Memory) 中提取对局的完整时间线、
玩家发言、投票记录及内部思维链，组装成 ExtractedGameData 供后续评测使用。
"""

import json
from typing import Dict, List, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_werewolf_core.db.models import EventRecord, GameRecord, PlayerRecord
from ai_werewolf_core.schemas.enums import EventType, Faction, Role, GamePhase
from ai_werewolf_core.agents.memory.private import PrivateMemoryManager
from ai_werewolf_core.schemas.eval import (
    ExtractedGameData,
    AgentActionLog,
    AgentInternalMonologue,
)
from ai_werewolf_core.schemas.enums import ActionType
from ai_werewolf_core.utils.logger import get_logger

logger = get_logger(__name__)

class DataExtractor:
    """评测数据抽取器"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.private_memory = PrivateMemoryManager()

    async def extract(self, game_id: str) -> ExtractedGameData:
        """
        抽取指定对局的完整评测数据。
        
        Args:
            game_id: 对局 ID
            
        Returns:
            ExtractedGameData: 抽取出的结构化数据
        """
        logger.info("开始抽取评测数据", game_id=game_id)
        
        # 1. 获取对局元数据和玩家信息
        game_record = await self._get_game_record(game_id)
        if not game_record:
            raise ValueError(f"GameRecord not found for game_id: {game_id}")
        
        global_roles: Dict[str, Role] = {}
        global_factions: Dict[str, Faction] = {}
        for player in game_record.players:
            global_roles[player.player_id] = player.role
            # 简单阵营划分：狼人是 WEREWOLF，其他是 VILLAGER
            global_factions[player.player_id] = Faction.WEREWOLF if player.role == Role.WEREWOLF else Faction.VILLAGER

        # 2. 获取所有事件
        events = await self._get_all_events(game_id)
        
        # 3. 解析事件，提取行为日志、投票记录、击杀记录
        agent_action_logs: Dict[str, List[AgentActionLog]] = {p.player_id: [] for p in game_record.players}
        vote_records: Dict[int, Dict[str, str]] = {}
        night_kills: Dict[int, str] = {}
        action_validation_failures: Dict[str, int] = {p.player_id: 0 for p in game_record.players}
        
        winner = Faction.VILLAGER # 默认值，从 GAME_OVER 事件中更新
        current_round = 1
        current_phase = GamePhase.INIT.value
        
        for event in events:
            payload = event.payload
            
            # 阶段转换事件
            if event.event_type == EventType.PHASE_TRANSITION_EVENT:
                # 更新当前轮数和阶段
                current_round = payload.get("round", current_round)
                current_phase = payload.get("new_phase", current_phase)
                
            # 游戏结束事件
            elif event.event_type == EventType.GAME_OVER_EVENT:
                winner_str = payload.get("winner_faction")
                if winner_str:
                    winner = Faction(winner_str)
                    
            # 发言事件
            elif event.event_type == EventType.SPEECH_EVENT:
                speaker = payload.get("player_id")
                if speaker and speaker in agent_action_logs:
                    # 记录发言内容
                    agent_action_logs[speaker].append(AgentActionLog(
                        round_num=current_round,
                        phase=current_phase,
                        action_type="SPEAK",
                        speech=payload.get("content"),
                        timestamp=event.timestamp.isoformat()
                    ))
                    
            # 投票事件
            elif event.event_type == EventType.VOTE_EVENT:
                voter = payload.get("voter")
                target = payload.get("target")
                if voter and voter in agent_action_logs:
                    # 记录投票行为
                    agent_action_logs[voter].append(AgentActionLog(
                        round_num=current_round,
                        phase=current_phase,
                        action_type="VOTE",
                        target=target,
                        timestamp=event.timestamp.isoformat()
                    ))
                    # 如果当前轮次没有记录过投票记录，则创建
                    if current_round not in vote_records:
                        vote_records[current_round] = {}
                    if target: # 忽略弃票
                        vote_records[current_round][voter] = target
                        
            elif event.event_type == EventType.PLAYER_DEATH:
                # 记录夜晚击杀 (仅记录狼人杀害的，用于评估狼人找神能力)
                # 死亡事件的 payload 中包含 death_reason 字段，其值为 ActionType 枚举的字符串形式
                reason = payload.get("death_reason")
                if reason == ActionType.WOLF_KILL.value:
                    dead_player = payload.get("player_id")
                    if dead_player:
                        night_kills[current_round] = dead_player
                    
            
        # 4. 提取内部思维链
        agent_internal_monologues: Dict[str, List[AgentInternalMonologue]] = {}
        for player in game_record.players:
            player_id = player.player_id
            monologues = await self._extract_internal_monologues(game_id, player_id)
            agent_internal_monologues[player_id] = monologues

        # 5. 组装结果
        # 计算对局时长
        duration_seconds = 0
        if events:
            start_time = events[0].timestamp
            end_time = events[-1].timestamp
            duration_seconds = int((end_time - start_time).total_seconds())

        return ExtractedGameData(
            game_id=game_id,
            duration_seconds=duration_seconds,
            winner=winner,
            global_roles=global_roles,
            global_factions=global_factions,
            agent_action_logs=agent_action_logs,
            agent_internal_monologues=agent_internal_monologues,
            vote_records=vote_records,
            action_validation_failures=action_validation_failures,
            night_kills=night_kills
        )

    async def _get_game_record(self, game_id: str) -> GameRecord | None:
        """获取对局元数据"""
        from sqlalchemy.orm import selectinload
        stmt = select(GameRecord).options(selectinload(GameRecord.players)).where(GameRecord.id == game_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_all_events(self, game_id: str) -> List[EventRecord]:
        """获取所有公有事件"""
        stmt = select(EventRecord).where(EventRecord.game_id == game_id).order_by(EventRecord.seq_num.asc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def _extract_internal_monologues(self, game_id: str, player_id: str) -> List[AgentInternalMonologue]:
        """从 PrivateMemoryManager 提取内部思维链"""
        monologues = []
        try:
            round_data = await self.private_memory.get_private_round_data(game_id, player_id)
            
            for round_num, data in round_data.items():
                reasoning_list = data.get("reasoning", [])
                
                # TODO: 目前 reasoning 中存储的是自然语言，无法直接解析出 suspect_heatmap。
                # 需要在 Agent Runtime 阶段，强制 Agent 在每轮结束时输出结构化的 suspect_list 并单独存储。
                # 此处暂时使用空字典占位。
                suspect_heatmap = {}
                
                monologues.append(AgentInternalMonologue(
                    round_num=round_num,
                    reasoning=reasoning_list,
                    suspect_heatmap=suspect_heatmap
                ))
        except Exception as e:
            logger.error("提取内部思维链失败", game_id=game_id, player_id=player_id, error=str(e))
            
        return monologues
