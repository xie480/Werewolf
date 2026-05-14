# Agent Prompt 与 Game Engine 深度集成技术方案

## 1. 架构概述与核心目标

在当前的狼人杀架构中，**Game Engine** 是绝对的规则裁判，而 **Agent Runtime** 是参与博弈的玩家大脑。本方案旨在设计一套高效、可靠的机制，将现有的分层 Prompt 体系（System, Role, Context, Format）深度嵌入到游戏引擎的核心业务逻辑中。

**核心目标：**
1. **实时上下文同步**：确保 Agent 每次决策都能获取最新、最准确的游戏状态（环境变化、玩家交互、私有反馈）。
2. **无缝主循环对接**：通过事件驱动和异步任务，实现 Engine 状态机与 Agent LangGraph 工作流的解耦与协同。
3. **高可用与低延迟**：解决大模型推理带来的延迟问题，提供完善的异常降级机制，确保游戏主循环永不阻塞。

---

## 2. 动态上下文构建与传递机制

Agent 的决策质量高度依赖于上下文的准确性。我们采用 **“按需快照 (On-Demand Snapshot)”** 机制来传递上下文。

### 2.1 状态数据源
- **公共时间线 (Public Timeline)**：来源于 EventBus 发布的全局事件（如玩家发言、投票结果、天亮死亡名单）。热数据存储于 Redis Stream。
- **私有状态 (Private State)**：来源于 Game Engine 维护的角色状态（如底牌、技能是否可用、昨晚验人结果）。存储于 Redis Hash。
- **阵营信息 (Faction State)**：如狼人视角的队友名单。

### 2.2 传递链路
1. **触发时机**：当 Engine 推进到需要玩家行动的阶段（如 `DAY_DISCUSSION`, `NIGHT_WOLF_ACT`）时。
2. **快照生成**：`MemoryManager` 聚合上述数据源，生成不可变的 `MemorySnapshot` 对象。
3. **Prompt 渲染**：LangGraph 的 `memory_node` 获取快照，传递给 `reasoning_node`，由 `PromptBuilder` 结合 Jinja2 模板动态渲染出最终的 LLM Prompt。

---

## 3. 事件驱动的触发逻辑流

为了不阻塞 FastAPI 主线程和 Engine 的核心逻辑，Engine 与 Agent 之间采用 **Celery 异步任务** 进行解耦交互。

### 3.1 核心交互时序

```text
[Game Engine]                          [Celery Worker (Agent)]                 [LLM Service]
      |                                          |                                   |
      |-- 1. advance_phase(DAY_DISCUSSION)       |                                   |
      |-- 2. 广播 PhaseTransitionEvent  -------->|                                   |
      |-- 3. 启动 Phase Timer (超时兜底)         |-- 4. 监听事件，派发 Agent 任务    |
      |                                          |      (run_agent_decision)         |
      |                                          |                                   |
      |                                          |-- 5. memory_node: 构建快照        |
      |                                          |-- 6. reasoning_node: 构建 Prompt -|-> 7. 请求 LLM
      |                                          |                                 <-|-  返回 JSON
      |                                          |-- 8. validation_node: 校验动作    |
      |                                          |      (若失败则内部重试)           |
      |                                          |                                   |
      |<-- 9. submit_action (API/内部调用) ------|-- 10. 提交最终 Action             |
      |                                          |                                   |
      |-- 11. ActionGate 校验                    |                                   |
      |-- 12. 收集齐所有动作，提前 advance_phase |                                   |
```

---

## 4. 核心接口与伪代码实现

### 4.1 Engine 侧：触发 Agent 任务
在 `PhaseStateMachine` 或专门的 `AgentDispatcher` 中监听阶段变化事件，为存活的 AI 玩家派发任务。

```python
# ai_werewolf_core/tasks/dispatch.py
from ai_werewolf_core.core.event.bus import EventBus
from ai_werewolf_core.schemas.enums import GamePhase
from ai_werewolf_core.tasks.agent_tasks import run_agent_decision

async def on_phase_transition(event: PhaseTransitionEvent):
    game_id = event.game_id
    new_phase = event.new_phase
    
    # 1. 获取当前阶段需要行动的存活玩家
    active_players = await get_active_players_for_phase(game_id, new_phase)
    
    # 2. 为每个 AI 玩家派发 Celery 任务
    for player_id in active_players:
        if is_ai_player(player_id):
            # 异步非阻塞派发
            run_agent_decision.apply_async(
                kwargs={
                    "game_id": game_id,
                    "player_id": player_id,
                    "current_phase": new_phase.value,
                    "current_round": event.round
                }
            )
```

