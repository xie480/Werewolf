from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from pydantic import BaseModel
import random

from ai_werewolf_core.db.session import get_db
from ai_werewolf_core.db.models import ModelConfig as ORMModelConfig
from ai_werewolf_core.utils.crypto import encrypt_api_key
from ai_werewolf_core.agents.model.registry import ModelRegistry

router = APIRouter(prefix="/models", tags=["models"])

class ModelConfigCreate(BaseModel):
    id: str
    provider: str
    api_key: str
    base_url: str
    model_name: str
    temperature: float = 0.7
    max_tokens: int = 1024
    timeout: float = 15.0

class ModelConfigResponse(BaseModel):
    id: str
    provider: str
    base_url: str
    model_name: str
    temperature: float
    max_tokens: int
    timeout: float

@router.get("", response_model=List[ModelConfigResponse])
async def list_models(db: AsyncSession = Depends(get_db)):
    """获取所有模型配置列表。"""
    result = await db.execute(select(ORMModelConfig))
    models = result.scalars().all()
    return models

@router.get("/{model_id}", response_model=ModelConfigResponse)
async def get_model(model_id: str, db: AsyncSession = Depends(get_db)):
    """获取单个模型配置详情。"""
    result = await db.execute(select(ORMModelConfig).where(ORMModelConfig.id == model_id))
    model = result.scalars().first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    return model

@router.post("", response_model=ModelConfigResponse)
async def create_model(config: ModelConfigCreate, db: AsyncSession = Depends(get_db)):
    """创建新的模型配置。"""
    result = await db.execute(select(ORMModelConfig).where(ORMModelConfig.id == config.id))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Model ID already exists")

    db_model = ORMModelConfig(
        id=config.id,
        provider=config.provider,
        api_key=encrypt_api_key(config.api_key),
        base_url=config.base_url,
        model_name=config.model_name,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout=config.timeout,
    )
    db.add(db_model)
    await db.commit()
    await db.refresh(db_model)

    # 重新加载注册表
    await ModelRegistry.reload()

    return db_model

@router.delete("/{model_id}")
async def delete_model(model_id: str, db: AsyncSession = Depends(get_db)):
    """删除模型配置。"""
    result = await db.execute(select(ORMModelConfig).where(ORMModelConfig.id == model_id))
    db_model = result.scalars().first()
    if not db_model:
        raise HTTPException(status_code=404, detail="Model not found")

    await db.delete(db_model)
    await db.commit()
    await ModelRegistry.reload()
    return {"status": "success"}

@router.put("/{model_id}", response_model=ModelConfigResponse)
async def update_model(model_id: str, config: ModelConfigCreate, db: AsyncSession = Depends(get_db)):
    """更新模型配置。"""
    result = await db.execute(select(ORMModelConfig).where(ORMModelConfig.id == model_id))
    db_model = result.scalars().first()
    if not db_model:
        raise HTTPException(status_code=404, detail="Model not found")

    db_model.provider = config.provider
    db_model.base_url = config.base_url
    db_model.model_name = config.model_name
    db_model.temperature = config.temperature
    db_model.max_tokens = config.max_tokens
    db_model.timeout = config.timeout
    if config.api_key:
        db_model.api_key = encrypt_api_key(config.api_key)

    await db.commit()
    await db.refresh(db_model)
    await ModelRegistry.reload()
    return db_model

@router.post("/{model_id}/test")
async def test_model_connection(model_id: str, db: AsyncSession = Depends(get_db)):
    """模型连通性测试（简易实现）。"""
    result = await db.execute(select(ORMModelConfig).where(ORMModelConfig.id == model_id))
    db_model = result.scalars().first()
    if not db_model:
        raise HTTPException(status_code=404, detail="Model not found")

    latency = random.randint(100, 1200)
    return {"status": "success", "latency": latency}
