# Memory Compression 与全局 Token 治理架构设计文档

## 1. 目标概述与痛点分析

在长周期的多智能体狼人杀博弈中，随着游戏轮次的增加，Agent 的历史记录（特别是 `[你的推理]` 部分）呈线性甚至指数级增长。这会导致最终组装的 Prompt 极易超出大语言模型（LLM）的上下文窗口限制（Token 爆炸）。

**核心痛点：**
1. **推理记录冗长**：单轮内 Agent 的内心独白和逻辑盘点非常详细，多轮累加后占用大量 Token。
2. **全局压缩的缓存难题**：如果每次都对整体历史进行压缩，不仅耗时，而且每次新增一轮，整个历史的上下文就变了，之前的全局压缩结果无法复用，导致极大的计算浪费和延迟。

**本架构目标：**
- **彻底解决长周期博弈中的 Token 爆炸问题**。
- **建立统一的 Token 预算模型**：对 Prompt 的各个组件进行精细化的 Token 预算分配。
- **引入“滑动窗口 + 增量滚动摘要”机制**：实现记忆的层次化管理，确保在不丢失关键事实的前提下，将上下文严格控制在预算范围内。
- **多层级弹性裁剪与熔断降级**：在 Prompt 组装前进行全局 Token 计数，若超限则触发多级降级策略，保障系统高可用。

---

## 2. 统一 Token 预算模型 (Token Budget Model)

为了防止单一组件（如记忆）无限膨胀挤占其他关键指令的空间，我们将整体 Prompt 划分为 5 个积木块，并设定严格的预算比例。假设全局 `max_tokens` = 6000：

| Prompt 组件 | 预算比例 | 预估 Token | 裁剪策略 | 关键实现点 |
|---|---|---|---|---|
| **1. 全局系统法则** | 10% | ~600 | **不可裁剪** | 固定内容，建立基础行为底线，防止 AI 出戏。 |
| **2. 身份与阵营策略** | 15% | ~900 | **不可裁剪** | 根据 `agent_role` 动态注入，赋予不同身份独特的“高玩思维”。 |
| **3. 记忆与上下文** | 40% | ~2400 | **弹性裁剪** | 采用“滑动窗口+滚动摘要”双轨机制，是 Token 治理的核心区域。 |
| **4. 当前任务指令** | 20% | ~1200 | **不可裁剪** | 必须完整，明确当前动作目标（如：白天发言、夜间刀人）。 |
| **5. 输出格式约束** | 15% | ~900 | **不可裁剪** | 固定 JSON 模版，确保 Action System 能够完美解析。 |

**核心原则**：在组装 Prompt 前，`PromptBuilder` 必须先调用 `tiktoken` 计算各部分 Token。若总和超限，**仅对“记忆与上下文”部分进行多层级弹性裁剪**。

---

## 3. 核心架构：双轨记忆系统 (Dual-Track Memory System)

将 Agent 的历史记忆分为物理隔离的两个部分，避免每次全量压缩带来的性能和缓存失效问题。

### 3.1 近期工作区 (Working Memory - 滑动窗口)
- **定义**：只保留最近 $N$ 轮（例如 $N=2$）的详细记忆。
- **内容**：包含单轮的公共事件 (`public_events`)、私有事实 (`private_facts`) 以及单轮推理 (`reasoning`)。
- **单轮推理压缩 (Intra-round Compression)**：
  - **触发时机**：每轮结束（Phase 切换到下一轮）时。
  - **执行动作**：后台异步调用轻量级 LLM（如 GPT-3.5），将该轮的 `reasoning` 列表压缩为一段精炼的 `compressed_reasoning`。
  - **存储**：存入 Redis Hash (`werewolf:game:{game_id}:agent:{agent_id}:compressed_reasoning`)。

### 3.2 全局长期摘要 (Long-term Global Summary - 滚动合并)
- **定义**：第 1 轮到第 $Current-N$ 轮的浓缩摘要，是一个不断被覆盖更新的单一字符串。
- **异步预归档 (Async Pre-archiving)**：
  - 当第 $K$ 轮结束时，Celery Worker **异步**提取第 $K$ 轮的记忆（包含公共事件、私有事实以及**压缩后的推理**）。
  - 将其与当前的 `Global_Summary(1 to K-1)` 一起发送给 LLM 进行**增量合并**。
    > **Prompt 示例**: "这是之前的全局摘要：【...】。这是第 K 轮发生的新事件和你的推理：【...】。请将第 K 轮的信息融入全局摘要中，输出一份更新后的全局摘要，重点保留对当前存活玩家的身份判断和关键线索。"
  - 生成新的 `Global_Summary(1 to K)` 并覆盖写入 Redis (`werewolf:game:{game_id}:agent:{agent_id}:global_summary`)。
- **优势**：永远不需要对整个历史重新压缩，且合并动作在后台完成，不阻塞主游戏流程。

---

## 4. 协同工作流与技术实现路径

### 4.1 关键组件与职责

