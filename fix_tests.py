import os

def fix_test_file(filepath):
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Replace create_initial_state calls
    content = content.replace(
        'create_initial_state(\n            game_id="game_1",\n            player_id="player_1",\n            current_phase=GamePhase.DAY_DISCUSSION\n        )',
        'create_initial_state(\n            game_id="game_1",\n            player_id="player_1",\n            current_phase=GamePhase.DAY_DISCUSSION,\n            current_round=1\n        )'
    )
    content = content.replace(
        'create_initial_state("game_1", "player_1", GamePhase.DAY_DISCUSSION)',
        'create_initial_state("game_1", "player_1", GamePhase.DAY_DISCUSSION, 1)'
    )
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Fixed {filepath}")

fix_test_file('tests/unit/test_agent_graph.py')
fix_test_file('tests/unit/agents/graph/test_nodes.py')
fix_test_file('tests/unit/agents/graph/test_graph.py')
