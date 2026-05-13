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
        template = self.env.get_template("system.j2")
        return template.render(
            agent_id=snapshot.agent_id,
            faction=snapshot.private_state.faction.value,
            current_phase=current_phase
        )

    def _render_role_strategy(self, role: Role, snapshot: MemorySnapshot, current_phase: str) -> str:
        template_name = f"roles/{role.value.lower()}.j2"
        template = self.env.get_template(template_name)
        return template.render(
            teammates=", ".join(snapshot.private_state.teammates) if snapshot.private_state.teammates else "无",
            skill_status=snapshot.private_state.skill_status,
            current_phase=current_phase
        )

    def _render_context(self, snapshot: MemorySnapshot) -> str:
        template = self.env.get_template("context.j2")
        return template.render(
            system_feedbacks=snapshot.private_state.system_feedbacks,
            public_timeline=snapshot.public_timeline,
            historical_reasoning=snapshot.historical_reasoning,
            experiences=snapshot.experiences
        )

    def _render_format(self) -> str:
        template = self.env.get_template("format.j2")
        return template.render()

    def build_prompt(self, snapshot: MemorySnapshot) -> str:
        """
        根据记忆快照，组装完整的 Prompt。
        """
        current_phase = "INIT"
        if snapshot.public_timeline:
            current_phase = snapshot.public_timeline[-1].phase.value

        system_part = self._render_system(snapshot, current_phase)
        role_part = self._render_role_strategy(snapshot.private_state.role, snapshot, current_phase)
        context_part = self._render_context(snapshot)
        format_part = self._render_format()

        return f"{system_part}\n\n{role_part}\n\n{context_part}\n\n{format_part}"
