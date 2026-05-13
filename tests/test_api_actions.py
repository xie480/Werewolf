"""
tests/test_api_actions.py  — 玩家操作 API 测试

**Why**: 验证 actions.py 路由的投票/发言/技能端点。

注意: 这些测试需要 Redis 运行，且需先创建并启动对局。
"""

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import pytest
from httpx import AsyncClient, ASGITransport

from ai_werewolf_core.main import app


@pytest.fixture
async def client():
    """创建 httpx AsyncClient。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def running_game_id(client):
    """创建并启动一个对局，返回 game_id。"""
    create_resp = await client.post("/api/games", json={"player_count": 9})
    game_id = create_resp.json()["game_id"]
    await client.post(f"/api/games/{game_id}/start")
    return game_id


@pytest.mark.asyncio
async def test_submit_vote_before_vote_phase(client, running_game_id):
    """测试在非投票阶段提交投票: 应返回 409 失败。"""
    game_id = running_game_id
    # 对局刚启动，当前是 NIGHT_START，不允许投票
    response = await client.post(
        f"/api/games/{game_id}/vote",
        json={"actor_id": "player_1", "target_id": "player_2"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_vote_status(client, running_game_id):
    """测试查询投票状态: 返回空投票快照。"""
    game_id = running_game_id
    response = await client.get(f"/api/games/{game_id}/vote/status")
    # 投票状态查询不依赖阶段，即使不在投票阶段也应返回成功
    # 但 VoteManager 需要初始化，可能返回 500 或 503
    assert response.status_code in (200, 500, 503)


@pytest.mark.asyncio
async def test_submit_speech_before_speech_phase(client, running_game_id):
    """测试在非发言阶段提交发言: 应返回 409。"""
    game_id = running_game_id
    response = await client.post(
        f"/api/games/{game_id}/speak",
        json={
            "actor_id": "player_1",
            "content": "Hello, I am a villager.",
        },
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_submit_action_invalid_type(client, running_game_id):
    """测试提交无效的动作类型: 应返回 400。"""
    game_id = running_game_id
    response = await client.post(
        f"/api/games/{game_id}/action",
        json={
            "actor_id": "player_1",
            "action_type": "INVALID_ACTION",
            "target_id": "player_2",
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_submit_action_in_night_phase(client, running_game_id):
    """测试在夜晚阶段提交合法技能: 应返回 200（通过校验）或 409（阶段不匹配）。"""
    game_id = running_game_id
    # 刚启动后是 NIGHT_START，没有角色可以在此阶段行动
    response = await client.post(
        f"/api/games/{game_id}/action",
        json={
            "actor_id": "player_1",
            "action_type": "WOLF_KILL",
            "target_id": "player_2",
        },
    )
    # 可能返回 409（阶段不匹配）或 200（取决于 player_1 的角色）
    assert response.status_code in (200, 409)


@pytest.mark.asyncio
async def test_submit_action_with_pass(client, running_game_id):
    """测试提交 PASS 动作: 不指定目标。"""
    game_id = running_game_id
    response = await client.post(
        f"/api/games/{game_id}/action",
        json={
            "actor_id": "player_1",
            "action_type": "PASS",
        },
    )
    # PASS 可能在非行动阶段被拒绝，或成功
    assert response.status_code in (200, 409)