"""init_tables

Revision ID: c971b5c3506b
Revises: 
Create Date: 2026-05-11 00:52:53.467178

Phase 1 Initial Migration: Create enum types and core tables (games, players, events).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = 'c971b5c3506b'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Enum values synced from ai_werewolf_core/schemas/enums.py
# **Why**: These must exactly match the Python enum members; Alembic autogenerate
# could not connect to the DB, so DDL is hand-written per docs/plan/ORM.md.

GAMESTATUS_VALUES = ('INIT', 'START', 'RUNNING', 'SETTLING', 'FINISHED', 'ABORTED')
GAMEPHASE_VALUES = (
    'INIT', 'NIGHT_START', 'NIGHT_ACTION', 'NIGHT_RESOLVE',
    'DAY_START', 'DAY_DISCUSSION', 'DAY_VOTE', 'VOTE_RESOLVE',
    'HUNTER_SHOOT', 'LAST_WORDS', 'GAME_OVER',
    'DAY_PK_DISCUSSION', 'DAY_PK_VOTE',
)
ROLE_VALUES = ('VILLAGER', 'WEREWOLF', 'SEER', 'WITCH', 'HUNTER')
EVENTTYPE_VALUES = (
    'SPEECH_EVENT', 'VOTE_EVENT', 'PHASE_TRANSITION_EVENT',
    'PRIVATE_RESOLUTION_EVENT', 'SYSTEM_ANNOUNCEMENT',
    'PLAYER_DEATH', 'GAME_OVER_EVENT',
)
VISIBILITY_VALUES = ('PUBLIC', 'PRIVATE', 'FACTION')


def upgrade() -> None:
    """Create PostgreSQL enum types and the three core tables."""

    # ---- 1. ENUM types ----
    op.execute(f"CREATE TYPE gamestatus AS ENUM {GAMESTATUS_VALUES}")
    op.execute(f"CREATE TYPE gamephase AS ENUM {GAMEPHASE_VALUES}")
    op.execute(f"CREATE TYPE role AS ENUM {ROLE_VALUES}")
    op.execute(f"CREATE TYPE eventtype AS ENUM {EVENTTYPE_VALUES}")
    op.execute(f"CREATE TYPE visibility AS ENUM {VISIBILITY_VALUES}")

    # ---- 2. games ----
    op.create_table(
        'games',
        sa.Column('id', sa.String(36), primary_key=True, index=True,
                  comment='对局全局唯一ID'),
        sa.Column('status', sa.Enum(*GAMESTATUS_VALUES, name='gamestatus'),
                  nullable=False, server_default='INIT', comment='对局状态'),
        sa.Column('phase', sa.Enum(*GAMEPHASE_VALUES, name='gamephase'),
                  nullable=False, server_default='INIT', comment='当前阶段'),
        sa.Column('round', sa.Integer(), nullable=False, server_default='1',
                  comment='当前轮次'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )
    op.create_index('ix_games_id', 'games', ['id'])

    # ---- 3. players ----
    op.create_table(
        'players',
        sa.Column('id', sa.String(36), primary_key=True, index=True,
                  comment='主键ID'),
        sa.Column('game_id', sa.String(36),
                  sa.ForeignKey('games.id', ondelete='CASCADE'),
                  nullable=False, index=True, comment='所属对局ID'),
        sa.Column('player_id', sa.String(32), nullable=False,
                  comment='玩家标识，如 player_1'),
        sa.Column('seat_number', sa.Integer(), nullable=False, comment='座位号'),
        sa.Column('role', sa.Enum(*ROLE_VALUES, name='role'),
                  nullable=False, comment='玩家身份'),
        sa.Column('is_alive', sa.Boolean(), nullable=False,
                  server_default='true', comment='是否存活'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )
    op.create_index('ix_players_id', 'players', ['id'])
    op.create_index('ix_players_game_id', 'players', ['game_id'])

    # ---- 4. events ----
    op.create_table(
        'events',
        sa.Column('id', sa.String(36), primary_key=True, index=True,
                  comment='主键ID'),
        sa.Column('event_id', sa.String(64), unique=True, index=True,
                  nullable=False, comment='事件业务ID'),
        sa.Column('game_id', sa.String(36),
                  sa.ForeignKey('games.id', ondelete='CASCADE'),
                  nullable=False, index=True, comment='所属对局ID'),
        sa.Column('seq_num', sa.Integer(), nullable=False, index=True,
                  comment='全局递增序列号，保证时序'),
        sa.Column('event_type', sa.Enum(*EVENTTYPE_VALUES, name='eventtype'),
                  nullable=False, index=True, comment='事件类型'),
        sa.Column('visibility', sa.Enum(*VISIBILITY_VALUES, name='visibility'),
                  nullable=False, comment='可见性'),
        sa.Column('target_agents', JSONB(), nullable=False,
                  server_default='[]', comment='目标玩家ID列表'),
        sa.Column('payload', JSONB(), nullable=False,
                  server_default='{}', comment='事件具体内容'),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False,
                  comment='事件发生时间'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )
    op.create_index('ix_events_id', 'events', ['id'])
    op.create_index('ix_events_event_id', 'events', ['event_id'])
    op.create_index('ix_events_game_id', 'events', ['game_id'])
    op.create_index('ix_events_seq_num', 'events', ['seq_num'])
    op.create_index('ix_events_event_type', 'events', ['event_type'])


def downgrade() -> None:
    """Drop tables and enum types in reverse order."""
    op.drop_table('events')
    op.drop_table('players')
    op.drop_table('games')
    op.execute('DROP TYPE IF EXISTS visibility')
    op.execute('DROP TYPE IF EXISTS eventtype')
    op.execute('DROP TYPE IF EXISTS role')
    op.execute('DROP TYPE IF EXISTS gamephase')
    op.execute('DROP TYPE IF EXISTS gamestatus')
