"""
FastAPI 应用入口 —— Werewolf Game Engine HTTP API。

**Why**: 这是整个后端的 ingress 层，负责组装所有路由、配置中间件、
管理启动/关闭生命周期。遵循项目架构规范：FastAPI is ingress only，
所有重计算委托给 Celery Worker（未来集成）。

当前状态（Phase 3）：
- RESTful API 路由已挂载（games / players / events）
- WebSocket 端点预留（Phase 3 后续）
- Celery Worker 通过 worker.py 独立启动

参考 [`docs/plan/Phase 3 FastAPI API.md`](../../docs/plan/Phase%203%20FastAPI%20API.md)。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai_werewolf_core.api.routes import games, players, events, actions, models
from ai_werewolf_core.api.ws.manager import connection_manager
from ai_werewolf_core.api.ws.routes import router as ws_router
from ai_werewolf_core.core.event.bus import event_bus
from ai_werewolf_core.db.session import close_db_engine
from ai_werewolf_core.tasks.dispatch import register_dispatchers
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.utils.redis_client import RedisClientManager
from ai_werewolf_core.utils.redis_lua_loader import LuaScriptManager

logger = get_logger(__name__)


# ============================================================================
# 应用生命周期管理
# ============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 应用生命周期管理器。

    **启动阶段**:
        1. 记录启动日志
        2. 验证 Redis 连接可用（惰性初始化，首次请求才真正建立连接）
        3. DB 引擎已在模块导入时初始化

    **关闭阶段**:
        1. 关闭所有 Redis 连接
        2. 关闭 DB 引擎连接池
        3. 记录关闭日志
    """
    # 启动
    logger.info("werewolf_game_engine_starting", version="0.1.0")

    # 验证 Redis 连接（失败仅告警，不阻止启动——支持降级运行）
    try:
        redis = await RedisClientManager.get_client()
        await redis.ping()
        logger.info("redis_connection_verified")
        
        # 加载所有 Lua 脚本
        await LuaScriptManager.load_all_scripts()
        logger.info("lua_scripts_loaded")
    except Exception as e:
        logger.warning("redis_connection_failed_at_startup", error=str(e))

    # 初始化模型注册表
    from ai_werewolf_core.agents.model.registry import ModelRegistry
    await ModelRegistry.init()
    logger.info("model_registry_initialized")

    yield

    # 关闭
    logger.info("werewolf_game_engine_shutting_down")
    try:
        await RedisClientManager.close()
        logger.info("redis_connections_closed")
    except Exception as e:
        logger.error("redis_close_failed", error=str(e), exc_info=True)

    await close_db_engine()
    logger.info("werewolf_game_engine_stopped")


# ============================================================================
# FastAPI 应用实例
# ============================================================================

app = FastAPI(
    title="Werewolf Game Engine",
    description="基于 LangGraph + FastAPI 的实时多智能体狼人杀博弈平台 API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ============================================================================
# 中间件配置
# ============================================================================

# CORS: 允许前端开发服务器 (Vite :5173) 跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# 路由挂载
# ============================================================================

# P0: 对局生命周期管理
app.include_router(games.router, prefix="/api/games", tags=["Games"])

# P1: 玩家查询
app.include_router(players.router, prefix="/api/games", tags=["Players"])

# P2: 事件查询
app.include_router(events.router, prefix="/api/games", tags=["Events"])

# P3: 玩家操作（投票、发言、技能）
app.include_router(actions.router, prefix="/api/games", tags=["Actions"])

# 模型管理
app.include_router(models.router, prefix="/api", tags=["Models"])

# Phase 3 后续: WebSocket 实时推送
app.include_router(ws_router, tags=["WebSocket"])

# 注册 WebSocket 连接管理器为 EventBus 全局订阅者
# 每当新事件发布时，自动推送给已连接的 WebSocket 客户端
event_bus.subscribe_all(connection_manager.on_event)

# 注册 Agent 任务分发器
register_dispatchers(event_bus)


# ============================================================================
# 健康检查端点
# ============================================================================


@app.get("/health", tags=["Health"])
async def health_check():
    """健康检查端点 —— 供负载均衡器和监控系统使用。

    返回 200 OK 表示服务正常运行。
    """
    return {"status": "ok", "version": "0.1.0"}


@app.get("/", tags=["Health"])
async def root():
    """根路径重定向到 API 文档。"""
    return {
        "name": "Werewolf Game Engine",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
    }
