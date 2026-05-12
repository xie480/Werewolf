"""
tests/test_api_games.py  — 对局生命周期 API 测试

**Why**: 验证 games.py 路由的所有端点（创建/查询/启动/推进/中止/列表/加入）。

注意: 这些测试需要 Redis 和 PostgreSQL 运行。在 CI 中使用 mock 或容器化环境。
"""

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from ai_werewolf_core.main import app


@pytest_asyncio.fixture
async def client():
    """创建 httpx AsyncClient，绑定 FastAPI app。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_create_game(client):
    """测试创建对局: POST /api/games 返回 201 + game_id + status=START。"""
    response = await client.post("/api/games", json={"player_count": 9})
    assert response.status_code == 201
    data = response.json()
    assert "game_id" in data
    assert data["status"] == "START"


@pytest.mark.asyncio
async def test_create_game_invalid_player_count(client):
    """测试非法的玩家人数: 应返回 400。"""
    response = await client.post("/api/games", json={"player_count": 3})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_get_game(client):
    """测试查询对局: GET /api/games/{game_id} 返回详情。"""
    # 先创建对局
    create_resp = await client.post("/api/games", json={"player_count": 9})
    game_id = create_resp.json()["game_id"]

    response = await client.get(f"/api/games/{game_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["game_id"] == game_id
    assert data["player_count"] == 9


@pytest.mark.asyncio
async def test_start_game(client):
    """测试启动对局: POST /api/games/{game_id}/start。"""
    create_resp = await client.post("/api/games", json={"player_count": 9})
    game_id = create_resp.json()["game_id"]

    response = await client.post(f"/api/games/{game_id}/start")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "RUNNING"
    assert "phase" in data


@pytest.mark.asyncio
async def test_advance_phase(client):
    """测试推进阶段: POST /api/games/{game_id}/advance。"""
    create_resp = await client.post("/api/games", json={"player_count": 9})
    game_id = create_resp.json()["game_id"]

    # 必须先启动
    await client.post(f"/api/games/{game_id}/start")

    # 推进阶段
    response = await client.post(f"/api/games/{game_id}/advance")
    assert response.status_code == 200
    data = response.json()
    assert "phase" in data


@pytest.mark.asyncio
async def test_abort_game(client):
    """测试中止对局: POST /api/games/{game_id}/abort。"""
    create_resp = await client.post("/api/games", json={"player_count": 9})
    game_id = create_resp.json()["game_id"]

    response = await client.post(f"/api/games/{game_id}/abort?reason=test")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ABORTED"


@pytest.mark.asyncio
async def test_list_games(client):
    """测试对局列表: GET /api/games 返回游戏列表。"""
    response = await client.get("/api/games")
    assert response.status_code == 200
    data = response.json()
    assert "games" in data
    assert "total" in data
    assert isinstance(data["games"], list)


@pytest.mark.asyncio
async def test_join_game(client):
    """测试加入对局: POST /api/games/{game_id}/join。"""
    create_resp = await client.post("/api/games", json={"player_count": 9})
    game_id = create_resp.json()["game_id"]

    response = await client.post(f"/api/games/{game_id}/join")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "START"


@pytest.mark.asyncio
async def test_health_check(client):
    """测试健康检查端点。"""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_root(client):
    """测试根路径端点。"""
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Werewolf Game Engine"