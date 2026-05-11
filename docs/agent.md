你好，AI 编程助手。当前项目是一个基于 LangGraph 和 FastAPI 的“AI 多智能体狼人杀系统”。请在为我生成代码、重构或提供建议时，严格遵循以下架构规范和编码要求。

## 1. 核心架构底线 (Architecture Boundaries)
- **计算与接入必须分离**：API 层（FastAPI）绝对不允许直接运行耗时的 LangGraph 大图计算。API 只负责接收请求并推送至 Redis/Celery 异步任务队列，由后台 Worker 进程执行。
- **规则硬编码**：Game Engine 是绝对的裁判，所有的对局规则、阶段流转必须硬编码，绝对不允许交给 LLM 自由发挥判定。
- **严格信息隔离**：狼人杀是信息不对称博弈。必须严格区分 `PUBLIC`（公共）、`PRIVATE`（私密）、`FACTION`（阵营）的事件可见性。Agent 只能读取自身权限范围内的记忆。

## 2. 标准目录结构 (Directory Structure)
在创建新文件或重构时，请严格遵守以下模块划分原则：

```text
Werewolf/
├── .env
├── .env.example
├── .gitignore
├── README.md
├── docker-compose.yml
│
├── docs/                       # 📚 项目文档库
│   ├── agent.md                # 后端架构规范
│   ├── plan/                   # 开发计划与进度
│   └── system/                 # 各子系统设计文档
│       ├── _Infrastructure system.md
│       ├── _Model Adapter.md
│       ├── Action System.md
│       ├── Agent Runtime.md
│       ├── Evaluation System.md
│       ├── Event System.md
│       ├── Frontend Interaction System.md
│       ├── Game Engine.md
│       ├── Memory System.md
│       ├── Observability System.md
│       ├── Phase System.md
│       ├── Prompt System.md
│       └── Replay System.md
│
├── frontend/                   # 💻 【前端工程】(Vite + Vue3 + TypeScript)
│   ├── index.html
│   ├── package.json
│   ├── package-lock.json
│   ├── shims-vue.d.ts
│   ├── tsconfig.json
│   ├── tsconfig.node.json
│   ├── vite.config.ts
│   ├── public/                 # 静态资源
│   └── src/
│       ├── App.vue
│       ├── main.ts
│       ├── api/                # HTTP 接口封装
│       ├── assets/             # 图片、全局 CSS 等资源
│       ├── components/         # 通用 UI 组件
│       ├── store/              # 状态管理 (Pinia)
│       ├── types/              # TypeScript 类型定义
│       ├── utils/              # 工具函数
│       ├── views/              # 页面级组件
│       └── websocket/          # WebSocket 客户端
│
├── ai_werewolf_core/           # 🧠 【后端工程】(FastAPI + LangGraph)
│   ├── __init__.py
│   ├── alembic.ini
│   ├── config.py               # 配置加载 (pydantic-settings)
│   ├── constants.py             # 全局常量
│   ├── main.py                 # FastAPI 启动入口
│   ├── requirements.txt        # Python 依赖
│   ├── worker.py               # Celery 异步任务入口
│   │
│   ├── agents/                 # 【AI 玩家的赛博大脑】
│   │   ├── adapter/            # LLM 接口适配器
│   │   ├── graph/              # LangGraph 工作流
│   │   └── memory/             # 记忆管理
│   │
│   ├── alembic/                # 数据库迁移
│   │   ├── env.py
│   │   ├── README
│   │   ├── script.py.mako
│   │   └── versions/
│   │       └── c971b5c3506b_init_tables.py
│   │
│   ├── api/                    # 【前台收发室】HTTP 路由和 WebSocket 网关
│   │   ├── routes/             # RESTful API 路由
│   │   └── ws/                 # WebSocket 连接管理
│   │
│   ├── core/                   # 【铁面无私的裁判系统】
│   │   ├── action/             # 动作校验、防作弊
│   │   ├── engine/             # 游戏状态机、阶段流转
│   │   │   ├── __init__.py
│   │   │   ├── evaluator.py
│   │   │   ├── exceptions.py
│   │   │   ├── lifecycle.py
│   │   │   ├── resolver.py
│   │   │   ├── special_action_resolver.py
│   │   │   ├── state_machine.py
│   │   │   ├── vote_manager.py
│   │   │   └── roles/          # 角色能力定义
│   │   │       ├── __init__.py
│   │   │       ├── base.py
│   │   │       ├── hunter.py
│   │   │       ├── seer.py
│   │   │       ├── villager.py
│   │   │       ├── werewolf.py
│   │   │       └── witch.py
│   │   └── event/              # 事件总线
│   │       ├── __init__.py
│   │       └── bus.py
│   │
│   ├── db/                     # 数据库访问层
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── models.py
│   │   └── session.py
│   │
│   ├── schemas/                # 【全局字典】Pydantic 模型、枚举类
│   │   ├── __init__.py
│   │   ├── enums.py
│   │   └── models.py
│   │
│   └── utils/                  # 【修车工具箱】
│       ├── __init__.py
│       ├── logger.py           # 结构化日志
│       ├── redis_seq.py        # Redis 序列号
│       └── snowflake.py        # Snowflake ID 生成器
│
└── tests/                      # 🧪 测试用例
```

