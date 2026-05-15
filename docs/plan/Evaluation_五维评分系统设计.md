# Evaluation 五维评分系统设计

## 1. 背景与目标

在 AI 多智能体狼人杀博弈平台中，单纯以“胜率”作为评估 AI 能力的唯一指标存在极大局限性。狼人杀游戏具有高度的随机性和信息不对称性，胜负往往受运气、角色分配及其他 Agent 行为的综合影响。

为了更科学、全面地评估 AI Agent 的“智商”与博弈能力，本项目（Phase 5）引入 **“过程导向 + 结果导向”相结合的量化评测体系**。依托事件溯源（Event Sourcing）架构持久化下来的对局事实，采用 **LLM-as-a-Judge（大模型作为裁判）** 与 **启发式脚本（Heuristic Rules）** 双轨并行的评分机制，构建五维评分雷达图。

## 2. 五维评分体系定义

为了让狼人阵营和好人阵营都能获得完整的五维评估，我们将维度划分为 **3个通用维度** 和 **2个阵营专属维度**。

### 通用维度 (Common)

#### 2.1 规则服从度 (Rule Compliance) - 客观/脚本计算
*   **定义**：评估 AI 是否严格遵守了 JSON 格式化输出和游戏规则（基础指令遵循能力）。
*   **计算方式**：`100 - (格式错误重试次数 + 非法动作拦截次数) * 10`。
*   **数据来源**：`EventRecord` 中的动作校验失败事件、重试日志。

#### 2.2 逻辑连贯性 (Logical Consistency) - 客观+主观/脚本+内部状态对比
*   **定义**：评估 AI 白天的发言、投票行为与其内心的 `Belief State`（信任热力图/嫌疑人名单）是否一致。
*   **计算方式**：对比 Agent 的公开行为（如投票给 X）与其私有记忆/内部状态（如认为 X 是好人）。若出现矛盾且非特定战术（如倒钩），则扣分。
*   **数据来源**：`EventRecord` (公开行为) + Agent Private Memory (内部状态)。

#### 2.3 角色扮演与沉浸感 (Roleplay) - 主观/LLM 裁判打分
*   **定义**：评估 AI 是否有出戏表现、是否带有明显的“AI 机器味”话术，以及是否符合其设定的性格。
*   **计算方式**：由 LLM 裁判根据全局发言记录进行主观打分。
*   **数据来源**：全局发言记录。

### 狼人专属维度 (Werewolf Only)

#### 2.4 伪装与欺骗 (Deception) - 主观/LLM 裁判打分
*   **定义**：评估狼人发言是否像好人，能否成功隐藏身份、编造完美逻辑链并煽动抗推好人。
*   **计算方式**：由 LLM 裁判结合上帝视角和好人玩家的内部怀疑度进行打分。好人对该狼人的平均怀疑度越低，得分越高。
*   **数据来源**：全局发言记录 + 好人玩家的 `suspect_heatmap`。

#### 2.5 找神能力 (God Role Deduction) - 客观+主观/脚本+LLM
*   **定义**：评估狼人推理出神职身份的能力。
*   **计算方式**：根据夜晚击杀目标的真实身份（杀到神职加分），结合狼人内部推理 OS（是否通过逻辑准确盘出神职而非盲刀）由 LLM 综合评分。
*   **数据来源**：夜晚击杀记录 + 狼人 Private Memory (内部推理 OS) + 全局真实身份数据。

### 好人专属维度 (Villager/God Only)

#### 2.6 态势感知与推理 (Situational Awareness) - 客观/脚本对比
*   **定义**：评估好人“找狼”的准确率和信息处理能力。
*   **计算方式**：对比 Agent 每轮结束时的 `suspect_heatmap`（嫌疑人名单）与上帝视角的“真实狼人名单”的重合度（IoU 或精准率/召回率）。
*   **数据来源**：Agent Private Memory + 全局真实身份数据。

#### 2.7 统帅与引导 (Leadership) - 客观+主观/数据统计+LLM
*   **定义**：评估好人玩家的发言能有多少人跟票/认同，是否能在关键时候站出来引导好人阵营。
*   **计算方式**：客观统计跟票率（号召票出 X，后续多少人跟票），结合 LLM 裁判评估其在关键轮次（如真假预言家对跳）的引导作用。
*   **数据来源**：发言语义提取（目标提取） + 投票事件记录 + LLM 裁判分析。

## 3. 评测管线架构 (Evaluation Pipeline)

评测过程为**异步离线计算**，避免阻塞游戏主流程。

### 3.1 触发机制
在 `GameEngine` 判定游戏结束（`GAME_OVER` 阶段）时，触发 Celery 异步任务 `evaluate_game_task`（位于 `ai_werewolf_core/tasks/eval.py`）。

### 3.2 处理流程
1.  **Data Extraction (数据抽取)**：
    *   从 `EventRecord` 表拉取本局完整时间线事件。
    *   从 Redis 或 DB 提取各 Agent 的内部心路历程（Belief States, 思考过程）。
2.  **Rule-based Scoring (规则打分)**：
    *   通过 Python 脚本计算“规则服从度”、“煽动与说服力”、“态势感知与推理”等客观指标。
3.  **LLM-as-a-Judge (大模型裁判)**：
    *   组装 Prompt，包含上帝视角数据、玩家发言与内部思维。
    *   调用独立的法官模型（建议使用成本较低但逻辑能力尚可的模型，如 `gpt-4o-mini` 或 `deepseek-chat`）进行质性分析与主观打分（伪装度、逻辑连贯性）。
