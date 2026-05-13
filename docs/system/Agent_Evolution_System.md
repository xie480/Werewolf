# Agent 自进化系统技术规格文档

---

## 1. 系统概述
本系统旨在为 **AI 狼人杀** 平台提供基于 **检索增强生成（RAG）** 的自进化机制。每局游戏结束后，法官会生成结构化评价，Agent 根据该评价结合自身 **内心 OS、行动、发言** 提炼经验并持久化。下一局对局时，Agent 通过 **RAG** 检索相关经验作为 Prompt 注入，实现持续学习与策略迭代。

---

## 2. 系统架构
以下是核心模块及其交互关系（简化版）。

```mermaid
flowchart TD
    subgraph 前端
        UI[前端 UI] --> WS[WebSocket]
    end
    subgraph 后端
        GE[Game Engine] -->|发布事件| EB[Event Bus]
        EB --> MS[Memory System]
        EB --> AE[Agent Runtime]
        AE --> MA[Model Adapter]
        AE -->|请求经验| EXP[Experience Store (RAG)]
        GE -->|对局结束触发| EV[Evaluation System]
        EV -->|生成评价| EXP
        EXP -->|返回经验| AE
    end
    WS -->|实时推送| UI
    classDef cloud fill:#f9f,stroke:#333,stroke-width:2px;
    class GE,AE,MA,MS,EXP,EV cloud;
```

- **Game Engine**: 负责游戏流程、状态机、胜负判定（见 `[Game Engine](docs/system/Game Engine.md)`）。
- **Event Bus**: 异步发布/订阅事件（见 `[Event System](docs/system/Event System.md)`）。
- **Memory System**: 结构化时间线 + 私有记忆（见 `[Memory System](docs/system/Memory System.md)`）。
- **Agent Runtime**: 每个 Agent 的 LangGraph 工作流（见 `[Agent Runtime](docs/system/Agent Runtime.md)`）。
- **Model Adapter**: 多模型适配与统一调用（见 `[Model Adapter](docs/system/Model Adapter.md)`）。
- **Experience Store**: 向量数据库 + 元数据表，提供 RAG 接口。
- **Evaluation System**: 对局后离线评分与复盘（见 `[Evaluation System](docs/system/Evaluation System.md)`）。

---

## 3. 数据流与模块交互
### 3.1 对局执行流
1. **Phase 切换**：`Game Engine` 按 `Phase System`（见 `[Phase System](docs/system/Phase System.md)`）触发 `Phase_Start` 事件。
2. **行为请求**：`Agent Runtime` 在对应 `Phase` 被唤醒，调用 `Model Adapter` 生成 **行动/发言**。
3. **经验检索**：在 `Agent Runtime` 进入 **决策前**，调用 `Experience Service`（RAG）检索最近 **相似局面**（基于角色、阶段、关键情境），返回 Top‑K 经验文本。
4. **Prompt 组装**：`Prompt System` 将 **系统法则、身份策略、记忆、检索到的经验** 组合成完整 Prompt（见 `[Prompt System](docs/system/Prompt System.md)`），发送至模型。
5. **事件写入**：模型返回结构化 `Action`，经 `Action System` 校验后写入 `Event Bus` → `Memory System` → `PostgreSQL` 持久化。

### 3.2 复盘与经验写入流
1. `Game Engine` 在 `GAME_OVER` 阶段发布 `GameOverEvent`。
2. `Evaluation System` 异步订阅，抽取 **全局事件日志** 与 **Agent 内部 BeliefState**，使用 **LLM‑as‑Judge** 生成结构化评价（JSON）。
3. `Reflection Service` 将评价与当前局的 **内心 OS、行动、发言** 合并，抽取 **成功点 / 失败点**，形成经验记录。
4. `Experience Service` 将经验记录写入经验库（向量化 + 元数据），供后续 RAG 使用。

> **交互示意图**

```mermaid
sequenceDiagram
    participant GE as Game Engine
    participant EB as Event Bus
    participant AE as Agent Runtime
    participant MA as Model Adapter
    participant EXP as Experience Store
    participant EV as Evaluation System
    participant REF as Reflection Service
    GE->>EB: Phase_Start Event
    EB->>AE: Wake Agent
    AE->>EXP: RAG Query (role, phase, context)
    EXP-->>AE: Top‑K Experience Texts
    AE->>MA: Prompt Assembly + Generate Action
    MA-->>AE: Structured Action
    AE->>EB: Action Event
    EB->>MS: Persist Event & Update Memory
    GE->>EV: GameOverEvent
    EV->>REF: Extract Logs & BeliefState
    REF->>EXP: Store Experience (JSON + Embedding)
```

