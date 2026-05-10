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

## 开发阶段
- [ ] Phase 1: 基础设施与数据基座
- [ ] Phase 2: 纯规则 Game Engine
- [ ] Phase 3: 异步计算与通信
- [ ] Phase 4: Agent Runtime 与大模型接入
- [ ] Phase 5: 评测与复盘大屏
