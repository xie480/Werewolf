# Celery 异步任务系统设计

## 概述

Celery 异步任务系统负责执行所有重计算任务，将 Game Engine 的推理负载从 API 进程中分离出来。遵循项目架构规范——**FastAPI is ingress only**，LangGraph Agent 推理、批量结算、评测统计等任务委托给 Celery Worker 异步执行。

**核心原则**：
- Worker 通过 Redis 作为 Broker 接收任务，与 API 进程完全解耦
- 按任务类型分队列路由（game / agent / eval），隔离不同类型任务的资源竞争
- 适配 LangGraph 重推理任务——单任务预取 + 完成后确认
- Worker 崩溃不丢任务——启用 late ack

参考：
- [`Phase 3 FastAPI API.md`](Phase%203%20FastAPI%20API.md)
- [`Agent Runtime.md`](../system/Agent%20Runtime.md)

---

## 架构设计

### 整体拓扑

```
FastAPI (ingress)
    │
    ├── POST /api/games        → 同步处理（状态变更）
    ├── POST /api/games/start  → 同步处理（状态变更）
    └── WebSocket               → 同步处理（事件推送）
    
Celery Worker (重计算)
    │
    ├── game 队列  → 对局生命周期任务（结算、胜负判定、阶段推进）
    ├── agent 队列 → Agent 推理任务（LangGraph 工作流、LLM 调用）
    └── eval 队列  → 评测统计任务（复盘评分、数据分析）
    
Redis (Broker + Backend)
    ├── 任务消息队列
    └── 任务结果存储
```

### 进程模型

```
┌─────────────────────────────────────────┐
│             Redis (Broker)              │
└──────────────┬──────────────────────────┘
               │
    ┌──────────┼──────────┐
    ▼          ▼          ▼
┌───────┐ ┌───────┐ ┌───────┐
│Worker 1│ │Worker 2│ │Worker N│
│game Q │ │agent Q│ │eval Q  │
└───────┘ └───────┘ └───────┘
```

---

## Celery 应用配置

### 基础配置

```python
# worker.py
celery_app = Celery(
    "werewolf_tasks",
    broker=settings.redis_url,    # Redis 作为消息队列
    backend=settings.redis_url,   # Redis 作为结果存储
)
```

### 任务路由

按模块自动分发到不同队列：

```python
task_routes={
    "ai_werewolf_core.tasks.game.*":  {"queue": "game"},
    "ai_werewolf_core.tasks.agent.*": {"queue": "agent"},
    "ai_werewolf_core.tasks.eval.*":  {"queue": "eval"},
}
```

| 队列 | 任务类型 | 特征 |
|------|---------|------|
| `game` | 对局结算、胜负判定、阶段推进 | 高频、轻量、需快速响应 |
| `agent` | LangGraph Agent 推理 | 低频、重量、长时间运行 |
| `eval` | 复盘评分、数据分析 | 离线、批量、可延迟 |

### 序列化

```python
task_serializer="json"
accept_content=["json"]
result_serializer="json"
```

所有任务使用 JSON 序列化，与 Pydantic 模型天然兼容。

---

## 并发与可靠性设计

### 单任务预取

```python
worker_prefetch_multiplier=1  # 每次只取一个任务
```

**Why**: LangGraph Agent 推理是重任务（LLM API 调用 + 图状态管理），单 Worker 同时处理多个推理任务会导致内存压力和 API 速率限制。每次只取一个任务可确保：
- 内存使用可控
- LLM API 调用不过载
- 任务超时更容易排查

### 延迟确认

```python
task_acks_late=True  # 任务完成后才确认
```

**Why**: 如果 Worker 在执行任务时崩溃（如 OOM），消息会重新回到队列由其他 Worker 接管。防止任务丢失。

### 重试策略

```python
task_default_retry_delay=5   # 重试间隔 5 秒
task_max_retries=3           # 最多重试 3 次
```

适用场景：
- LLM API 临时不可用（429 / 503）
- Redis 短暂断连
- DB 连接池耗尽

### 结果过期

```python
result_expires=3600  # 1 小时后自动清理
```

---

## Worker 启动

```bash
# 启动默认 Worker（处理所有队列）
celery -A worker.celery_app worker --loglevel=info

# 按队列启动专用 Worker（生产环境推荐）
celery -A worker.celery_app worker -Q game  --loglevel=info --concurrency=4
celery -A worker.celery_app worker -Q agent --loglevel=info --concurrency=1
celery -A worker.celery_app worker -Q eval  --loglevel=info --concurrency=2
```

| 队列 | 推荐并发数 | 原因 |
|------|-----------|------|
| `game` | 4 | 轻量级计算，可并发处理多局 |
| `agent` | 1 | 重推理任务，单 Worker 处理 |
| `eval` | 2 | 中等计算量 |

---

## 任务模块规划（Phase 3 已实现）

### tasks/game.py

```python
@celery_app.task(queue="game")
async def resolve_night_actions(game_id: str) -> dict:
    """夜晚行动结算任务。"""
    ...

@celery_app.task(queue="game")
async def check_win_condition(game_id: str) -> dict:
    """胜负判定任务。"""
    ...
```

### tasks/agent.py

```python
@celery_app.task(queue="agent")
async def run_agent_inference(game_id: str, player_id: str) -> dict:
    """执行单个 Agent 的 LangGraph 推理。"""
    ...

@celery_app.task(queue="agent")
async def batch_agent_inference(game_id: str) -> dict:
    """批量执行所有存活 Agent 的推理。"""
    ...
```

### tasks/eval.py

```python
@celery_app.task(queue="eval")
async def evaluate_game(game_id: str) -> dict:
    """对局复盘五维评分。"""
    ...
```

---

## 文件清单

| 文件 | 说明 |
|------|------|
| `ai_werewolf_core/worker.py` | Celery 应用配置入口 |
| `ai_werewolf_core/tasks/` | 任务模块目录（Phase 4 实现） |
| `ai_werewolf_core/config.py` | Redis URL 配置（`settings.redis_url`） |