4.  **Report Generation (报告生成)**：
    *   汇总客观与主观得分，生成结构化复盘日志（JSON）。
    *   持久化到数据库。

## 4. 数据库设计扩展 (ORM)

需要在 `ai_werewolf_core/db/models.py` 中新增表结构（需同步更新 `docs/db/sql table.md`）：

### 4.1 `MatchReport` (对局复盘报告表)
*   `id`: 唯一标识
*   `game_id`: 关联 `GameRecord`
*   `duration_seconds`: 对局时长
*   `winner`: 获胜阵营
*   `mvp_agent_id`: MVP 玩家 ID
*   `faction_win_probability_curve`: 阵营胜率走势（JSONB，用于前端折线图）
*   `created_at`: 生成时间

### 4.2 `AgentEvaluation` (玩家评测明细表)
*   `id`: 唯一标识
*   `report_id`: 关联 `MatchReport`
*   `player_id`: 关联 `PlayerRecord`
*   `role`: 玩家身份
*   `rule_compliance_score`: 规则服从度得分 (通用)
*   `logical_consistency_score`: 逻辑连贯性得分 (通用)
*   `roleplay_score`: 角色扮演得分 (通用)
*   `deception_score`: 伪装与欺骗得分 (狼人专属)
*   `god_deduction_score`: 找神能力得分 (狼人专属)
*   `situational_awareness_score`: 态势感知得分 (好人专属)
*   `leadership_score`: 统帅与引导得分 (好人专属)
*   `strengths`: 高光时刻总结 (LLM 生成)
*   `weaknesses`: 致命失误总结 (LLM 生成)
*   `overall_review`: 综合评价 (LLM 生成)

## 5. 核心模块划分

建议在 `ai_werewolf_core/core/` 下新增 `eval` 包：

```text
ai_werewolf_core/core/eval/
├── __init__.py
├── pipeline.py      # 评测管线主入口，串联各步骤
├── extractor.py     # 数据抽取器，从 EventRecord 和 Memory 提取结构化数据
├── heuristic.py     # 启发式规则评分器 (计算客观指标)
├── llm_judge.py     # LLM 裁判调用器 (组装 Prompt，解析 JSON 结果)
└── schemas.py       # 评测相关的 Pydantic 模型定义
```

## 6. LLM-as-a-Judge 核心 Prompt 设计

```text
【裁判任务说明】
你是一个专业的狼人杀多智能体系统评测裁判。
现在有一局游戏已经结束，请你根据以下提供的【上帝视角数据】和【该玩家的全部发言与内部思维】，对玩家 [{agent_id}] 进行行为评分与复盘分析。

【评测原则】
1. 请剥离上帝视角，仅根据该玩家当时能够获取的公开信息，评判其推理是否合理。哪怕他的结论是错的，只要当时逻辑自洽，就不应扣减逻辑推理分。
2. 评分尺度：9-10分代表完美骗过所有人/找出所有狼；6-8分代表表现正常但无亮眼发挥；3-5分代表出现明显逻辑漏洞但未暴露；0-2分代表自爆或违规。

【上帝视角数据】
{global_roles_json}

【被评测玩家数据】
该玩家底牌：{agent_role}
该玩家所属阵营：{agent_faction}

【该玩家的关键行为日志 (按时间线)】
{agent_action_and_speech_logs}

【该玩家的内部思维链 (Belief State)】
{agent_internal_monologues}

【评测任务约束】
请你根据以上信息，严格输出一个 JSON 格式的评测报告。不要输出其他废话。
JSON 结构如下：
{{
  "roleplay_score": 0-10, // 角色扮演得分（满分10，是否有出戏表现或AI特有的话术痕迹，通用）
  "deception_score": 0-10, // 伪装与欺骗得分（满分10，狼人专属，好人填 null）
  "god_deduction_score": 0-10, // 找神能力得分（满分10，狼人专属，基于击杀目标和推理OS，好人填 null）
  "leadership_score": 0-10, // 统帅与引导得分（满分10，好人专属，关键时刻引导好人阵营的表现，狼人填 null）
  "strengths": "简短的一句话总结他的高光时刻/优点",
  "weaknesses": "简短的一句话总结他的致命失误/逻辑漏洞",
  "overall_review": "一段100字左右的综合评价，用于展示在前端复盘大屏上。"
}}
```

## 7. 实施步骤 (Todo List)

1.  **数据库 Schema 更新**: 在 `docs/db/sql table.md` 中补充 `MatchReport` 和 `AgentEvaluation` 表定义，并在 `ai_werewolf_core/db/models.py` 中实现 ORM 模型，生成 Alembic 迁移脚本。
2.  **数据抽取模块 (`extractor.py`)**: 实现从 `EventRecord` 提取对局时间线、玩家发言、投票记录及内部思维链的逻辑。
3.  **启发式规则打分 (`heuristic.py`)**: 实现规则服从度、跟票率、找狼准确率等客观指标的计算逻辑。
4.  **LLM 裁判模块 (`llm_judge.py`)**: 实现 Prompt 组装、调用 Model Adapter 获取评价并解析 JSON 的逻辑。
5.  **评测管线整合 (`pipeline.py` & `eval.py`)**: 将上述模块串联，在 `evaluate_game_task` 中完整实现异步评测流程，并将结果落库。
6.  **API 接口暴露**: 在 `ai_werewolf_core/api/routes/` 下新增接口，供前端查询对局复盘报告和雷达图数据。