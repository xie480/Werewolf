# Werewolf - AI 多智能体狼人杀系统

基于 LangGraph + FastAPI + Vue 3 的实时多智能体狼人杀博弈平台。

## 技术栈
- **后端**: FastAPI, Celery, LangGraph, PostgreSQL, Redis
- **前端**: Vue 3, Vite, TypeScript, WebSocket

## 快速开始

### 环境要求
- Python 3.11+
- Node.js 20+
- Docker & Docker Compose (可选)

### 安装运行
```bash
# 1. 克隆仓库
git clone <repo-url>
cd Werewolf

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API Key 等配置

# 3. 启动所有服务 (Docker)
docker-compose up -d

# 或者手动启动

# 后端
cd ai_werewolf_core
pip install -r requirements.txt
uvicorn main:app --reload

# 前端 (新终端)
cd frontend
npm install
npm run dev
```

## 项目结构
参见 [docs/agent.md](docs/agent.md) 了解完整架构文档。

## 模型配置与记忆压缩

系统支持通过前端界面动态配置大模型 API（如 OpenAI、Anthropic 等），用于 Agent 推理和**记忆压缩**。

1. 启动前后端服务后，访问前端页面。
2. 导航至“模型管理”页面。
3. 添加或编辑模型配置（需提供 API Key、Base URL 等）。
4. 系统会自动将配置同步至数据库，并在需要时（如记忆 Token 超限时）调用指定的模型进行历史事件的摘要压缩，以保证 Agent 的上下文窗口安全。

## 开发阶段
- [ ] Phase 1: 基础设施与数据基座
- [ ] Phase 2: 纯规则 Game Engine
- [ ] Phase 3: 异步计算与通信
- [ ] Phase 4: Agent Runtime 与大模型接入
- [ ] Phase 5: 评测与复盘大屏
