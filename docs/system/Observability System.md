**核心结论先行：**
一定要分清“对局复盘（Replay/Evaluation）”和“可观测性（Observability）”的区别！
前两者是给**用户和评委**看的业务逻辑；而可观测性系统是给**开发者（你）**看的底层诊断工具。
面对多智能体在 LangGraph 中的高并发黑盒调用，如果出现死锁、幻觉或 Token 爆炸，没有可观测性系统，排查 Bug 无异于大海捞针。
针对 LangGraph AI 狼人杀，我们必须围绕**“链路追踪 (Tracing)”**、**“结构化日志 (Logging)”**和**“指标监控 (Metrics)”**三大支柱进行设计。
以下是 Observability System 的落地架构方案：
---
### 一、 链路追踪 (Distributed Tracing)：无缝接入 LangSmith
因为你使用的是 LangChain/LangGraph 生态，**强烈建议直接接入官方的 LangSmith，这是目前排查智能体黑盒的最佳神器。**
#### 1. 解决什么痛点？
* **工作流可视化**：你能在一个可视化的树状图中，清晰看到 `Agent_Workflow` 执行到了哪个 Node，是否在 `Action_Validator` 节点因为 JSON 格式错误陷入了死循环重试。
* **Prompt 透视**：点击任何一个 LLM 调用节点，能直接看到**最终拼接好发给大模型的那一大长串完整 Prompt 文本**，以及大模型返回的原生 Raw Text。这对于排查“记忆拼接错误”或“幻觉”至关重要。
#### 2. 落地配置建议
无需改动大量业务代码，只需在环境变量中注入：
```bash
export LANGCHAIN_TRACING_V2=true
export LANGCHAIN_ENDPOINT=[https://api.smith.langchain.com](https://api.smith.langchain.com)
export LANGCHAIN_API_KEY=<your-api-key>
export LANGCHAIN_PROJECT="AI_Werewolf_Alpha" # 按环境或版本区分Project
```
在代码中，为每一次 Agent 的图调用打上 `tags` 或 `metadata`（如 `game_id: g_123`, `agent_id: p_5`），方便在 LangSmith 后台精准过滤检索。
---
### 二、 结构化日志 (Structured Logging)：追踪并发幽灵
夜晚阶段 12 个 Agent 是并发推理的，如果使用传统的 `print` 或文本日志，不同 Agent 的日志会像乱码一样交织在一起。
#### 1. Correlation ID (关联 ID 机制)
每一条日志都必须带有上下文标签。推荐使用 Python 的 `structlog` 或 `loguru`，配置全局 Context：
* `trace_id` / `game_id`：标识属于哪一局游戏。
* `phase`：当前处于什么阶段（如 NIGHT_ACTION）。
* `agent_id`：是哪个 Agent 触发的。
#### 2. 日志级别与输出示例
全部输出为 JSON 格式，方便后续接入 ELK (Elasticsearch/Logstash/Kibana) 或直接在控制台用 `jq` 过滤。
```json
// 一条标准的排障日志示例
{
  "timestamp": "2023-10-25T14:32:01.123Z",
  "level": "WARN",
  "logger": "action_validator",
  "game_id": "game_999",
  "agent_id": "player_3",
  "message": "Action validation failed. Initiating retry 2/3.",
  "error_detail": "Target player_7 is already dead.",
  "duration_ms": 1500
}
```
---
### 三、 核心指标监控 (Metrics Dashboard)
为了把控项目进度、优化性能和汇报成本，你需要在系统内埋点，收集以下三大类核心 Metrics：
#### 1. 性能指标 (Performance Metrics)
* **LLM 响应延迟 (Latency)**：记录各个厂商 API 的首字响应时间（TTFB）和总耗时。如果 OpenAI 突然变慢导致游戏卡顿，你能立刻发现。
* **Phase 流转时长**：统计每个“白天发言阶段”和“夜晚行动阶段”的平均耗时，用于优化并发调度。
#### 2. 质量指标 (Quality Metrics)
* **JSON 解析失败率 (Parse Error Rate)**：这非常重要！如果发现某个开源模型的解析失败率激增到 30% 以上，说明该模型指令遵循能力差，应触发预警。
* **重试熔断率 (Fallback Rate)**：统计有多少次 Action 连续 3 次重试失败，最终触发了系统的强制兜底（Fallback）。
#### 3. 成本账单指标 (Cost Metrics)
* （结合上一节 Model Adapter 设计的 Token Ledger）
* 聚合展示`Total Tokens per Game`（单局消耗）`Cost by Agent Role`（看是不是预言家比村民消耗更多的 Token）。
---
### 四、 异常告警策略 (Alerting)
在进行自动化评测（比如周末写个脚本自动跑 100 局对局测试胜率）时，没人会一直盯着屏幕。你需要配置基础的告警钩子（如发送到飞书/钉钉机器人）：
* **死锁告警**`game_session` 停留在同一个 Phase 超过 5 分钟未能推进。
* **熔断告警**：连续 10 次 LLM API 调用返回 HTTP 502 / 429。
* **资金告警**：当日累积 Token 消耗折合超过设定阈值（比如 20 美金）。