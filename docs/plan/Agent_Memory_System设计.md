# Agent Memory System 深度架构设计方案

## 1. 架构定位与核心目标

在非对称信息博弈（如狼人杀）中，信息隔离是系统的生命线。Agent Memory System（记忆系统）的核心目标是**彻底解决大模型在多智能体环境下的“信息泄露”与“作弊”问题**。如果所有 Agent 共享同一个上下文窗口，游戏将毫无意义。

### 1.1 核心职责边界
- **双轨记忆隔离**：严格区分“公共记忆（Public Memory）”与“私有记忆（Private Memory）”，在物理存储和逻辑读取层面实现 100% 隔离。
- **上下文组装与裁剪**：将海量的历史事件转化为 LLM 易于理解的自然语言时间线（Timeline），并根据 Token 限制进行智能裁剪（Pruning）或摘要（Summarization）。
- **防幻觉锚定（Grounding）**：将确切的系统反馈（如法官告知的验人结果）作为不可篡改的“绝对记忆”注入，覆盖 LLM 可能产生的逻辑幻觉。

---

## 2. 核心数据结构与 API 契约

### 2.1 记忆快照契约 (Memory Snapshot Schema)

在每次 Agent 被唤醒时，Memory System 会为其生成一份专属的记忆快照，供 Prompt 组装使用。
> **注意**: 所有数据模型已统一收拢至 `ai_werewolf_core/schemas/models.py` 中进行管理。

```python
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any
from ai_werewolf_core.schemas.enums import GamePhase, Role, Faction

class PublicEventLog(BaseModel):
    """单条公共事件日志"""
    seq_num: int = Field(..., description="全局事件序号，保证严格时序")
    phase: GamePhase = Field(..., description="事件发生的游戏阶段")
    description: str = Field(..., description="自然语言描述，如'玩家3发言：我是预言家'")

class PrivateEventLog(BaseModel):
    """单条私有事件日志"""
    seq_num: int = Field(..., description="全局事件序号，保证严格时序")
    round_num: int = Field(default=1, description="轮次编号")
    phase: GamePhase = Field(..., description="事件发生的游戏阶段")
    description: str = Field(..., description="自然语言描述，如'昨晚你查验了3号，他是狼人'")

class PrivateState(BaseModel):
    """Agent 私有状态"""
    role: Role = Field(..., description="真实底牌身份")
    faction: Faction = Field(..., description="所属阵营")
    teammates: List[str] = Field(default_factory=list, description="已知队友ID列表（如狼人队友）")
    skill_status: Dict[str, Any] = Field(default_factory=dict, description="技能状态（如女巫解药是否可用）")

class RoundMemory(BaseModel):
    """单轮记忆 - 包含该轮的公共事件、私有事实和推理"""
    round_num: int = Field(..., description="轮次编号")
    public_events: List[PublicEventLog] = Field(default_factory=list, description="本轮公共事件")
    private_facts: List[PrivateEventLog] = Field(default_factory=list, description="本轮私有事实")
    reasoning: List[str] = Field(default_factory=list, description="本轮推理摘要")

class MemorySnapshot(BaseModel):
    """传递给 LangGraph 的完整记忆快照"""
    agent_id: str
    game_id: str
    private_state: PrivateState = Field(..., description="当前私有状态")
    history: List[RoundMemory] = Field(..., description="记忆轮次列表，按轮次顺序")
```

---

## 3. 类设计与生命周期管理

### 3.1 公共记忆管理器 (`public.py`)

负责从全局事件总线（EventBus）或数据库中重构公共时间线。

```python
import structlog
from ai_werewolf_core.core.event.bus import EventBus

logger = structlog.get_logger(__name__)

class PublicMemoryManager:
    def __init__(self, redis_client, db_session):
        self.redis = redis_client
        self.db = db_session
        self.event_bus = EventBus(redis_client, db_session)

    async def fetch_round_memories(self, game_id: str, max_events: int = 50) -> List[RoundMemory]:
        """
        获取按轮次聚合的公共记忆。
        优先从 Redis Stream (werewolf:events:{game_id}) 读取热数据。
        如果 Redis 缺失或被裁剪，降级查询 PostgreSQL EventRecord 表。
        """
        raw_events = await self.event_bus.get_recent_events(game_id, limit=max_events)
        
        round_dict = {}
        for event in raw_events:
            # 过滤掉非 PUBLIC 可见性的事件
            if event.visibility != Visibility.PUBLIC:
                continue
                
            # 仅保留关键事实：发言、投票、死亡
            if event.event_type not in (EventType.SPEECH_EVENT, EventType.VOTE_EVENT, EventType.PLAYER_DEATH):
                continue
                
            # 将结构化 Event 转换为自然语言描述
            desc = self._format_event_to_nl(event)
            round_num = event.payload.get("round", 1)
            
            if round_num not in round_dict:
                round_dict[round_num] = []
                
            round_dict[round_num].append(PublicEventLog(
                seq_num=event.seq_num,
                phase=event.phase,
                description=desc
            ))
            
        # 组装为 RoundMemory 列表
        return [RoundMemory(round_num=r, public_events=events) for r, events in round_dict.items()]
        
    def _format_event_to_nl(self, event: Event) -> str:
        # 根据 EventType 格式化，例如：
        # EventType.SPEECH -> f"玩家{event.source_player}发言：{event.data['content']}"
        # EventType.VOTE_RESULT -> f"投票结果公布：{event.data['summary']}"
        pass
```

### 3.2 私有记忆管理器 (`private.py`)

