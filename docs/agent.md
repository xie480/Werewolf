你好，AI 编程助手。当前项目是一个基于 LangGraph 和 FastAPI 的“AI 多智能体狼人杀系统”。请在为我生成代码、重构或提供建议时，严格遵循以下架构规范和编码要求。

## 1. 核心架构底线 (Architecture Boundaries)
- **计算与接入必须分离**：API 层（FastAPI）绝对不允许直接运行耗时的 LangGraph 大图计算。API 只负责接收请求并推送至 Redis/Celery 异步任务队列，由后台 Worker 进程执行 [2]。
- **规则硬编码**：Game Engine 是绝对的裁判，所有的对局规则、阶段流转必须硬编码，绝对不允许交给 LLM 自由发挥判定 [13]。
- **严格信息隔离**：狼人杀是信息不对称博弈。必须严格区分 `PUBLIC`（公共）、`PRIVATE`（私密）、`FACTION`（阵营）的事件可见性。Agent 只能读取自身权限范围内的记忆 [7, 12]。

## 2. 标准目录结构 (Directory Structure)
在创建新文件或重构时，请严格遵守以下模块划分原则：

```text
ai_werewolf_core/
├── api/                        # 【前台收发室】只管收发前端请求和推送 WebSocket 消息，绝对不在这里跑大模型 
│
├── core/                       # 【铁面无私的裁判系统】这里的规则必须用代码写死，绝不让大模型自由发挥插手
│   ├── engine/                 # 游戏进程引擎：控制“天黑请闭眼”、结算伤亡名单、判断胜负结束 
│   ├── event/                  # 全局广播大喇叭：把“确定发生的事”（如谁死了、谁说话了）通知给所有人并记账 
│   └── action/                 # 防作弊安检机：大模型发神经想让死人发言？想自刀？全在这里拦截并打回重做
│
├── agents/                     # 【AI 玩家的赛博大脑】它们在这里看局势、盘逻辑、做决定
│   ├── graph/                  # 大脑思考工作流：LangGraph 定义的“读信息 -> 回忆 -> 盘逻辑 -> 决定”流水线
│   ├── memory/                 # 记忆保险箱：存着大家的公屏发言，以及自己偷偷藏好的底牌、验人结果和内心小九九 
│   └── adapter/                # 大模型翻译官：对接 OpenAI 兼容接口，顺便干点 JSON 抢救修复和断线重连的脏活 
│
├── schemas/                    # 【全局字典】把游戏里所有阶段、角色、动作的名字钉死在这，绝不允许魔法字符串
├── utils/                      # 【修车工具箱】结构化排错日志、AI 吐出乱码 JSON 时的强行修补工具
│
├── config.py                   # 【机密档案室】API Key、数据库账号等敏感配置统一下发的地方
├── main.py                     # 【前台保安】启动 FastAPI 接口服务的入口
└── worker.py                   # 【后台苦力】在后台默默跑那些长达几十分钟完整对局的异步进程入口
```

## 3. 字符串非硬编码要求 (No Hardcoded Strings)
- **绝对禁止魔法字符串 (Magic Strings)**：所有代表游戏阶段、玩家角色、动作类型、事件类型的字符串，必须在 `schemas/enums.py` 中定义为 `Enum` 枚举类 [8, 9]。
  - *错误示例*：`if phase == "DAY_VOTE":`
  - *正确示例*：`if phase == GamePhase.DAY_VOTE:`
- **环境变量强类型化**：所有 API 密钥、数据库连接串、模型名称配置，必须通过 `pydantic-settings` 在 `config.py` 中进行统一的强类型校验和加载 [2]。
- **Prompt 模板分离**：System Prompts 和角色模板不得与核心逻辑耦合，必须统一定义在独立的模板文件或专门的 Prompt 模块中，并通过变量注入（如 `str.format()` 或 LangChain PromptTemplate）。

## 4. 开发全阶段指南 (Development Lifecycle)
请协助我按以下阶段推进开发，不要跨阶段跳跃生成代码：
- **Phase 1: 基础设施与数据基座**。完成 `docker-compose.yml` (Postgres+Redis)、数据库 ORM 定义、Pydantic Schema 与 Event Bus [7]。
- **Phase 2: 纯规则 Game Engine**。完成 `PhaseMachine` 的状态流转、写死 Mock 数据进行天黑天亮、动作校验、胜负判定等核心逻辑 [8, 13]。
- **Phase 3: 异步计算与通信**。完成 FastAPI 的接口搭建、WebSocket 下行推送、以及 Celery/Worker 任务派发 [2]。
- **Phase 4: Agent Runtime 与大模型接入**。编写 LangGraph 工作流、Memory 记忆获取、以及 Model Adapter [11, 12]。
- **Phase 5: 评测与复盘大屏**。引入 `structlog` 排障辅助，编写离线 Evaluator 脚本计算五维分数 [3, 6]。

## 5. 编码规范要求 (Coding Standards)
- **强类型声明 (Type Hinting)**：所有函数参数、返回值必须标明类型。数据校验强依赖 `pydantic` 的 `BaseModel`。LangGraph 的内部状态传递必须使用明确定义的 `TypedDict` [12]。
- **JSON 兜底防爆 (Resilience)**：LLM 吐出的 JSON 极度不可靠。必须配备尝试解析 -> 正则提取 -> `json-repair` 修复 -> 重试 Prompt -> 强制接管 (Fallback) 的完整防线 [4, 9]。
- **异步优先**：所有数据库 I/O (使用 `asyncpg`)、API 请求、大模型调用必须使用 `async/await`，以支持并发的夜间阶段推理 [8]。

## 6. 日志与注释要求 (Logging & Commenting)
- **禁止使用 print()**：多智能体并发环境下 `print` 会导致信息交织。必须使用 `structlog` 等结构化日志库，并强制注入 Context（如 `game_id`, `agent_id`, `phase`），输出为 JSON 格式 [3]。
- **注释要求**：
  - 类和核心业务函数必须有完整的 Docstring，说明输入、输出和**异常抛出情况**。
  - 核心逻辑（尤其是涉及到状态机流转、死锁处理、记忆组装的地方）的注释必须说明 **"Why" (为什么要这样写)**，而不是简单的 "What" (这段代码在干什么)。
  - 若代码为了规避大模型幻觉而做了特殊处理，必须在注释中标记 `FIXME` 或 `HACK` 并说明原因。