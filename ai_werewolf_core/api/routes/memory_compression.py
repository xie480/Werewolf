from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ai_werewolf_core.schemas.models import CompressionResponse
from ai_werewolf_core.agents.memory.compression import MemoryCompressionService
from ai_werewolf_core.utils.redis_client import RedisClientManager
from ai_werewolf_core.constant.redis_keys import RedisKeys
from ai_werewolf_core.agents.memory.public import PublicMemoryManager

router = APIRouter(prefix="/memory", tags=["memory"])

from typing import Dict
import json

class CompressRequest(BaseModel):
    game_id: str
    round_num: int
    model_id: str = "default-openai"

@router.post("/compress", response_model=CompressionResponse)
async def compress_memory(request: CompressRequest):
    """
    手动触发单轮对局记忆压缩
    """
    # 获取公共记忆
    public_mgr = PublicMemoryManager()
    round_memories = await public_mgr.fetch_round_memories(request.game_id)
    
    # 提取指定轮次的 public_events
    target_events = []
    for rm in round_memories:
        if rm.round_num == request.round_num:
            target_events.extend(rm.public_events)
            break
            
    if not target_events:
        raise HTTPException(status_code=400, detail=f"No events found for round {request.round_num}")
        
    result = await MemoryCompressionService.compress(
        events=target_events,
        game_id=request.game_id,
        round_num=request.round_num,
        model_id=request.model_id
    )
    
    return result

@router.get("/summary/{game_id}", response_model=Dict[int, CompressionResponse])
async def get_memory_summary(game_id: str):
    """
    获取已存储的对局记忆摘要（按轮次）
    """
    redis = await RedisClientManager.get_client()
    key = RedisKeys.compressed_memory_summary(game_id)
    
    # 获取 Hash 中的所有数据
    raw_data = await redis.hgetall(key)
    
    if not raw_data:
        return {}
        
    result = {}
    for round_str, json_str in raw_data.items():
        try:
            round_num = int(round_str)
            data = json.loads(json_str)
            result[round_num] = CompressionResponse(**data)
        except (ValueError, json.JSONDecodeError) as e:
            continue
            
    return result
