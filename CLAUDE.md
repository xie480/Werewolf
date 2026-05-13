# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

基于 LangGraph + FastAPI + Vue 3 的实时多智能体狼人杀博弈平台，AI Agent 扮演村民、狼人、预言家、女巫、猎人等角色进行非对称信息博弈。

### 技术栈

| 层 | 技术 |
|---|---|
| 后端框架 | FastAPI (异步), Uvicorn |
| 异步任务 | Celery + Redis |
| Agent 运行时 | LangGraph, LangChain, OpenAI |
| 数据库 | PostgreSQL 16 (asyncpg + SQLAlchemy 2.0), Alembic 迁移 |
| 缓存/队列 | Redis 7 (Stream 事件存储 + Lua 原子脚本) |
| 前端 | Vue 3 (Composition API), Vite 6, TypeScript 5.7 |
| 日志 | structlog (结构化 JSON) |
| 配置 | pydantic-settings |
| ID 生成 | Snowflake (自定义实现) |

### 常用命令

```bash
# --- 环境准备 ---
cp .env.example .env                          # 编辑填入 API Key 和数据库配置
cd ai_werewolf_core && pip install -r requirements.txt
cd frontend && npm install

# --- Docker 一键启动 ---
docker-compose up -d                          # 启动全部服务

# --- 手动启动后端 ---
cd ai_werewolf_core
uvicorn main:app --reload --host 0.0.0.0 --port 8000
celery -A worker.celery_app worker --loglevel=info

# --- 手动启动前端 ---
cd frontend && npm run dev                    # Vite 开发服务器 :5173

# --- 数据库迁移 ---
cd ai_werewolf_core
alembic upgrade head                          # 执行迁移
alembic revision --autogenerate -m "描述"      # 生成新迁移

# --- 测试 ---
pytest                                        # 运行全部测试
pytest tests/unit/                            # 只运行单元测试
pytest tests/test_event_bus.py                # 运行单个测试文件
pytest -k "test_event"                        # 按关键字筛选运行

# --- 类型检查 ---
cd frontend && npx vue-tsc -b                 # 前端 TypeScript 类型检查
```

---

