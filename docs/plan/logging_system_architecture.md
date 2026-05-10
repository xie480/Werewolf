# AI 狼人杀日志与可观测性系统架构方案

## 1. 设计背景与目标

在多智能体（Multi-Agent）并发执行的狼人杀游戏环境中，传统的 `print()` 或纯文本日志会导致不同 Agent 的输出交织在一起，极难排查死锁、大模型幻觉或状态机流转错误。

根据 `docs/agent.md` 和 `docs/system/Observability System.md` 的规范，本系统旨在建立一个**结构化、带上下文追踪、且易于与 LangSmith 集成**的底层日志基座。

**核心目标：**
1. **绝对禁止魔法打印**：统一收口所有日志输出。
2. **结构化输出**：生产环境强制输出 JSON 格式，便于后续接入 ELK 或使用 `jq` 分析。
3. **上下文追踪 (Correlation ID)**：每条日志必须能追溯到具体的 `game_id`、`phase` 和 `agent_id`。
4. **无缝对接大模型追踪**：为 LangSmith 的接入预留环境变量和配置规范。

## 2. 核心技术选型

* **核心日志库**：`structlog` (轻量、原生支持 JSON 和上下文绑定)。
* **上下文管理**：Python 原生 `contextvars` (完美支持 `asyncio` 异步并发环境下的变量隔离)。
* **链路追踪**：LangSmith (通过环境变量 `LANGCHAIN_TRACING_V2` 开启)。

## 3. 模块设计与目录结构

日志系统将作为核心工具类，放置在 `utils` 目录下：

```text
ai_werewolf_core/
└── utils/
    ├── __init__.py
    └── logger.py        # 日志核心配置与工厂方法
```

### 3.1 `logger.py` 核心功能规划

1. **`setup_logger()` 初始化函数**：
   - 根据环境变量（如 `ENVIRONMENT=dev/prod`）决定输出格式。
   - 开发环境 (dev)：输出带颜色的 Console 友好格式 (ConsoleRenderer)。
   - 生产环境 (prod)：输出纯 JSON 格式 (JSONRenderer)。
   - 注入全局时间戳、日志级别等基础信息。

2. **上下文绑定机制**：
   - 利用 `structlog.contextvars` 模块。
   - 提供便捷函数，如 `bind_game_context(game_id: str, phase: str)` 和 `bind_agent_context(agent_id: str)`。
   - 确保在 FastAPI 接收请求或 Celery Worker 启动任务时，第一时间注入这些上下文。

## 4. 日志规范与示例

### 4.1 必须包含的 Context 字段
- `game_id`: 当前对局的唯一标识 (UUID)。
- `phase`: 当前游戏阶段 (如 `GamePhase.NIGHT_ACTION.value`)。
- `agent_id`: (可选) 当前执行动作的智能体 ID。
- `event_type`: (可选) 关键事件类型，用于指标统计 (如 `llm_call`, `action_validation_failed`)。

### 4.2 预期输出示例 (JSON 格式)

```json
{
  "timestamp": "2023-10-25T14:32:01.123Z",
  "level": "warning",
  "logger": "action_validator",
  "game_id": "game_999",
  "phase": "NIGHT_ACTION",
  "agent_id": "player_3",
  "event_type": "action_validation_failed",
  "message": "Action validation failed. Initiating retry 2/3.",
  "error_detail": "Target player_7 is already dead."
}
```

## 5. 实施步骤 (Implementation Steps)

1. **基础配置**：在 `logger.py` 中完成 `structlog` 的 Processor 链条配置。
2. **上下文封装**：封装 `contextvars` 的绑定和清理方法。
3. **配置更新**：在 `config.py` 和 `.env.example` 中加入 LangSmith 相关的环境变量声明。
4. **测试验证**：编写简单的异步测试脚本，模拟多个 Agent 并发写入日志，验证 Context 是否正确隔离且未发生串号。

## 6. 与其他系统的交互

- **Game Engine**：在状态机流转时，调用 `bind_game_context` 更新当前的 `phase`。
- **Agent Runtime**：在 LangGraph 节点执行前，绑定当前的 `agent_id`。
- **API 层**：在 FastAPI 中间件中生成并绑定初始的 `trace_id` 或 `game_id`。