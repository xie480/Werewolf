# Agent LangGraph 工作流深度架构设计方案

## 1. 架构定位与核心目标

在 AI 狼人杀架构中，LangGraph 并不用于编排全局游戏流程（全局流程由 Game Engine 的状态机掌控），而是作为**单个 Agent 内部的“认知-决策微工作流（Micro-Workflow）”**。其核心目标是将 Agent 的思考过程拆解为可观测、可干预、可重试的离散节点，确保最终输出的动作既符合逻辑又符合系统规范。

### 1.1 核心职责边界
- **状态流转管理**：维护单次推理任务（如“白天发言决策”或“夜间刀人决策”）的内部状态（`AgentState`）。
- **管线编排**：按顺序执行感知（Perception）、记忆（Memory）、推理（Reasoning）、行动（Action）和校验（Validation）节点。
- **闭环重试与自愈**：通过条件边（Conditional Edges）处理 LLM 输出格式错误或业务规则校验失败，实现自动重试。
- **安全降级**：在重试次数耗尽时，强制路由至 Fallback 节点，生成安全默认动作，防止阻塞全局游戏。

---

## 2. 核心数据结构与状态定义

### 2.1 AgentState 契约 (TypedDict)

`AgentState` 是在 LangGraph 各个节点之间传递的唯一数据结构。它必须包含单次推理所需的所有上下文和中间结果。

```python
from typing import TypedDict, List, Dict, Optional, Any
from ai_werewolf_core.schemas.enums import GamePhase

class AgentState(TypedDict):
    # --- 基础上下文 (由 Engine 传入) ---
    game_id: str
    player_id: str
    current_phase: GamePhase
    current_round: int
    
    # --- 记忆与感知 (由 MemoryNode 填充) ---
    memory_snapshot: Optional[Any]  # MemorySnapshot 实例
    
    # --- 推理与决策 (由 ReasoningNode 填充) ---
    raw_llm_response: str           # LLM 原始返回文本
    internal_monologue: str         # 解析出的内心 OS
    suspect_list: Dict[str, float]  # 嫌疑热力图
    
    # --- 最终输出 (由 ActionNode 填充) ---
    proposed_action: Optional[Dict] # 拟提交的动作字典
    
    # --- 控制流与重试状态 (由 ValidationNode 维护) ---
    retry_count: int                # 当前重试次数
    max_retries: int                # 最大允许重试次数
    validation_errors: List[str]    # 校验失败的错误信息列表，用于反馈给 LLM
    is_valid: bool                  # 动作是否合法
```

---

## 3. 核心节点设计与实现逻辑

### 3.1 感知与记忆节点 (`memory_node`)
**职责**：调用 Memory System，获取当前 Agent 的公共和私有记忆，生成 `MemorySnapshot`。
```python
async def memory_node(state: AgentState) -> AgentState:
    # 调用 PublicMemoryManager 和 PrivateMemoryManager
    snapshot = await memory_system.build_snapshot(state["game_id"], state["player_id"])
    return {"memory_snapshot": snapshot}
```

### 3.2 推理节点 (`reasoning_node`)
**职责**：调用 Prompt Builder 组装提示词，调用 Model Adapter 获取 LLM 响应。
```python
async def reasoning_node(state: AgentState) -> AgentState:
    prompt = prompt_builder.build(state["memory_snapshot"], state["validation_errors"])
    
    # 调用 Model Adapter
    response = await model_adapter.agenerate(prompt)
    
    if response.is_success:
        return {
            "raw_llm_response": response.raw_content,
            "internal_monologue": response.parsed_data.internal_monologue,
            "suspect_list": response.parsed_data.suspect_list,
            "proposed_action": response.parsed_data.action_dict
        }
    else:
        # 解析失败，直接记录错误，交由后续节点处理
        return {
            "validation_errors": state.get("validation_errors", []) + [response.error_message],
            "is_valid": False
        }
```

### 3.3 校验节点 (`validation_node`)
**职责**：对 `proposed_action` 进行严格的 Schema 校验和基础业务规则校验（如目标玩家是否存活）。
```python
async def validation_node(state: AgentState) -> AgentState:
    proposed_action = state.get("proposed_action")
    if not proposed_action:
        return {"is_valid": False, "retry_count": state.get("retry_count", 0) + 1}
        
    errors = []
    action_obj = None
    
    # 1. Schema 校验 (Pydantic 强类型校验)
    try:
        action_obj = AgentAction(**proposed_action)
    except Exception as e:
        errors.append(f"Schema validation error: {str(e)}")
        
    # 2. 基础业务校验 (调用 Engine 的只读基础校验接口)
    if not errors and action_obj:
        try:
            result = await ActionValidator.validate_basic(action_obj, state["game_id"])
            if not result.is_valid:
                errors.append(f"Business validation error: {result.reason}")
        except Exception as e:
            errors.append(f"Business validation error: {str(e)}")
            
    if errors:
        return {
            "is_valid": False,
            "validation_errors": state.get("validation_errors", []) + errors,
            "retry_count": state.get("retry_count", 0) + 1
        }
        
    return {"is_valid": True, "validation_errors": [], "retry_count": state.get("retry_count", 0)}
```

### 3.4 降级节点 (`fallback_node`)
**职责**：当重试次数耗尽时，生成安全的默认动作。
```python
async def fallback_node(state: AgentState) -> AgentState:
    logger.error("agent_fallback_triggered", player_id=state["player_id"], errors=state.get("validation_errors", []))
    
    # 根据当前阶段和轮次生成完全符合 AgentAction Schema 的安全默认动作
    default_action = generate_safe_default_action(
        state["current_phase"],
        state.get("current_round", 1),
        state["player_id"]
    )
    
    return {
        "proposed_action": default_action,
        "is_valid": True,
        "internal_monologue": "系统强制接管：重试次数耗尽，执行默认动作。",
        "validation_errors": []
    }
```

