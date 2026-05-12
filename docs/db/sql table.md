# SQL 表结构文档

> **用途**: 本文档汇总项目中所有 PostgreSQL 数据库表的定义，作为 Schema 设计的唯一参考来源。
>
> **数据源**: 基于 `ai_werewolf_core/db/models.py` 中的 SQLAlchemy ORM 模型自动提取。
>
> **最后更新**: 2026-05-12

---

## 表一览

| 表名 | ORM 模型 | 用途 | 主键策略 |
|------|---------|------|---------|
| `games` | `GameRecord` | 对局元信息与当前状态快照 | Snowflake (String 19) |
| `players` | `PlayerRecord` | 对局内每个 Agent 的身份、座位和存活状态 | Snowflake (String 19) |
| `events` | `EventRecord` | Event Sourcing 核心存储，记录所有对局事实 | Snowflake (String 19) |

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
| `game_id` | `VARCHAR(36)` | `FOREIGN KEY → games.id ON DELETE CASCADE`, `INDEX` | 所属对局 ID |
| `player_id` | `VARCHAR(32)` | — | 玩家标识，如 `player_1` |
| `seat_number` | `INTEGER` | — | 座位号 (1-based) |
| `role` | `ENUM(Role)` | — | 玩家身份 (VILLAGER / WEREWOLF / SEER / WITCH / HUNTER) |
| `is_alive` | `BOOLEAN` | `DEFAULT TRUE` | 是否存活 |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL`, `DEFAULT now()` | 记录创建时间 |
| `updated_at` | `TIMESTAMPTZ` | `NOT NULL`, `DEFAULT now()`, `ON UPDATE now()` | 记录更新时间 |

**关联关系**:
- `game`: 多对一 → `GameRecord`

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
| `game_id` | `VARCHAR(36)` | `FOREIGN KEY → games.id ON DELETE CASCADE`, `INDEX` | 所属对局 ID |
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

## 表关系图

```
games (1) ────< players (N)
  │
  └───────────< events (N)
```

- `players.game_id` → `games.id` (CASCADE DELETE)
- `events.game_id` → `games.id` (CASCADE DELETE)
- 删除 `games` 行时，关联的 `players` 和 `events` 行自动级联删除

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