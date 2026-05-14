import os
from jinja2 import Environment, FileSystemLoader, select_autoescape
from ai_werewolf_core.schemas.models import MemorySnapshot
from ai_werewolf_core.schemas.enums import Role

class PromptBuilder:
    """Prompt 组装器，负责将 MemorySnapshot 转化为 LLM 可理解的 Prompt。"""

    def __init__(self, template_dir: str = "ai_werewolf_core/agents/prompts/templates"):
        self.template_dir = template_dir
        # 初始化 Jinja2 环境
        self.env = Environment(
            loader=FileSystemLoader(self.template_dir),
            autoescape=select_autoescape(),
            trim_blocks=True,
            lstrip_blocks=True
        )

    def _render_system(self, snapshot: MemorySnapshot, current_phase: str) -> str:
        """
        System Prompt：注入agentID，阵营，当前游戏阶段
        """
        template = self.env.get_template("system.j2")
        return template.render(
            agent_id=snapshot.agent_id,
            faction=snapshot.private_state.faction.value,
            current_phase=current_phase
        )

    def _render_role_strategy(self, role: Role, snapshot: MemorySnapshot, current_phase: str) -> str:
        """
        根据角色，注入角色信息，如队友，技能状态，当前游戏阶段
        """
        template_name = f"roles/{role.value.lower()}.j2"
        template = self.env.get_template(template_name)
        return template.render(
            teammates=", ".join(snapshot.private_state.teammates) if snapshot.private_state.teammates else "无",
            skill_status=snapshot.private_state.skill_status,
            current_phase=current_phase
        )

    def _render_context(self, snapshot: MemorySnapshot, window_size: int = 2) -> str:
        """
        注入历史信息，如系统反馈，公共时间线，历史内核，历史经验
        """
        template = self.env.get_template("context.j2")
        
        # 根据 window_size 划分近期记忆和全局摘要
        current_round = snapshot.history[-1].round_num if snapshot.history else 1
        
        recent_history = []
        if window_size > 0:
            recent_history = snapshot.history[-window_size:]
            
        return template.render(
            global_summary=snapshot.global_summary,
            recent_history=recent_history,
            current_round=current_round,
            window_size=window_size,
            last_suspect_list=snapshot.last_suspect_list,
            experiences=snapshot.experiences
        )

    def _render_format(self) -> str:
        """
        注入 Prompt 输出格式。
        """
        template = self.env.get_template("format.j2")
        return template.render()

    async def build_prompt(self, snapshot: MemorySnapshot, max_tokens: int = 6000) -> str:
        """
        根据记忆快照，组装完整的 Prompt。
        执行多层级熔断管线 (Assembly & Fallback Pipeline)
        """
        current_phase = "INIT"
        if snapshot.history and snapshot.history[-1].public_events:
            current_phase = snapshot.history[-1].public_events[-1].phase.value

        system_part = self._render_system(snapshot, current_phase)
        role_part = self._render_role_strategy(snapshot.private_state.role, snapshot, current_phase)
        format_part = self._render_format()
        
        from ai_werewolf_core.agents.memory.pruner import MemoryPruner
        from ai_werewolf_core.config import settings
        pruner = MemoryPruner(settings.compression_model_name)
        
        base_tokens = pruner.count_tokens(system_part) + pruner.count_tokens(role_part) + pruner.count_tokens(format_part)
        
        # 1. 初次组装尝试 (Normal Assembly)
        window_size = 2
        
        while window_size >= 0:
            context_part = self._render_context(snapshot, window_size=window_size)
            total_tokens = base_tokens + pruner.count_tokens(context_part)
            
            if total_tokens <= max_tokens:
                return f"{system_part}\n\n{role_part}\n\n{context_part}\n\n{format_part}"
                
            # 2. 降级 1：强制缩小滑动窗口 (Shrink Window)
            window_size -= 1
            
        # 3. 降级 2：极限压缩全局摘要 (Extreme Compression)
        if snapshot.global_summary:
            from ai_werewolf_core.agents.memory.compression import MemoryCompressionService
            from ai_werewolf_core.utils.logger import get_logger
            logger = get_logger(__name__)
            logger.warning("prompt_tokens_exceeded_triggering_extreme_compression", agent_id=snapshot.agent_id)
            
            compressed_summary = await MemoryCompressionService.extreme_compress_summary(
                snapshot.global_summary, snapshot.game_id, snapshot.agent_id
            )
            snapshot.global_summary = compressed_summary
            
            context_part = self._render_context(snapshot, window_size=0)
            total_tokens = base_tokens + pruner.count_tokens(context_part)
            
            if total_tokens <= max_tokens:
                return f"{system_part}\n\n{role_part}\n\n{context_part}\n\n{format_part}"
                
        # 4. 降级 3：暴力截断 (Truncation)
        # 如果依然超限，直接截断 global_summary
        if snapshot.global_summary:
            # 粗略截断一半
            half_len = len(snapshot.global_summary) // 2
            snapshot.global_summary = snapshot.global_summary[:half_len] + "...(截断)"
            context_part = self._render_context(snapshot, window_size=0)
            
        return f"{system_part}\n\n{role_part}\n\n{context_part}\n\n{format_part}"
