# Agent Celery 接入与联调深度架构设计方案

## 1. 架构定位与核心目标

在实时多智能体狼人杀平台中，Game Engine 是同步的、极速的状态机，而 Agent 的 LLM 推理是异步的、耗时的（可能长达数秒至数十秒）。Agent Celery 接入层的核心目标是**实现重计算任务与核心引擎的彻底解耦**，确保 Engine 不会被任何一个思考缓慢的 Agent 阻塞。

### 1.1 核心职责边界
- **异步任务调度**：接收 Engine 发出的“唤醒 Agent”指令，将其转化为 Celery 异步任务放入消息队列。
- **工作流驱动**：在 Celery Worker 进程中实例化并执行 LangGraph 认知工作流。
- **结果回调与并发控制**：将 Agent 的决策结果安全地提交回 Game Engine，并处理多个 Agent 同时提交动作时的并发竞态（Race Conditions）。
- **容错与重试**：处理 Worker 崩溃、任务丢失、执行超时等基础设施级故障。

---

## 2. 核心任务定义与 API 契约

在 `ai_werewolf_core/tasks/agent.py` 中定义核心任务。

### 2.1 单体推理任务 (`agent_infer_task`)

```python
from celery import shared_task
from ai_werewolf_core.agents.graph.workflow import build_agent_graph
from ai_werewolf_core.core.action.gate import ActionGate

@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    time_limit=60,          # 硬超时：60秒强制杀死 Worker 进程
    soft_time_limit=45,     # 软超时：45秒抛出 SoftTimeLimitExceeded 异常
    name="tasks.agent.agent_infer"
)
def agent_infer_task(self, game_id: str, player_id: str, phase: str) -> dict:
    """
    执行单个 Agent 的推理工作流。
    """
    logger = get_logger(__name__).bind(game_id=game_id, player_id=player_id, task_id=self.request.id)
    logger.info("agent_infer_started")
    
    try:
        # 1. 初始化状态
        initial_state = {
            "game_id": game_id,
            "player_id": player_id,
            "current_phase": phase,
            "retry_count": 0,
            "max_retries": 3,
            "validation_errors": []
        }
        
        # 2. 编译并执行 LangGraph 工作流
        graph = build_agent_graph()
        # 注意：Celery 默认是同步环境，如果 Graph 是 async 的，需要使用 asyncio.run()
        final_state = asyncio.run(graph.ainvoke(initial_state))
        
        action_dict = final_state.get("proposed_action")
        if not action_dict:
            raise ValueError("Graph execution completed but no action proposed.")
            
        # 3. 提交动作给 Game Engine
        # ActionGate 封装了 Redis Lua 脚本，保证并发提交的安全性
        gate = ActionGate()
        submission_result = asyncio.run(gate.submit_action(game_id, player_id, action_dict))
        
        logger.info("agent_infer_completed", action=action_dict, result=submission_result)
        return {"status": "success", "action": action_dict}
        
    except SoftTimeLimitExceeded:
        logger.error("agent_infer_timeout_soft")
        # 触发安全降级逻辑，提交默认动作
        _submit_fallback_action(game_id, player_id, phase)
        return {"status": "timeout_fallback"}
        
    except Exception as e:
        logger.exception("agent_infer_failed", error=str(e))
        # 触发 Celery 级别的重试
        raise self.retry(exc=e)
```

### 2.2 批量推理任务 (`batch_infer_task`)

用于在夜间阶段（如狼人集体行动）或白天投票阶段，同时唤醒多个 Agent。

```python
from celery import group

def trigger_batch_infer(game_id: str, player_ids: List[str], phase: str):
    """
    由 Game Engine 调用，触发批量推理。
    使用 Celery Group 并发执行多个 agent_infer_task。
    """
    task_group = group(
        agent_infer_task.s(game_id, pid, phase) for pid in player_ids
    )
    result = task_group.apply_async()
    return result.id
```

---

## 3. 并发控制与竞态处理 (Concurrency & Race Conditions)

