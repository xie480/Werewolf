**核心结论先行：**
Phase System 绝对不能用线性脚本（比如 `sleep(10)` 然后下一步）来写！它必须是一个**事件驱动的状态机（Event-Driven State Machine）**。每一个 Phase 都有清晰的 `Entry`（进入逻辑）`Action_Window`（行动窗口，允许某些 Agent 调 LLM）、和 `Exit_Condition`（退出条件）。
以下是专为 LangGraph 狼人杀设计的 Phase System 落地方案：
---
### 一、 核心阶段定义 (Phase State Enumeration)
整个游戏的状态扭转必须严格遵循以下阶段字典。建议在代码中定义为一个强类型的状态机：
```python
from enum import Enum
class GamePhase(str, Enum):
    # 准备阶段
    INIT = "INIT"                     # 分发底牌，初始化记忆

    # 夜晚阶段
    NIGHT_START = "NIGHT_START"       # 黑夜降临播报
    NIGHT_ACTION = "NIGHT_ACTION"     # 并发执行：狼人刀、预言家验、女巫救/毒
    NIGHT_RESOLVE = "NIGHT_RESOLVE"   # 系统结算夜间伤亡（不让LLM参与）

    # 白天阶段
    DAY_START = "DAY_START"           # 天亮播报，公布昨夜死者
    DAY_DISCUSSION = "DAY_DISCUSSION" # 依次发言阶段
    DAY_VOTE = "DAY_VOTE"             # 全员投票阶段
    VOTE_RESOLVE = "VOTE_RESOLVE"     # 结算投票，公布出局者

    # 特殊阶段
    LAST_WORDS = "LAST_WORDS"         # 遗言阶段（首夜死者或白天被投出者）
    GAME_OVER = "GAME_OVER"           # 游戏结束，生成复盘
```
---
### 二、 阶段运转逻辑架构 (Phase Lifecycle)
对于每一个 Phase，后端逻辑都必须拆分成三个钩子函数（Hooks）：
#### 1. On_Enter (进入阶段)
*   **动作**：发送系统播报（System Announcement）到公共记忆（Public Ledger）。
*   **举例**：进入 `DAY_START`，系统向全局日志写入：“天亮了。昨晚，3号玩家死亡。”
*   **引擎触发**：在这个钩子中，调用 `Win Condition Evaluator`，检查游戏是否已经因昨夜死人而结束（如狼人全灭）。
#### 2. Process_Window (行动处理窗口)
*   **动作**：唤醒拥有当前阶段行动权的 Agent，触发 LangGraph 子图运行。
*   **模式 A（串行/Sequential）**：如 `DAY_DISCUSSION`。引擎按照特定的顺序（如死者左手边开始），依次唤醒 Agent_1 -> Agent_2，前一个发言落库后，再给下一个 Agent 构建 Context。
*   **模式 B（并发/Concurrent）**：如 `NIGHT_ACTION` 或 `DAY_VOTE`。引擎同时唤醒所有存活/有技能的 Agent，使用异步任务`asyncio.gather`）同时请求 LLM。
#### 3. Exit_Condition (流转检查器)
*   **动作**：判定是否满足离开当前 Phase 的条件。
*   **举例**：
    *   在 `DAY_VOTE` 中，Exit Condition 是`len(received_votes) == len(alive_players)`（所有存活玩家都提交了合法选票）。
    *   在 `DAY_DISCUSSION` 中，Exit Condition 是：所有在当前队列里的玩家都已经完成了发言 Action。
---
### 三、 经典流转路径图 (State Transition Graph)
你可以直接把这段路径用在架构文档或设计图中：
```text
INIT
│
├──▶ NIGHT_START ──▶ NIGHT_ACTION ──▶ NIGHT_RESOLVE
│                                        │
│                                        ▼
│    ┌───────────────────────────────────┘
│    │
│    ▼
└──▶ DAY_START ──▶ (检查是否首夜双死? 若是则触发 LAST_WORDS)
        │
        ▼
      DAY_DISCUSSION (顺序发言)
        │
        ▼
      DAY_VOTE (并发投票) ──▶ VOTE_RESOLVE
        │                       │
        │                       ▼
        │                 (有人出局? 触发 LAST_WORDS)
        │                       │
        ▼                       ▼
      (检查胜利条件) ◀──────────┘
        │
        ├─▶ [满足条件] ──▶ GAME_OVER
        │
        └─▶ [未满足]   ──▶ NIGHT_START (进入下一轮循环)
```
---
### 四、 核心难点方案：死锁防范与并发调度
多智能体系统在 Phase 调度时最容易出现的 Bug 就是**游戏卡死（Deadlock）**。
#### 1. 投票平局死锁 (Tie Vote Deadlock)
*   **场景**`DAY_VOTE` 阶段，3号和5号各得 3 票，平局。
*   **Phase 介入方案**：
    1.  当 `VOTE_RESOLVE` 发现平局时，**不要推进到夜晚**。
    2.  动态插入一个子状态`DAY_PK_DISCUSSION`（PK发言）。
    3.  仅唤醒 3号和5号 进行一轮发言（更新发言 Prompt：“你正在与X号进行PK，请拉票”）。
    4.  再动态插入`DAY_PK_VOTE`，除 3/5 号外的人再次投票。若再次平局，则今日无人出局，直接强制切入 `NIGHT_START`。
#### 2. Agent 离线/超时死锁 (Timeout Blocking)
*   **场景**：并发 `NIGHT_ACTION` 时，某个 Agent 的大模型接口一直转圈，或者内部 LangGraph 持续报错重试，导致整个 Phase 无法退出。
*   **Phase 介入方案**：
    在 `Process_Window` 中引入**严格超时与默认流转机制**。
    ```python
    # 伪代码：带超时的并发调度
    try:
        actions = await asyncio.wait_for(
            asyncio.gather(*[run_agent_workflow(agent) for agent in active_agents]),
            timeout=120.0 # 强制 120 秒超时
        )
    except TimeoutError:
        # 捕捉超时，强制未返回的 Agent 执行 Fallback 动作（如空过/弃权）
        force_fallback_for_timeout_agents()

    # 无论如何，推入下一阶段
    transition_to(NextPhase)
    ```
---
### 五、 LangGraph 主控图与子图设计建议
在 LangGraph 中，你可以这样划分边界：
1. **主控图 (Master Graph / Game Engine Graph)**：
   管理全局 Phase 的流转。Node 就是 `NightPhaseNode`, `DayDiscussionNode`。这个大图**不包含**任何 LLM 调用，纯规则引擎。

2. **子图 (Agent Workflow Graph)**：
   在主控图的节点执行时（如进入 `DayDiscussionNode` 时），主控图通过 `SubGraph` 或外部函数调用，按需拉起对应 Agent 的执行图（即我们在第二步设计的 Agent Runtime 图），拿到结果后销毁子图，主控图继续推进。