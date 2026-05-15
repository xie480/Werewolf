import os
from jinja2 import Environment, FileSystemLoader, select_autoescape
from ai_werewolf_core.schemas.models import MemorySnapshot
from ai_werewolf_core.schemas.enums import Role

class PromptBuilder:
    """Prompt 组装器，负责将 MemorySnapshot 转化为 LLM 可理解的 Prompt。"""

    def __init__(self, template_dir: str | None = None):
        """Initialize the PromptBuilder.

        Args:
            template_dir: Optional directory containing Jinja2 templates. If omitted,
                the directory is resolved relative to this file's location, ensuring
                that template loading works regardless of the current working directory.
        """
        # Resolve the absolute path to the templates directory. This guards against
        # failures when the process's working directory differs from the project root.
        if template_dir is None:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "templates"))
        else:
            base_dir = os.path.abspath(template_dir)
        self.template_dir = base_dir
        # 初始化 Jinja2 环境
        self.env = Environment(
            loader=FileSystemLoader(self.template_dir),
            autoescape=select_autoescape(),
            trim_blocks=True,
            lstrip_blocks=True,
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

    def _get_phase_constraints(self, current_phase: str, role: Role) -> tuple[bool, list[str]]:
        """
        根据当前阶段和角色，计算允许的动作和是否可以发言。
        """
        from ai_werewolf_core.schemas.enums import GamePhase, ActionType

        can_speak = False
        allowed_actions = [ActionType.PASS.value]

        if current_phase in (GamePhase.DAY_DISCUSSION.value, GamePhase.DAY_PK_DISCUSSION.value, GamePhase.LAST_WORDS.value):
            can_speak = True
            allowed_actions = [ActionType.SPEAK.value]
        elif current_phase in (GamePhase.DAY_VOTE.value, GamePhase.DAY_PK_VOTE.value):
            allowed_actions = [ActionType.VOTE.value]
        elif current_phase == GamePhase.NIGHT_WOLF_ACT.value:
            if role == Role.WEREWOLF:
                allowed_actions = [ActionType.WOLF_KILL.value, ActionType.PASS.value]
        elif current_phase == GamePhase.NIGHT_WITCH_ACT.value:
            if role == Role.WITCH:
                allowed_actions = [ActionType.WITCH_SAVE.value, ActionType.WITCH_POISON.value, ActionType.PASS.value]
        elif current_phase == GamePhase.NIGHT_SEER_ACT.value:
            if role == Role.SEER:
                allowed_actions = [ActionType.SEER_CHECK.value, ActionType.PASS.value]
        elif current_phase == GamePhase.HUNTER_SHOOT.value:
            if role == Role.HUNTER:
                allowed_actions = [ActionType.HUNTER_SHOOT.value, ActionType.PASS.value]

        return can_speak, allowed_actions

    def _render_format(self, faction: str, allowed_actions: list[str], can_speak: bool, role: Role, current_phase: str) -> str:
        """
        注入 Prompt 输出格式。
        """
        from ai_werewolf_core.schemas.enums import ActionType
        
        if can_speak and ActionType.SPEAK.value in allowed_actions:
            template_name = "formats/speak_format.j2"
        elif ActionType.VOTE.value in allowed_actions:
            template_name = "formats/vote_format.j2"
        elif ActionType.WOLF_KILL.value in allowed_actions:
            template_name = "formats/wolf_kill_format.j2"
        elif ActionType.SEER_CHECK.value in allowed_actions:
            template_name = "formats/seer_check_format.j2"
        elif ActionType.WITCH_SAVE.value in allowed_actions or ActionType.WITCH_POISON.value in allowed_actions:
            template_name = "formats/witch_act_format.j2"
        elif ActionType.HUNTER_SHOOT.value in allowed_actions:
            template_name = "formats/hunter_shoot_format.j2"
        else:
            template_name = "formats/pass_format.j2"
            
        from ai_werewolf_core.schemas.enums import ActionType
        template = self.env.get_template(template_name)
        return template.render(
            faction=faction,
            allowed_actions=allowed_actions,
            can_speak=can_speak,
            role=role.value if role else None,
            current_phase=current_phase,
            ActionType=ActionType
        )

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
        # 根据当前阶段和角色确定可用动作及是否可以发言
        can_speak, allowed_actions = self._get_phase_constraints(current_phase, snapshot.private_state.role)
        format_part = self._render_format(
            faction=snapshot.private_state.faction.value,
            allowed_actions=allowed_actions,
            can_speak=can_speak,
            role=snapshot.private_state.role,
            current_phase=current_phase
        )
        
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
