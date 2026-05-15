"""add_evaluation_tables

Revision ID: d8e9f0a1b2c3
Revises: b2c3d4e5f6g7
Create Date: 2026-05-15 16:45:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = 'd8e9f0a1b2c3'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6g7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create match_reports and agent_evaluations tables."""
    # ---- 1. match_reports ----
    op.create_table(
        'match_reports',
        sa.Column('id', sa.String(length=19), primary_key=True, index=True, comment='雪花算法全局唯一ID'),
        sa.Column('game_id', sa.String(length=19), sa.ForeignKey('games.id', ondelete='CASCADE'), unique=True, index=True, comment='所属对局ID'),
        sa.Column('duration_seconds', sa.Integer(), nullable=False, comment='对局时长（秒）'),
        sa.Column('winner', sa.String(length=32), nullable=False, comment='获胜阵营 (VILLAGER / WEREWOLF)'),
        sa.Column('mvp_agent_id', sa.String(length=32), nullable=False, comment='MVP 玩家标识'),
        sa.Column('faction_win_probability_curve', JSONB, nullable=False, server_default=sa.text("'[]'"), comment='阵营胜率走势（用于前端折线图）'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ---- 2. agent_evaluations ----
    op.create_table(
        'agent_evaluations',
        sa.Column('id', sa.String(length=19), primary_key=True, index=True, comment='雪花算法全局唯一ID'),
        sa.Column('report_id', sa.String(length=19), sa.ForeignKey('match_reports.id', ondelete='CASCADE'), index=True, nullable=False, comment='所属复盘报告ID'),
        sa.Column('player_id', sa.String(length=19), sa.ForeignKey('players.id', ondelete='CASCADE'), index=True, nullable=False, comment='关联的玩家记录ID'),
        sa.Column('role', sa.Enum('VILLAGER', 'WEREWOLF', 'SEER', 'WITCH', 'HUNTER', name='role'), nullable=False, comment='玩家身份'),
        
        # 通用维度
        sa.Column('rule_compliance_score', sa.Integer(), nullable=False, comment='规则服从度得分'),
        sa.Column('logical_consistency_score', sa.Integer(), nullable=False, comment='逻辑连贯性得分'),
        sa.Column('roleplay_score', sa.Integer(), nullable=False, comment='角色扮演得分'),
        
        # 专属维度
        sa.Column('deception_score', sa.Integer(), nullable=True, comment='伪装与欺骗得分 (狼人专属)'),
        sa.Column('god_deduction_score', sa.Integer(), nullable=True, comment='找神能力得分 (狼人专属)'),
        sa.Column('situational_awareness_score', sa.Integer(), nullable=True, comment='态势感知得分 (好人专属)'),
        sa.Column('leadership_score', sa.Integer(), nullable=True, comment='统帅与引导得分 (好人专属)'),
        
        # LLM 评价
        sa.Column('strengths', sa.String(), nullable=True, comment='高光时刻总结'),
        sa.Column('weaknesses', sa.String(), nullable=True, comment='致命失误总结'),
        sa.Column('overall_review', sa.String(), nullable=True, comment='综合评价'),
        
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    """Revert changes: drop agent_evaluations and match_reports tables."""
    op.drop_table('agent_evaluations')
    op.drop_table('match_reports')
