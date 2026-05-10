核心结论先行：

Game Engine 是整个狼人杀系统的“绝对裁判”。它的最高设计原则是：**规则必须硬编码，绝对不允许 LLM 自由发挥或参与规则裁定** [1, 2]。Agent 只负责提交意图（Intent），由引擎负责解析、结算和推进状态 [1]。

以下为你整理的 Game Engine 完整设计方案，可以直接作为你的架构文档或开发蓝图：

---

### 一、 架构与模块拆分

对局引擎层建议拆分为以下 6 个核心子模块 [1]：

1. Lifecycle Manager（生命周期管理器）：负责整局游戏的创建、初始化与销毁。

2. Phase State Machine（阶段状态机）：负责白天与夜晚的流程流转。

3. Role & Ability System（身份与能力系统）：定义角色属性与行动权限。

4. Action Resolver（行动解析与结算器）：处理并发意图并结算结果（如夜晚的刀、救、验）。

5. Communication & Vote Controller（交流与投票控制器）：管理发言顺序与投票统计。

6. Win Condition Evaluator（胜负判定器）：独立评估触发游戏结束的条件。

---

### 二、 核心状态机与流程设计

强烈建议**不要用 if-else 写死流程**，而是采用标准的状态机设计，这在后续接入 LangGraph 时会非常平滑 [1]。

#### 1. 游戏全局生命周期状态

```text

INIT (初始化玩家与身份) -> START (开局) -> RUNNING (对局中) -> SETTLING (结算) -> FINISHED (结束) / ABORTED (异常中断)

```

#### 2. 对局内阶段推进流（Phase Transition）

```text

NIGHT (夜晚行动) 

  -> DAY_DISCUSSION (白天发言) 

  -> DAY_VOTE (白天投票) 

  -> LAST_WORD (遗言环节) 

  -> CHECK_WINNER (胜负判定) 

  -> NEXT_ROUND (进入下一夜)

```

#### 3. 身份与行动窗口映射

规则系统必须明确在什么阶段，谁拥有什么行动权 [1]：

* 狼人/预言家NIGHT 阶段

* 女巫NIGHT 阶段（但在系统底层结算时，女巫的救人结算需在狼人刀人之后）

* 猎人：死亡事件触发后

* 村民DAY_DISCUSSION 和 DAY_VOTE 阶段

---

### 三、 核心数据协议（Schema 设计）

为了防止后期日志爆炸和格式混乱，引擎层必须强制规定所有输入输出的结构化协议 [1]。

#### 1. 统一行动协议 (Action Schema)

所有 Agent 的动作必须统一封装为此结构再交由引擎处理：

```json

{

  "action_type": "KILL/SAVE/CHECK/VOTE/SPEAK",

  "actor_id": "玩家ID",

  "target_id": "目标玩家ID (无目标可为null)",

  "phase": "当前阶段 (如 NIGHT)",

  "round": 1,

  "reason": "Agent的内部推理依据",

  "confidence": 0.85,

  "timestamp": "ISO时间戳"

}

```

#### 2. 发言结构协议 (Speech Schema)

不要只记录一段文本，这对于后续做“怀疑热力图”和“复盘”极其重要 [1]：

```json

{

  "speech": "我昨晚验了3号，他是金水...",

  "suspects": [5, 7], 

  "stance": "PRO_VILLAGER",

  "emotion": "CONFIDENT",

  "confidence": 0.9

}

```

#### 3. 投票推理记录 (Vote Schema)

记录为什么投，而不仅仅是投给谁：

```json

{

  "vote_target": 5,

  "reason": "5号发言前后矛盾，且与狼人票型一致",

  "certainty": 0.82,

  "alternative_targets": [7]

}

```

---

### 四、 关键难点方案：夜晚特殊身份行动结算

夜晚阶段是**并发意图 + 顺序结算**。

* 错误做法：让狼人先动，动完更新全局状态，再让女巫动。这会导致信息泄露或时序 Bug。

* 正确做法（推荐）：

  1. 提交意图（Intent Submission）：所有夜间行动的 Agent 并发思考，并向引擎提交 Action [1]。

  2. 统一解析（Resolution）：倒计时结束或全员提交后，Action Resolver 按照“规则优先级”统一结算 [1]。

     * 逻辑链：狼人刀 3 号 -> 判断女巫是否用解药救 3 号 -> 判断预言家验人结果 -> 输出最终天亮死亡名单。

---

### 五、 风险提示与设计建议

1. 边界风险：如果不严格做 Schema 校验，Agent 可能会输出 action_type: "FLY" 或试图在夜晚发言。

   * 建议：在 Engine 层入口增加 Pydantic 校验，如果 Agent 动作非法，直接抛弃或强制令其重试（最多重试 3 次，否则按“弃权/空过”处理）。

2. 胜负判定散落风险：不要在各个逻辑分支里写 if 狼人数量 == 0。

   * 建议：把 Win Condition Evaluator 抽离成独立函数，在每个 Phase 结束时统一下发调用 [1]。