---

## 4. 经验存储与检索机制 (RAG)
### 4.1 数据模型
```sql
-- 表: experience_records
CREATE TABLE experience_records (
    id BIGSERIAL PRIMARY KEY,
    agent_id VARCHAR(32) NOT NULL,
    role VARCHAR(16) NOT NULL,
    phase VARCHAR(32) NOT NULL,
    situation_text TEXT NOT NULL,   -- 复盘时的情景描述
    lesson TEXT NOT NULL,          -- 成功点或改进点
    embedding VECTOR(768) NOT NULL, -- 使用 OpenAI/Claude embedding
    meta JSONB NOT NULL,            -- {"game_id":"g_123","day":2,"event_seq":45}
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```
> 该表位于 `ai_werewolf_core/db/models.py` 中的 `ExperienceRecord` ORM（参考 `[DB Models](ai_werewolf_core/db/models.py:114)`）。

### 4.2 向量化与元数据写入示例
```python
# File: ai_werewolf_core/agents/memory/public.py
from openai import OpenAI
from sqlalchemy.orm import Session
from .models import ExperienceRecord

async def store_experience(session: Session, agent_id: str, role: str, phase: str, situation: str, lesson: str, meta: dict):
    client = OpenAI()
    # 生成 1536 维度的 embedding（示例）
    resp = await client.embeddings.create(input=situation, model="text-embedding-3-large")
    embedding = resp.data[0].embedding
    record = ExperienceRecord(
        agent_id=agent_id,
        role=role,
        phase=phase,
        situation_text=situation,
        lesson=lesson,
        embedding=embedding,
        meta=meta,
    )
    session.add(record)
    session.commit()
```

### 4.3 检索流程（Hybrid Search）
```python
# File: ai_werewolf_core/agents/memory/private.py
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from .models import ExperienceRecord

async def retrieve_experiences(session: Session, role: str, phase: str, query_text: str, top_k: int = 3):
    # 1. 元数据过滤：仅同角色同阶段
    base_q = select(ExperienceRecord).where(
        ExperienceRecord.role == role,
        ExperienceRecord.phase == phase,
    )
    # 2. 向量相似度排序（pgvector >=）
    client = OpenAI()
    q_emb = (await client.embeddings.create(input=query_text, model="text-embedding-3-large")).data[0].embedding
    sim_q = base_q.order_by(func.l2_distance(ExperienceRecord.embedding, q_emb)).limit(top_k)
    results = session.execute(sim_q).scalars().all()
    return [r.situation_text + "\n" + r.lesson for r in results]
```
> 通过 **Hybrid Search**（元数据 + 向量）实现高效、精准的经验召回。

---

## 5. 复盘评估与评分模型
### 5.1 评分维度（五维雷达图）
| 维度 | 计算方式 | 归一化范围 |
|------|----------|------------|
| 规则服从度 (Rule Compliance) | `100 - (format_errors + illegal_actions) * 10` | 0‑100 |
| 逻辑连贯性 (Logical Consistency) | 对比 `internal_monologue` 与 `action` 的一致性（脚本 + LLM） | 0‑100 |
| 伪装与欺骗 (Deception) | LLM‑Judge 依据其他玩家的怀疑度 | 0‑10 |
| 煽动与说服力 (Persuasion) | 跟票率统计 | 0‑100 |
| 态势感知 (Situational Awareness) | 嫌疑热力图与真实身份 IoU | 0‑100 |

### 5.2 评测管线
```mermaid
flowchart LR
    A[GameOverEvent] --> B[Data Extraction]
    B --> C[Rule‑Based Scoring]
    C --> D[LLM‑as‑Judge]
    D --> E[Report Generation]
    E --> F[Persist to match_reports]
```

### 5.3 评测结果示例（JSON）
```json
{
  "match_id": "game_1001",
  "duration_seconds": 1850,
  "winner": "WEREWOLF",
  "agent_evaluations": {
    "player_5": {
      "role": "WEREWOLF",
      "radar_scores": {
        "rule_compliance": 95,
        "logical_consistency": 88,
        "deception": 92,
        "persuasion": 85,
        "situational_awareness": null
      },
      "llm_review": {
        "strengths": "第一天悍跳预言家，逻辑链严密，成功逆转投票。",
        "weaknesses": "第三天刀错平民，导致阵营失误。",
        "overall_review": "本局 MVP，表现出色但需注意夜间刀人精准度。"
      }
    }
  }
}
```
> 该结构直接供前端 **复盘大屏** 渲染（见 `[Replay System](docs/system/Replay System.md)`）。

---


