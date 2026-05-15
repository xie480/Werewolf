# SQL 表结构文档

> **用途**: 本文档汇总项目中所有 PostgreSQL 数据库表的定义，作为 Schema 设计的唯一参考来源。
>
> **数据源**: 基于 `ai_werewolf_core/db/models.py` 中的 SQLAlchemy ORM 模型自动提取。
>
> **最后更新**: 2026-05-15

---

## 表一览

| 表名 | ORM 模型 | 用途 | 主键策略 |
|------|---------|------|---------|
| `games` | `GameRecord` | 对局元信息与当前状态快照 | Snowflake (String 19) |
| `players` | `PlayerRecord` | 对局内每个 Agent 的身份、座位和存活状态 | Snowflake (String 19) |
| `events` | `EventRecord` | Event Sourcing 核心存储，记录所有对局事实 | Snowflake (String 19) |
| `model_config` | `ModelConfig` | 存储 LLM 供应商配置 | String (64) |
| `ai_player_profiles` | `AIPlayerProfile` | 记录 AI 玩家的固有属性和配置 | Snowflake (String 19) |
| `ai_player_stats` | `AIPlayerStats` | 记录并持久化玩家的核心行为数据 | Foreign Key (String 19) |
| `match_reports` | `MatchReport` | 存储每局游戏结束后的全局复盘数据 | Snowflake (String 19) |
| `agent_evaluations` | `AgentEvaluation` | 存储每个 Agent 在对局中的五维评分及 LLM 裁判的详细评价 | Snowflake (String 19) |

---

## 1. `games` — 对局记录表

**用途**: 维护每局游戏的元信息和当前状态快照。对局的真实状态由 `events` 表的事件溯源决定，此表仅用于快速查询而无需回放全部事件。

**ORM 模型**: `ai_werewolf_core.db.models.GameRecord`

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | `VARCHAR(19)` | `PRIMARY KEY`, `INDEX` | 雪花算法全局唯一 ID |
| `status` | `ENUM(GameStatus)` | `DEFAULT 'INIT'` | 对局生命周期状态 (INIT / START / RUNNING / SETTLING / FINISHED / ABORTED) |
| `phase` | `ENUM(GamePhase)` | `DEFAULT 'INIT'` | 当前游戏阶段 (INIT / NIGHT_START / NIGHT_WOLF_ACT / ... / GAME_OVER) |
| `round` | `INTEGER` | `DEFAULT 1` | 当前轮次 |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL`, `DEFAULT now()` | 记录创建时间 |
| `updated_at` | `TIMESTAMPTZ` | `NOT NULL`, `DEFAULT now()`, `ON UPDATE now()` | 记录更新时间 |

**关联关系**:
- `players`: 一对多 → `PlayerRecord`（级联删除）
- `events`: 一对多 → `EventRecord`（级联删除）
- `match_report`: 一对一 → `MatchReport`（级联删除）

**枚举值参考**:
- `GameStatus`: `INIT`, `START`, `RUNNING`, `SETTLING`, `FINISHED`, `ABORTED`
- `GamePhase`: `INIT`, `NIGHT_START`, `NIGHT_WOLF_ACT`, `NIGHT_WITCH_ACT`, `NIGHT_SEER_ACT`, `NIGHT_RESOLVE`, `DAY_START`, `DAY_DISCUSSION`, `DAY_VOTE`, `VOTE_RESOLVE`, `DAY_PK_DISCUSSION`, `DAY_PK_VOTE`, `HUNTER_SHOOT`, `LAST_WORDS`, `GAME_OVER`

---

## 2. `players` — 玩家记录表

**用途**: 记录对局中每个 Agent 的身份、座位和存活状态，便于快速查询当前存活玩家列表等聚合信息。

**ORM 模型**: `ai_werewolf_core.db.models.PlayerRecord`

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | `VARCHAR(19)` | `PRIMARY KEY`, `INDEX` | 雪花算法全局唯一 ID |
| `game_id` | `VARCHAR(19)` | `FOREIGN KEY → games.id ON DELETE CASCADE`, `INDEX` | 所属对局 ID |
| `player_id` | `VARCHAR(32)` | — | 玩家标识，如 `player_1` |
| `ai_profile_id` | `VARCHAR(19)` | `FOREIGN KEY → ai_player_profiles.id ON DELETE SET NULL`, `NULLABLE` | 关联的 AI 玩家档案 ID |
| `seat_number` | `INTEGER` | — | 座位号 (1-based) |
| `role` | `ENUM(Role)` | — | 玩家身份 (VILLAGER / WEREWOLF / SEER / WITCH / HUNTER) |
| `is_alive` | `BOOLEAN` | `DEFAULT TRUE` | 是否存活 |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL`, `DEFAULT now()` | 记录创建时间 |
| `updated_at` | `TIMESTAMPTZ` | `NOT NULL`, `DEFAULT now()`, `ON UPDATE now()` | 记录更新时间 |

