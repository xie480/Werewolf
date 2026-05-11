**核心结论先行：**
在真正的多智能体项目中，**绝对不能把特定厂商（如 OpenAI）的 SDK 强耦合在业务代码里**。
一方面，你需要防范 API 抽风、限流（Rate Limit）；另一方面，AI 狼人杀最有趣的玩法是**“异构模型大乱斗”**（例如：让 4 个 GPT-4o 扮演神职，对战 4 个 DeepSeek 扮演的狼人，看看谁的推理能力更强）。
因此，Model Adapter System 必须是一个标准的**工厂模式（Factory Pattern） + 统一网关（Unified Gateway）**。
以下是专为多智能体高频调用设计的 Model Adapter 落地方案：
---
### 一、 核心架构：多态适配器 (Adapter Pattern)
在 Agent Runtime 和真实的 LLM API 之间，建立一层标准的 `BaseLLMAdapter` 接口。所有的系统模块只认这个接口。
#### 1. 统一调用接口 (Unified Interface)
```python
from abc import ABC, abstractmethod
class BaseLLMAdapter(ABC):
    @abstractmethod
    async def generate_action(
        self,
        system_prompt: str,
        user_prompt: str,
        expected_schema: type[BaseModel] # Pydantic 类
    ) -> BaseModel:
        """所有具体的模型适配器必须实现这个方法，并保证返回合法的格式化对象"""
        pass
```
#### 2. 具体厂商实现层 (Provider Implementations)
你需要根据不同厂商的特性实现对应的 Adapter：
* **OpenAI Adapter**：原生支持 `response_format={"type": "json_object"}` 或 Structured Outputs。
* **Anthropic (Claude) Adapter**：不支持原生 JSON 模式，需要强制在 Prompt 尾部加上 `"{` 诱导其输出 JSON，并在代码中捕获。
* **Ollama / vLLM Adapter**：用于本地私有化部署测试，无需考虑网络计费，但需要处理并发排队问题。
---
### 二、 核心难点：JSON 格式的强制约束与兜底
在前面设计的 `Action System` 中，我们要求模型必须输出 JSON。但并非所有模型都听话。Model Adapter 必须在这里做**“脏活累活”**：
**降级解析管线 (Fallback Parsing Pipeline)：**
1. **尝试原生结构化输出**：如果模型支持（如 GPT-4o），直接使用 API 提供的强 Schema 校验。
2. **正则剥离 (Regex Extraction)**：如果模型输出了 ````json { ... } ```` 或者在 JSON 前后加了废话“好的，我的决定是：”，Adapter 必须使用正则表达式 `re.search(r'\{.*\}', response_text, re.DOTALL)` 强行抠出 JSON 块。
3. **JSON 修复 (Auto-Fix)**：如果抠出的 JSON 缺引号或有尾随逗号，可接入 `json-repair` 等轻量级 Python 库进行静默修复。
4. **抛出重试异常**：如果彻底救不回来，向上层 `Action System` 抛出特定格式的 `JSONParseException`，触发我们之前设计的 Retry Prompt 流程。
---
### 三、 稳定性保障：重试与降级路由 (Resilience & Routing)
12 个 Agent 频繁互动极易触发 API 厂商的 QPS (每秒请求数) 或 TPM (每分钟 Token 数) 限制。
1. **退避重试机制 (Exponential Backoff)**
   Adapter 内部必须封装异步请求的重试逻辑（如使用 `tenacity` 库）。当遇到 `HTTP 429 Too Many Requests` 或 `502 Bad Gateway` 时，自动等待 2s、4s、8s 后重试，**不要把这种网络层错误暴露给 LangGraph 状态机**。

2. **自动降级路由 (Model Fallback)**
   在配置文件中为 Agent 设定主备模型。
   * _配置示例_`primary: "claude-3.5-sonnet", fallback: "gpt-4o"`
   * 当主模型连续 3 次报错或超时，Adapter 自动无缝切换到备用模型执行，保证游戏对局不中断。
---
### 四、 成本与监控：Token 账本系统 (Token Ledger)
一场完整的 12 人局可能消耗数万到数十万 Token。Adapter 必须充当“计费电表”。
* **机制**：每次调用 API 后，从 Response 中提取 `prompt_tokens` 和 `completion_tokens`。
* **落库**：将这些数据连同 `game_idagent_idmodel_name` 异步写入 `api_usage_logs` 表。
* **价值**：
  1. 结算成本：算出一局游戏花了多少美元。
  2. 评测性价比：结合 `Evaluation System`，得出“GPT-4o 胜率 60% 花了 $1.5，DeepSeek 胜率 55% 花了 $0.1，性价比极高”的硬核数据结论。
---
### 五、 初始化与装配 (Dependency Injection)
在全局 `Lifecycle Manager` 初始化游戏时，根据配置为不同的角色注入不同的模型实例。这允许你实现**人机混战**或**多模型争霸**：
```python
# 伪装配逻辑
agents = []
for player_conf in game_config.players:
    if player_[conf.is](http://conf.is)_human:
        # 人类玩家适配器：将 prompt 推送到前端，等待 websocket 传回动作
        adapter = HumanWebSocketAdapter(player_id)
    elif player_conf.model == "gpt-4o":
        adapter = OpenAIAdapter(api_key, model="gpt-4o")
    elif player_conf.model == "deepseek-chat":
        adapter = DeepSeekAdapter(api_key, model="deepseek-chat")

    agent = AgentRuntime(agent_id=player_[conf.id](http://conf.id), llm_adapter=adapter)
    agents.append(agent)
```