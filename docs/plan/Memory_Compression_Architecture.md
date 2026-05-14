# Memory Compression 架构设计文档

## 目标概述
- 为 `PublicMemoryManager` 引入轻量 LLM，实现记忆摘要压缩，降低 Token 消耗。
- 自动触发压缩：在 `MemoryPruner.compress_events` 中检测 token 超限后调用压缩服务。
- 将模型配置抽象为 URL、Key、模型名，以便在 `.env` 中灵活替换。

## 关键组件
| 组件 | 说明 | 代码位置 |
|------|------|----------|
| **Compression Prompt** | Jinja2 模板 `compression.j2`，统一管理压缩提示。 | [`compression.j2`](ai_werewolf_core/agents/prompts/templates/compression.j2) |
| **MemoryCompressionService** | 记忆压缩服务，调用 LLM，返回 `CompressionResponse`。 | [`MemoryCompressionService.compress`](ai_werewolf_core/agents/memory/compression.py:15) |
| **Settings** | 新增 `compression_model_url`, `compression_model_key`, `compression_model_name`，统一管理模型访问。 | [`Settings`](ai_werewolf_core/config.py:55) |
| **MemoryPruner** | 负责 token 检测和自动压缩，使用 `settings.compression_*` 配置。 | [`MemoryPruner.compress_events`](ai_werewolf_core/agents/memory/pruner.py:39) |
| **PublicMemoryManager** | `get_memory_context` 合并压缩记忆与近期全量记忆，供后续 Agent 使用。 | [`PublicMemoryManager.get_memory_context`](ai_werewolf_core/agents/memory/public.py:80) |
| **Graph Node (memory_node)** | 在工作流中通过 `PublicMemoryManager.get_memory_context` 获取记忆上下文。 | [`memory_node`](ai_werewolf_core/agents/graph/nodes.py:57) |

## 工作流程
1. **获取公共事件**：`PublicMemoryManager.fetch_round_memories` 拉取最近公共事件。
2. **Token 检测**：`MemoryPruner.compress_events` 计算所有轮次记忆的 token (`count_round_memories_tokens`)。
3. **触发压缩**：若 token 超过阈值 (`max_tokens`)，遍历未压缩的轮次，调用 `MemoryCompressionService.compress`。
4. **持久化**：压缩结果写入 Redis 哈希 `RedisKeys.compressed_memory_summary`，设置 7 天过期。
5. **记忆上下文**：`PublicMemoryManager.get_memory_context` 合并 `compressed_memories` 与 `recent_memories`，返回给 `memory_node`。
6. **Agent 使用**：在图节点 `memory_node` 中，将记忆上下文交给 LLM 生成行为。

## 配置示例 (.env)
```dotenv
# 轻量压缩模型配置
COMPRESSION_MODEL_URL=https://api.openai.com/v1
COMPRESSION_MODEL_KEY=sk-xxxxxxx
COMPRESSION_MODEL_NAME=gpt-3.5-turbo
```
> `Settings` 会自动读取上述变量并注入 `settings.compression_model_*`。

## 单元测试覆盖
- `tests/unit/agents/memory/test_compression.py` 验证压缩服务的异常回退路径。
- `tests/unit/agents/graph/test_nodes.py` 确认 `memory_node` 正确获取 `compressed_memories`。
- `tests/unit/agents/memory/test_pruner.py`（新建）可检测 `compress_events` 的 token 检测与自动压缩逻辑。

## 扩展与未来工作
- **多模型支持**：通过 `ModelRegistry` 动态注入不同压缩模型，实现模型热切换。
- **增量压缩**：仅对新增事件触发压缩，减少重复调用。
- **可观测性**：在 `structlog` 中加入 `compression_token_before/after` 统计，监控压缩效果。

---
*本文档由自动化脚本生成，记录了轻量记忆压缩的架构实现细节，供开发者审阅与后续迭代参考。*