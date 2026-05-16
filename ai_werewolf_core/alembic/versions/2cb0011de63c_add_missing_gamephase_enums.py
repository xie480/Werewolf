"""add missing gamephase enums

Revision ID: 2cb0011de63c
Revises: e9f0a1b2c3d4
Create Date: 2026-05-16 20:07:40.518171

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2cb0011de63c'
down_revision: Union[str, Sequence[str], None] = 'e9f0a1b2c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("COMMIT")
    op.execute("ALTER TYPE gamephase ADD VALUE IF NOT EXISTS 'NIGHT_WOLF_ACT'")
    op.execute("ALTER TYPE gamephase ADD VALUE IF NOT EXISTS 'NIGHT_WITCH_ACT'")
    op.execute("ALTER TYPE gamephase ADD VALUE IF NOT EXISTS 'NIGHT_SEER_ACT'")


def downgrade() -> None:
    """Downgrade schema."""
    pass