## 7. 实现步骤与关键技术说明
| 步骤 | 关键文件/模块 | 主要工作 |
|------|---------------|----------|
| 1 | `ai_werewolf_core/db/models.py` | 新增 `ExperienceRecord` ORM（参照上文 SQL） |
| 2 | `ai_werewolf_core/agents/memory/public.py` & `private.py` | 实现 `store_experience` 与 `retrieve_experiences`（代码示例） |
| 3 | `docs/system/Prompt System.md` | 在 `Prompt Assembly` 中加入 `【经验引用】` 区块，调用检索服务 |
| 4 | `ai_werewolf_core/core/engine/lifecycle.py` | 在 `GAME_OVER` 阶段触发 `Evaluation System` → `Reflection Service` |
| 5 | `ai_werewolf_core/tasks/eval.py` | 实现离线评分、LLM‑as‑Judge Prompt（复用 `Evaluation System` 示例） |
| 6 | `docker-compose.yml` | 为向量数据库（如 **pgvector**）或 **Qdrant** 添加服务，更新环境变量 |
| 7 | CI/CD (`.github/workflows/ci.yml`) | 加入迁移步骤 `alembic upgrade head`、代码风格检查、单元测试覆盖新模块 |
| 8 | 监控仪表盘 (`observability`) | 新增 **Experience RAG Latency** 指标（见 `[Observability System](docs/system/Observability System.md)`） |

### 关键技术点
- **向量数据库**：推荐使用 PostgreSQL + **pgvector**（部署简单）或 **Qdrant**（水平扩展）。
- **Hybrid Search**：先按 `role`/`phase` 过滤，再做向量相似度排序，避免全库遍历。
- **Prompt 注入**：在 `Prompt System` 中以 `【经验引用】` 块形式加入检索到的经验，确保 LLM 能在 **当前局面** 中看到历史经验。
- **安全性**：经验库仅对 **内部系统** 可见，前端不直接暴露经验文本，防止信息泄露。
- **并发控制**：RAG 检索采用 **异步缓存**（如 `aioworkers`），防止在高并发对局时成为瓶颈。

---

## 8. 部署与维护指南
### 8.1 容器化部署
```yaml
version: '3.8'
services:
  api_server:
    build:
      context: .
      dockerfile: Dockerfile.api
    ports: ["8000:8000"]
    depends_on: [postgres, redis, vector_db]
    environment:
      - DATABASE_URL=postgresql://user:pass@postgres:5432/werewolf
      - REDIS_URL=redis://redis:6379/0
      - VECTOR_DB_URL=postgresql://user:pass@vector_db:5432/vecdb
  engine_worker:
    build:
      context: .
      dockerfile: Dockerfile.worker
    depends_on: [postgres, redis, vector_db]
    environment:
      - VECTOR_DB_URL=${VECTOR_DB_URL}
  vector_db:
    image: ankane/pgvector
    environment:
      POSTGRES_PASSWORD: pass
      POSTGRES_DB: vecdb
    ports: ["5433:5432"]
  postgres:
    image: postgres:15-alpine
    ...
  redis:
    image: redis:7-alpine
    ...
```
> `vector_db` 为向量存储服务，使用 **pgvector** 扩展（见 `[Infrastructure system](docs/system/Infrastructure system.md)`）。

### 8.2 数据库迁移
```bash
# 生成迁移
alembic revision --autogenerate -m "add experience_records table"
# 应用迁移
alembic upgrade head
```
> 迁移脚本放在 `ai_werewolf_core/alembic/versions/`，遵循项目 **Alembic** 规范。

### 8.3 环境变量
| 变量 | 含义 |
|------|------|
| `VECTOR_DB_URL` | 向量数据库连接串（必须包含 pgvector 扩展） |
| `RAG_TOP_K` | 检索返回的经验条数（默认 3） |

### 8.4 监控与报警
- **RAG 延迟**：`RAG_latency_ms` > 200 ms 触发告警（在 `Observability System` 中配置）。
- **数据备份**：PostgreSQL 与向量库每日快照，使用 `pg_dump` + `pg_basebackup`。

---

## 9. 参考链接
- `[Game Engine](docs/system/Game Engine.md)`
- `[Phase System](docs/system/Phase System.md)`
- `[Memory System](docs/system/Memory System.md)`
- `[Evaluation System](docs/system/Evaluation System.md)`
- `[Prompt System](docs/system/Prompt System.md)`
- `[Model Adapter](docs/system/Model Adapter.md)`
- `[Observability System](docs/system/Observability System.md)`
- `[Replay System](docs/system/Replay System.md)`

---

> **文档维护者**：AI 研发团队
> **最后更新**：2026‑05‑13
