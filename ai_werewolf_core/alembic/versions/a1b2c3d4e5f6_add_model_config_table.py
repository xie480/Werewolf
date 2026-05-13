"""add_model_config_table

Revision ID: a1b2c3d4e5f6
Revises: c971b5c3506b
Create Date: 2026-05-13 21:47:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'c971b5c3506b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('model_config',
    sa.Column('id', sa.String(length=64), nullable=False, comment='模型唯一标识'),
    sa.Column('provider', sa.String(length=32), nullable=False, comment='提供者名称'),
    sa.Column('name', sa.String(length=64), nullable=False, comment='业务层使用的模型名称'),
    sa.Column('api_key', sa.String(length=255), nullable=False, comment='API Key'),
    sa.Column('base_url', sa.String(length=255), nullable=False, comment='API 基础 URL'),
    sa.Column('model_name', sa.String(length=64), nullable=False, comment='LLM 实际模型名称'),
    sa.Column('temperature', sa.Float(), nullable=False, comment='默认温度'),
    sa.Column('max_tokens', sa.Integer(), nullable=False, comment='默认最大 token'),
    sa.Column('timeout', sa.Float(), nullable=False, comment='硬超时（秒）'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('model_config')