**关联关系**:
- `game`: 多对一 → `GameRecord`
- `ai_profile`: 多对一 → `AIPlayerProfile`
- `evaluations`: 一对多 → `AgentEvaluation`（级联删除）

**枚举值参考**:
- `Role`: `VILLAGER`, `WEREWOLF`, `SEER`, `WITCH`, `HUNTER`

---

## 3. `events` — 事件记录表

**用途**: Event Sourcing 架构的核心存储。所有对局中发生的事实（Fact）都通过此表持久化，支持按可见性过滤查询、按 `seq_num` 保证时序，以及复盘时的完整事件回放。

**ORM 模型**: `ai_werewolf_core.db.models.EventRecord`

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | `VARCHAR(19)` | `PRIMARY KEY`, `INDEX` | 雪花算法全局唯一 ID |
| `event_id` | `VARCHAR(64)` | `UNIQUE`, `INDEX` | 事件业务 ID (UUID v4) |
| `game_id` | `VARCHAR(19)` | `FOREIGN KEY → games.id ON DELETE CASCADE`, `INDEX` | 所属对局 ID |
| `seq_num` | `INTEGER` | `INDEX` | 全局递增序列号，保证时序 |
| `event_type` | `ENUM(EventType)` | `INDEX` | 事件类型 |
| `visibility` | `ENUM(Visibility)` | — | 可见性 (PUBLIC / PRIVATE / FACTION) |
| `target_agents` | `JSONB` | `DEFAULT []` | 目标玩家 ID 列表 |
| `payload` | `JSONB` | `DEFAULT {}` | 事件具体内容（灵活结构） |
| `timestamp` | `TIMESTAMPTZ` | — | 事件发生时间 |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL`, `DEFAULT now()` | 记录创建时间 |

**关联关系**:
- `game`: 多对一 → `GameRecord`

**枚举值参考**:
- `EventType`: `PHASE_TRANSITION_EVENT`, `SYSTEM_ANNOUNCEMENT`, `GAME_OVER_EVENT`, `PLAYER_DEATH`, `VOTE_EVENT` (等)
- `Visibility`: `PUBLIC`, `PRIVATE`, `FACTION`

---

## 4. `model_config` — 模型配置表

**用途**: 存储 LLM 供应商配置。支持运行时动态增删改模型配置，无需重启服务。

**ORM 模型**: `ai_werewolf_core.db.models.ModelConfig`

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | `VARCHAR(64)` | `PRIMARY KEY` | 模型唯一标识 |
| `provider` | `VARCHAR(32)` | `NOT NULL` | 提供者名称 |
| `name` | `VARCHAR(64)` | `NOT NULL` | 业务层使用的模型名称 |
| `api_key` | `VARCHAR(255)` | `NOT NULL` | API Key |
| `base_url` | `VARCHAR(255)` | `NOT NULL` | API 基础 URL |
| `model_name` | `VARCHAR(64)` | `NOT NULL` | LLM 实际模型名称 |
| `temperature` | `FLOAT` | `DEFAULT 0.7` | 默认温度 |
| `max_tokens` | `INTEGER` | `DEFAULT 1024` | 默认最大 token |
| `timeout` | `FLOAT` | `DEFAULT 15.0` | 硬超时（秒） |

---

## 5. `ai_player_profiles` — AI 玩家档案表

**用途**: 记录 AI 玩家的固有属性和配置。

**ORM 模型**: `ai_werewolf_core.db.models.AIPlayerProfile`

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | `VARCHAR(19)` | `PRIMARY KEY` | 雪花算法全局唯一 ID |
| `name` | `VARCHAR(64)` | `INDEX` | 玩家显示名称 |
| `avatar_url` | `VARCHAR(255)` | `NULLABLE` | 玩家头像 URL |
| `model_provider` | `VARCHAR(32)` | — | 模型提供商 |
| `model_name` | `VARCHAR(64)` | — | 具体模型版本 |
| `system_prompt` | `TEXT` | `NULLABLE` | 特定性格或行为准则 Prompt |
| `temperature` | `FLOAT` | `DEFAULT 0.7` | 模型生成温度参数 |
| `is_active` | `BOOLEAN` | `DEFAULT TRUE` | 是否在玩家库中激活可用 |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL`, `DEFAULT now()` | 记录创建时间 |
| `updated_at` | `TIMESTAMPTZ` | `NOT NULL`, `DEFAULT now()`, `ON UPDATE now()` | 记录更新时间 |