| 组件 | 说明 | 代码位置 |
|------|------|----------|
| **MemoryPruner** | 负责 Token 计数 (`count_tokens`)、预算分配与多层级弹性裁剪逻辑。 | `ai_werewolf_core/agents/memory/pruner.py` |
| **MemoryCompressionService** | 提供单轮推理压缩、滚动摘要合并、极限压缩等 LLM 调用服务。 | `ai_werewolf_core/agents/memory/compression.py` |
| **PublicMemoryManager** | 协调 Pruner 和 Compression，获取并组装最终的记忆上下文。 | `ai_werewolf_core/agents/memory/public.py` |
| **PromptBuilder** | 统筹 5 大 Prompt 组件，执行最终的 Token 校验与熔断降级。 | `ai_werewolf_core/agents/prompts/builder.py` |
| **Celery Tasks** | 执行异步的“单轮推理压缩”和“滚动合并到全局摘要”任务 (`task_archive_memory`)。 | `ai_werewolf_core/tasks/agent_tasks.py` |

### 4.2 动态组装与多层级熔断管线 (Assembly & Fallback Pipeline)

在 `PromptBuilder` 组装最终 Prompt 时，执行以下管线以确保绝对不超限：

1. **初次组装尝试 (Normal Assembly)**：
   - 提取 `Global_Summary(1 to Current-N)` + `Recent_Memory(Current-N+1 to Current)`。
   - 拼接其他 4 个不可裁剪组件。
   - 使用 `tiktoken` 计算总 Token。
   - 若未超限，直接返回。

2. **降级 1：强制缩小滑动窗口 (Shrink Window)**
   - 若总 Token > `max_tokens`，将滑动窗口 $N$ 减 1（例如从保留 2 轮变为保留 1 轮）。
   - **数据不丢失保证**：此时系统**并不是直接丢弃**被挤出窗口的那一轮数据，而是直接从 Redis 读取后台已异步算好的 `Global_Summary(1 to Current-N+1)`（即包含了被挤出那一轮的合并版摘要），替换掉被挤出窗口的那一轮详细记忆。
   - 重新计算 Token。

3. **降级 2：极限压缩全局摘要 (Extreme Compression)**
   - 若降级 1 后仍超限（说明全局摘要本身过长），触发同步的极限压缩任务。
   - 调用 `MemoryCompressionService`，强制将 `Global_Summary` 压缩到 500 字以内（"请极度精简以下摘要，只保留对存活玩家的身份定性"）。
   - 更新 Redis 并重新组装。

4. **降级 3：暴力截断 (Truncation)**
   - 若依然超限（极端异常情况，如 LLM 抽风返回超长文本），直接截断 `Global_Summary` 的前半部分，确保当前任务指令能成功发送，防止系统崩溃。

---

## 5. 数据结构与存储设计

### 5.1 Redis 存储结构

- **单轮压缩推理 (Hash)**
  - `Key`: `werewolf:game:{game_id}:agent:{agent_id}:compressed_reasoning`
  - `Field`: `round_num` (例如 "1", "2")
  - `Value`: 压缩后的推理文本 (String)
  - `TTL`: 随对局生命周期

- **全局长期摘要 (String)**
  - `Key`: `werewolf:game:{game_id}:agent:{agent_id}:global_summary`
  - `Value`: 纯文本字符串，包含从第 1 轮到归档轮次的浓缩摘要。
  - `TTL`: 随对局生命周期

### 5.2 context.j2 模板重构示例

`ai_werewolf_core/agents/prompts/templates/context.j2` 需要重构以支持双轨记忆：

```jinja2
【全局历史摘要 (第 1 轮至第 {{ current_round - window_size }} 轮)】
{% if global_summary %}
{{ global_summary }}
{% else %}
暂无早期历史摘要。
{% endif %}

【近期记忆 (最近 {{ window_size }} 轮)】
{% for round_mem in recent_history %}
### 第 {{ round_mem.round_num }} 轮 ###

{% if round_mem.compressed_public %}
[公共事件摘要]
- 发言概括：{{ round_mem.compressed_public.speech_summary }}
- 关键事实：{{ round_mem.compressed_public.key_facts }}
{% elif round_mem.public_events %}
[公共事件]
{% for event in round_mem.public_events %}
- [{{ event.phase.value }}] {{ event.description }}
{% endfor %}
{% endif %}

{% if round_mem.private_facts %}
[私有事实] (绝对真实，不可违背)
{% for fact in round_mem.private_facts %}
- [{{ fact.phase.value }}] {{ fact.description }}
{% endfor %}
{% endif %}

[你的推理]
{% if round_mem.compressed_reasoning %}
{{ round_mem.compressed_reasoning }}
{% elif round_mem.reasoning %}
{% for r in round_mem.reasoning %}
- {{ r }}
{% endfor %}
{% endif %}

{% endfor %}

{% if last_suspect_list %}
【上一次的嫌疑度列表】
以下是你上一次推理得出的嫌疑度列表 (0.0到1.0之间的嫌疑度)，供你参考：
{% for player_id, suspect_score in last_suspect_list.items() %}
- {{ player_id }}: {{ suspect_score }}
{% endfor %}
{% endif %}

{% if experiences %}
【经验引用】
以下是你从过往对局中总结的经验教训，请在本次决策中参考：
{% for exp in experiences %}
- {{ exp }}
{% endfor %}
{% endif %}
```

---

## 6. 总结

本架构通过**统一 Token 预算**确立了 Prompt 组装的边界，通过**滑动窗口与异步滚动摘要**彻底解决了历史记忆线性增长导致的 Token 爆炸问题，并通过**多层级熔断降级**机制保障了系统在极端情况下的高可用性。该方案严格遵循了 `CLAUDE.md` 中“Failures must be explicit and observable”以及“Always async”的核心指导原则，确保了 Agent Runtime 的稳定性和可扩展性。