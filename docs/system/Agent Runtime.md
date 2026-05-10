**核心结论先行：**
在 AI 狼人杀中，Agent Runtime 必须解决的最大痛点是**“信息不对称与绝对隔离”**。如果用 LangGraph 编排，**绝对不能把所有 Agent 塞进同一个拥有共享全局状态（Shared State）的大图里自由交互**，这极易导致“串话”和“信息泄露（作弊）”。
正确的设计模式是：**Game Engine 统筹全局状态，LangGraph 充当每个 Agent 内部的“认知与决策微工作流（Micro-Workflow）”**。
以下是 Agent Runtime 的详细架构拆解与落地级方案：
---
### 一、 核心架构：认知-决策管线 (Cognitive Pipeline)
在 LangGraph 中，为单个 Agent 定义一个完整的 `StateGraph`。每当轮到某个 Agent 行动（发言、投票或夜间技能）时，Engine 唤醒该 Agent 并传入上下文，Agent 依次执行以下 Node：
1. **Perception Node（感知节点）**：接收 Engine 下发的最新事件（如昨夜谁死了、刚才谁发言了）。
2. **Memory Node（记忆节点）**：将新事件写入数据库（公共记忆），并检索与当前决策相关的私有记忆和历史推理。
3. **Reasoning Node（推理节点）**：核心大脑！计算“狼坑”、评估信任度、盘逻辑（Chain of Thought）。
4. **Action Node（行动节点）**：根据推理结果，生成符合 Game Engine Schema 的行动（发言稿或投票对象）。
---
### 二、 核心难点：记忆隔离方案 (Memory Isolation)
狼人杀是隐藏信息的博弈，数据隔离级别必须达到银行账单级。建议采用 **双规记忆模型**：
#### 1. 公共记忆池 (Public Event Stream)
* **包含内容**：白天发言日志、投票结果、法官公开播报（如“昨夜平安夜”）。
* **存储方式**：数据库中的全局对局流水表，按时间线（Timeline）严格排序。
* **访问权限**：所有存活 Agent 均可按需读取。
#### 2. 私有记忆墙 (Private Context)
* **包含内容**：Agent 自己的底牌、同伴身份（如狼人队友）、自己的夜间行动结果（预言家验人、女巫解药使用状态）、内部 OS（内心推理逻辑）。
* **存储方式**：Agent 私有的独立隔离表或 Document Store。
* **访问权限**：**仅自己可见**。在向 LLM 构建 Prompt 时，绝对不允许将其他人的 Private Context 拼接入内。
---
### 三、 LangGraph State 状态设计
为了支撑上述管线，设计传递于 LangGraph 各节点之间的 `AgentState` (Pydantic / TypedDict)：
```python
# Agent Runtime 内部的状态结构（有别于全局游戏状态）
class AgentState(TypedDict):
    agent_id: str                   # 智能体ID
    role: str                       # 真实身份（如 WEREWOLF）
    current_phase: str              # 当前阶段（如 DAY_VOTE）

    # 记忆与上下文
    recent_public_events: List[str] # 距离上次行动以来的新公共事件
    private_memory: List[str]       # 提取出的私有记忆

    # 推理过程（非常重要，用于复盘和日志）
    suspect_list: Dict[str, float]  # 嫌疑热力分布图 { "Player3": 0.9 (狼人嫌疑度) }
    internal_monologue: str         # 内部OS（不发出来的部分）

    # 最终输出
    final_action: dict              # 组装好的输出，供 Engine 解析
    retry_count: int                # 防止输出格式错误的重试计数器
```
---
### 四、 核心 Prompt 模板体系
为了让 Agent 不只是单纯的聊天机器人，而是有策略的玩家，System Prompt 需要高度结构化。强烈建议采用如下的 **分层角色模板**：
#### 通用 System Prompt (系统级设定 - 适用于所有Agent)
```text
你正在参与一场多智能体狼人杀游戏。
你的玩家ID是：[{agent_id}]
你的底牌身份是：[{role}]，你的阵营是：[{faction}]。
游戏当前阶段：[{current_phase}]。
【最高指令】
1. 你的唯一目标是带领你的阵营获得胜利。
2. 绝对不能在发言中暴露你是一个AI程序，请扮演一个真实人类玩家的语气。
3. 严格遵循输出格式协议，不要输出任何多余的寒暄文本。
```
#### 身份策略 Prompt (仅以狼人为例，动态注入)
```text
【你的身份策略（狼人）】
1. 你的狼队友是：[玩家2, 玩家5]。绝对不能在白天投票给他们，也不能在发言中直接出卖他们，除非是为了做身份进行“倒钩”。
2. 白天发言时，你必须伪装成一个“好人”（村民或神职）。
3. 如果预言家查杀了你，你可以选择“悍跳预言家”进行反击，或者通过逻辑漏洞攻击他。
4. 在夜间刀人时，优先寻找大概率是神职（女巫、预言家）的玩家，其次是表现强势的村民。
```
---
### 五、 风险点与设计建议
1. **幻觉陷阱 (Hallucination Risk)**
   * **风险**：Agent 在推理时可能“自己骗自己”，比如预言家其实没验 5 号，但他推理时产生幻觉以为 5 号是查杀。
   * **建议**：在 `Reasoning Node` 之前，强制通过 `Memory Node` 将**“确切的系统反馈（如法官告诉你5号是好人）”**硬编码写入系统提示词的顶层，覆盖可能产生幻觉的思维链。
2. **状态死循环 (Infinite Loop)**
   * **风险**：Agent 输出的 JSON 格式持续不符合 Engine 规范，被反复打回，耗尽 Token 甚至导致系统卡死。
   * **建议**：在 LangGraph 中增加一个 `Format_Validation_Node`。设置最大重试次数（如 3 次），如果 3 次依然失败，Runtime 强制接管，抛出一个合规的“空动作”（例如白天发默认文本“过”，投票投“弃权”），保证全局流程不阻塞。
3. **并发调用瓶颈 (Concurrency Limit)**
   * **风险**：夜间阶段，如果 12 个 Agent 并发向 LLM API 发起请求，可能触发 API 厂商的 QPS 限制。
   * **建议**：在 Runtime 外壳实现一个带有退避重试策略的请求队列机制；对于无关紧要的动作可以适当增加时延。