**关联关系**:
- `stats`: 一对一 → `AIPlayerStats`（级联删除）

---

## 6. `ai_player_stats` — 玩家统计数据表

**用途**: 精确记录并持久化玩家的核心行为数据。

**ORM 模型**: `ai_werewolf_core.db.models.AIPlayerStats`

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `player_id` | `VARCHAR(19)` | `PRIMARY KEY`, `FOREIGN KEY → ai_player_profiles.id ON DELETE CASCADE` | 关联 ai_player_profiles.id |
| `total_games` | `INTEGER` | `DEFAULT 0` | 参与的总对局数 |
| `wins` | `INTEGER` | `DEFAULT 0` | 获胜局数 |
| `losses` | `INTEGER` | `DEFAULT 0` | 失败局数 |
| `response_failures` | `INTEGER` | `DEFAULT 0` | 模型调用失败/超时/格式错误的累计次数 |
| `total_actions` | `INTEGER` | `DEFAULT 0` | 累计成功执行的行动次数 |
| `total_action_time_ms` | `INTEGER` | `DEFAULT 0` | 累计行动耗时（毫秒） |
| `role_stats` | `JSONB` | `DEFAULT {}` | 按角色统计的胜负数据 |
| `last_played_at` | `TIMESTAMPTZ` | `NULLABLE` | 最后一次参与对局的时间 |

**关联关系**:
- `profile`: 一对一 → `AIPlayerProfile`

---

## 7. `match_reports` — 对局复盘报告表

**用途**: 存储每局游戏结束后的全局复盘数据，包括胜负结果、MVP、阵营胜率走势等。

