# WebSocket 实时推送系统设计

## 概述

WebSocket 实时推送系统负责在前端与 Game Engine 之间建立持久化双向通信通道，使客户端能够实时接收对局状态变更和事件通知，无需轮询 REST API。

**核心原则**：
- 作为 EventBus 的全局订阅者，自动接收所有 PUBLIC 事件并推送给已连接的客户端
- 按 `game_id` 分组管理连接池，实现精准路由
- 连接断开自动清理，不影响其他连接
- PRIVATE / FACTION 事件不在 WebSocket 层面处理（由 Agent 自行轮询拉取）

参考：
- [`Event System.md`](../system/Event%20System.md)
- [`Phase 3 FastAPI API.md`](Phase%203%20FastAPI%20API.md)

---

## 架构设计

### 整体数据流

```
Game Engine → EventBus.publish(event)
    │
    ▼
EventBus._global_subscribers
    │
    ├── _default_log_subscriber (结构化日志)
    ├── _persist_to_db (EventRecord 持久化)
    └── ConnectionManager.on_event (WebSocket 推送)  ← 本模块
            │
            ▼
    broadcast_to_game(game_id, payload)
            │
            ▼
    WebSocket 客户端 (前端大屏 / 观战页面)
```

### 模块组成

| 模块 | 文件 | 职责 |
|------|------|------|
| 连接管理器 | `api/ws/manager.py` | 连接池管理、消息广播、EventBus 订阅回调 |
| WebSocket 端点 | `api/ws/routes.py` | 客户端连接入口、心跳保持、断线清理 |
| 应用挂载 | `main.py` | 注册 WebSocket 路由 + 订阅 EventBus |

---

## ConnectionManager 设计

### 连接池结构

```python
_connections: Dict[str, Set[WebSocket]]
# game_id → 该对局所有已连接的 WebSocket 客户端集合
```

### 生命周期

```
客户端连接
    │
    ▼
connect(game_id, ws)
    ├── ws.accept()
    └── _connections[game_id].add(ws)
    
消息循环 (routes.py)
    ├── ws.receive_json()  ← 接收客户端消息（心跳等）
    └── ws.send_json()     ← 发送事件推送
    
客户端断开
    │
    ▼
disconnect(game_id, ws)
    ├── _connections[game_id].discard(ws)
    └── 若集合为空，删除 game_id 条目
```

### 广播机制

`broadcast_to_game(game_id, message, exclude=None)`：
1. 查找该 `game_id` 的所有连接
2. 遍历发送 JSON 消息
3. 遇到断开连接时自动收集到 `dead` 列表
4. 发送完成后统一清理已断开的连接

---

## WebSocket 端点设计

### 连接路径

```
ws://localhost:8000/ws/games/{game_id}
```

### 消息协议

**服务端 → 客户端**：

```json
// 连接确认
{
    "type": "connected",
    "game_id": "1234567890123456789",
    "message": "已连接到对局 xxx 的实时事件推送"
}

// 事件推送
{
    "type": "event",
    "event_id": "uuid",
    "game_id": "1234567890123456789",
    "seq_num": 42,
    "event_type": "PHASE_TRANSITION_EVENT",
    "timestamp": "2026-05-12T10:30:00+00:00",
    "payload": { ... }
}

// 心跳响应
{
    "type": "pong"
}
```

**客户端 → 服务端**：

```json
// 心跳请求
{
    "type": "ping"
}
```

### 心跳机制

- 客户端定期发送 `{"type": "ping"}` 保持连接
- 服务端回复 `{"type": "pong"}`
- 连接断开时自动从连接池移除

---

## EventBus 集成

### 订阅注册

在 `main.py` 启动时注册：

```python
from ai_werewolf_core.api.ws.manager import connection_manager
from ai_werewolf_core.core.event.bus import event_bus

event_bus.subscribe_all(connection_manager.on_event)
```

### 事件过滤

`ConnectionManager.on_event(event)` 仅推送 `Visibility.PUBLIC` 事件：

```python
async def on_event(self, event: Event) -> None:
    if event.visibility != Visibility.PUBLIC:
        return  # PRIVATE / FACTION 事件不推送
    await self.broadcast_to_game(event.game_id, payload)
```

**Why**: PRIVATE 和 FACTION 事件包含非对称信息（如狼人队友身份、预言家查验结果），必须由 Agent 自行通过认证后的 REST API 拉取，不可通过 WebSocket 广播。

---

## 单例模式

`ConnectionManager` 使用模块级单例：

```python
# api/ws/manager.py
connection_manager = ConnectionManager()
```

所有路由和 EventBus 订阅共享同一实例，确保连接池全局唯一。

---

## 文件清单

| 文件 | 说明 |
|------|------|
| `ai_werewolf_core/api/ws/__init__.py` | 包初始化 |
| `ai_werewolf_core/api/ws/manager.py` | ConnectionManager 连接管理器 |
| `ai_werewolf_core/api/ws/routes.py` | WebSocket 路由端点 |
| `ai_werewolf_core/main.py` | 注册 WebSocket 路由 + EventBus 订阅 |
