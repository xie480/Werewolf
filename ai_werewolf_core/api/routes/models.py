from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from pydantic import BaseModel

from ai_werewolf_core.db.session import get_db
from ai_werewolf_core.db.models import ModelConfig as ORMModelConfig
from ai_werewolf_core.utils.crypto import encrypt_api_key
from ai_werewolf_core.agents.model.registry import ModelRegistry

router = APIRouter(prefix="/models", tags=["models"])

class ModelConfigCreate(BaseModel):
    id: str
    provider: str
    name: str
    api_key: str
    base_url: str
    model_name: str
    temperature: float = 0.7
    max_tokens: int = 1024
    timeout: float = 15.0

class ModelConfigResponse(BaseModel):
    id: str
    provider: str
    name: str
    base_url: str
    model_name: str
    temperature: float
    max_tokens: int
    timeout: float

@router.get("", response_model=List[ModelConfigResponse])
async def list_models(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ORMModelConfig))
    models = result.scalars().all()
    return models

@router.post("", response_model=ModelConfigResponse)
async def create_model(config: ModelConfigCreate, db: AsyncSession = Depends(get_db)):
    # 检查是否存在
    result = await db.execute(select(ORMModelConfig).where(ORMModelConfig.id == config.id))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Model ID already exists")
        
    db_model = ORMModelConfig(
        id=config.id,
        provider=config.provider,
        name=config.name,
        api_key=encrypt_api_key(config.api_key),
        base_url=config.base_url,
        model_name=config.model_name,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout=config.timeout
    )
    db.add(db_model)
    await db.commit()
    await db.refresh(db_model)
    
    # 重新加载注册表
    await ModelRegistry.reload()
    
    return db_model

@router.delete("/{model_id}")
async def delete_model(model_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ORMModelConfig).where(ORMModelConfig.id == model_id))
    db_model = result.scalars().first()
    if not db_model:
        raise HTTPException(status_code=404, detail="Model not found")
        
    await db.delete(db_model)
    await db.commit()
    
    # 重新加载注册表
    await ModelRegistry.reload()
    
    return {"status": "success"}
