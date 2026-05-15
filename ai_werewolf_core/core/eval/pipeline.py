"""
评测管线 (Evaluation Pipeline)

串联数据抽取、启发式评分、LLM 裁判评分，并生成最终的复盘报告落库。
"""

import asyncio
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

from ai_werewolf_core.db.models import MatchReport, AgentEvaluation
from ai_werewolf_core.core.eval.extractor import DataExtractor
from ai_werewolf_core.core.eval.heuristic import HeuristicScorer
from ai_werewolf_core.core.eval.llm_judge import LLMJudge
from ai_werewolf_core.schemas.eval import ExtractedGameData
from ai_werewolf_core.schemas.enums import Role
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.utils.snowflake import get_snowflake
from ai_werewolf_core.schemas.enums import Role

logger = get_logger(__name__)

class EvaluationPipeline:
    """评测管线"""

    def __init__(self, db_session: AsyncSession, model_config: Dict[str, Any]):
        self.db = db_session
        self.extractor = DataExtractor(db_session)
        self.llm_judge = LLMJudge(model_config)

    async def run(self, game_id: str) -> MatchReport:
        """
        执行评测管线。
        
        Args:
            game_id: 对局 ID
            
        Returns:
            MatchReport: 生成的复盘报告 ORM 对象
        """
        logger.info("开始执行评测管线", game_id=game_id)
        
        # 1. 数据抽取
        data = await self.extractor.extract(game_id)
        
        # 2. 初始化启发式评分器
        heuristic_scorer = HeuristicScorer(data)
        
        # 3. 并发执行每个 Agent 的评测
        evaluations = []
        report_id = get_snowflake().next_id()
        
        # 准备并发任务
        tasks = []
        for player_id in data.global_roles.keys():
            tasks.append(self._evaluate_single_agent(player_id, data, heuristic_scorer, report_id))
            
        # 等待所有 Agent 评测完成
        agent_evals = await asyncio.gather(*tasks, return_exceptions=True)
        
        for eval_result in agent_evals:
            if isinstance(eval_result, Exception):
                logger.error("Agent 评测任务异常", game_id=game_id, error=str(eval_result))
            elif eval_result:
                evaluations.append(eval_result)
                
        # 4. 生成并保存 MatchReport
        
        # 简单评选 MVP (综合得分最高者)
        mvp_agent_id = ""
        max_score = -1
        for ev in evaluations:
            # 简单加总各项得分作为 MVP 评判标准
            total = (ev.rule_compliance_score + ev.logical_consistency_score + ev.roleplay_score +
                     (ev.deception_score or 0) + (ev.god_deduction_score or 0) +
                     (ev.situational_awareness_score or 0) + (ev.leadership_score or 0))
            if total > max_score:
                max_score = total
                mvp_agent_id = ev.player_id
                
        report = MatchReport(
            id=report_id,
            game_id=game_id,
            duration_seconds=data.duration_seconds,
            winner=data.winner.value,
            mvp_agent_id=mvp_agent_id,
            evaluations=evaluations
        )
        
        self.db.add(report)
        await self.db.commit()
        
        logger.info("评测管线执行完毕", game_id=game_id, report_id=report_id)
        return report

    async def _evaluate_single_agent(
        self, 
        player_id: str, 
        data: ExtractedGameData, 
        heuristic_scorer: HeuristicScorer,
        report_id: str
    ) -> AgentEvaluation:
        """评测单个 Agent"""
        logger.debug("开始评测 Agent", player_id=player_id)
        
        # 客观评分
        rule_compliance = heuristic_scorer.calculate_rule_compliance(player_id)
        logical_consistency = heuristic_scorer.calculate_logical_consistency(player_id)
        situational_awareness = heuristic_scorer.calculate_situational_awareness(player_id)
        
        # 主观评分 (LLM)
        llm_result = await self.llm_judge.evaluate_agent(player_id, data)
        
        # 组装 Evaluation
        role = data.global_roles.get(player_id)
        
        evaluation = AgentEvaluation(
            id=get_snowflake().next_id(),
            report_id=report_id,
            player_id=player_id,
            role=role,
            rule_compliance_score=rule_compliance,
            logical_consistency_score=logical_consistency,
            roleplay_score=llm_result.roleplay_score,
            deception_score=llm_result.deception_score,
            god_deduction_score=llm_result.god_deduction_score,
            situational_awareness_score=situational_awareness if role != Role.WEREWOLF else None,
            leadership_score=llm_result.leadership_score if role != Role.WEREWOLF else None,
            strengths=llm_result.strengths,
            weaknesses=llm_result.weaknesses,
            overall_review=llm_result.overall_review
        )
        
        return evaluation
