# Redis 缓存架构优化方案

## 1. 背景与目标

当前《AI 狼人杀》项目的核心引擎（Game Engine）和事件总线（Event Bus）采用 **Redis 作为高性能缓存层和分布式状态共享层**，已实现无状态（Stateless）架构，支持多 Worker 进程水平扩展。

本方案严格遵循 `docs/agent.md` 的架构规范：
- **计算与接入分离**：通过 Redis 实现状态共享，支持多 Worker 节点无状态处理请求。
- **规则硬编码**：所有状态流转和计票逻辑依然在 Python 代码中硬编码，Redis 仅作为状态存储。
- **严格信息隔离**：Redis 中的数据结构设计配合现有的可见性（Visibility）过滤机制。

## 2. Redis 数据结构全景图

| 场景 | Redis 类型 | Key | 操作 | TTL | 状态 |
|---|---|---|---|---|---|
| 事件总线热数据 | **Stream** | `werewolf:events:{game_id}` | XADD / XRANGE / XREAD / XLEN | MAXLEN ~1000 | ✅ 已实现 |
| 投票数据 | **Hash** | `werewolf:vote:{game_id}:{round}` | HSET / HGETALL / HLEN / HEXISTS | 24h | ✅ 已实现 |
| 对局上下文 | **Hash** | `werewolf:game:{game_id}:context` | HSET (phase/round/status) / HGETALL | 对局结束后 1h | ✅ 已实现 |
| 玩家身份字典 | **Hash** | `werewolf:players:{game_id}` | HSET / HGET / HGETALL | 对局结束后 1h | ✅ 已实现 |
| 玩家存活状态 | **BitMap** | `werewolf:alive:{game_id}` | GETBIT / SETBIT / BITFIELD | 对局结束后 1h | ✅ 已实现 |
| 事件时序编号 | **String (INCR)** | `werewolf:seq:{game_id}` | INCR (RedisSeqGenerator) | 持久化 | ✅ 已实现 |

## 3. 基础设施组件（已实现）

### 3.1 Redis 客户端管理器 (`utils/redis_client.py`)
- **RedisClientManager**: 单例模式管理共享 `redis.asyncio` 连接池
- 所有模块通过 `RedisClientManager.get_client()` 获取客户端，避免重复创建连接
- 连接池参数通过 `settings` (pydantic-settings) 配置

### 3.2 Redis Key 常量 (`constants.py`)
- `RedisKeys` 类统一定义所有 Key 前缀和组装方法
- 示例：`RedisKeys.event_stream(game_id)` → `werewolf:events:{game_id}`
- 消除魔法字符串，所有 Key 通过工厂方法生成

### 3.3 时序编号生成器 (`utils/redis_seq.py`)
- **RedisSeqGenerator**: 基于 `INCR` 命令的全局单调递增编号
- 单线程模型天然保证原子性，高并发无竞态
- 持久化存储，进程重启后计数不丢失
- 多进程共享同一计数器
- `RedisUnavailableException`: 统一异常类型，便于调用方统一处理降级逻辑

### 3.4 雪花 ID 生成器 (`utils/snowflake.py`)
- **Snowflake**: 用于实体持久化 ID（如 `EventRecord.id`）
- 与 `RedisSeqGenerator` 职责分离：雪花 ID 用于 DB 主键，Redis INCR 用于事件时序编号

## 4. 优化场景实施详情

### 场景一：事件总线热数据缓存与分布式路由（EventBus）✅ 已实现

**文件**: `ai_werewolf_core/core/event/bus.py`

- **发布流程（双轨制混合架构）**:
  1. `RedisSeqGenerator` 分配全局唯一 `seq_num`
  2. `XADD` 写入 Redis Stream（`MAXLEN ~ 1000` 近似裁剪）
  3. `for` 循环分发给内存中的内部订阅者（日志记录、DB 持久化）
  
- **消费模式**:
  - **对内（Push/Pub-Sub）**：内部组件（日志、DB 持久化）通过 `subscribe()` / `subscribe_all()` 注册回调，`publish()` 时内存直接推送
  - **对外（Pull/MQ Stream）**：AI Agent 通过 `get_events()` 主动拉取，支持 `XRANGE`（范围查询）和 `XREAD`（增量阻塞读取）
  
- **冷热分离**:
  - Redis Stream 仅保留约 1000 条热数据
  - 全量历史事件穿透到 PostgreSQL `EventRecord` 表查询
  - `get_events()` 内置可见性过滤（PUBLIC/PRIVATE/FACTION）

- **降级策略**:
  - Redis 不可用时：`seq_num` 分配失败抛出异常；Stream 写入失败记录 CRITICAL 日志但继续分发内部事件（含 DB 持久化）
  - 事件查询时：Redis 数据不足 → 穿透 DB 查询

### 场景二：白天投票与 PK 意图的实时聚合（VoteManager）✅ 已实现

**文件**: `ai_werewolf_core/core/engine/vote_manager.py`

- **数据结构**: Redis Hash `werewolf:vote:{game_id}:{round}`
  - Field: `voter_id`（投票人）
  - Value: `target_id`（被投人，空字符串表示弃权）
  
- **并发安全**:
  - `HSET` 天然支持覆盖更新，多 Worker 并发无竞态
  - 采用"最后一次投票为准"策略，允许 AI Agent 改票
  