**ORM 模型**: `ai_werewolf_core.db.models.MatchReport`

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | `VARCHAR(19)` | `PRIMARY KEY`, `INDEX` | 雪花算法全局唯一 ID |
| `game_id` | `VARCHAR(19)` | `FOREIGN KEY → games.id ON DELETE CASCADE`, `UNIQUE`, `INDEX` | 所属对局 ID |
| `duration_seconds` | `INTEGER` | — | 对局时长（秒） |
| `winner` | `VARCHAR(32)` | — | 获胜阵营 (VILLAGER / WEREWOLF) |
| `mvp_agent_id` | `VARCHAR(32)` | — | MVP 玩家标识 |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL`, `DEFAULT now()` | 记录创建时间 |

**关联关系**:
- `game`: 一对一 → `GameRecord`
- `evaluations`: 一对多 → `AgentEvaluation`（级联删除）

---

## 8. `agent_evaluations` — 玩家评测明细表

**用途**: 存储每个 Agent 在对局中的五维评分及 LLM 裁判的详细评价。

**ORM 模型**: `ai_werewolf_core.db.models.AgentEvaluation`

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | `VARCHAR(19)` | `PRIMARY KEY`, `INDEX` | 雪花算法全局唯一 ID |
| `report_id` | `VARCHAR(19)` | `FOREIGN KEY → match_reports.id ON DELETE CASCADE`, `INDEX` | 所属复盘报告 ID |
| `player_id` | `VARCHAR(19)` | `FOREIGN KEY → players.id ON DELETE CASCADE`, `INDEX` | 关联的玩家记录 ID |
| `role` | `ENUM(Role)` | — | 玩家身份 |
| `rule_compliance_score` | `INTEGER` | — | 规则服从度得分 (通用) |
| `logical_consistency_score` | `INTEGER` | — | 逻辑连贯性得分 (通用) |
| `roleplay_score` | `INTEGER` | — | 角色扮演得分 (通用) |
| `deception_score` | `INTEGER` | `NULLABLE` | 伪装与欺骗得分 (狼人专属) |
| `god_deduction_score` | `INTEGER` | `NULLABLE` | 找神能力得分 (狼人专属) |
| `situational_awareness_score` | `INTEGER` | `NULLABLE` | 态势感知得分 (好人专属) |
| `leadership_score` | `INTEGER` | `NULLABLE` | 统帅与引导得分 (好人专属) |
| `strengths` | `TEXT` | `NULLABLE` | 高光时刻总结 |
| `weaknesses` | `TEXT` | `NULLABLE` | 致命失误总结 |
| `overall_review` | `TEXT` | `NULLABLE` | 综合评价 |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL`, `DEFAULT now()` | 记录创建时间 |

**关联关系**:
- `report`: 多对一 → `MatchReport`
- `player`: 多对一 → `PlayerRecord`

---

## 表关系图

```text
games (1) ────< players (N)
  │               │
  │               └───────────< agent_evaluations (N)
  │                                   │
  ├───────────< events (N)            │
  │                                   │
  └───────────- match_reports (1) ────┘

ai_player_profiles (1) ────- ai_player_stats (1)
  │
  └───────────< players (N)
```

- `players.game_id` → `games.id` (CASCADE DELETE)
- `players.ai_profile_id` → `ai_player_profiles.id` (SET NULL)
- `events.game_id` → `games.id` (CASCADE DELETE)
- `match_reports.game_id` → `games.id` (CASCADE DELETE)
- `agent_evaluations.report_id` → `match_reports.id` (CASCADE DELETE)
- `agent_evaluations.player_id` → `players.id` (CASCADE DELETE)
- `ai_player_stats.player_id` → `ai_player_profiles.id` (CASCADE DELETE)
- 删除 `games` 行时，关联的 `players`, `events`, `match_reports` 及 `agent_evaluations` 行自动级联删除
- 删除 `ai_player_profiles` 行时，关联的 `ai_player_stats` 自动级联删除，`players` 中的 `ai_profile_id` 置空

---

## 新增 SQL 表规范

当需要新增 SQL 表时，必须遵循以下流程：

1. **先在本文件中声明新表**：添加表结构描述（表名、列定义、关联关系、枚举值参考）
2. **再在 `db/models.py` 中新增 ORM 模型类**：继承 `Base`，使用 `Mapped[]` + `mapped_column` 风格
3. **最后通过 Alembic 生成迁移**：
   ```bash
   cd ai_werewolf_core
   alembic revision --autogenerate -m "新增 xxx 表"
   alembic upgrade head
   ```

**ID 策略**: 所有持久化实体 ID 使用 `utils/snowflake.py` 雪花算法生成，字符串类型，长度 19。