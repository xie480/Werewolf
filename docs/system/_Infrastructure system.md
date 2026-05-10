**核心结论先行：**
在跑 LangGraph 这类长耗时的多智能体工作流时，**绝对不能把图的执行逻辑直接写在 Web API（如 HTTP 请求）的同步主线程里！** 跑一局游戏可能需要 1 小时，HTTP 连接早就超时断开了。
因此，基础设施架构的核心设计原则是：**“计算与接入分离、重度依赖异步任务队列、全面容器化”**。
以下是专为 LangGraph AI 狼人杀设计的生产级 Infrastructure 落地方案：
---
### 一、 整体物理拓扑架构 (Topology)
系统应拆分为以下独立部署的物理容器/服务组：
1. **API Gateway / Web Server (接入层)**
   * **技术选型**：FastAPI + Uvicorn
   * **职责**：处理前端 HTTP 请求（创建房间、获取历史记录）、维持 WebSocket 长连接（推送实时对局 Event）。绝对不跑复杂的 LLM 计算。
2. **Game Engine Worker (计算层 / 核心大脑)**
   * **技术选型**：Python 守护进程 + Celery 或 Redis Queue (RQ)
   * **职责**：从队列中拉取任务，真正执行 `LangGraph` 状态机、调用 `Model Adapter`、计算 `Action Validation`。
3. **Database Layer (数据层)**
   * **PostgreSQL**：存储持久化状态（Game Sessions, Events）以及作为 LangGraph Checkpointer。
   * **Redis**：作为任务队列（Message Broker）、WebSocket 的发布订阅频道（Pub/Sub）、以及高频读写的短效缓存。
4. **Observability Layer (监控层)**
   * 接入我们在上一节设计的 LangSmith、Prometheus/Grafana（采集容器与服务指标）。
---
### 二、 核心难点：异步任务队列调度 (Task Queue)
当用户在前端点击“开始游戏”时，基础设施层应按以下流程运转：
1. **触发器**：前端向 FastAPI 发送 `POST /api/game/start`。
2. **入队**：FastAPI 在 Postgres 创建 `game_session` 记录，然后向 Redis 队列发送一个异步任务 `run_game(game_id)`。FastAPI 立即给前端返回 HTTP 200。
3. **消费**：后端的 Game Engine Worker 监听到任务，启动 LangGraph 主控图。
4. **通信**：Worker 在执行图的过程中，不断向 Redis Pub/Sub 推送产生的 `GameEvent`。
5. **触达**：FastAPI 中的 WebSocket 模块订阅了 Redis 的频道，将事件实时推送给连接的前端大屏。
*这套解耦架构保证了即便 Web Server 因为并发过高重启，后台正在跑的数十局游戏也不会中断。*
---
### 三、 容器化编排设计 (Docker & Docker Compose)
为了保证团队开发环境一致，以及未来一键部署，必须提供标准的 `docker-compose.yml`：
```yaml
version: '3.8'
services:
  # 1. API 接入服务器
  api_server:
    build:
      context: .
      dockerfile: Dockerfile.api
    ports:
      - "8000:8000"
    depends_on:
      - postgres
      - redis
    environment:
      - DATABASE_URL=postgresql://user:pass@postgres:5432/werewolf
      - REDIS_URL=redis://redis:6379/0
  # 2. 狼人杀引擎计算节点 (可横向扩展多个)
  engine_worker:
    build:
      context: .
      dockerfile: Dockerfile.worker
    depends_on:
      - postgres
      - redis
    environment:
      - DATABASE_URL=postgresql://user:pass@postgres:5432/werewolf
      - REDIS_URL=redis://redis:6379/0
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - LANGCHAIN_API_KEY=${LANGCHAIN_API_KEY}
  # 3. 基础依赖：Postgres
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: pass
      POSTGRES_DB: werewolf
    volumes:
      - pgdata:/var/lib/postgresql/data
  # 4. 基础依赖：Redis
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
volumes:
  pgdata:
```
*提示：如果要做纯离线的大规模 LLM 评测，你可以通过 `docker-compose up --scale engine_worker=5` 瞬间启动 5 个并行计算节点，极大加快评测速度。*
---
### 四、 持续集成与部署 (CI/CD Pipeline)
由于系统包含大量的 Prompt、Graph 逻辑和数据模型，每次提交代码必须经过严格检验。建议配置 **GitHub Actions**：
1. **Lint & Format (代码规范)**：运行 `Ruff` 或 `Flake8` 检查 Python 代码。
2. **Unit Testing (单元测试)**：
   * 必须拦截所有真实的 LLM 请求！使用 `unittest.mock` 模拟大模型的返回。
   * 重点测试 `Action System` 里的业务校验器（比如：死人不能投票、女巫只有一瓶解药等）。
3. **Integration Testing (集成测试)**：
   * 自动拉起轻量级的 SQLite 或 Postgres 容器。
   * 使用一个极其便宜、极速的假模型（或 `gpt-4o-mini`），跑一局极简的 3 人局（1狼2民），只要确保 `GAME_OVER` 状态能正常触发，且中间没抛异常，即可判定核心主干无堵塞。
---
### 五、 环境变量与密钥管理 (Secret Management)
多智能体系统会用到极其多样的模型和 API Key。
推荐使用 `.env` 文件配合 Pydantic 的 `BaseSettings` 进行统一强类型管理，防止代码中散落死编码的 API Key：
```python
from pydantic_settings import BaseSettings
class AppSettings(BaseSettings):
    # 基础存储
    database_url: str
    redis_url: str

    # 大模型 API Keys
    openai_api_key: str | None = None
    deepseek_api_key: str | None = None
    claude_api_key: str | None = None

    # 监控与调试
    langchain_tracing_v2: bool = False

    class Config:
        env_file = ".env"
settings = AppSettings()
```