**🛡️ 全局开发规范注释：系统内部所有涉及 ID 生成的业务场景，请务必统一引入并调用 [`ai_werewolf_core/utils/snowflake.py`](ai_werewolf_core/utils/snowflake.py) 文件中的方法。**

## 3. 字符串非硬编码要求 (No Hardcoded Strings)
- **绝对禁止魔法字符串 (Magic Strings)**：所有代表游戏阶段、玩家角色、动作类型、事件类型的字符串，必须在 `schemas/enums.py` 中定义为 `Enum` 枚举类。
  - *错误示例*：`if phase == "DAY_VOTE":`
  - *正确示例*：`if phase == GamePhase.DAY_VOTE:`
- **环境变量强类型化**：所有 API 密钥、数据库连接串、模型名称配置，必须通过 `pydantic-settings` 在 `config.py` 中进行统一的强类型校验和加载。
- **Prompt 模板分离**：System Prompts 和角色模板不得与核心逻辑耦合，必须统一定义在独立的模板文件或专门的 Prompt 模块中，并通过变量注入（如 `str.format()` 或 LangChain PromptTemplate）。

## 4. 开发全阶段指南 (Development Lifecycle)
请协助我按以下阶段推进开发，不要跨阶段跳跃生成代码：
- **Phase 1: 基础设施与数据基座**。完成 `docker-compose.yml` (Postgres+Redis)、数据库 ORM 定义、Pydantic Schema 与 Event Bus。
- **Phase 2: 纯规则 Game Engine**。完成 `PhaseMachine` 的状态流转、写死 Mock 数据进行天黑天亮、动作校验、胜负判定等核心逻辑。
- **Phase 3: 异步计算与通信**。完成 FastAPI 的接口搭建、WebSocket 下行推送、以及 Celery/Worker 任务派发 。
- **Phase 4: Agent Runtime 与大模型接入**。编写 LangGraph 工作流、Memory 记忆获取、以及 Model Adapter 。
- **Phase 5: 评测与复盘大屏**。引入 `structlog` 排障辅助，编写离线 Evaluator 脚本计算五维分数。

## 5. 编码规范要求 (Coding Standards)
- **强类型声明 (Type Hinting)**：所有函数参数、返回值必须标明类型。数据校验强依赖 `pydantic` 的 `BaseModel`。LangGraph 的内部状态传递必须使用明确定义的 `TypedDict`。
- **JSON 兜底防爆 (Resilience)**：LLM 吐出的 JSON 极度不可靠。必须配备尝试解析 -> 正则提取 -> `json-repair` 修复 -> 重试 Prompt -> 强制接管 (Fallback) 的完整防线。
- **异步优先**：所有数据库 I/O (使用 `asyncpg`)、API 请求、大模型调用必须使用 `async/await`，以支持并发的夜间阶段推理。

## 6. 日志与注释要求 (Logging & Commenting)
- **禁止使用 print()**：多智能体并发环境下 `print` 会导致信息交织。必须使用 `structlog` 等结构化日志库，并强制注入 Context（如 `game_id`, `agent_id`, `phase`），输出为 JSON 格式。
- **注释要求**：
  - 类和核心业务函数必须有完整的 Docstring，说明输入、输出和**异常抛出情况**。
  - 核心逻辑（尤其是涉及到状态机流转、死锁处理、记忆组装的地方）的注释必须说明 **"Why" (为什么要这样写)**，而不是简单的 "What" (这段代码在干什么)。
  - 若代码为了规避大模型幻觉而做了特殊处理，必须在注释中标记 `FIXME` 或 `HACK` 并说明原因。