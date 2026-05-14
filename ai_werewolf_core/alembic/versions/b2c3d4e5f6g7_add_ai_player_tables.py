"""add_ai_player_tables

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-14 08:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6g7'
 down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create AI player profile and stats tables, and add ai_profile_id to players."""
    # ---- 1. ai_player_profiles ----
    op.create_table(
        'ai_player_profiles',
        sa.Column('id', sa.String(length=19), primary_key=True, comment='雪花算法全局唯一ID'),
        sa.Column('name', sa.String(length=64), nullable=False, index=True, comment='玩家显示名称'),
        sa.Column('avatar_url', sa.String(length=255), nullable=True, comment='玩家头像URL'),
        sa.Column('model_provider', sa.String(length=32), nullable=False, comment='模型提供商'),
        sa.Column('model_name', sa.String(length=64), nullable=False, comment='具体模型版本'),
        sa.Column('system_prompt', sa.String(), nullable=True, comment='特定性格或行为准则 Prompt'),
        sa.Column('temperature', sa.Float(), nullable=False, server_default=sa.text('0.7'), comment='模型生成温度参数'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true'), comment='是否在玩家库中激活可用'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # ---- 2. ai_player_stats ----
    op.create_table(
        'ai_player_stats',
        sa.Column('player_id', sa.String(length=19), sa.ForeignKey('ai_player_profiles.id', ondelete='CASCADE'), primary_key=True, comment='关联 ai_player_profiles.id'),
        sa.Column('total_games', sa.Integer(), nullable=False, server_default=sa.text('0'), comment='参与的总对局数'),
        sa.Column('wins', sa.Integer(), nullable=False, server_default=sa.text('0'), comment='获胜局数'),
        sa.Column('losses', sa.Integer(), nullable=False, server_default=sa.text('0'), comment='失败局数'),
        sa.Column('response_failures', sa.Integer(), nullable=False, server_default=sa.text('0'), comment='模型调用失败/超时/格式错误的累计次数'),
        sa.Column('total_actions', sa.Integer(), nullable=False, server_default=sa.text('0'), comment='累计成功执行的行动次数'),
        sa.Column('total_action_time_ms', sa.Integer(), nullable=False, server_default=sa.text('0'), comment='累计行动耗时（毫秒）'),
        sa.Column('role_stats', JSONB, nullable=False, server_default=sa.text("'{}'"), comment='按角色统计的胜负数据'),
        sa.Column('last_played_at', sa.DateTime(timezone=True), nullable=True, comment='最后一次参与对局的时间'),
    )

    # ---- 3. Add ai_profile_id to players ----
    op.add_column('players', sa.Column('ai_profile_id', sa.String(length=19), nullable=True, comment='关联的 AI 玩家档案 ID'))
    op.create_foreign_key(
        'fk_players_ai_profile',
        'players', 'ai_player_profiles',
        ['ai_profile_id'], ['id'],
        ondelete='SET NULL'
    )


def downgrade() -> None:
    """Revert changes: drop ai_player_stats, ai_player_profiles, and remove column from players."""
    # Drop foreign key first
    op.drop_constraint('fk_players_ai_profile', 'players', type_='foreignkey')
    # Drop column
    op.drop_column('players', 'ai_profile_id')
    # Drop stats table
    op.drop_table('ai_player_stats')
    # Drop profiles table
    op.drop_table('ai_player_profiles')
