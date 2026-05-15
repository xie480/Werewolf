"""
启发式规则评分器 (Heuristic Scorer)

负责计算客观的评测指标，如规则服从度、跟票率、找狼准确率等。
"""

from typing import Dict, List
from ai_werewolf_core.schemas.enums import Faction, Role
from ai_werewolf_core.schemas.eval import ExtractedGameData
from ai_werewolf_core.utils.logger import get_logger

logger = get_logger(__name__)

class HeuristicScorer:
    """启发式规则评分器"""

    def __init__(self, data: ExtractedGameData):
        self.data = data

    def calculate_rule_compliance(self, player_id: str) -> int:
        """
        计算规则服从度。
        公式: 100 - (格式错误重试次数 + 非法动作拦截次数) * 10
        最低 0 分。
        """
        failures = self.data.action_validation_failures.get(player_id, 0)
        score = 100 - (failures * 10)
        return max(0, score)

    def calculate_logical_consistency(self, player_id: str) -> int:
        """
        计算逻辑连贯性（客观部分）。
        对比投票行为与内部嫌疑人名单。
        如果投票给了一个在自己嫌疑人名单中怀疑度很低（< 0.3）的人，扣分。
        """
        score = 100
        deduction_per_inconsistency = 20
        
        monologues = self.data.agent_internal_monologues.get(player_id, [])
        # 构建 round_num -> suspect_heatmap 的映射
        heatmap_by_round = {m.round_num: m.suspect_heatmap for m in monologues}
        
        for round_num, votes in self.data.vote_records.items():
            if player_id in votes:
                target = votes[player_id]
                heatmap = heatmap_by_round.get(round_num, {})
                
                # 如果目标在嫌疑人名单中，且怀疑度很低，说明言行不一
                if target in heatmap and heatmap[target] < 0.3:
                    # 狼人可能有倒钩战术，好人言行不一扣分更重
                    role = self.data.global_roles.get(player_id)
                    if role != Role.WEREWOLF:
                        score -= deduction_per_inconsistency
                        
        return max(0, score)

    def calculate_situational_awareness(self, player_id: str) -> int:
        """
        计算态势感知与推理（找狼准确率）。
        对比 suspect_heatmap 和真实的狼人名单。
        """
        role = self.data.global_roles.get(player_id)
        if role == Role.WEREWOLF:
            return 0 # 狼人不需要找狼，此维度不适用，外部应处理为 None
            
        actual_wolves = [p for p, r in self.data.global_roles.items() if r == Role.WEREWOLF]
        if not actual_wolves:
            return 100
            
        monologues = self.data.agent_internal_monologues.get(player_id, [])
        if not monologues:
            return 50
            
        total_accuracy = 0.0
        
        for m in monologues:
            heatmap = m.suspect_heatmap
            if not heatmap:
                continue
                
            # 计算对真实狼人的平均怀疑度
            wolf_suspicions = [heatmap.get(w, 0.0) for w in actual_wolves]
            avg_wolf_suspicion = sum(wolf_suspicions) / len(actual_wolves)
            
            # 计算对好人的平均怀疑度
            good_players = [p for p in self.data.global_roles.keys() if p not in actual_wolves and p != player_id]
            good_suspicions = [heatmap.get(g, 0.0) for g in good_players]
            avg_good_suspicion = sum(good_suspicions) / len(good_players) if good_players else 0.0
            
            # 准确率 = 狼人怀疑度 - 好人怀疑度 (范围 -1 到 1)
            accuracy = avg_wolf_suspicion - avg_good_suspicion
            total_accuracy += accuracy
            
        avg_accuracy = total_accuracy / len(monologues)
        
        # 映射到 0-100: -1 -> 0, 0 -> 50, 1 -> 100
        score = int((avg_accuracy + 1) / 2 * 100)
        return max(0, min(100, score))