### 4.2 Agent 侧：LangGraph 节点集成 Prompt
在 `reasoning_node` 中，将 `MemorySnapshot` 转化为 Prompt。

```python
# ai_werewolf_core/agents/graph/nodes.py
from ai_werewolf_core.agents.prompts.builder import PromptBuilder
from ai_werewolf_core.agents.adapter.factory import get_model_adapter

async def reasoning_node(state: AgentState) -> Dict[str, Any]:
    snapshot = state["memory_snapshot"]
    validation_errors = state.get("validation_errors", [])
    
    # 1. 动态构建 Prompt
    prompt_builder = PromptBuilder()
    system_prompt, user_prompt = prompt_builder.build_messages(snapshot)
    
    # 如果有之前的校验错误，追加到 Prompt 中强制纠正
    if validation_errors:
        user_prompt += f"\n\n【系统警告】你上一次的输出存在以下错误，请务必修正：\n{validation_errors[-1]}"
        
    # 2. 调用大模型
    adapter = get_model_adapter(state["player_id"])
    response_json = await adapter.agenerate(system_prompt, user_prompt)
    
    # 3. 解析响应
    proposed_action = parse_llm_response(response_json)
    
    return {
        "proposed_action": proposed_action,
        "internal_monologue": response_json.get("internal_monologue", "")
    }
```

### 4.3 Agent 侧：提交动作回 Engine
Celery 任务完成后，调用 Engine 的 `submit_action` 接口。

```python
# ai_werewolf_core/tasks/agent_tasks.py
@shared_task(name="agents.run_agent_decision")
def run_agent_decision(game_id, player_id, current_phase, current_round):
    # 运行 LangGraph
    final_state = asyncio.run(run_agent_workflow(...))
    
    action = final_state.get("proposed_action")
    
    # 提交给 Engine
    if action:
        submit_agent_action.apply_async(kwargs={
            "game_id": game_id,
            "player_id": player_id,
            "action": action
        })
```

---

## 5. 工程化问题评估与解决方案

### 5.1 性能开销与上下文组装延迟
**问题**：随着游戏进行，历史事件不断增加，每次构建 `MemorySnapshot` 和渲染 Prompt 会导致严重的 I/O 延迟和 Token 消耗。
**解决方案**：
1. **Redis 缓存热数据**：`Public Timeline` 必须直接从 Redis Stream 读取，避免查询 PostgreSQL。
2. **记忆修剪 (Memory Pruning)**：引入 `MemoryPruner`。对于早期的白天发言，只保留摘要（Summary）或关键结论（如“1号跳预言家发2号金水”），丢弃冗长的原始对话文本。
3. **Token 限制**：在 `PromptBuilder` 中加入 Token 估算逻辑，若超过模型上下文窗口，强制截断最旧的非关键记忆。

### 5.2 并发与竞态条件
**问题**：多个 Agent 同时完成推理并向 Engine 提交 Action，可能导致状态机错乱或数据覆盖。
**解决方案**：
1. **Engine 侧单点串行化**：Engine 的 `ActionResolver` 和 `VoteManager` 状态更新必须使用 Redis Lua 脚本保证原子性。
2. **无状态 Engine**：GameEngine 实例本身不持有状态，所有状态读写实时穿透到 Redis。

### 5.3 异常降级处理 (Fallback Mechanism)
**问题**：LLM 接口超时、频繁输出不合法 JSON、或输出违反游戏规则的动作，导致游戏卡死。
**解决方案**：
1. **LangGraph 内部重试**：`validation_node` 发现错误后，将错误信息注入状态，路由回 `reasoning_node` 重试（最多 3 次）。
2. **安全降级节点 (Fallback Node)**：重试耗尽后，路由至 `fallback_node`。该节点不调用 LLM，直接根据当前阶段生成合法的默认动作（如：白天强制 `PASS` 或随机投票，夜晚强制 `PASS`）。
3. **Engine 侧全局超时兜底**：Engine 在进入新阶段时会启动 Celery 延迟任务（Phase Timer）。如果 Agent 彻底崩溃未提交动作，Timer 到期后 Engine 会强制结算当前已收集的动作并推进阶段，未提交动作的 Agent 视为“弃权”。

---

## 6. 总结

本方案通过 **Celery 异步任务** 和 **LangGraph 状态机**，实现了 Game Engine 与 Agent Prompt 体系的松耦合、高内聚集成。Engine 专注于规则与状态流转，Agent 专注于基于动态上下文的策略推理。完善的重试与降级机制确保了在 LLM 不稳定情况下的游戏主循环健壮性，满足了实时多智能体博弈的业务要求。