# Replay 回放系统 (后端) 架构设计

## 1. 概述

本设计文档旨在规范 AI 狼人杀平台 Replay 回放系统后端接口的架构与实现。回放系统本质上是一个“事件播放器”，后端负责提供按时间序列排序、按“天数/阶段”打包（Chunking）且经过视角过滤（Perspective Control）的结构化事件数据。

## 2. 核心需求

1.  **视角控制 (Perspective Control)**：
    *   **上帝视角 (GOD)**：无视可见性限制，拉取对局所有事件（包括所有私密行动、狼人夜间交流等）。
    *   **第一人称视角 (POV)**：以特定玩家（Agent）的视角拉取事件，仅包含公共事件（PUBLIC）以及该玩家有权查看的私密/阵营事件（PRIVATE/FACTION）。
2.  **数据打包 (Chunking)**：
    *   将扁平的事件流按“天 (Day)”和“阶段 (Phase)”进行层级组装，方便前端渲染时间轴（Timeline）和章节划分。
3.  **初始状态提供**：
    *   提供对局的初始状态（如玩家列表、座位号、初始角色分配），作为前端状态归约器（Reducer）的基态。

## 3. API 接口设计

### 3.1 接口定义

**Endpoint**: `GET /api/v1/games/{game_id}/replay`

**Query Parameters**:
*   `perspective` (str, required): 视角模式，可选值为 `GOD` 或 `POV`。
*   `agent_id` (str, optional): 当 `perspective=POV` 时必填，指定第一人称视角的玩家 ID。

### 3.2 响应数据结构 (Schema)

```python
class ReplayPlayerInfo(BaseModel):
    agent_id: str
    seat_number: int
    role: str  # 初始角色，POV视角下非己方角色可能为 "UNKNOWN"

class ReplayInitialState(BaseModel):
    players: List[ReplayPlayerInfo]

class ReplayPhaseChunk(BaseModel):
    phase_name: str  # 如 "NIGHT_WOLF_ACT", "DAY_DISCUSSION"
    events: List[EventResponse]

class ReplayDayChunk(BaseModel):
    day_num: int
    phases: List[ReplayPhaseChunk]

class ReplayResponse(BaseModel):
    game_id: str
    perspective: str
    agent_id: Optional[str] = None
    initial_state: ReplayInitialState
    timeline: List[ReplayDayChunk]
```

## 4. 核心逻辑实现方案

### 4.1 视角过滤 (Perspective Control)

目前 `EventBus.get_events` 已经实现了基于 `agent_id` 的基础过滤逻辑。为了支持上帝视角，需要对 `EventBus` 或查询逻辑进行扩展：

*   **方案 A (推荐)**：在 `EventBus.get_events` 中增加 `is_god_mode: bool = False` 参数。当为 `True` 时，跳过可见性检查，直接返回所有事件。
*   **方案 B**：在 Replay 路由层直接调用 `EventBus._xrange_events` 和 `EventBus._query_db_events` 获取全量数据，自行处理过滤。

**POV 视角过滤规则**：
*   `visibility == PUBLIC` -> 保留
*   `visibility in (PRIVATE, FACTION)` 且 `agent_id in target_agents` -> 保留
*   其他 -> 剔除

### 4.2 数据打包逻辑 (Chunking Algorithm)

由于底层的 Event Sourcing 是一个扁平的事件流，后端需要在内存中对其进行一次 O(N) 的遍历组装。

**组装状态机**：
1.  初始化 `current_day = 0`, `current_phase = "INIT"`, `timeline = []`。
2.  遍历过滤后的事件流 `events`。
3.  监听 `PHASE_TRANSITION_EVENT`：
    *   当遇到阶段切换事件时，从 `payload` 中提取 `round` (天数) 和 `new_phase` (新阶段)。
    *   如果 `round` 发生变化（例如从 0 变 1，或从 1 变 2），则创建新的 `ReplayDayChunk`。
    *   创建新的 `ReplayPhaseChunk` 并挂载到当前的 `ReplayDayChunk` 下。
4.  对于非阶段切换事件：
    *   将其追加到当前活跃的 `ReplayPhaseChunk.events` 列表中。

**特殊处理**：
*   对局刚开始时（INIT 阶段），可能没有明确的 `PHASE_TRANSITION_EVENT`，需要根据第一个事件的默认状态初始化 Chunk。
*   确保空阶段（没有事件发生的阶段）也能被正确记录或合理忽略（建议保留空阶段以维持时间轴完整性）。

### 4.3 初始状态获取

*   从 `PlayerRecord` 数据库表或 Redis 玩家状态缓存中拉取该 `game_id` 下的所有玩家信息。
*   提取 `player_id`, `seat_number`, `role` 组装成 `ReplayInitialState`。
*   **安全要求**：为了绝对的数据安全，**POV 模式下的 `initial_state` 必须对其他玩家的角色进行掩码（设为 "UNKNOWN"）**，仅保留自己的角色。上帝视角则下发全量真实角色。

## 5. 内部 OS (内心戏) 透视支持

*   Agent 的内部推理（Belief State / Chain of Thought）在生成动作时，应作为 `inner_thought` 字段附加在 `SPEECH_EVENT` 或 `VOTE_EVENT` 的 `payload` 中。
*   这些事件的可见性通常是 `PUBLIC`（因为发言和投票是公开的）。
*   **安全要求**：在 POV 模式下，如果事件的 `actor` 不是当前 `agent_id`，后端在下发前**必须**将 `payload` 中的 `inner_thought` 字段剔除，防止玩家通过抓包作弊。在 GOD 模式下则全量保留。

## 6. 性能与降级策略

1.  **缓存机制**：对于已结束（`FINISHED` / `ABORTED`）的对局，其回放数据是不可变的。可以在首次请求组装完成后，将整个 `ReplayResponse` JSON 序列化缓存到 Redis 中（设置较长 TTL），后续请求直接命中缓存，避免重复组装。
2.  **分页与全量**：回放通常需要全量数据以保证前端 Reducer 计算正确。如果单局事件极多（>1000），可考虑在 Chunking 时按 Day 进行分页拉取（`GET /replay?day=1`），但目前狼人杀单局事件量可控，建议初期实现**单次全量下发**。

## 7. 实施步骤 (Todo)

1.  **Schema 定义**：在 `ai_werewolf_core/schemas/api.py` 中新增 Replay 相关的响应模型。
2.  **EventBus 改造**：修改 `EventBus.get_events`，增加 `is_god_mode` 参数支持上帝视角全量拉取。
3.  **路由实现**：在 `ai_werewolf_core/api/routes/games.py` (或新建 `replay.py`) 中实现 `GET /{game_id}/replay` 接口。
4.  **组装逻辑**：编写 Chunking 算法，处理 `PHASE_TRANSITION_EVENT` 的解析和层级嵌套。
5.  **安全过滤**：实现 POV 模式下的初始角色掩码和 `inner_thought` 剔除逻辑。
6.  **单元测试**：编写针对 Chunking 逻辑和视角过滤的单元测试。
