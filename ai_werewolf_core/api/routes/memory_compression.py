from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ai_werewolf_core.schemas.models import CompressionResponse
from ai_werewolf_core.agents.memory.compression import MemoryCompressionService
from ai_werewolf_core.utils.redis_client import RedisClientManager
from ai_werewolf_core.constant.redis_keys import RedisKeys
from ai_werewolf_core.agents.memory.public import PublicMemoryManager

router = APIRouter(prefix="/memory", tags=["memory"])

class CompressRequest(BaseModel):
    game_id: str
    model_id: str = "default-openai"

@router.post("/compress", response_model=CompressionResponse)
async def compress_memory(request: CompressRequest):
    """
    手动触发对局记忆压缩
    """
    # 获取公共记忆
    public_mgr = PublicMemoryManager()
    round_memories = await public_mgr.fetch_round_memories(request.game_id)
    
    # 提取所有 public_events
    all_events = []
    for rm in round_memories:
        all_events.extend(rm.public_events)
        
    if not all_events:
        raise HTTPException(status_code=400, detail="No events found for this game")
        
    summary = await MemoryCompressionService.compress(
        events=all_events,
        game_id=request.game_id,
        model_id=request.model_id
    )
    
    return CompressionResponse(summary=summary)

@router.get("/summary/{game_id}", response_model=CompressionResponse)
async def get_memory_summary(game_id: str):
    """
    获取已存储的对局记忆摘要
    """
    redis = await RedisClientManager.get_client()
    key = RedisKeys.compressed_memory_summary(game_id)
    summary = await redis.get(key)
    
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")
        
    return CompressionResponse(summary=summary)
