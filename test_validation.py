import asyncio
from ai_werewolf_core.schemas.enums import GamePhase, ActionType
from ai_werewolf_core.schemas.models import AgentAction
from ai_werewolf_core.core.action.validator import ActionValidator

async def main():
    # Simulate LLM output with action_target = "null"
    proposed_action = {
        "action_type": "PASS",
        "actor_id": "player_1",
        "target_id": "null",
        "phase": "NIGHT_WITCH_ACT",
        "round": 1,
        "reason": "test",
        "inner_thought": "test",
        "confidence": 1.0,
    }
    
    errors = []
    action_obj = None
    try:
        action_obj = AgentAction(**proposed_action)
        print("Schema validation passed")
    except Exception as e:
        print(f"Schema validation error: {e}")
        errors.append(str(e))
        
    if action_obj:
        result = await ActionValidator.validate_basic(action_obj, "game_1")
        if not result.is_valid:
            print(f"Business validation error: {result.reason}")
            errors.append(result.reason)
        else:
            print("Business validation passed")

if __name__ == "__main__":
    asyncio.run(main())
