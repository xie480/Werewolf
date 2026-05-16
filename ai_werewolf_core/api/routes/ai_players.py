"""
AI 玩家档案路由 —— 管理 AI 玩家配置与统计数据。

**Why**: 支持创建/查询/删除 AI 玩家档案，以及查看每个 AI 玩家的统计数据。
玩家统计数据包含总对局数、胜场、败场、模型调用失败次数等指标。

**修改说明**: 响应中去除了 avatar_url、model_provider、model_name、temperature 字段，
新增 model_id 字段，模型信息通过关联 model_config 表获取。

参考 [`docs/db/sql table.md`](../../../docs/db/sql%20table.md) 中的 `ai_player_profiles` 和 `ai_player_stats` 表定义。
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ai_werewolf_core.db.session import get_db
from ai_werewolf_core.db.models import AIPlayerProfile, AIPlayerStats, ModelConfig as ORMModelConfig
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
    model_id: str = Field(..., description="绑定的模型配置 ID")
    system_prompt: Optional[str] = Field(default=None, description="特定性格或行为准则 Prompt")


class AIProfileUpdate(BaseModel):
    """更新 AI 玩家档案请求。"""

    name: Optional[str] = Field(default=None, max_length=64)
    model_id: Optional[str] = Field(default=None, description="绑定的模型配置 ID")
    system_prompt: Optional[str] = Field(default=None)
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
    """AI 玩家档案响应。

    **修改说明**: 去除了 avatar_url、model_provider、model_name、temperature，
    新增 model_id 字段，模型展示信息由前端根据 model_id 自行查询。
    """

    id: str
    name: str
    model_id: Optional[str] = None
    system_prompt: Optional[str] = None
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
        model_id=profile.model_id,
        system_prompt=profile.system_prompt,
        is_active=profile.is_active,
        created_at=profile.created_at.isoformat() if profile.created_at else "",
        stats=stats_resp,
    )


async def _resolve_model_config(
    model_id: str, db: AsyncSession
) -> ORMModelConfig:
    """根据 model_id 查询 ModelConfig 表，不存在则抛出 400 异常。"""
    result = await db.execute(
        select(ORMModelConfig).where(ORMModelConfig.id == model_id)
    )
    model = result.scalars().first()
    if not model:
        raise HTTPException(
            status_code=400,
            detail=f"模型配置 [{model_id}] 不存在，请先创建该模型",
        )
    return model


# ============================================================================
# API 端点
# ============================================================================


@router.get("", response_model=AIProfileListResponse)
async def list_ai_players(
    active_only: bool = False,
    db: AsyncSession = Depends(get_db),
) -> AIProfileListResponse:
    """查询所有 AI 玩家档案列表（含统计数据）。"""
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
    """查询单个 AI 玩家档案详情（含统计数据）。"""
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

    前端传入 model_id，后端仅校验 model_config 是否存在并建立关联，
    模型相关信息由前端通过 model_config 接口自行查询。
    """
    try:
        # 1. 校验模型配置是否存在
        await _resolve_model_config(config.model_id, db)

        # 2. 生成雪花 ID
        profile_id = get_snowflake().next_id()

        # 3. 创建 AI 玩家档案，仅保留 model_id 关联
        profile = AIPlayerProfile(
            id=profile_id,
            name=config.name,
            model_id=config.model_id,
            system_prompt=config.system_prompt,
            is_active=True,
        )
        db.add(profile)

        # 4. 同时创建初始统计数据
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

        logger.info("ai_player_created", profile_id=profile_id, name=config.name, model_id=config.model_id)
        return await _profile_to_response(profile, stats)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("create_ai_player_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"创建 AI 玩家失败: {str(e)}")


@router.put("/{profile_id}", response_model=AIProfileResponse)
async def update_ai_player(
    profile_id: str,
    update: AIProfileUpdate,
    db: AsyncSession = Depends(get_db),
) -> AIProfileResponse:
    """更新 AI 玩家档案信息。"""
    try:
        result = await db.execute(
            select(AIPlayerProfile).where(AIPlayerProfile.id == profile_id)
        )
        profile = result.scalars().first()

        if not profile:
            raise HTTPException(status_code=404, detail=f"AI 玩家 [{profile_id}] 不存在")

        # 更新基本字段
        if update.name is not None:
            profile.name = update.name
        if update.system_prompt is not None:
            profile.system_prompt = update.system_prompt
        if update.is_active is not None:
            profile.is_active = update.is_active

        # 如果提供了 model_id，校验并更新
        if update.model_id is not None:
            await _resolve_model_config(update.model_id, db)
            profile.model_id = update.model_id

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
    """删除 AI 玩家档案及关联统计数据。"""
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
