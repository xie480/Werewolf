import pytest
from ai_werewolf_core.agents.prompts.builder import PromptBuilder
from ai_werewolf_core.schemas.models import MemorySnapshot, PrivateState, PublicEventLog, PrivateEventLog
from ai_werewolf_core.schemas.enums import Role, Faction, GamePhase

@pytest.fixture
def prompt_builder():
    return PromptBuilder()

@pytest.fixture
def sample_snapshot():
    from ai_werewolf_core.schemas.models import RoundMemory
    return MemorySnapshot(
        agent_id="player_1",
        game_id="game_123",
        history=[
            RoundMemory(
                round_num=1,
                public_events=[
                    PublicEventLog(
                        seq_num=1,
                        phase=GamePhase.DAY_START,
                        description="天亮了，昨晚平安夜。"
                    )
                ],
                private_facts=[
                    PrivateEventLog(
                        seq_num=2,
                        round_num=1,
                        phase=GamePhase.NIGHT_WOLF_ACT,
                        description="你和队友决定击杀 player_3。"
                    )
                ],
                reasoning=["我觉得 player_3 是预言家，必须杀掉。"]
            )
        ],
        private_state=PrivateState(
            role=Role.WEREWOLF,
            faction=Faction.WEREWOLF,
            teammates=["player_2"],
            skill_status={}
        ),
        experiences=["上次悍跳预言家时，发言不够自信被识破，这次要注意语气。"]
    )

@pytest.mark.asyncio
async def test_build_prompt_werewolf(prompt_builder, sample_snapshot):
    prompt = await prompt_builder.build_prompt(sample_snapshot)
    
    # 验证系统层
    assert "你的玩家ID是：player_1" in prompt
    assert "带领你的阵营（WEREWOLF）获得最终胜利" in prompt
    assert "当前游戏阶段（DAY_START）" in prompt
    
    # 验证角色策略层
    assert "你的底牌是：狼人" in prompt
    assert "你的已知狼人队友是：player_2" in prompt
    
    # 验证上下文层
    assert "[NIGHT_WOLF_ACT] 你和队友决定击杀 player_3。" in prompt
    assert "[DAY_START] 天亮了，昨晚平安夜。" in prompt
    assert "我觉得 player_3 是预言家，必须杀掉。" in prompt
    assert "上次悍跳预言家时，发言不够自信被识破，这次要注意语气。" in prompt
    
    # 验证输出格式层
    assert "你必须且只能输出一个合法的 JSON 对象" in prompt
    assert "internal_monologue" in prompt

@pytest.mark.asyncio
async def test_build_prompt_seer(prompt_builder, sample_snapshot):
    sample_snapshot.private_state.role = Role.SEER
    sample_snapshot.private_state.faction = Faction.VILLAGER
    sample_snapshot.private_state.teammates = []
    
    prompt = await prompt_builder.build_prompt(sample_snapshot)
    
    assert "你的底牌是：预言家" in prompt
    assert "带领你的阵营（VILLAGER）获得最终胜利" in prompt

@pytest.mark.asyncio
async def test_build_prompt_witch(prompt_builder, sample_snapshot):
    sample_snapshot.private_state.role = Role.WITCH
    sample_snapshot.private_state.faction = Faction.VILLAGER
    sample_snapshot.private_state.teammates = []
    sample_snapshot.private_state.skill_status = {"has_antidote": True, "has_poison": True}
    
    prompt = await prompt_builder.build_prompt(sample_snapshot)
    
    assert "你的底牌是：女巫" in prompt
    assert "{'has_antidote': True, 'has_poison': True}" in prompt

@pytest.mark.asyncio
async def test_build_prompt_villager(prompt_builder, sample_snapshot):
    sample_snapshot.private_state.role = Role.VILLAGER
    sample_snapshot.private_state.faction = Faction.VILLAGER
    sample_snapshot.private_state.teammates = []
    
    prompt = await prompt_builder.build_prompt(sample_snapshot)
    
    assert "你的底牌是：村民" in prompt

@pytest.mark.asyncio
async def test_build_prompt_hunter(prompt_builder, sample_snapshot):
    sample_snapshot.private_state.role = Role.HUNTER
    sample_snapshot.private_state.faction = Faction.VILLAGER
    sample_snapshot.private_state.teammates = []
    
    prompt = await prompt_builder.build_prompt(sample_snapshot)
    
    assert "你的底牌是：猎人" in prompt

@pytest.mark.asyncio
async def test_build_prompt_empty_timeline(prompt_builder, sample_snapshot):
    sample_snapshot.history = []
    prompt = await prompt_builder.build_prompt(sample_snapshot)
    
    assert "当前游戏阶段（INIT）" in prompt
