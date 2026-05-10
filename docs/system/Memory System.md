**核心结论先行：**
在狼人杀中，**绝对不要使用传统的 RAG（向量检索）来做记忆系统！** 狼人杀是强逻辑、强时序的游戏，向量检索会丢失时间线，导致 AI 拿前天的发言去反驳昨天的逻辑。
最好的记忆架构是：**“结构化时间线（Timeline）” + “动态信念树（Belief State）” + “长上下文压缩（Summarization）”**。
以下是专为多智能体狼人杀设计的 Memory System 完整落地方案：
---
### 一、 记忆系统的三层架构
为了控制 Token 消耗并防止 AI “遗忘”或“串戏”，我们需要把记忆严格分层：
#### 1. Public Ledger（公共事件账本 - 事实层）
*   **定位**：全局唯一，所有 Agent 共享只读。
*   **内容**：按时间顺序记录法官播报、玩家发言原文、投票结果。
*   **形态**：线性追加的不可变日志（Append-only Log）。
#### 2. Private Ledger（私有事件账本 - 事实层）
*   **定位**：Agent 私有，绝对物理隔离。
*   **内容**：初始底牌、夜间技能使用结果（预言家验人结果、女巫知道谁被刀）、狼人队友名单。
*   **形态**：KV 结构或短列表。**这部分在组装 Prompt 时必须具有最高优先级，强制覆盖 AI 的幻觉。**
#### 3. Belief State（内部信念状态 - 认知层 / 最核心！）
*   **定位**：Agent 每轮推理后的“状态快照”，用于维持逻辑连贯性（避免上一轮踩 3 号，这一轮突然保 3 号）。
*   **内容**：记录当前对局中每个人的身份猜测、信任度打分、核心逻辑。
*   **形态**：一个 JSON 字典。
---
### 二、 核心数据表结构设计 (以关系型数据库/JSON为基准)
对于后端工程落地，建议设计以下三张核心表（可存在 MySQL/PostgreSQL，或 MongoDB）：
#### 1. `game_event_logs` (全局公共流水表)
```sql
CREATE TABLE game_event_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    game_id VARCHAR(64) NOT NULL,
    day_num INT NOT NULL,               -- 第几天 (如 1, 2)
    phase VARCHAR(32) NOT NULL,         -- 阶段 (如 DAY_DISCUSSION)
    event_type VARCHAR(32) NOT NULL,    -- SPEECH, VOTE, SYSTEM_ANNOUNCEMENT
    actor_id VARCHAR(32),               -- 发起者 (系统播报为 null)
    content TEXT NOT NULL,              -- 发言内容或动作详情
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```
#### 2. `agent_private_memories` (私有记忆表)
```sql
-- 记录绝对真实的私有信息
CREATE TABLE agent_private_memories (
    game_id VARCHAR(64) NOT NULL,
    agent_id VARCHAR(32) NOT NULL,
    memory_type VARCHAR(32) NOT NULL,   -- INITIAL_ROLE, NIGHT_ACTION_RESULT
    content JSON NOT NULL,              -- 例: {"target": 3, "result": "WEREWOLF"}
    day_num INT NOT NULL
);
```
#### 3. `agent_belief_states` (信念状态表 - 认知存档)
```json
// 每当 Agent 完成一轮行动，更新此 JSON 并落库
{
  "game_id": "g_123",
  "agent_id": "player_5",
  "current_day": 2,
  "claims": {
    "player_1": "预言家 (悍跳嫌疑大)",
    "player_2": "村民",
    "player_8": "女巫 (单方面跳，暂信)"
  },
  "suspect_heatmap": {
    "player_1": 0.9,  // 90% 概率是狼
    "player_4": 0.6,
    "player_7": 0.1   // 铁好人
  },
  "my_strategy": "白天跟着8号女巫走，找机会抗推1号" // 下一轮 Prompt 的行为指导
}
```
---
### 三、 Context 组装策略 (Prompt 动态拼接逻辑)
在 LangGraph 的 `Memory Node` 中，当轮到 Agent 行动时，你需要写一段逻辑来组装输入给 LLM 的 Context。
**组装公式 = 绝对事实（短） + 当前信念（短） + 历史摘要（中） + 最新动态（长）**
直接可用的 Prompt 组装模板：
```text
【系统设定与私有记忆】（最高权重）
你的身份：{role}。
你的私有信息：{private_ledger_formatted}
(例：你是预言家，第一夜你验了3号，是狼人。第二夜你验了7号，是好人。)
【你当前的逻辑盘点 (Belief State)】
你上一轮结束时的内心分析：
- 场上身份声明：{claims_json}
- 你的重点怀疑对象：{suspects_json}
- 你的既定策略：{strategy_text}
【昨日及更早的剧情摘要】
{history_summary}
(由一个轻量级LLM后台异步压缩，例：第一天1号跳预言家查杀2号，2号被票出局，昨夜平安夜。)
【当前阶段最新动态】（时间线严格排序）
{recent_events_from_public_ledger}
(例：
法官：现在是第二天白天发言阶段。
3号玩家发言："我觉得1号是真预言家..."
4号玩家发言："3号在划水，我怀疑3号...")
请结合以上信息，给出你现在的推理并决定你的行动。
```
---
### 四、 关键机制设计：记忆压缩机制 (Memory Summarization)
到了游戏的第 3 天、第 4 天，对话历史会变得极长，直接喂给大模型会导致 **Lost in the Middle（中间注意力丢失）**，并且费用高昂。
**落地建议（异步压缩管线）：**
1. 每天（Day_N）结算进入夜晚时，触发一个后台任务。
2. 调用一个便宜的模型（如 GPT-4o-mini 或 DeepSeek-Chat），输入当天的完整 `game_event_logs`。
3. 生成一份 **全局每日摘要（Global Day Summary）**（例如：“第二天白天，5号起跳预言家保8号，最终7号被高票票死”）。
4. 第二天起，Prompt 中的“历史信息”用摘要替代，只有“当天发生的最新事件”才给原文日志。
---
### 五、 风险提示与防坑指南
1. **幻觉覆盖问题**
   * **坑点**：Agent 自己推理出“3号是预言家”，然后在之后的记忆里把这个当成了“事实”，忽略了3号其实是狼人悍跳。
   * **防范**：在 Prompt 中必须用明确的结构化标签区分 `【系统判定的绝对事实】` 和 `【你个人的主观猜测】`。
2. **死者发言污染**
   * **坑点**：死亡玩家的状态如果没有被明确标记，AI 会在盘逻辑时继续要求死亡玩家表水，或者给死者投票。
   * **防范**：在 Belief State 和 Public Ledger 传递前，由 Game Engine 在名单中强制加 tag，例如`3号玩家(已死亡)`。
3. **“上帝视角”泄露**
   * **坑点**：开发时为了调试，不小心把全场身份 JSON 打印到了所有 Agent 的日志里，导致 LangGraph 读取上下文时带入了全局数据。
   * **防范**：严格执行“只根据 `agent_id` 和 `game_id` 拉取数据”的 SQL/ORM 规则，禁止传递包含全场底牌的 Global State 给具体的 Agent Node。