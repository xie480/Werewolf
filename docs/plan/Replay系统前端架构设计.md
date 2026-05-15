# Replay 回放系统 (前端) 架构设计

## 1. 概述

本设计文档旨在规范 AI 狼人杀平台 Replay 回放系统前端的架构与实现。回放系统前端本质上是一个“事件播放器（Event Player）”，它接收后端按时间序列打包好的事件数据，通过本地状态机（Reducer）逐帧计算游戏状态，并驱动 UI 渲染，实现类似视频播放的观战体验。

## 2. 核心需求

1.  **多视角支持**：支持上帝视角（GOD）和第一人称视角（POV），根据后端下发的数据渲染不同的可见信息。
2.  **播放控制**：支持播放、暂停、快进（倍速）、拖拽进度条（跳转到指定事件帧）。
3.  **状态归约（Reducer）**：能够根据初始状态和事件流，快速计算出任意时刻（`seq_id`）的全局游戏状态。
4.  **内心戏（OS）透视**：在上帝视角或自身 POV 视角下，展示 AI Agent 的内部推理过程（Belief State / Chain of Thought）。
5.  **动画与交互**：平滑展示发言打字机效果、死亡动画、技能使用特效等。

## 3. 架构设计

前端回放系统采用 **单向数据流** 和 **状态驱动** 的架构，核心分为三层：

### 3.1 数据层 (Data Layer)
*   **API Client**: 负责调用 `GET /api/v1/games/{game_id}/replay` 接口获取回放数据。
*   **Data Store (Pinia)**: 存储原始回放数据（`initial_state`, `timeline`）和当前播放状态（`current_seq_id`, `is_playing`, `playback_speed`）。

### 3.2 逻辑层 (Logic Layer)
*   **Event Reducer**: 纯函数，接收 `initial_state` 和 `events` 数组，以及目标 `seq_id`，计算并返回该时刻的完整游戏状态（如玩家存活状态、当前阶段、历史发言等）。
*   **Player Engine**: 播放控制器，内部维护一个 `requestAnimationFrame` 或 `setInterval` 定时器。负责按设定的倍速推进 `current_seq_id`，并触发 Reducer 重新计算状态。

### 3.3 视图层 (View Layer)
*   **Timeline Component**: 渲染按天/阶段划分的进度条，支持点击跳转。
*   **Game Board Component**: 根据 Reducer 计算出的当前状态，渲染玩家座位、存活状态、角色底牌（视视角而定）。
*   **Event Log / Chat Component**: 渲染当前及历史发言、系统公告。
*   **Inner OS Panel**: 侧边栏或悬浮窗，展示当前发言/行动 Agent 的内部推理日志。

## 4. 核心模块实现方案

### 4.1 状态归约器 (Event Reducer)

Reducer 是回放系统的核心，必须保证高效且无副作用。

```typescript
// 状态定义
interface GameState {
  players: Record<string, PlayerState>; // 玩家状态字典
  currentDay: number;
  currentPhase: string;
  chatHistory: Event[]; // 历史消息记录
  // ... 其他全局状态
}

// Reducer 函数
function calculateGameState(
  initialState: ReplayInitialState,
  timeline: ReplayDayChunk[],
  targetSeqId: number
): GameState {
  // 1. 深拷贝初始状态
  let state = cloneDeep(initialState);
  
  // 2. 扁平化 timeline 提取所有事件
  const allEvents = flattenTimeline(timeline);
  
  // 3. 遍历事件，应用状态变更
  for (const event of allEvents) {
    if (event.seq_id > targetSeqId) break;
    
    // 根据事件类型更新状态
    applyEventToState(state, event);
  }
  
  return state;
}

function applyEventToState(state: GameState, event: Event) {
  switch (event.type) {
    case 'DEATH':
    case 'VOTED_OUT':
      state.players[event.target].isAlive = false;
      break;
    case 'PHASE_TRANSITION':
      state.currentDay = event.payload.round;
      state.currentPhase = event.payload.new_phase;
      break;
    case 'SPEECH':
    case 'SYSTEM_ANNO':
      state.chatHistory.push(event);
      break;
    // ... 处理其他事件
  }
}
```

### 4.2 播放器引擎 (Player Engine)

负责控制时间轴的推进。

```typescript
class ReplayEngine {
  private timer: number | null = null;
  private currentSeqIndex = 0;
  private allEvents: Event[] = [];
  
  constructor(private store: useReplayStore) {}

  play() {
    if (this.timer) return;
    this.timer = setInterval(() => {
      this.stepForward();
    }, 1000 / this.store.playbackSpeed); // 根据倍速调整间隔
  }

  pause() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  seek(seqId: number) {
    this.store.currentSeqId = seqId;
    // 触发视图更新
  }

  private stepForward() {
    // 找到下一个事件的 seq_id 并更新
    // 如果到达末尾则停止
  }
}
```

### 4.3 内部 OS (内心戏) 透视展示

*   在 `SPEECH` 或 `ACTION` 事件的 `payload` 中提取 `inner_thought` 字段。
*   UI 上，当播放到该事件时，除了在主聊天区域显示公开的发言内容，同时在专属的 **Inner OS Panel** 中以打字机效果或高亮形式展示其内部推理过程。
*   这能极大增强观赏性，让观众理解 AI "为什么这么做"。

## 5. 性能优化与防坑指南

1.  **Reducer 性能优化 (Memoization)**：
    *   如果用户频繁拖拽进度条，每次都从头计算可能会卡顿。
    *   **优化方案**：可以缓存关键帧（Keyframes）。例如，每 50 个事件或每一天结束时，缓存一次完整的 `GameState`。当跳转到 `seq_id = 120` 时，只需从 `seq_id = 100` 的缓存状态开始计算，减少循环次数。
2.  **长文本渲染卡顿**：
    *   AI 发言可能很长，打字机动画如果按固定时间间隔播放，会导致回放拖沓。
    *   **优化方案**：打字机速度应与 `playbackSpeed` 挂钩。提供“跳过动画”按钮，点击直接显示完整文本。在极高倍速（如 4x）下，自动关闭打字机动画。
3.  **时序依赖**：
    *   前端严格依赖后端下发的 `seq_id` 顺序。如果遇到乱序，Reducer 可能会产生非法状态（如死人发言）。前端在接收数据后，可进行一次防御性的排序校验。

## 6. 实施步骤 (Todo)

1.  **Store 定义**：在 `frontend/src/store/` 下创建 `replay.ts`，定义回放状态和 Reducer 逻辑。
2.  **API 封装**：在 `frontend/src/api/` 下新增 `replay.ts`，封装获取回放数据的接口。
3.  **组件开发**：
    *   开发 `ReplayTimeline.vue` (进度条与控制面板)。
    *   开发 `ReplayBoard.vue` (复用或改造现有的 GameBoard，使其支持只读和状态注入)。
    *   开发 `InnerOSPanel.vue` (内心戏展示组件)。
4.  **视图集成**：创建 `ReplayView.vue`，整合上述组件，并实例化 `ReplayEngine`。
5.  **动画与交互调优**：实现打字机效果、倍速切换、进度拖拽等细节功能。