---

## 4. 图编译与路由逻辑 (Graph Compilation)

使用 LangGraph 的 `StateGraph` 将节点串联，并定义条件边（Conditional Edges）实现重试循环。

```python
from langgraph.graph import StateGraph, END

# 节点名称常量
NODE_MEMORY = "memory"
NODE_REASONING = "reasoning"
NODE_VALIDATION = "validation"
NODE_FALLBACK = "fallback"

def build_agent_graph():
    workflow = StateGraph(AgentState)
    
    # 添加节点
    workflow.add_node(NODE_MEMORY, memory_node)
    workflow.add_node(NODE_REASONING, reasoning_node)
    workflow.add_node(NODE_VALIDATION, validation_node)
    workflow.add_node(NODE_FALLBACK, fallback_node)
    
    # 定义主流程边
    workflow.set_entry_point(NODE_MEMORY)
    workflow.add_edge(NODE_MEMORY, NODE_REASONING)
    workflow.add_edge(NODE_REASONING, NODE_VALIDATION)
    
    # 定义条件路由逻辑
    def route_after_validation(state: AgentState) -> str:
        if state.get("is_valid"):
            return END  # 校验通过，结束工作流
        if state.get("retry_count", 0) >= state.get("max_retries", 3):
            return NODE_FALLBACK  # 重试耗尽，进入降级
        return NODE_REASONING  # 继续重试
        
    # 添加条件边
    workflow.add_conditional_edges(
        NODE_VALIDATION,
        route_after_validation,
        {
            END: END,
            NODE_FALLBACK: NODE_FALLBACK,
            NODE_REASONING: NODE_REASONING
        }
    )
    
    workflow.add_edge(NODE_FALLBACK, END)
    
    return workflow.compile()
```

---

## 5. 极端边界条件与应对策略

### 5.1 死循环与无限重试 (Infinite Loop)
**场景**：LLM 固执地输出同一种错误的格式或非法的动作，导致 `validation_node` 反复报错。
**应对**：
- 状态机中严格维护 `retry_count` 和 `max_retries`（通常设为 3）。
- `route_after_validation` 必须无条件遵守 `max_retries` 限制，一旦触达阈值，强制切入 `fallback_node`，从架构层面杜绝死循环。

### 5.2 节点执行超时 (Node Timeout)
**场景**：`reasoning_node` 调用 LLM API 时发生长时间阻塞。
**应对**：
- LangGraph 本身不提供节点级别的硬超时机制。必须在 `reasoning_node` 内部调用 Model Adapter 时，使用 `asyncio.wait_for` 包装。
- 捕获 `TimeoutError` 后，将其视为一种特殊的 Validation Error，增加 `retry_count` 并触发重试，或直接路由至 `fallback`。

### 5.3 状态污染 (State Contamination)
**场景**：在重试循环中，旧的错误状态（如上一次的 `proposed_action`）未被清理，影响了下一次判断。
**应对**：
- 在 LangGraph 中，节点返回的字典会与当前状态进行 Merge（合并）。
- 在 `reasoning_node` 开始时，必须显式覆盖或清空上一轮的 `proposed_action`，确保每次推理都是干净的。

---

## 6. 与其他系统模块的交互与状态流转

### 6.1 与 Celery Worker 的交互 (`agent_tasks.py`)
- **任务收口**：废弃了早期的 `agent.py` 占位文件，所有 Agent 相关的 Celery 任务统一收口至 `ai_werewolf_core/tasks/agent_tasks.py`。
- **异步桥接**：Celery 任务本身是同步的，而 LangGraph 节点是异步的。在 `run_agent_decision` 任务中，通过 `asyncio.run(graph.ainvoke(initial_state))` 桥接执行异步工作流。
- **动作提交**：工作流执行完毕后，由独立的 `submit_agent_action` Celery 任务负责将最终动作提交给 Game Engine。

### 6.2 与 Game Engine 的交互及终极兜底策略
- **只读基础校验**：LangGraph 内部的 `validation_node` 仅调用 `ActionValidator.validate_basic` 进行轻量级的结构和存活状态校验，**故意跳过冷却校验**，以允许 Agent 内部高频重试而不触发防刷机制。
- **API 层复用**：`submit_agent_action` 任务内部复用了 API 层的 `submit_action_internal` 逻辑，确保动作提交经过完整的“铁面裁判”校验（包含阶段、权限、冷却等）。
- **终极兜底机制 (Ultimate Fallback)**：
  如果在 `submit_agent_action` 阶段动作被 Engine 严格拒绝（如并发导致状态变化），或者发生任何未捕获异常，任务会触发终极兜底逻辑：
  1. 捕获异常或拒绝结果。
  2. 再次调用 `generate_safe_default_action` 构造一个绝对安全的默认动作（如 `PASS` 或 `SPEAK`）。
  3. 强制将该兜底动作提交给 Engine。
  4. 确保 Agent 不会因为非法动作而“挂机”，防止游戏流程卡死。

## 7. 测试与质量保证

为确保 LangGraph 工作流的健壮性，系统包含完整的单元测试与集成测试覆盖：
- **节点单元测试 (`test_nodes.py`)**：独立测试 `memory_node`、`reasoning_node`、`validation_node`（覆盖成功、业务校验失败、Schema 校验失败等分支）以及 `fallback_node` 的逻辑。
- **图集成测试 (`test_graph.py`)**：测试 `route_after_validation` 的条件路由逻辑，以及 `run_agent_workflow` 的完整执行路径（包括正常通过路径和重试耗尽触发降级的路径）。