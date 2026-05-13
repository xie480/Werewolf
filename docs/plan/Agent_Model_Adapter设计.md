# Agent Model Adapter 深度架构设计方案

## 1. 架构定位与核心目标

在多智能体狼人杀博弈平台中，Agent Model Adapter（模型适配层）是连接上层 LangGraph 认知工作流与底层大语言模型（LLM）供应商的唯一桥梁。其核心目标不仅是屏蔽不同 LLM API 的差异，更关键的是**确保非确定性的 LLM 输出能够被确定性的 Game Engine 严格解析与执行**。

### 1.1 核心职责边界
- **协议转换**：将内部的 `AgentState` 和 Prompt 模板转换为特定 LLM 供应商（如 OpenAI, 智谱, Anthropic）所需的请求格式。
- **结构化输出保障**：强制 LLM 遵循 Pydantic Schema 输出 JSON，并处理截断、格式畸变等问题。
- **自愈与重试机制**：在遇到网络抖动、API 限流（Rate Limit）、内容安全拦截或 JSON 解析失败时，执行带指数退避的重试策略。
- **安全降级（Fallback）**：在所有重试耗尽后，生成符合当前游戏阶段的“安全默认动作”，确保全局状态机不被单个 Agent 阻塞。

---

## 2. 核心数据结构与 API 契约

为了保证强类型约束，所有进出 Adapter 的数据必须经过 Pydantic 校验。

### 2.1 输入契约 (Input Schema)

```python
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from ai_werewolf_core.schemas.enums import GamePhase

class AdapterRequest(BaseModel):
    """发送给 Model Adapter 的标准请求"""
    agent_id: str = Field(..., description="发起请求的智能体ID")
    game_id: str = Field(..., description="当前对局ID")
    phase: GamePhase = Field(..., description="当前游戏阶段")
    system_prompt: str = Field(..., description="系统级提示词，包含规则与角色策略")
    user_prompt: str = Field(..., description="用户级提示词，包含当前上下文与记忆")
    temperature: float = Field(default=0.7, description="生成温度，白天发言可高，夜间决策需低")
    max_tokens: int = Field(default=1024, description="最大生成长度")
    response_model: Any = Field(..., description="期望输出的 Pydantic 模型类")
```

### 2.2 输出契约 (Output Schema)

```python
class AdapterResponse(BaseModel):
    """Model Adapter 返回的标准响应"""
    raw_content: str = Field(..., description="LLM 返回的原始文本")
    parsed_data: Optional[BaseModel] = Field(None, description="解析成功后的 Pydantic 实例")
    is_success: bool = Field(..., description="是否成功解析并校验")
    error_message: Optional[str] = Field(None, description="失败时的错误信息")
    retry_count: int = Field(default=0, description="实际发生的重试次数")
    usage: Dict[str, int] = Field(default_factory=dict, description="Token 消耗统计")
```

---

## 3. 类设计与生命周期管理

采用工厂模式（Factory Pattern）和策略模式（Strategy Pattern）管理不同供应商的客户端。

### 3.1 接口定义 (`base.py`)

```python
from abc import ABC, abstractmethod
import structlog

logger = structlog.get_logger(__name__)

class BaseModelAdapter(ABC):
    """模型适配器基类"""
    
    def __init__(self, config: dict):
        self.config = config
        self.client = self._initialize_client()
        
    @abstractmethod
    def _initialize_client(self) -> Any:
        """初始化底层 SDK 客户端"""
        pass

    @abstractmethod
    async def agenerate(self, request: AdapterRequest) -> AdapterResponse:
        """异步生成结构化响应"""
        pass
        
    async def close(self):
        """清理资源，如关闭 aiohttp session"""
        pass
```

### 3.2 OpenAI 兼容实现 (`openai_adapter.py`)

```python
from openai import AsyncOpenAI
from pydantic import ValidationError
import json

class OpenAIAdapter(BaseModelAdapter):
    def _initialize_client(self):
        return AsyncOpenAI(
            api_key=self.config.get("api_key"),
            base_url=self.config.get("base_url")
        )

    async def agenerate(self, request: AdapterRequest) -> AdapterResponse:
        # 具体实现见第 4 节
        pass
```

### 3.3 生命周期管理
- **初始化**：在 FastAPI 启动或 Celery Worker 启动时，根据 `config.py` 中的配置，通过 `AdapterFactory` 实例化全局单例的 Adapter。
- **调用期**：每次 LangGraph 节点调用时，传入独立的 `AdapterRequest`。
- **销毁期**：在 Worker 关闭时，调用 `close()` 释放连接池。

---

## 4. 核心逻辑与故障重试机制 (带伪代码)

Adapter 的核心难点在于处理 LLM 的不确定性。我们采用 **"生成 -> 解析 -> 报错反馈 -> 修正生成"** 的闭环重试机制。

### 4.1 带有自愈能力的生成逻辑

