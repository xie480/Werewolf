**核心结论先行：**
大模型是混沌的，它可能在黑夜想要发言，可能想要刀一个已经死亡的玩家，甚至可能因为幻觉想要复活自己。
**Action System 的核心职责不是“执行”，而是“拦截与规整”**。它必须在 LLM 吐出 JSON 和 Game Engine 处理状态之间，建立一套**“动作空间（Action Space）映射 + 严格校验（Validation） + 退避重试（Retry） + 兜底（Fallback）”**的管线。
以下是专为大模型不可控性设计的 Action System 落地架构：
---
### 一、 动作空间定义 (Action Space Registry)
不要让模型自由发挥动作名称，必须通过枚举（Enum）严格限定动作空间，并按白天/黑夜进行权限隔离。
```python
from enum import Enum
class ActionType(str, Enum):
    # 通用动作
    SPEAK = "SPEAK"             # 发言
    VOTE = "VOTE"               # 投票（包含弃权，目标设为 null）
    PASS = "PASS"               # 空过/不发动技能 (非常重要，用于女巫不救人等)

    # 夜间技能动作
    WOLF_KILL = "WOLF_KILL"     # 狼人刀人
    SEER_CHECK = "SEER_CHECK"   # 预言家验人
    WITCH_SAVE = "WITCH_SAVE"   # 女巫用解药
    WITCH_POISON = "WITCH_POISON" # 女巫用毒药
```
---
### 二、 执行管线与 LangGraph 节点设计
在 LangGraph 的单个 Agent Workflow 中，Action System 应该被设计为一个**带反馈的循环（Feedback Loop）**。
流程流转`LLM Output` -> `Parse (解析)` -> `Validate (业务校验)`
-> **(若通过)** -> 提交给 Game Engine
-> **(若失败)** -> 生成 Error Prompt -> 重新进入 `LLM Inference` 节点。
---
### 三、 核心模块：多维度动作校验器 (Action Validator)
这是 Action System 的核心，必须通过硬编码实现，绝不能交给 LLM 判定。校验应分为三个维度：
#### 1. 语法与格式校验 (Schema Validation)
*   **职责**：检查输出是否是合法 JSON，字段是否缺失。
*   **实现**：使用 `Pydantic` 强校验。
```python
from pydantic import BaseModel, validator
class AgentAction(BaseModel):
    action_type: ActionType
    target_id: str | None

    @validator('target_id')
    def target_must_be_valid_format(cls, v):
        # 如果不是 null，必须符合玩家ID格式（如 player_1）
        if v is not None and not v.startswith('player_'):
            raise ValueError("target_id 格式错误")
        return v
```
#### 2. 时序与权限校验 (Phase & Permission Check)
*   **职责**：拦截不属于当前阶段的动作。
*   **规则**：
    *   当前是 `DAY_VOTE`，如果动作是 `WOLF_KILL` -> **拦截，报错“当前阶段不允许使用夜间技能”。**
    *   当前是 `NIGHT`，如果动作是 `SPEAK` -> **拦截，报错“夜间严禁发言”。**
#### 3. 业务状态校验 (Business State Check - 最容易踩坑)
*   **职责**：拦截不符合游戏逻辑的动作。
*   **规则**：
    *   **死亡校验**`target_id` 指向的玩家必须是存活状态`is_alive == True`）。
    *   **技能耗尽校验**：女巫如果尝试使用 `WITCH_SAVE`，必须检查数据库中她的解药状态`has_antidote == True`）。
    *   **自杀校验**：通常狼人不能自刀（除非村规允许），需要在此处拦截。
---
### 四、 重试与兜底机制 (Retry & Fallback)
当校验器拦截了错误动作时，我们不能直接让程序崩溃，也不能让游戏卡死。
#### 1. 动态错误注入重试 (Dynamic Error Prompting)
如果校验失败，Action System 将错误原因包装成 Prompt 塞回给 LLM，强制它重做。
**重试 Prompt 模板：**
```text
【系统警告：动作执行失败！】
你刚刚提交的动作由于以下原因被系统拒绝执行：
"{error_message}"  // 例如：目标 player_3 已在昨晚死亡，无法作为投票目标。
请修正你的逻辑，重新输出合法的动作格式。
这是你第 {retry_count}/3 次重试机会。
```
#### 2. 强制兜底策略 (Fallback Strategy)
如果 LLM 连续 3 次（或设定阈值）重试依然失败（经常发生于小模型或复杂的长逻辑中），Action System 必须**强行接管**，生成一个合法的默认动作，保证游戏引擎继续流转。
*   如果是发言阶段失败 -> 强制输出`{"action_type": "SPEAK", "speech_content": "（系统接管：该玩家陷入沉思，没有发言）"}`
*   如果是投票阶段失败 -> 强制输出`{"action_type": "VOTE", "target_id": null}` (弃权)
*   如果是夜间技能失败 -> 强制输出`{"action_type": "PASS", "target_id": null}` (空过)
---
### 五、 动作池处理 (Action Pool / 专为多狼并发设计)
在夜晚阶段，整体流程是**串行**的（狼人 -> 女巫 -> 预言家）。但是，如果存在多匹狼人，狼人内部的刀人决策可以是**并发推理（Concurrent Reasoning）**的。
Action System 需要提供一个**Action Pool（动作池）**机制来处理多狼并发：
1. 进入 `NIGHT_WOLF_ACT` 阶段，引擎并发唤醒所有存活的狼人 Agent。
2. Action System 接收各个狼人 Agent 的 `AgentAction`，存入缓存池。
3. 等待所有狼人提交完毕（或到达最大超时时间 60 秒）。
4. **统一封包**，将包含多个刀人动作的 List 打包发送给上一层的 `Game Engine -> Action Resolver` 进行意见统一（如少数服从多数，或默认第一刀）。