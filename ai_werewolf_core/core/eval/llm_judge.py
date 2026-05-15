"""
LLM 裁判模块 (LLM Judge)

负责组装 Prompt，调用 Model Adapter 获取评价并解析 JSON 结果。
"""

import json
from typing import Dict, Any
from ai_werewolf_core.schemas.enums import Role, Faction
from ai_werewolf_core.schemas.eval import ExtractedGameData, LLMJudgeResult
from ai_werewolf_core.agents.adapter.factory import AdapterFactory
from ai_werewolf_core.utils.logger import get_logger

logger = get_logger(__name__)

class LLMJudge:
    """大模型裁判"""

    def __init__(self, model_config: Dict[str, Any]):
        """
        初始化 LLM 裁判。
        
        Args:
            model_config: 用于裁判的模型配置，通常选择推理能力较强的模型。
        """
        self.adapter = AdapterFactory.create_adapter(
            provider=model_config.get("provider", "openai"),
            config=model_config
        )

    async def evaluate_agent(self, player_id: str, data: ExtractedGameData) -> LLMJudgeResult:
        """
        对单个 Agent 进行主观评分。
        
        Args:
            player_id: 被评测的玩家 ID
            data: 抽取出的对局数据
            
        Returns:
            LLMJudgeResult: 裁判评分结果
        """
        prompt = self._build_prompt(player_id, data)
        
        try:
            # 调用模型
            response = await self.adapter.generate(
                prompt=prompt,
                system_prompt="你是一个专业的狼人杀多智能体系统评测裁判。请严格按照要求输出 JSON 格式的评测报告。",
                response_format={"type": "json_object"}
            )
            
            # 解析结果
            result_dict = json.loads(response)
            return LLMJudgeResult.model_validate(result_dict)
            
        except Exception as e:
            logger.error("LLM 裁判评分失败", player_id=player_id, error=str(e))
            # 返回默认的保底结果
            return LLMJudgeResult(
                roleplay_score=5,
                deception_score=5 if data.global_roles.get(player_id) == Role.WEREWOLF else None,
                god_deduction_score=5 if data.global_roles.get(player_id) == Role.WEREWOLF else None,
                leadership_score=5 if data.global_roles.get(player_id) != Role.WEREWOLF else None,
                strengths="无（评测失败）",
                weaknesses="无（评测失败）",
                overall_review="由于系统原因，未能生成有效评价。"
            )

    def _build_prompt(self, player_id: str, data: ExtractedGameData) -> str:
        """组装评测 Prompt"""
        from ai_werewolf_core.agents.prompts.builder import PromptBuilder
        
        role = data.global_roles.get(player_id)
        faction = data.global_factions.get(player_id)
        
        # 格式化全局身份数据
        global_roles_str = json.dumps({p: r.value for p, r in data.global_roles.items()}, ensure_ascii=False, indent=2)
        
        # 格式化行为日志
        action_logs = data.agent_action_logs.get(player_id, [])
        action_logs_str = "\n".join([
            f"第 {log.round_num} 轮 [{log.phase}] {log.action_type}: " +
            (f"目标={log.target}" if log.target else "") +
            (f"发言='{log.speech}'" if log.speech else "")
            for log in action_logs
        ])
        
        # 格式化内部思维
        monologues = data.agent_internal_monologues.get(player_id, [])
        monologues_str = "\n".join([
            f"第 {m.round_num} 轮推理:\n" + "\n".join(m.reasoning) +
            f"\n嫌疑人名单: {json.dumps(m.suspect_heatmap, ensure_ascii=False)}"
            for m in monologues
        ])
        
        # 格式化夜晚击杀记录 (用于狼人找神能力评估)
        night_kills_str = json.dumps(data.night_kills, ensure_ascii=False)
        
        return PromptBuilder.build(
            "eval_judge",
            player_id=player_id,
            global_roles_str=global_roles_str,
            night_kills_str=night_kills_str,
            role=role.value if role else '未知',
            faction=faction.value if faction else '未知',
            action_logs_str=action_logs_str,
            monologues_str=monologues_str
        )
