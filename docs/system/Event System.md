**核心结论先行：**
千万不要把“动作（Action）”和“事件（Event）”混为一谈！
* **Action 是“意图（Intent）”**：是 AI 想要做的事，可能会被规则驳回（比如死人想发言）。
* **Event 是“既定事实（Fact）”**：是 Action 经过 Game Engine 结算后，**确定已经发生并不可篡改的结果**。
Event System 必须采用 **发布/订阅（Pub/Sub）机制** 与 **事件溯源（Event Sourcing）模式**。它负责把 Engine 结算的结果分发给 Memory（让 AI 记住事实）、Frontend（让前端大屏播动画）和 Evaluator（让日志复盘有迹可循）。
以下是专为多智能体狼人杀设计的 Event System 落地架构：
---
### 一、 核心架构：Event Bus (事件总线)
整个对局中，所有的信息流转必须通过中心的 `Event Dispatcher`。
**数据流向：**
1. **Producer（生产者）**`Phase Machine`（产生阶段切换事件）和 `Action Resolver`（产生动作结算事件）。
2. **Event Bus（总线）**：接收事件，打上全局递增的序号（Sequence ID）和时间戳。
3. **Consumers（消费者）**：
   * **Memory Updater**：拉取事件写入数据库，供 Agent 下次推理使用。
   * **WebSocket Broadcaster**：推送到前端观战大屏。
   * **Replay Logger**：写盘入库，用于赛局回放和 AI 胜率分析。
---
### 二、 事件分类与可见性隔离 (Visibility Control)
狼人杀是“信息不对称博弈”，Event System 最重要的核心能力是**根据 Visibility（可见性）进行严格的路由隔离**。
所有的 Event 必须带有 `visibility` 属性，分为三类：
1. **PUBLIC（全局公开事件）**
   * **路由策略**：发给所有存活/死亡的 Agent 的公共记忆池，同时发给前端观战大屏。
   * **举例**：系统播报（天亮了）、玩家发言、公开投票结果。
2. **PRIVATE（私人隐秘事件）**
   * **路由策略**：**仅**发给目标 Agent 的私有记忆池，**并**发给前端观战大屏（观众通常拥有上帝视角）。
   * **举例**：预言家的验人结果反馈、女巫昨夜得知谁被刀了。
3. **FACTION（阵营共享事件）**
   * **路由策略**：发给特定阵营（如所有狼人），并发给前端。
   * **举例**：夜晚狼人同伴的交流、狼人刀人意图的汇总。
---
### 三、 核心事件数据协议 (Event Schema)
强烈建议所有事件继承自一个基础大类（BaseEvent），在数据库里存为一条 JSON。这比建十几张不同的表要灵活得多。
#### 1. 基础事件结构 (Base Event)
```json
{
  "event_id": "evt_1001",
  "game_id": "game_999",
  "seq_num": 15,                 // 严格递增的序列号（极其重要，防止时序错乱）
  "event_type": "SPEECH_EVENT",
  "visibility": "PUBLIC",        // PUBLIC | PRIVATE | FACTION
  "target_agents": ["ALL"],      // 如果是 PRIVATE，这里填具体的 agent_id
  "timestamp": "2023-10-24T10:00:00Z",
  "payload": {}                  // 具体的事件内容
}
```
#### 2. 派生事件示例 (Payload Definitions)
**A. 系统流转事件 (Phase Transition Event)**
```json
// payload
{
  "from_phase": "NIGHT_ACTION",
  "to_phase": "NIGHT_RESOLVE",
  "announcement": "系统正在结算夜间行动..."
}
```
**B. 玩家发言事件 (Speech Event)**
```json
// payload
{
  "speaker_id": "player_3",
  "content": "我是预言家，昨晚验了5号，他是查杀。",
  "emotion_tags": ["CONFIDENT", "AGGRESSIVE"] // 可选：给前端驱动虚拟人表情使用
}
```
**C. 私密结算事件 (Private Resolution Event - 如验人)**
```json
// visibility: "PRIVATE", target_agents: ["player_3"]
// payload
{
  "action_type": "SEER_CHECK",
  "target_id": "player_5",
  "result_faction": "WEREWOLF",  // 查杀结果
  "system_message": "你昨晚查验了5号玩家，他的身份是：狼人。"
}
```
---
### 四、 高级特性：事件溯源 (Event Sourcing) 与回放
在传统的 CRUD 系统里，数据库只存“当前状态”（比如谁死了、谁活着）。但我们做 AI 狼人杀，必须引入**事件溯源**概念。
*   **原则**：整个对局的“当前状态”是无法直接修改的，所有的状态变更都是通过“回放/叠加（Reduce）”从第一天到现在的 Event 来计算得出的。
*   **收益**：
    1.  **极低成本的“时间倒流”**：前端如果要实现“拖动进度条看复盘”，只需要按照 `seq_num` 重新回放事件即可，无需后端写复杂的倒退逻辑。
    2.  **LangGraph 容错性**：如果大模型在第 3 天宕机了，只要拉取前 3 天的 Event 重建 State，可以完美无缝拉起恢复断点。
---
### 五、 风险防范与调优（防坑指南）
1. **时序错乱风险 (Out of Order Execution)**
   * **坑点**：并发结算时`timestamp` 可能会由于毫秒级误差导致事件在数据库中的顺序与实际发生顺序颠倒，这会让基于 Timeline 盘逻辑的 AI 产生幻觉。
   * **解决方案**：引入全局单调递增的 `seq_num`（如 Redis 的 `INCR` 或 MySQL 的自增ID）。Memory 获取数据和 Frontend 渲染必须**严格按照 `seq_num` 排序**。
2. **前端上帝视角的剧透风险 (God View Leak)**
   * **坑点**：虽然观众能看到所有人的底牌，但如果你用同一个 Websocket 频道无差别广播，如果有恶意用户或评测脚本抓包，就会泄露。或者后端一不小心把广播给前端的包发给了 Agent。
   * **解决方案**：Event Bus 分离 `Agent Stream` 和 `Spectator Stream`。Agent 只能从特定的安全接口拉取经过 `visibility` 过滤的内容。
3. **大量事件导致的 Token 爆炸**
   * **坑点**：如果每局发生 200 个 Event，全部塞给大模型，必超上下文。
   * **解决方案**：在上一节 `Memory System` 提到的异步摘要机制，其实就是订阅了 Event Bus 的消费者。当侦测到 `NIGHT_START` 事件时，触发一个轻量级大模型把过去白天的 Event 聚合成一段文字。