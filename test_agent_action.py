import asyncio
from ai_werewolf_core.schemas.enums import GamePhase, ActionType
from ai_werewolf_core.schemas.models import AgentAction

def test():
    proposed_action = {
        "action_type": "PASS",
        "actor_id": "player_1",
        "target_id": None,
        "phase": "INIT",
        "round": 1,
        "reason": "test",
        "inner_thought": "test",
        "confidence": 1.0,
    }
    try:
        action_obj = AgentAction(**proposed_action)
        print("Success:", action_obj)
    except Exception as e:
        print("Error:", e)

test()
