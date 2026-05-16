"""cleanup_model_config_and_ai_player_profiles

**修改说明**:
1. model_config: 删除冗余的 name 列，保留 model_name 作为唯一标识
2. ai_player_profiles: 删除 avatar_url, model_provider, model_name, temperature 列
3. ai_player_profiles: 新增 model_id 列（FK → model_config.id）

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-05-16 16:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9f0a1b2c3d4'
down_revision: Union[str, Sequence[str], None] = 'd8e9f0a1b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- 1. model_config: 删除 name 列 ----
    op.drop_column('model_config', 'name')

    # ---- 2. ai_player_profiles: 删除旧列 ----
    op.drop_column('ai_player_profiles', 'avatar_url')
    op.drop_column('ai_player_profiles', 'model_provider')
    op.drop_column('ai_player_profiles', 'model_name')
    op.drop_column('ai_player_profiles', 'temperature')

    # ---- 3. ai_player_profiles: 新增 model_id 列（FK → model_config.id） ----
    op.add_column(
        'ai_player_profiles',
        sa.Column('model_id', sa.String(length=64), nullable=True, comment='绑定的模型配置 ID'),
    )
    op.create_foreign_key(
        'fk_ai_player_profiles_model_id',
        'ai_player_profiles', 'model_config',
        ['model_id'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    # ---- 1. ai_player_profiles: 回退 - 删除外键和 model_id列 ----
    op.drop_constraint('fk_ai_player_profiles_model_id', 'ai_player_profiles', type_='foreignkey')
    op.drop_column('ai_player_profiles', 'model_id')

    # ---- 2. ai_player_profiles: 恢复旧列 ----
    op.add_column(
        'ai_player_profiles',
        sa.Column('temperature', sa.Float(), nullable=False, server_default=sa.text('0.7'), comment='模型生成温度参数'),
    )
    op.add_column(
        'ai_player_profiles',
        sa.Column('model_name', sa.String(length=64), nullable=False, server_default=sa.text("''"), comment='具体模型版本'),
    )
    op.add_column(
        'ai_player_profiles',
        sa.Column('model_provider', sa.String(length=32), nullable=False, server_default=sa.text("''"), comment='模型提供商'),
    )
    op.add_column(
        'ai_player_profiles',
        sa.Column('avatar_url', sa.String(length=255), nullable=True, comment='玩家头像URL'),
    )

    # ---- 3. model_config: 恢复 name 列 ----
    op.add_column(
        'model_config',
        sa.Column('name', sa.String(length=64), nullable=False, server_default=sa.text("''"), comment='业务层使用的模型名称'),
    )
