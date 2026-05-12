# Phase 3: FastAPI RESTful API 接口实现计划

## Context

**问题**: 项目的底层 Game Engine 已经完整（LifecycleManager、PhaseStateMachine、VoteManager、PlayerStatusManager、EventBus 全部可用），但 `main.py` 为空文件，`api/routes/` 和 `api/ws/` 目录完全为空。这意味着 Engine 无法被外部调用，前端和测试也无法接入。

**目标**: 创建完整的 FastAPI 入口和 RESTful API 路由层，将 Game Engine 暴露为标准 HTTP 接口，实现 Phase 3（异步通信）的核心交付物。

**现有资源（直接复用）**:
- `LifecycleManager(game_id, event_bus)` — 生命周期管理，公开方法：`init_game()`, `start_game()`, `advance_phase()`, `end_game()`, `abort_game()`, `get_status()`
- `PhaseStateMachine(game_id, event_bus)` — 阶段状态机，公开方法：`get_current_phase()`, `get_round()`, `transition_to()`
- `EventBus` — 全局单例 `event_bus`，提供 `publish()`, `get_events()`, `get_event_count()`
- `async_session_factory` — DB 会话工厂（`async with async_session_factory() as session:`）
- `PlayerStatusManager` — 玩家状态管理，提供 `init_players()`, `is_alive()`, `get_all_players()`
- `get_snowflake()` — ID 生成器

---

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `ai_werewolf_core/main.py` | **重写** | FastAPI 应用入口：CORS、路由挂载、启动/关闭事件 |
| `ai_werewolf_core/api/__init__.py` | **新增** | 空文件，标记为 Python 包 |
| `ai_werewolf_core/api/routes/__init__.py` | **新增** | 空文件，标记为 Python 包 |
| `ai_werewolf_core/api/routes/games.py` | **新增** | 对局管理路由（创建、启动、推进、查询、中止） |
| `ai_werewolf_core/api/routes/players.py` | **新增** | 玩家查询路由 |
| `ai_werewolf_core/api/routes/events.py` | **新增** | 事件查询路由 |
| `ai_werewolf_core/api/routes/actions.py` | **新增 (Phase 3 补全)** | 玩家操作路由（投票、发言、技能） |
| `ai_werewolf_core/schemas/api.py` | **新增** | API 专有 Pydantic 模型（请求/响应 Schema，与 `models.py` 核心模型分离） |

---

## 架构设计

### FastAPI 应用结构 (main.py)

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Werewolf Game Engine", version="0.1.0")

# CORS: 允许前端开发服务器跨域
app.add_middleware(CORSMiddleware, allow_origins=["*"], ...)

# 挂载路由
app.include_router(games.router, prefix="/api/games", tags=["games"])
app.include_router(players.router, prefix="/api/games", tags=["players"])
app.include_router(events.router, prefix="/api/games", tags=["events"])

# 启动事件: 验证 Redis 和 DB 连接
# 关闭事件: 关闭 DB 引擎 (close_db_engine)
```

### 路由设计

#### P0: 对局生命周期（games.py）

| 方法 | 路径 | 说明 | 输入 | 输出 |
|------|------|------|------|------|
| POST | `/api/games` | 创建对局 | `CreateGameRequest` (player_count, rule_config?) | `CreateGameResponse` (game_id, status) |
| POST | `/api/games/{game_id}/start` | 启动对局 | — | `GameStatusResponse` |
| GET | `/api/games/{game_id}` | 查询对局状态 | — | `GameDetailResponse` (status, phase, round, players) |

#### P1: 阶段推进与玩家查询

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/games/{game_id}/advance` | 推进到下一阶段 |
| GET | `/api/games/{game_id}/players` | 查询玩家列表 |
| GET | `/api/games/{game_id}/players/{player_id}` | 查询单个玩家 |

#### P2: 事件与中止

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/games/{game_id}/events` | 查询事件流（支持 seq_num 分页） |
| POST | `/api/games/{game_id}/abort` | 中止对局 |

### 请求/响应 Schema（schemas/api.py）

与核心 `schemas/models.py` 分离，仅定义 API 层的契约：

```python
class CreateGameRequest(BaseModel):
    player_count: int = Field(ge=6, le=12, default=9)

class CreateGameResponse(BaseModel):
    game_id: str
    status: str

class GameDetailResponse(BaseModel):
    game_id: str
    status: str
    phase: str | None
    round: int
    player_count: int

class PlayerResponse(BaseModel):
    player_id: str
    seat_number: int
    role: str   # API 层暴露角色字符串，不暴露枚举内部值
    is_alive: bool

class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
    game_id: str | None = None
```

### 错误处理模式

统一使用 FastAPI `HTTPException`，在路由层 try/except 特定异常后映射为 HTTP 状态码：

| 异常 | HTTP 状态码 |
|------|-------------|
| `InvalidTransitionError` | 409 Conflict |
| `GameNotRunnableError` | 422 Unprocessable Entity |
| `RedisUnavailableException` | 503 Service Unavailable |
| `ValueError` | 400 Bad Request |
| 未知异常 | 500 Internal Server Error |

使用 `structlog` 记录所有异常，日志格式与项目现有风格一致。

### 依赖注入

- 每个路由端点独立创建 `LifecycleManager(game_id, EventBus())` 实例（无状态，每次创建开销很小）
- DB session 通过 `async with async_session_factory() as session:` 获取
- 不使用 FastAPI `Depends` 模式（`game_id` 通过路径参数传入，不在 DI 容器中管理）

### 关键实现细节

1. **创建对局 (POST /api/games)**:
   - 用 `get_snowflake().next_id()` 生成 game_id
   - 调用 `LifecycleManager.init_game()` 完成 GameRecord INSERT + Redis 初始化
   - 需要分配合并初始化玩家身份（调用 `PlayerStatusManager.init_players()`）
   - 返回 `{game_id, status: "START"}`

2. **启动对局 (POST /api/games/{game_id}/start)**:
   - 直接委托 `LifecycleManager.start_game()`

3. **推进阶段 (POST /api/games/{game_id}/advance)**:
   - 读取当前 phase，按硬编码规则自动推导 next_phase
   - 调用 `LifecycleManager.advance_phase(next_phase)`

4. **查询事件 (GET /api/games/{game_id}/events)**:
   - 调用 `EventBus.get_events(game_id, agent_id, start_seq, count)`
   - 支持 `?since_seq=N` 分页参数

---

## 验证方案

1. **语法检查**: `python -c "import py_compile; ..."` 验证所有新文件
2. **导入检查**: `python -c "from ai_werewolf_core.main import app"` 验证 FastAPI 应用启动
3. **单元测试**: 使用 `pytest` + `httpx.AsyncClient` 测试每个端点：
   - 创建对局 → 断言返回 game_id 和 status
   - 启动对局 → 断言状态变为 RUNNING
   - 查询状态 → 断言返回完整 GameDetail
   - 异常场景 → 断言正确的 HTTP 错误码
4. **手动验证**: `uvicorn main:app --reload` 启动后访问 `/docs` Swagger UI

---

## 实施顺序

1. 创建 `schemas/api.py` — API Schema（无依赖）
2. 创建 `api/__init__.py` 和 `api/routes/__init__.py` — 包初始化
3. 创建 `api/routes/games.py` — 核心对局路由（P0 + P1）
4. 创建 `api/routes/players.py` — 玩家查询路由（P1）
5. 创建 `api/routes/events.py` — 事件查询路由（P2）
6. 重写 `main.py` — 组装所有路由
7. 验证 + 运行现有测试确保无回归