```python
import asyncio
from json.decoder import JSONDecodeError

async def agenerate_with_retry(self, request: AdapterRequest, max_retries: int = 3) -> AdapterResponse:
    messages = [
        {"role": "system", "content": request.system_prompt},
        {"role": "user", "content": request.user_prompt}
    ]
    
    schema_json = request.response_model.model_json_schema()
    format_instruction = f"\n\n请严格按照以下 JSON Schema 输出，不要包含任何 Markdown 标记或其他文本：\n{json.dumps(schema_json)}"
    messages[1]["content"] += format_instruction

    for attempt in range(max_retries):
        try:
            # 1. 调用 LLM
            response = await self.client.chat.completions.create(
                model=self.config.get("model_name"),
                messages=messages,
                temperature=request.temperature,
                response_format={"type": "json_object"} # 强制 JSON 模式
            )
            
            raw_text = response.choices[0].message.content
            
            # 2. 尝试解析与 Pydantic 校验
            parsed_dict = json.loads(raw_text)
            validated_data = request.response_model(**parsed_dict)
            
            return AdapterResponse(
                raw_content=raw_text,
                parsed_data=validated_data,
                is_success=True,
                retry_count=attempt,
                usage=response.usage.model_dump()
            )
            
        except (JSONDecodeError, ValidationError) as e:
            logger.warning("llm_output_parse_failed", attempt=attempt, error=str(e), raw_text=raw_text)
            
            if attempt == max_retries - 1:
                return AdapterResponse(
                    raw_content=raw_text,
                    is_success=False,
                    error_message=f"解析失败: {str(e)}",
                    retry_count=attempt
                )
                
            # 3. 自愈反馈：将错误信息喂给 LLM 要求修正
            error_feedback = f"你的上一次输出无法解析。错误信息：{str(e)}。请修正你的 JSON 格式并重新输出。"
            messages.append({"role": "assistant", "content": raw_text})
            messages.append({"role": "user", "content": error_feedback})
            
            # 指数退避
            await asyncio.sleep(2 ** attempt)
            
        except Exception as e:
            # 处理网络异常、限流等
            logger.error("llm_api_error", error=str(e))
            if attempt == max_retries - 1:
                raise e
            await asyncio.sleep(2 ** attempt)
```

---

## 5. 极端边界条件与应对策略

在非对称信息博弈（狼人杀）中，Adapter 必须处理以下极端情况：

### 5.1 幻觉导致的非法动作 (Illegal Action due to Hallucination)
**场景**：预言家 LLM 产生幻觉，试图查验一个已经死亡的玩家，或者狼人试图在白天刀人。
**应对**：
- Adapter 仅负责格式校验（Schema Validation），不负责业务规则校验（Business Validation）。
- 业务校验由 Game Engine 的 `ActionValidator` 负责。
- 如果 Engine 拒绝了该动作，LangGraph 会捕获异常，并将 Engine 的拒绝原因（如“目标玩家已死亡”）作为新的 `user_prompt` 再次调用 Adapter 进行重试。

### 5.2 严重超时与死锁 (Timeout & Deadlock)
**场景**：LLM 供应商 API 响应极慢，导致 Celery Worker 阻塞，进而拖慢整个游戏进度。
**应对**：
- 在 `agenerate` 内部强制使用 `asyncio.wait_for` 设置硬超时（如 15 秒）。
- 超时后直接抛出 `TimeoutError`，触发外层的 Fallback 机制。

### 5.3 格式畸变与截断 (Format Distortion & Truncation)
**场景**：LLM 输出的 JSON 被截断（达到了 `max_tokens` 限制）。
**应对**：
- 捕获 `JSONDecodeError`。
- 在重试时，动态增加 `max_tokens`，或者在 `error_feedback` 中提示 LLM 缩短其内部推理（Internal Monologue）的长度，优先保证核心动作字段的完整性。

---

## 6. 与其他系统模块的交互与状态流转

1. **与 LangGraph 的交互**：
   - LangGraph 的 `ReasoningNode` 组装 Prompt 并调用 Adapter。
   - Adapter 返回 `AdapterResponse`。
   - 如果 `is_success == False`，LangGraph 状态机流转至 `FallbackNode`。
2. **与 Observability System (日志系统) 的交互**：
   - Adapter 必须记录每一次 LLM 调用的完整 Request 和 Response（包括耗时、Token 消耗），使用 `structlog` 打印结构化日志。
   - 日志字段必须包含 `game_id`, `agent_id`, `phase`，以便后续在复盘系统（Replay System）中进行链路追踪。
3. **与 Model Provider 的交互**：
   - 严格遵守供应商的并发限制。在 Adapter 外部（或内部）可引入基于 Redis 的令牌桶限流器（Token Bucket Rate Limiter），防止夜间阶段多个 Agent 并发唤醒时触发 HTTP 429 Too Many Requests 错误。