负责管理 Agent 的私密状态。为了解决高并发下的数据竞态问题并提升读写性能，Redis 存储结构被拆分为三部分（统一在 `RedisKeys` 中管理）：
1. **基础状态 (Hash)**: `werewolf:memory:private:{game_id}:{player_id}`，存储 `PrivateState` 的基础字段。
2. **系统反馈 (List)**: `werewolf:memory:private:{game_id}:{player_id}:feedbacks`，使用 `RPUSH` 原子追加 `PrivateEventLog`。
3. **历史推理 (List)**: `werewolf:memory:private:{game_id}:{player_id}:reasoning`，使用 `RPUSH` 原子追加内心 OS。

```python
class PrivateMemoryManager:
    async def get_private_state(self, game_id: str, player_id: str, request_agent_id: str) -> PrivateState:
        """
        获取指定玩家的私有状态。
        严格校验 request_agent_id 与 player_id 是否一致，防止越权读取。
        从 Hash 读取基础状态，从 List 读取系统反馈，并组装返回。
        """
        pass

    async def append_system_feedback(self, game_id: str, player_id: str, feedback: PrivateEventLog):
        """
        追加系统私密反馈（如法官告知预言家查验结果）。
        这是防幻觉的核心机制。直接使用 RPUSH 追加到 List，天然保证原子性。
        """
        pass
        
    async def save_reasoning(self, game_id: str, player_id: str, round_num: int, reasoning: str):
        """保存 Agent 的内心 OS，用于后续回合的连贯性。使用 RPUSH 追加到 List。"""
        pass
        
    async def get_private_round_data(self, game_id: str, player_id: str) -> dict[int, dict]:
        """获取按轮次聚合的私有事实和推理。"""
        pass
```

---

## 4. 记忆裁剪与上下文窗口管理 (Memory Pruning)

随着游戏进行到第 3 天或第 4 天，公共发言日志会极其庞大，轻易突破 LLM 的 Context Window 限制。必须引入裁剪机制。

### 4.1 滑动窗口与关键帧策略
- **近期全量保留**：保留最近 2 个 Phase（如昨晚 + 今天白天）的所有详细发言和事件。
- **远期摘要保留（模型压缩）**：对于 2 个 Phase 之前的事件，**不应直接丢弃具体发言内容**。相反，使用轻量级 LLM（如 GPT-3.5‑Turbo、Qwen‑1.5B 本地部署等）作为“记忆压缩器”，将冗长的历史发言提炼为高度浓缩的逻辑摘要（示例：“第1天白天：1号跳预言家查杀2号；3号跟票；4号划水”），并与关键帧（死亡播报、投票结果）合并存储，以保留博弈核心链路。
  > **当前实现状态**: `MemoryPruner` 中已使用 `tiktoken` 实现 Token 计算。轻量级 LLM 压缩功能目前使用 `TODO` 占位，当前降级策略为**直接从后往前截断裁剪**，直到满足 Token 限制。
- **动态 Token 计算与级联压缩**：在组装 `MemorySnapshot` 时，使用 `tiktoken` 估算 Token 数量。如果仍超过阈值（如 6000 tokens），则触发级联压缩逻辑，对更早期的摘要再次使用轻量模型进行二次浓缩，确保上下文窗口始终在安全范围内。

---

## 5. 极端边界条件与应对策略

### 5.1 越权读取尝试 (Privilege Escalation)
**场景**：由于代码 Bug，Agent A 的工作流试图读取 Agent B 的私有记忆。
**应对**：
- 在 `PrivateMemoryManager` 的底层接口中，强制要求传入当前执行上下文的 `player_id`。
- 任何跨 ID 的读取请求直接抛出 `SecurityViolationException`，并触发告警。

### 5.2 记忆不一致与脑裂 (Memory Inconsistency)
**场景**：Redis 发生主从切换或数据丢失，导致 Agent 的私有记忆与 Game Engine 的权威状态不一致（例如 Engine 记录女巫已用解药，但 Agent 记忆中显示未用）。
**应对**：
- **Engine is Authority**：Agent 的私有记忆仅作为“认知缓存”。在每次生成 `MemorySnapshot` 时，`PrivateMemoryManager` 必须与 Game Engine 的权威状态（如 `PlayerManager`）进行一次 Checksum 或全量同步。
- 如果发现不一致，以 Engine 状态为准，强制覆盖 Agent 的私有记忆缓存。

### 5.3 幻觉记忆的污染 (Hallucination Contamination)
**场景**：Agent 在上一轮的 `historical_reasoning` 中产生了幻觉（如“我认为 5 号是预言家，因为他查杀了 6 号”，但实际上 5 号根本没发言）。如果将这段推理存入记忆，会在下一轮继续误导 Agent。
**应对**：
- 引入 **Fact-Checking (事实核查)** 机制。在将 `reasoning` 存入私有记忆前，通过一个轻量级的规则引擎或小模型，校验其是否与 `PublicEventLog` 严重冲突。
- 在 Prompt 中明确指示：“你的历史推理仅供参考，一切以公共事件日志和系统反馈为绝对事实。”

---

## 6. 与其他系统模块的交互与状态流转

1. **与 EventBus 的交互**：
   - `PublicMemoryManager` 是 EventBus 的只读消费者。它不修改任何事件，仅负责查询和格式化。
2. **与 Game Engine 的交互**：
   - Game Engine 在结算夜间行动后（如预言家验人结算完毕），主动调用 `PrivateMemoryManager.append_system_feedback()`，将结果写入对应 Agent 的私有记忆。
3. **与 LangGraph 的交互**：
   - LangGraph 的 `MemoryNode` 负责编排调用 `PublicMemoryManager` 和 `PrivateMemoryManager`，生成完整的 `MemorySnapshot`，并将其注入到 `AgentState` 中，供后续的 `ReasoningNode` 使用。