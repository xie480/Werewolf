# 事件总线 (Event Bus) 架构设计与实施计划

## 1. 架构概述 (Architecture Overview)

根据 `docs/agent.md` 和 `docs/system/Event System.md` 的规范，AI 狼人杀系统采用 **事件溯源 (Event Sourcing)** 架构。
**Event Bus (事件总线)** 是整个系统的“神经中枢”。所有的状态变化（如阶段切换、玩家死亡、发言、投票）都必须封装为 `Event` 对象，通过 Event Bus 进行分发和记录。

**核心原则：**
*   **不可篡改的事实**：Event 代表已经发生的既定事实（Fact），一旦产生不可修改。
*   **发布/订阅模式 (Pub/Sub)**：Game Engine 作为生产者发布事件，Memory、WebSocket、Logger 等作为消费者订阅事件。
*   **严格的可见性隔离**：必须根据 `Visibility` (PUBLIC, PRIVATE, FACTION) 严格控制事件的路由，防止信息泄露（防剧透）。
*   **严格的时序保证**：每个事件必须拥有全局单调递增的 `seq_num`，防止并发导致的时序错乱和 AI 幻觉。

## 2. 核心组件设计 (Core Components)

### 2.1 Event 模型 (已在 `schemas/models.py` 中定义)
复用现有的 `Event` Pydantic 模型，确保强类型校验。
包含 `event_id`, `game_id`, `seq_num`, `event_type`, `visibility`, `target_agents`, `timestamp`, `payload`。

### 2.2 EventBus 类 (核心调度器)
位于 `ai_werewolf_core/core/event/bus.py`。
*   **职责**：接收事件、分配 `seq_num`、持久化（Phase 1 先用内存/日志，后续接 DB）、路由分发给订阅者。
*   **核心方法**：
    *   `publish(event: Event)`: 发布事件。
    *   `subscribe(event_type: EventType, handler: Callable)`: 订阅特定类型的事件。
    *   `subscribe_all(handler: Callable)`: 订阅所有事件（如全局 Logger 或 Replay 系统）。
    *   `get_events(game_id: str, agent_id: str)`: 为 Agent 提供按权限拉取历史事件的接口（内置可见性过滤）。

### 2.3 Sequence Generator (序列号生成器)
 Redis 的 `INCR` 命令，以支持分布式和持久化。

## 3. 可见性路由策略 (Visibility Routing)

Event Bus 在分发事件或响应拉取请求时，必须执行以下过滤逻辑：

*   **PUBLIC (公开)**：分发给所有订阅者；任何 Agent 均可拉取。
*   **PRIVATE (私密)**：仅分发给 `target_agents` 列表中的 Agent；仅允许 `target_agents` 拉取。
*   **FACTION (阵营)**：仅分发给属于特定阵营的 Agent（需结合 GameState 判断）；仅允许该阵营 Agent 拉取。

## 4. Phase 1 实施步骤 (Implementation Steps)

为了快速解耦并推进 Game Engine 的开发，Phase 1 的 Event Bus 采取**轻量级、内存优先**的策略。

1.  **定义接口与基础结构**：创建 `bus.py`，实现 `EventBus` 类的单例模式或依赖注入结构。
2.  **实现内存存储与 SeqNum 分配**：使用 Python 的 `list` 或 `dict` 在内存中暂存当前对局的 Event，并实现简单的递增计数器。
3.  **实现 Pub/Sub 机制**：使用简单的回调函数列表实现订阅和发布。
4.  **集成结构化日志**：编写一个默认的 Subscriber，将所有流经 Event Bus 的事件通过 `structlog` 输出为 JSON 日志，方便调试。
5.  **编写单元测试**：验证时序递增、可见性过滤和基本的 Pub/Sub 功能。
