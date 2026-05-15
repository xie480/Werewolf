"""
AI 玩家档案路由 —— 管理 AI 玩家配置与统计数据。

**Why**: 支持创建/查询/删除 AI 玩家档案，以及查看每个 AI 玩家的统计数据。
玩家统计数据包含总对局数、胜场、败场、模型调用失败次数等指标。

参考 [`docs/db/sql table.md`](../../../docs/db/sql%20table.md) 中的 `ai_player_profiles` 和 `ai_player_stats` 表定义。
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_werewolf_core.db.session import get_db
from ai_werewolf_core.db.models import AIPlayerProfile, AIPlayerStats
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.utils.snowflake import get_snowflake

logger = get_logger(__name__)

router = APIRouter(prefix="/ai-players", tags=["ai-players"])


# ============================================================================
# Pydantic Schema
# ============================================================================


class AIProfileCreate(BaseModel):
    """创建 AI 玩家档案请求。"""

    name: str = Field(..., min_length=1, max_length=64, description="玩家显示名称")
    avatar_url: Optional[str] = Field(default=None, max_length=255, description="玩家头像URL")
    model_provider: str = Field(default="openai", max_length=32, description="模型提供商")
    model_name: str = Field(..., max_length=64, description="具体模型版本")
    system_prompt: Optional[str] = Field(default=None, description="特定性格或行为准则 Prompt")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="模型生成温度参数")


class AIProfileUpdate(BaseModel):
    """更新 AI 玩家档案请求。"""

    name: Optional[str] = Field(default=None, max_length=64)
    avatar_url: Optional[str] = Field(default=None, max_length=255)
    model_provider: Optional[str] = Field(default=None, max_length=32)
    model_name: Optional[str] = Field(default=None, max_length=64)
    system_prompt: Optional[str] = Field(default=None)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    is_active: Optional[bool] = Field(default=None)


class AIStatsResponse(BaseModel):
    """AI 玩家统计数据响应。"""

    total_games: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    response_failures: int = 0
    total_actions: int = 0
    last_played_at: Optional[str] = None


class AIProfileResponse(BaseModel):
    """AI 玩家档案响应。"""

    id: str
    name: str
    avatar_url: Optional[str] = None
    model_provider: str
    model_name: str
    system_prompt: Optional[str] = None
    temperature: float
    is_active: bool
    created_at: str
    stats: Optional[AIStatsResponse] = None


class AIProfileListResponse(BaseModel):
    """AI 玩家列表响应。"""

    players: List[AIProfileResponse] = Field(default_factory=list)
    total: int = 0


# ============================================================================
# 工具函数
# ============================================================================


async def _profile_to_response(
    profile: AIPlayerProfile, stats: AIPlayerStats | None = None
) -> AIProfileResponse:
    """将 ORM 模型转换为响应 Schema。

    Args:
        profile: AI 玩家档案 ORM 实例。
        stats: 可选的统计数据 ORM 实例。

    Returns:
        序列化后的响应对象。
    """
    stats_resp = None
    if stats:
        win_rate = round(stats.wins / max(stats.total_games, 1), 4)
        stats_resp = AIStatsResponse(
            total_games=stats.total_games,
            wins=stats.wins,
            losses=stats.losses,
            win_rate=win_rate,
            response_failures=stats.response_failures,
            total_actions=stats.total_actions,
            last_played_at=stats.last_played_at.isoformat() if stats.last_played_at else None,
        )

    return AIProfileResponse(
        id=profile.id,
        name=profile.name,
        avatar_url=profile.avatar_url,
        model_provider=profile.model_provider,
        model_name=profile.model_name,
        system_prompt=profile.system_prompt,
        temperature=profile.temperature,
        is_active=profile.is_active,
        created_at=profile.created_at.isoformat() if profile.created_at else "",
        stats=stats_resp,
    )


# ============================================================================
# API 端点
# ============================================================================


@router.get("", response_model=AIProfileListResponse)
async def list_ai_players(
    active_only: bool = False,
    db: AsyncSession = Depends(get_db),
) -> AIProfileListResponse:
    """查询所有 AI 玩家档案列表（含统计数据）。

    Args:
        active_only: 是否只返回激活状态的玩家。

    Returns:
        玩家档案列表及总数。
    """
    try:
        stmt = select(AIPlayerProfile)
        if active_only:
            stmt = stmt.where(AIPlayerProfile.is_active.is_(True))
        stmt = stmt.order_by(AIPlayerProfile.created_at.desc())

        result = await db.execute(stmt)
        profiles = result.scalars().all()

        player_list: list[AIProfileResponse] = []
        for profile in profiles:
            stats = await profile.awaitable_attrs.stats if hasattr(profile, "awaitable_attrs") else profile.stats
            player_list.append(await _profile_to_response(profile, stats))

        return AIProfileListResponse(players=player_list, total=len(player_list))

    except Exception as e:
        logger.error("list_ai_players_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询 AI 玩家列表失败: {str(e)}")


@router.get("/{profile_id}", response_model=AIProfileResponse)
async def get_ai_player(
    profile_id: str,
    db: AsyncSession = Depends(get_db),
) -> AIProfileResponse:
    """查询单个 AI 玩家档案详情（含统计数据）。

    Args:
        profile_id: AI 玩家档案 ID。

    Returns:
        包含统计数据的玩家档案详情。

    Raises:
        404: 玩家不存在。
    """
    try:
        result = await db.execute(
            select(AIPlayerProfile).where(AIPlayerProfile.id == profile_id)
        )
        profile = result.scalars().first()

        if not profile:
            raise HTTPException(status_code=404, detail=f"AI 玩家 [{profile_id}] 不存在")

        stats = await profile.awaitable_attrs.stats if hasattr(profile, "awaitable_attrs") else profile.stats
        return await _profile_to_response(profile, stats)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_ai_player_failed", profile_id=profile_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询 AI 玩家失败: {str(e)}")


@router.post("", response_model=AIProfileResponse, status_code=201)
async def create_ai_player(
    config: AIProfileCreate,
    db: AsyncSession = Depends(get_db),
) -> AIProfileResponse:
    """创建新的 AI 玩家档案。

    自动生成 Snowflake ID，同时创建对应的空统计数据记录。

    Args:
        config: 玩家档案配置。

    Returns:
        创建后的玩家档案（含初始统计数据）。
    """
    try:
        # 生成雪花 ID
        profile_id = get_snowflake().next_id()

        profile = AIPlayerProfile(
            id=profile_id,
            name=config.name,
            avatar_url=config.avatar_url,
            model_provider=config.model_provider,
            model_name=config.model_name,
            system_prompt=config.system_prompt,
            temperature=config.temperature,
            is_active=True,
        )
        db.add(profile)

        # 同时创建初始统计数据
        stats = AIPlayerStats(
            player_id=profile_id,
            total_games=0,
            wins=0,
            losses=0,
            response_failures=0,
            total_actions=0,
            total_action_time_ms=0,
            role_stats={},
        )
        db.add(stats)

        await db.commit()
        await db.refresh(profile)

        logger.info("ai_player_created", profile_id=profile_id, name=config.name)
        return await _profile_to_response(profile, stats)

    except Exception as e:
        logger.error("create_ai_player_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"创建 AI 玩家失败: {str(e)}")


@router.put("/{profile_id}", response_model=AIProfileResponse)
async def update_ai_player(
    profile_id: str,
    update: AIProfileUpdate,
    db: AsyncSession = Depends(get_db),
) -> AIProfileResponse:
    """更新 AI 玩家档案信息。

    Args:
        profile_id: AI 玩家档案 ID。
        update: 需要更新的字段。

    Returns:
        更新后的玩家档案。
    """
    try:
        result = await db.execute(
            select(AIPlayerProfile).where(AIPlayerProfile.id == profile_id)
        )
        profile = result.scalars().first()

        if not profile:
            raise HTTPException(status_code=404, detail=f"AI 玩家 [{profile_id}] 不存在")

        update_data = update.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(profile, field, value)

        await db.commit()
        await db.refresh(profile)

        stats = await profile.awaitable_attrs.stats if hasattr(profile, "awaitable_attrs") else profile.stats
        logger.info("ai_player_updated", profile_id=profile_id)
        return await _profile_to_response(profile, stats)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("update_ai_player_failed", profile_id=profile_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"更新 AI 玩家失败: {str(e)}")


@router.delete("/{profile_id}")
async def delete_ai_player(
    profile_id: str,
    db: AsyncSession = Depends(get_db),
):
    """删除 AI 玩家档案及关联统计数据。

    Args:
        profile_id: AI 玩家档案 ID。
    """
    try:
        result = await db.execute(
            select(AIPlayerProfile).where(AIPlayerProfile.id == profile_id)
        )
        profile = result.scalars().first()

        if not profile:
            raise HTTPException(status_code=404, detail=f"AI 玩家 [{profile_id}] 不存在")

        await db.delete(profile)
        await db.commit()

        logger.info("ai_player_deleted", profile_id=profile_id)
        return {"status": "success"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("delete_ai_player_failed", profile_id=profile_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"删除 AI 玩家失败: {str(e)}")