- **操作指令**:
  - 投票：`HSET` + `EXPIRE`（刷新 TTL）
  - 查已投票人数：`HLEN`
  - 查是否已投票：`HEXISTS`
  - 结算计票：`HGETALL` → Python `Counter` 统计
  
- **重试策略**: 关键写操作支持最多 3 次指数退避重试（0.1s, 0.2s, 0.3s），Redis 不可用时拒绝投票

- **死亡同步**: 放逐结算时 → `role.die()` → `PlayerStatusManager.mark_dead()` → 发布 `PLAYER_DEATH` 事件

### 场景三：全局对局上下文与状态机共享（Lifecycle & StateMachine）✅ 已实现

**文件**: `ai_werewolf_core/core/engine/state_machine.py`、`ai_werewolf_core/core/engine/lifecycle.py`

- **数据结构**: Redis Hash `werewolf:game:{game_id}:context`
  - Fields: `phase`（当前阶段）、`round`（当前轮次）、`status`（全局状态）
  
- **PhaseStateMachine**:
  - 从实例变量改为 Redis Hash 读写：`_load_context()` / `_save_context()`
  - `transition_to()`: 校验 `VALID_TRANSITIONS` → `HSET` 更新 Redis → 记录日志 → 发布 `PHASE_TRANSITION_EVENT`
  - 轮次递增：仅当目标阶段为 `NIGHT_START` 时 `round` 自增
  
- **LifecycleManager (Write-Through 模式)**:
  - `_set_status()`: 校验 → Redis HSET → 同步 UPDATE `GameRecord` → 发布 `SYSTEM_ANNOUNCEMENT`
  - 支持完整生命周期：`INIT → START → RUNNING → SETTLING → FINISHED / ABORTED`
  - DB 更新失败不阻塞（记录 ERROR 日志，Redis 缓存已更新）

### 场景四：玩家存活状态与身份字典（PlayerStatus）✅ 已实现

**文件**: `ai_werewolf_core/utils/player_status.py`

- **身份字典 (Hash)**: `werewolf:players:{game_id}`
  - Field: `player_id`
  - Value: JSON `{"role": "SEER", "seat": 3, "faction": "VILLAGER"}`
  
- **存活状态 (BitMap)**: `werewolf:alive:{game_id}`
  - Offset: `seat_number`（紧凑整数，1-12）
  - Bit: 1 = alive, 0 = dead
  
- **操作指令**:
  - 存活校验：`GETBIT`，O(1) 复杂度
  - 死亡标记：`SETBIT ... 0` → 异步更新 DB `PlayerRecord`
  - 复活标记：`SETBIT ... 1` → 异步更新 DB
  - 批量查询存活：`BITFIELD` 一次获取所有位
  - 初始化：`Pipeline` 批量写入身份 + BitMap + 设置 TTL
  
- **降级策略**:
  - Redis 不可用时降级查询 DB `PlayerRecord`
  - DB 更新失败不阻塞（记录 ERROR 日志，后续可通过对账修复）

## 5. 跨模块死亡结算协作流程

所有死亡结算模块（VoteManager、ActionResolver、SpecialActionResolver）遵循统一的协作模式：

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  结算模块        │────▶│  role.die()       │     │  EventBus       │
│  (VoteManager/  │     │  (内存标记)        │     │  (事件广播)      │
│   Resolver等)   │     └──────────────────┘     └─────────────────┘
│                 │                                              ▲
│                 │     ┌──────────────────┐                     │
│                 │────▶│ PlayerStatusMgr  │─────────────────────┘
│                 │     │ mark_dead()      │
└─────────────────┘     │ SETBIT 0 (Redis) │
                        │ UPDATE DB (异步)  │
                        └──────────────────┘
```

1. 结算模块调用 `target_role.die()` 标记内存 `is_alive=False`
2. 结算模块调用 `PlayerStatusManager.mark_dead(game_id, player_id, seat_number)` 同步 Redis BitMap
3. `PlayerStatusManager` 异步更新 DB `PlayerRecord.is_alive=False`（最终一致性）
4. 结算模块通过 EventBus 发布 `PLAYER_DEATH` 事件

## 6. 一致性保证策略

| 数据层 | 一致性策略 | 说明 |
|---|---|---|
| 角色实例 `is_alive` (内存) | 即时一致 | 同步 `die()` / `revive()` 标记，当前进程内优先读取 |
| Redis BitMap (缓存) | 即时一致 | `SETBIT` 原子操作，多 Worker 实时共享 |
| DB `PlayerRecord` (持久化) | 最终一致 | 异步更新，失败不阻塞游戏；可通过对账修复 |
| 投票 Hash (缓存) | 即时一致 | `HSET` 原子操作，Redis 为投票阶段的 Source of Truth |
| 对局上下文 (缓存) | Write-Through | 先写 Redis 后同步写 DB；DB 失败不阻塞 |
| 事件 Stream (热缓存) | 即时一致 | `XADD` 原子追加，MAXLEN 近似裁剪 |
| 事件 DB `EventRecord` (冷数据) | 即时一致 | 通过 `subscribe_all` 自动持久化，`publish()` 时同步触发 |

## 7. 编码规范

- 所有 Redis Key 使用 `constants.py` 中的 `RedisKeys` 工厂方法组装
- Redis 操作使用 `redis.asyncio` 异步客户端
- 统一捕获 `RedisUnavailableException`，提供合理的降级或报错机制
- Redis 连接通过 `RedisClientManager.get_client()` 共享连接池获取
- 关键写操作支持最多 3 次指数退避重试