当多个 Agent（如 3 个狼人）在夜间同时被唤醒并提交动作时，极易发生并发冲突。

### 3.1 乐观锁与 Lua 脚本保障
- **绝对禁止**：在 Celery Task 中直接修改 PostgreSQL 数据库或执行非原子的 Redis 读写操作。
- **标准路径**：Celery Task 只能调用 `ActionGate.submit_action()`。该方法底层调用 `ai_werewolf_core/redis_lua/vote_submit.lua` 或 `special_action_resolver.lua`。
- **Lua 原子性**：Lua 脚本在 Redis 中单线程执行，确保“校验当前阶段 -> 检查玩家存活状态 -> 写入动作记录”这三个步骤是绝对原子的。如果 Agent A 的动作先到达并导致阶段变更，Agent B 的动作在 Lua 脚本校验时会被直接拒绝（返回 `PHASE_MISMATCH`）。

### 3.2 动作被拒后的处理
如果 Celery Task 提交动作时被 Engine 拒绝（例如手速慢了，阶段已经结束）：
- Task 不应抛出异常或重试，而应记录一条 `action_rejected` 日志，并正常结束。因为游戏状态机已经向前推进，旧阶段的动作已无意义。

---

## 4. 极端边界条件与应对策略

### 4.1 Worker 进程崩溃 (Worker Crash / OOM)
**场景**：执行 LangGraph 的 Worker 进程因为内存溢出（OOM）或被宿主机 Kill 掉，导致任务丢失。
**应对**：
- 启用 Celery 的 `acks_late=True` 配置。任务只有在完全执行成功并返回后，才会从 RabbitMQ/Redis 队列中确认删除。
- 如果 Worker 崩溃，任务会被重新分发给其他存活的 Worker 执行。
- **幂等性要求**：由于任务可能被重复执行，`ActionGate.submit_action()` 必须是幂等的（Idempotent）。如果同一个 Agent 在同一个 Phase 提交了两次相同的动作，Engine 应静默忽略第二次提交。

### 4.2 消息队列积压 (Queue Backlog)
**场景**：大量并发对局导致 Celery 队列积压，Agent 推理任务排队时间超过了游戏阶段的超时时间（如白天发言限时 60 秒，但任务排队了 70 秒）。
**应对**：
- 在 `agent_infer_task` 的开头，增加**时效性校验（Staleness Check）**。
- 任务启动时，首先查询 Redis 中当前对局的 `current_phase`。如果发现当前阶段已经不等于任务参数传入的 `phase`，说明任务已过期（Stale），直接 `return {"status": "skipped_stale"}`，放弃执行 LLM 推理，节省算力。

### 4.3 API 厂商全局限流 (Global Rate Limiting)
**场景**：夜间 12 个 Agent 同时唤醒，瞬间打满 OpenAI 的并发限制（HTTP 429）。
**应对**：
- 在 Celery 层面引入速率限制（Rate Limiting）。可以使用 Celery 自带的 `rate_limit` 参数，或者引入基于 Redis 的分布式令牌桶（Token Bucket）。
- 结合 LangGraph 内部 Model Adapter 的指数退避重试机制，平滑削峰。

---

## 5. 与其他系统模块的交互与状态流转

1. **与 Game Engine 的交互**：
   - **触发**：Engine 的 `PhaseStateMachine` 在进入新阶段（如 `DAY_DISCUSS`）时，调用 `trigger_batch_infer` 唤醒相关 Agent。
   - **回调**：Celery Task 执行完毕后，通过 `ActionGate` 将动作写入 Redis，Engine 的后台轮询任务（或 Redis Keyspace Notifications）检测到动作集齐后，触发状态机流转。
2. **与 Observability System 的交互**：
   - Celery Task 必须使用 `structlog` 绑定 `task_id`, `game_id`, `player_id`。
   - 记录关键生命周期事件：`task_received`, `graph_started`, `llm_called`, `action_submitted`, `task_completed`。这对于排查“为什么 5 号玩家一直不发言”这类问题至关重要。