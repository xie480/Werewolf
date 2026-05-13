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
    if not state.get("proposed_action"):
        return {"is_valid": False}
        
    errors = []
    try:
        # 1. Schema 校验
        action_obj = AgentAction(**state["proposed_action"])
        
        # 2. 基础业务校验 (调用 Engine 的 Validator)
        await action_validator.validate_basic(action_obj, state["game_id"])
        
        return {"is_valid": True, "validation_errors": []}
    except Exception as e:
        errors.append(str(e))
        return {
            "is_valid": False,
            "validation_errors": state.get("validation_errors", []) + errors,
            "retry_count": state.get("retry_count", 0) + 1
        }
```

### 3.4 降级节点 (`fallback_node`)
**职责**：当重试次数耗尽时，生成安全的默认动作。
```python
async def fallback_node(state: AgentState) -> AgentState:
    logger.error("agent_fallback_triggered", player_id=state["player_id"], errors=state["validation_errors"])
    
    # 根据当前阶段生成默认动作
    default_action = generate_safe_default_action(state["current_phase"], state["player_id"])
    
    return {
        "proposed_action": default_action,
        "is_valid": True,
        "internal_monologue": "系统强制接管：重试次数耗尽，执行默认动作。"
    }
```

---

## 4. 图编译与路由逻辑 (Graph Compilation)

使用 LangGraph 的 `StateGraph` 将节点串联，并定义条件边（Conditional Edges）实现重试循环。

```python
from langgraph.graph import StateGraph, END

def build_agent_graph():
    workflow = StateGraph(AgentState)
    
    # 添加节点
    workflow.add_node("memory", memory_node)
    workflow.add_node("reasoning", reasoning_node)
    workflow.add_node("validation", validation_node)
    workflow.add_node("fallback", fallback_node)
    
    # 定义主流程边
    workflow.set_entry_point("memory")
    workflow.add_edge("memory", "reasoning")
    workflow.add_edge("reasoning", "validation")
    
    # 定义条件路由逻辑
    def route_after_validation(state: AgentState) -> str:
        if state.get("is_valid"):
            return END  # 校验通过，结束工作流
        if state.get("retry_count", 0) >= state.get("max_retries", 3):
            return "fallback"  # 重试耗尽，进入降级
        return "reasoning"  # 继续重试
        
    # 添加条件边
    workflow.add_conditional_edges(
        "validation",
        route_after_validation,
        {
            END: END,
            "fallback": "fallback",
            "reasoning": "reasoning"
        }
    )
    
    workflow.add_edge("fallback", END)
    
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

1. **与 Celery Worker 的交互**：
   - LangGraph 工作流的实例化和 `invoke()` 调用发生在 Celery Task 内部。
   - 工作流执行完毕到达 `END` 节点后，Celery Task 提取最终的 `proposed_action` 并返回。
2. **与 Game Engine 的交互**：
   - LangGraph 内部的 `validation_node` 会调用 Engine 的只读校验接口。
   - 工作流结束后，最终动作由外部的 Celery Task 提交给 Engine 的 `Resolver` 进行状态变更。LangGraph 绝对不直接修改 Engine 的全局状态。