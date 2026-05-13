"""
ORM 模型定义 - 数据库表映射。

**Why**: 遵循 Event Sourcing 架构，EventRecord 记录所有对局事件作为事实来源，
GameRecord 和 PlayerRecord 仅维护当前状态快照，方便快速查询。

参考 [`docs/plan/ORM.md`](../../docs/plan/ORM.md)。
"""

from datetime import datetime
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Enum as SQLEnum, Float
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from ai_werewolf_core.schemas.enums import (
    GameStatus,
    GamePhase,
    Role,
    EventType,
    Visibility,
)
from ai_werewolf_core.db.base import Base


class GameRecord(Base):
    """对局记录表 —— 每局游戏的元信息和当前状态快照。

    **Why**: 对局的真实状态由 EventRecord 事件溯源决定，
    此表仅作为当前回合、阶段等快照，用于快速查询而无需回放全部事件。
    """

    __tablename__ = "games"

    id: Mapped[str] = mapped_column(
        String(19), primary_key=True, index=True, comment="雪花算法全局唯一ID"
    )
    status: Mapped[GameStatus] = mapped_column(
        SQLEnum(GameStatus), default=GameStatus.INIT, comment="对局状态"
    )
    phase: Mapped[GamePhase] = mapped_column(
        SQLEnum(GamePhase), default=GamePhase.INIT, comment="当前阶段"
    )
    round: Mapped[int] = mapped_column(Integer, default=1, comment="当前轮次")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # 关联
    players: Mapped[list["PlayerRecord"]] = relationship(
        "PlayerRecord", back_populates="game", cascade="all, delete-orphan"
    )
    events: Mapped[list["EventRecord"]] = relationship(
        "EventRecord", back_populates="game", cascade="all, delete-orphan"
    )


class PlayerRecord(Base):
    """玩家记录表 —— 对局中每个 Agent 的身份、座位和存活状态。

    **Why**: 与 Pydantic 的 Player 模型对应，便于数据库持久化和快速查询
    当前存活玩家列表等聚合信息。
    """

    __tablename__ = "players"

    id: Mapped[str] = mapped_column(
        String(19), primary_key=True, index=True, comment="雪花算法全局唯一ID"
    )
    game_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("games.id", ondelete="CASCADE"),
        index=True,
        comment="所属对局ID",
    )
    player_id: Mapped[str] = mapped_column(
        String(32), comment="玩家标识，如 player_1"
    )
    seat_number: Mapped[int] = mapped_column(Integer, comment="座位号")
    role: Mapped[Role] = mapped_column(SQLEnum(Role), comment="玩家身份")
    is_alive: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否存活")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # 关联
    game: Mapped["GameRecord"] = relationship("GameRecord", back_populates="players")


class EventRecord(Base):
    """事件记录表 —— Event Sourcing 的核心存储。

    **Why**: 所有对局中发生的事实（Fact）都通过此表持久化，
    支持按可见性过滤查询、按 seq_num 保证时序，以及复盘时的完整事件回放。
    注意：target_agents 和 payload 使用 JSONB 以支持灵活的结构化数据。
    """

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(
        String(19), primary_key=True, index=True, comment="雪花算法全局唯一ID"
    )
    event_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, comment="事件业务ID"
    )
    game_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("games.id", ondelete="CASCADE"),
        index=True,
        comment="所属对局ID",
    )
    seq_num: Mapped[int] = mapped_column(
        Integer, index=True, comment="全局递增序列号，保证时序"
    )

    event_type: Mapped[EventType] = mapped_column(
        SQLEnum(EventType), index=True, comment="事件类型"
    )
    visibility: Mapped[Visibility] = mapped_column(
        SQLEnum(Visibility), comment="可见性"
    )

    target_agents: Mapped[list[str]] = mapped_column(
        JSONB, default=list, comment="目标玩家ID列表"
    )
    payload: Mapped[dict] = mapped_column(
        JSONB, default=dict, comment="事件具体内容"
    )

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), comment="事件发生时间"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # 关联
    game: Mapped["GameRecord"] = relationship("GameRecord", back_populates="events")


class ModelConfig(Base):
    """模型配置表 —— 存储 LLM 供应商配置。
    
    **Why**: 支持运行时动态增删改模型配置，无需重启服务。
    """

    __tablename__ = "model_config"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="模型唯一标识")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, comment="提供者名称")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="业务层使用的模型名称")
    api_key: Mapped[str] = mapped_column(String(255), nullable=False, comment="API Key")
    base_url: Mapped[str] = mapped_column(String(255), nullable=False, comment="API 基础 URL")
    model_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="LLM 实际模型名称")
    temperature: Mapped[float] = mapped_column(Float, default=0.7, comment="默认温度")
    max_tokens: Mapped[int] = mapped_column(Integer, default=1024, comment="默认最大 token")
    timeout: Mapped[float] = mapped_column(Float, default=15.0, comment="硬超时（秒）")

    def to_adapter_config(self) -> dict:
        """返回给 AdapterFactory 使用的配置字典"""
        return {
            "api_key": self.api_key,
            "base_url": self.base_url,
            "model_name": self.model_name,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": self.timeout,
        }
