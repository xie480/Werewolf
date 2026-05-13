import sys
from pathlib import Path

# Ensure the project root (Werewolf) is on sys.path so that
# `ai_werewolf_core` is importable from the tests/ directory.
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import pytest
import asyncio
from ai_werewolf_core.utils.logger import setup_logger
from ai_werewolf_core.core.event.bus import EventBus
from ai_werewolf_core.schemas.models import Event
from ai_werewolf_core.schemas.enums import EventType, Visibility
from ai_werewolf_core.utils.time_utils import now_tz

import pytest_asyncio

import uuid
from ai_werewolf_core.utils.redis_client import RedisClientManager

@pytest_asyncio.fixture
async def event_bus():
    bus = EventBus()
    yield bus
    await bus.clear()
    await RedisClientManager.close()

@pytest.mark.asyncio
async def test_event_bus_publish_and_seq_num(event_bus):
    game_id = f"game_{uuid.uuid4().hex}"
    event1 = Event(
        event_id="evt_1",
        game_id=game_id,
        seq_num=0,
        event_type=EventType.SYSTEM_ANNOUNCEMENT,
        visibility=Visibility.PUBLIC,
        timestamp=now_tz(),
        payload={"msg": "hello"}
    )
    
    event2 = Event(
        event_id="evt_2",
        game_id=game_id,
        seq_num=0,
        event_type=EventType.SYSTEM_ANNOUNCEMENT,
        visibility=Visibility.PUBLIC,
        timestamp=now_tz(),
        payload={"msg": "world"}
    )
    
    await event_bus.publish(event1)
    await event_bus.publish(event2)
    
    assert event1.seq_num == 1
    assert event2.seq_num == 2
    
    events = await event_bus.get_events(game_id, "player_1")
    assert len(events) == 2
    assert events[0].seq_num == 1
    assert events[1].seq_num == 2

@pytest.mark.asyncio
async def test_event_bus_subscribe(event_bus):
    game_id = f"game_{uuid.uuid4().hex}"
    received_events = []
    
    def handler(event: Event):
        received_events.append(event)
        
    event_bus.subscribe(EventType.SPEECH_EVENT, handler)
    
    event1 = Event(
        event_id="evt_1",
        game_id=game_id,
        seq_num=0,
        event_type=EventType.SPEECH_EVENT,
        visibility=Visibility.PUBLIC,
        timestamp=now_tz(),
        payload={"msg": "I am villager"}
    )
    
    event2 = Event(
        event_id="evt_2",
        game_id=game_id,
        seq_num=0,
        event_type=EventType.SYSTEM_ANNOUNCEMENT,
        visibility=Visibility.PUBLIC,
        timestamp=now_tz(),
        payload={"msg": "Day 1"}
    )
    
    await event_bus.publish(event1)
    await event_bus.publish(event2)
    
    assert len(received_events) == 1
    assert received_events[0].event_id == "evt_1"

@pytest.mark.asyncio
async def test_event_bus_visibility_filtering(event_bus):
    game_id = f"game_{uuid.uuid4().hex}"
    # PUBLIC event
    event_pub = Event(
        event_id="evt_pub",
        game_id=game_id,
        seq_num=0,
        event_type=EventType.SYSTEM_ANNOUNCEMENT,
        visibility=Visibility.PUBLIC,
        timestamp=now_tz()
    )
    
    # PRIVATE event for player_1
    event_priv = Event(
        event_id="evt_priv",
        game_id=game_id,
        seq_num=0,
        event_type=EventType.PRIVATE_RESOLUTION_EVENT,
        visibility=Visibility.PRIVATE,
        target_agents=["player_1"],
        timestamp=now_tz()
    )
    
    # FACTION event: target_agents 统一填具体玩家 ID（此处为狼人 player_2）
    event_fac = Event(
        event_id="evt_fac",
        game_id=game_id,
        seq_num=0,
        event_type=EventType.SPEECH_EVENT,
        visibility=Visibility.FACTION,
        target_agents=["player_2"],
        timestamp=now_tz()
    )
    
    await event_bus.publish(event_pub)
    await event_bus.publish(event_priv)
    await event_bus.publish(event_fac)
    
    # player_1 should see PUBLIC and PRIVATE(player_1), but not FACTION(player_2)
    events_p1 = await event_bus.get_events(game_id, "player_1")
    assert len(events_p1) == 2
    assert "evt_pub" in [e.event_id for e in events_p1]
    assert "evt_priv" in [e.event_id for e in events_p1]
    
    # player_2 should see PUBLIC and FACTION(player_2), but not PRIVATE(player_1)
    events_p2 = await event_bus.get_events(game_id, "player_2")
    assert len(events_p2) == 2
    assert "evt_pub" in [e.event_id for e in events_p2]
    assert "evt_fac" in [e.event_id for e in events_p2]


if __name__ == "__main__":
    """
    直接运行入口（python tests/test_event_bus.py）。
    
    初始化 structlog 日志系统后通过 pytest 运行当前文件中的所有测试。
    亦可使用 `python -m pytest tests/test_event_bus.py -v` 运行（推荐）。
    """
    sys.exit(pytest.main([__file__, "-v", "-s"]))
