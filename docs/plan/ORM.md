# 数据库 ORM 与表结构设计 (Phase 1)

本文档定义了 AI 狼人杀系统后端的数据库 ORM 模型和 SQL 表结构设计。基于 `SQLAlchemy` 2.0 和 `asyncpg` 异步驱动实现。

## 1. 核心设计原则

- **Event Sourcing (事件溯源)**：对局的真实状态由 `EventRecord` 表中的事件序列决定，`GameRecord` 和 `PlayerRecord` 仅作为当前状态的快照（Snapshot），方便快速查询。
- **异步优先**：所有数据库操作必须使用 `asyncio` 和 `asyncpg`。
- **强类型映射**：数据库中的枚举类型必须与 `ai_werewolf_core/schemas/enums.py` 中的 Pydantic 枚举严格对应。
- **JSONB 存储**：对于灵活的事件载荷（Payload）和目标列表，使用 PostgreSQL 的 `JSONB` 类型以支持高效查询和灵活扩展。

## 2. ORM 模型定义

我们将使用 SQLAlchemy 的 Declarative Base 来定义模型。建议在 `ai_werewolf_core/db/models.py` 中实现。

### 2.1 GameRecord (对局记录表)

记录每一局游戏的基础信息和当前状态快照。

```python
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from ai_werewolf_core.schemas.enums import GameStatus, GamePhase
from ai_werewolf_core.db.base import Base

class GameRecord(Base):
    __tablename__ = "games"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True, comment="对局全局唯一ID")
    status: Mapped[GameStatus] = mapped_column(SQLEnum(GameStatus), default=GameStatus.INIT, comment="对局状态")
    phase: Mapped[GamePhase] = mapped_column(SQLEnum(GamePhase), default=GamePhase.INIT, comment="当前阶段")
    round: Mapped[int] = mapped_column(Integer, default=1, comment="当前轮次")
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # 关联
    players: Mapped[list["PlayerRecord"]] = relationship("PlayerRecord", back_populates="game", cascade="all, delete-orphan")
    events: Mapped[list["EventRecord"]] = relationship("EventRecord", back_populates="game", cascade="all, delete-orphan")
```

### 2.2 PlayerRecord (玩家记录表)

记录对局中每个玩家（Agent）的身份、座位号和存活状态。

```python
from datetime import datetime
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from ai_werewolf_core.schemas.enums import Role
from ai_werewolf_core.db.base import Base

class PlayerRecord(Base):
    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True, comment="主键ID")
    game_id: Mapped[str] = mapped_column(String(36), ForeignKey("games.id", ondelete="CASCADE"), index=True, comment="所属对局ID")
    player_id: Mapped[str] = mapped_column(String(32), comment="玩家标识，如 player_1")
    seat_number: Mapped[int] = mapped_column(Integer, comment="座位号")
    role: Mapped[Role] = mapped_column(SQLEnum(Role), comment="玩家身份")
    is_alive: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否存活")
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # 关联
    game: Mapped["GameRecord"] = relationship("GameRecord", back_populates="players")
```

### 2.3 EventRecord (事件记录表)

核心表，记录对局中发生的所有事件，用于事件溯源和复盘。

```python
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from ai_werewolf_core.schemas.enums import EventType, Visibility
from ai_werewolf_core.db.base import Base

class EventRecord(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True, comment="主键ID")
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, comment="事件业务ID")
    game_id: Mapped[str] = mapped_column(String(36), ForeignKey("games.id", ondelete="CASCADE"), index=True, comment="所属对局ID")
    seq_num: Mapped[int] = mapped_column(Integer, index=True, comment="全局递增序列号，保证时序")
    
    event_type: Mapped[EventType] = mapped_column(SQLEnum(EventType), index=True, comment="事件类型")
    visibility: Mapped[Visibility] = mapped_column(SQLEnum(Visibility), comment="可见性")
    
    target_agents: Mapped[list[str]] = mapped_column(JSONB, default=list, comment="目标玩家ID列表")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, comment="事件具体内容")
    
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="事件发生时间")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # 关联
    game: Mapped["GameRecord"] = relationship("GameRecord", back_populates="events")
```

## 3. SQL 表结构 (PostgreSQL)

上述 ORM 模型对应的底层 PostgreSQL 表结构如下（由 SQLAlchemy 自动生成，此处为等效的 DDL）：

```sql
-- 创建枚举类型
CREATE TYPE gamestatus AS ENUM ('INIT', 'START', 'RUNNING', 'SETTLING', 'FINISHED', 'ABORTED');
CREATE TYPE gamephase AS ENUM ('INIT', 'NIGHT_START', 'NIGHT_ACTION', 'NIGHT_RESOLVE', 'DAY_START', 'DAY_DISCUSSION', 'DAY_VOTE', 'VOTE_RESOLVE', 'HUNTER_SHOOT', 'LAST_WORDS', 'GAME_OVER', 'DAY_PK_DISCUSSION', 'DAY_PK_VOTE');
CREATE TYPE role AS ENUM ('VILLAGER', 'WEREWOLF', 'SEER', 'WITCH', 'HUNTER');
CREATE TYPE eventtype AS ENUM ('SPEECH_EVENT', 'VOTE_EVENT', 'PHASE_TRANSITION_EVENT', 'PRIVATE_RESOLUTION_EVENT', 'SYSTEM_ANNOUNCEMENT', 'PLAYER_DEATH', 'GAME_OVER_EVENT');
CREATE TYPE visibility AS ENUM ('PUBLIC', 'PRIVATE', 'FACTION');

-- 1. games 表
CREATE TABLE games (
    id VARCHAR(36) PRIMARY KEY,
    status gamestatus NOT NULL DEFAULT 'INIT',
    phase gamephase NOT NULL DEFAULT 'INIT',
    round INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);
CREATE INDEX ix_games_id ON games (id);

-- 2. players 表
CREATE TABLE players (
    id VARCHAR(36) PRIMARY KEY,
    game_id VARCHAR(36) NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    player_id VARCHAR(32) NOT NULL,
    seat_number INTEGER NOT NULL,
    role role NOT NULL,
    is_alive BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);
CREATE INDEX ix_players_id ON players (id);
CREATE INDEX ix_players_game_id ON players (game_id);

-- 3. events 表
CREATE TABLE events (
    id VARCHAR(36) PRIMARY KEY,
    event_id VARCHAR(64) NOT NULL UNIQUE,
    game_id VARCHAR(36) NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    seq_num INTEGER NOT NULL,
    event_type eventtype NOT NULL,
    visibility visibility NOT NULL,
    target_agents JSONB NOT NULL DEFAULT '[]'::jsonb,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);
CREATE INDEX ix_events_id ON events (id);
CREATE INDEX ix_events_event_id ON events (event_id);
CREATE INDEX ix_events_game_id ON events (game_id);
CREATE INDEX ix_events_seq_num ON events (seq_num);
CREATE INDEX ix_events_event_type ON events (event_type);
```

## 4. 下一步开发计划

1. 创建 `ai_werewolf_core/db/base.py` 定义 SQLAlchemy 的 `Base` 类。
2. 创建 `ai_werewolf_core/db/models.py` 实现上述 ORM 模型。
3. 创建 `ai_werewolf_core/db/session.py` 实现异步数据库连接池和 Session 依赖注入。
4. 编写 Alembic 迁移脚本，初始化数据库表结构。
5. 更新 `EventBus`，在 `publish` 事件时异步写入 `EventRecord`。