@docs/system/Infrastructure system.md
@docs/system/Model Adapter.md
@docs/system/Action System.md
@docs/system/Agent Runtime.md
@docs/system/Game Engine.md
@docs/system/Evaluation System.md
@docs/system/Event System.md
@docs/system/Frontend Interaction System.md
@docs/system/Memory System.md
@docs/system/Observability System.md
@docs/system/Phase System.md
@docs/system/Prompt System.md
@docs/system/Replay System.md
@docs/plan/*.md

---

## Architecture

- FastAPI is ingress only
- Heavy compute runs in Celery workers
- LangGraph never executes in API handlers

---

## 仓库结构

```text
.
├─ .claudeignore               # Claude ignore configuration
├─ .env.example                # Example environment variables
├─ .gitignore                  # Git ignore patterns
├─ CLAUDE.md                   # Guidance for Claude Code
├─ docker-compose.yml          # Docker Compose configuration
├─ pytest.ini                  # Pytest configuration
├─ README.md                   # Project overview and instructions
├─ run.bat / start scripts    # Windows scripts to launch services
├─ ai_werewolf_core/          # Backend core
│   ├─ __init__.py            # Package initialization
│   ├─ alembic/               # Database migration scripts (Alembic)
│   │   └─ ...                # Alembic version files
│   ├─ agents/                # Agent definitions
│   │   ├─ graph/             # Graph definition for agent reasoning
│   │   │   └─ ...            # Graph modules
│   │   └─ memory/            # Memory modules (private, public, pruner)
│   │       └─ ...            # Memory implementations
│   ├─ api/                   # REST API definitions
│   │   ├─ routes/            # API route handlers (actions, events, games, players)
│   │   └─ ws/                # WebSocket endpoints
│   ├─ constant/              # Constant definitions (e.g., Redis keys)
│   ├─ core/                  # Core engine components
│   │   ├─ action/            # Action validation and anti-cheat logic
│   │   ├─ engine/            # Game engine, lifecycle, state machine, vote manager
│   │   │   └─ roles/         # Role implementations (hunter, seer, villager, werewolf, witch)
│   │   └─ event/             # EventBus implementation
│   ├─ db/                    # Database models and session handling
│   ├─ redis_lua/             # Lua scripts for atomic Redis operations
│   ├─ schemas/               # Pydantic schemas (API, enums, models)
│   ├─ tasks/                 # Celery task definitions
│   └─ utils/                 # Utility modules (logging, Redis client, etc.)
├─ docs/                       # Documentation
│   ├─ db/                     # Database schema documentation
│   ├─ plan/                   # Design and planning documents
│   └─ system/                 # Subsystem specifications
├─ frontend/                  # Frontend UI (Vue 3)
│   ├─ public/                # Static assets (images, icons)
│   └─ src/                   # Vue source code
│       ├─ api/                # API client wrappers
│       ├─ components/        # UI components
│       ├─ store/              # State management (Pinia)
│       ├─ types/              # TypeScript type definitions
│       ├─ views/              # Page views
│       └─ websocket/          # WebSocket client handling
├─ resource/                  # Static assets: backgrounds, identity icons
└─ tests/                     # Test suite: unit and integration tests
```

---

## Authority

Game engine is final authority.

LLM may:
- reason
- strategize
- generate dialogue

LLM may never:
- mutate canonical state
- decide legality
- resolve rules
- decide winners
- advance phases

---

## Memory Isolation

Visibility is enforced in code, never prompts.

Scopes:
- PUBLIC — 所有玩家的公共记忆池 + 前端观战大屏
- PRIVATE — 仅目标玩家的私有记忆池
- FACTION — 同阵营玩家共享

Agents may only access authorized memory.

---

## 关键设计决策

### ID 生成双轨策略
- **实体持久化 ID** (玩家、对局、事件记录等) → `utils/snowflake.py` (雪花算法，改善 B-Tree 索引写入性能)
- **事件时序 seq_num** → `utils/redis_seq.py` (Redis INCR 原子递增，保证多 Worker 全局时序)

### SQL 表管理
- 所有 SQL 表定义汇总于 [`docs/db/sql table.md`](docs/db/sql table.md)，作为 Schema 设计的唯一参考来源
- **新增 SQL 表必须先在该文档中声明**，包含表名、列定义、关联关系、枚举值参考，再在 `db/models.py` 中新增 ORM 模型类，最后通过 Alembic 生成迁移

### Plan 管理
- 所有架构规划文档统一放在 [`docs/plan/`](docs/plan/) 目录下
- **需要规划架构时，必须先在 `docs/plan/` 中创建 plan 文件**，经审批后再开始实施代码

### Event Sourcing (事件溯源)
- 所有对局事实通过 `EventBus` 发布，全局订阅者自动完成 DB 持久化和日志记录
- 热数据缓存于 Redis Stream (`werewolf:events:{game_id}`)，MAXLEN ~1000 近似裁剪
- 冷数据穿透到 PostgreSQL `EventRecord` 表
- Redis 不可用时自动降级到 DB 查询

### 状态存储策略
- `PhaseStateMachine` 不保留实例状态，`phase` 和 `round` 存入 Redis Hash (`werewolf:game:{game_id}:context`)
- 阶段迁移使用 Lua 脚本 (`phase_transition.lua`) 原子完成"加载→校验→更新"，避免多 Worker 竞态
- 所有合法迁移路径在 `VALID_TRANSITIONS` 字典中硬编码为有向图

### Redis 并发安全
- 凡是存在并发竞态风险的 Redis 操作（读-改-写、多 Key原子更新等），必须封装为 Lua 脚本
- Lua 脚本统一放置在 ai_werewolf_core/redis_lua/ 目录下
- 脚本通过 ai_werewolf_core/utils/redis_lua_loader.py加载和注册
- 禁止在业务代码中使用 WATCH/MULTI/EXEC 事务

---

## Constants

No magic strings.

Use enums from:
`schemas/enums.py`

Never:
```python
if phase == "DAY"
```

Always:
```python
if phase == GamePhase.DAY
```

---

## IDs

Use:
`utils/snowflake.py`

Never ad-hoc UUID/random IDs.

---

## Config

All env/config loads go through:
`config.py`

Never use:
```python
os.getenv()
```
outside config layer.

---

## Prompts

Prompt templates are isolated.

Never inline prompts into runtime logic.

---

## Runtime Rules

Always async.
Always typed.
Always structured logging.

Never:
- print()
- silent except
- blocking IO
- untyped dict contracts

---

## LLM Output Safety

Always:
parse → repair → retry → fallback

Never trust raw JSON.

---

## Modification Rules

Prefer minimal changes.
Always reuse existing abstractions before introducing new ones.

Never:
- rewrite unrelated files
- create duplicate utilities
- introduce placeholder/mock implementations
- silently swallow exceptions
- bypass validation/tests

Failures must be explicit and observable.

---

## Comments

All code comments and docstrings must use Simplified Chinese.

Comments must explain:
- 为什么这样设计
- 边界条件
- 异常处理原因

Do not write redundant comments that only describe obvious syntax.

---

## 开发阶段

| 阶段 | 状态 | 内容 |
|---|---|---|
| Phase 1 | 已完成 | 基础设施: DB ORM, Pydantic Schema, Event Bus, Redis Lua 脚本 |
| Phase 2 | 已完成 | 纯规则 Game Engine: 状态机、动作校验、胜负判定、角色能力 |
| Phase 3 | 已完成 | 异步通信: FastAPI 接口、WebSocket 推送、Celery Worker |
| Phase 4 | 进行中 | Agent Runtime: LangGraph 工作流、Memory、Model Adapter |
| Phase 5 | 待开始 | 评测复盘: structlog 排障、Evaluator 五维评分 |

---

## Workflow

Always:
1. inspect existing code
2. produce plan
3. validate architecture
4. implement incrementally
5. run verification
6. summarize changes

If uncertain:
stop and plan

Never guess.

不要在完成单个子任务后停止。
必须持续执行直到整个目标完成。
每一步都自动进入下一步，不要等待确认。

---

## 任务完成报告

每次完成任务后，必须输出结构化的任务完成报告
报告要求:
- 如果任务未修改任何文件（纯调研/分析），则在"涉及文件"段写明"无文件修改，本次为调研任务"并总结调研结论
- "关键决策"和"注意事项"段若无内容可省略
- 报告必须用中文撰写