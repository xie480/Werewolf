# GameEngine 编排器设计

## 1. 架构定位与设计目标

GameEngine 是狼人杀对局的**中心化编排器 (Orchestrator)**，负责将所有 Game Engine 子系统串联为完整的游戏主循环。

**当前状态**: 所有子系统已独立实现完毕（PhaseStateMachine、LifecycleManager、ActionResolver、VoteManager、SpecialActionResolver、WinEvaluator、角色系统），但缺少一个统一的调度中心将各子系统按游戏规则协调运行。

**设计目标**:
- **零游戏逻辑**：Engine 自身不做任何规则判定，所有规则判定委托给下属 Manager 和 Role System。
- **纯流程编排**：Engine 的唯一职责是按游戏阶段调度对应的子系统，收集结果，决定下一阶段。
- **单一入口**：对外（API 层 / WebSocket 网关 / Celery Worker）暴露统一接口：初始化对局、提交动作、查询状态。

**文件路径**: `ai_werewolf_core/core/engine/game_engine.py`

## 2. 核心接口设计

### 2.1 GameEngine 主类

```python
class GameEngine:
    """游戏引擎编排器 —— 纯流程调度，不做规则判定。

    作为 Game Engine 子系统的 Facade，对外暴露统一接口。
    内部持有所有子系统的引用，在 submit_action 和 advance_phase
    两个核心方法中按阶段路由到对应的 Manager。

    **Why (编排器而非上帝对象)**:
    GameEngine 不包含任何游戏规则逻辑。所有规则判定由以下组件完成：
    - 阶段合法性 → PhaseStateMachine
    - 生命周期合法性 → LifecycleManager
    - 动作合法性 → ActionGate + Role System
    - 夜晚结算 → ActionResolver
    - 投票结算 → VoteManager
    - 特殊行动结算 → SpecialActionResolver
    - 胜负判定 → WinEvaluator

    Engine 只负责"在正确的时机调用正确的组件"。
    """

    def __init__(
        self,
        game_id: str,
        event_bus: EventBus,
        roles: dict[str, BaseRole],
    ):
        """初始化游戏引擎编排器。

        Args:
            game_id: 对局唯一标识（雪花 ID）。
            event_bus: 事件总线实例。
            roles: 初始角色映射 ``player_id → BaseRole``，
                由外部（对局初始化服务）在分配身份后传入。
        """
        self.game_id = game_id
        self.event_bus = event_bus
        self.roles = roles

        # ── 子系统初始化 ──
        self.state_machine = PhaseStateMachine(game_id, event_bus)
        self.lifecycle = LifecycleManager(game_id, event_bus)
        self.action_gate = ActionGate(game_id, event_bus)
        self.resolver = ActionResolver(game_id, event_bus)
        self.vote_manager = VoteManager(game_id, event_bus)
        self.special_action_resolver = SpecialActionResolver(game_id, event_bus)

        self._logger = logger.bind(game_id=game_id, module="GameEngine")

    # ==================================================================
    # 公开接口: 对局生命周期
    # ==================================================================

    async def init_game(self) -> None:
        """初始化对局: 写入 Redis 上下文，进入 INIT 阶段。

        调用 LifecycleManager.init_game() 完成:
        1. 初始化 Redis 对局上下文 (status=INIT, phase=None, round=0)
        2. 迁移到 START 状态
        3. 初始化 PhaseStateMachine (进入 INIT 阶段)

        此方法在对局创建时由 API 层调用。
        """

    async def start_game(self) -> GameStartResult:
        """启动对局: START → RUNNING，进入首轮 NIGHT_START。

        调用 LifecycleManager.start_game()，激活阶段状态机进入首轮黑夜。
        返回 GameStartResult 包含初始对局信息（玩家人数、角色分配等）。
        """

    async def end_game(self, winner_faction: str) -> None:
        """正常结束对局: RUNNING → SETTLING → FINISHED。

        由引擎在胜负判定后自动调用，或由外部（超时/管理员）触发。
        """

    async def abort_game(self, reason: str) -> None:
        """异常中止对局: 任意可中止状态 → ABORTED。"""

    # ==================================================================
    # 公开接口: 动作提交（Agent → Engine 的核心入口）
    # ==================================================================

    async def submit_action(self, action: AgentAction) -> SubmitResult:
        """接收 Agent 提交的动作，经门控校验后路由到对应 Manager。

        处理流程:
        1. ActionGate.admit()      —— 纯规则防火墙（存活/阶段/冷却/防作弊）
        2. 按阶段路由到 Manager    —— 游戏规则校验 + 业务逻辑

        路由规则:
        - 夜晚阶段 (NIGHT_WOLF_ACT/NIGHT_WITCH_ACT/NIGHT_SEER_ACT)
          → ActionResolver.submit_action()
        - 投票阶段 (DAY_VOTE/DAY_PK_VOTE)
          → VoteManager.submit_vote()
        - 特殊行动阶段 (HUNTER_SHOOT)
          → SpecialActionResolver.handle_action()
        - 发言阶段 (DAY_DISCUSSION/DAY_PK_DISCUSSION/LAST_WORDS)
          → Engine 直接处理（发言不需要复杂结算）

        Returns:
            SubmitResult 包含接受/拒绝状态及原因。
        """

    # ==================================================================
    # 公开接口: 阶段推进（Engine 内部自驱 or 外部触发）
    # ==================================================================

    async def advance_phase(self) -> AdvanceResult:
        """推进到下一个合法阶段。

        此方法是游戏主循环的核心驱动。Engine 根据当前阶段决定下一步:
        1. 读取当前阶段 (state_machine.get_current_phase())
        2. 执行当前阶段的退出逻辑（结算/胜负判定）
        3. 确定下一阶段
        4. 调用 lifecycle.advance_phase() 完成迁移

        阶段推进决策表见第3节。
        """

    # ==================================================================
    # 公开接口: 查询
    # ==================================================================

    async def get_game_state(self) -> GameState:
        """获取当前对局的完整快照（供 API 和 WebSocket 使用）。

        Returns:
            GameState 包含: status, phase, round, players (含存活状态),
            vote_status, recent_events 等。
        """

    async def get_status(self) -> GameStatus:
        """获取当前全局生命周期状态。"""
```

### 2.2 GameStartResult / SubmitResult / AdvanceResult 数据类

```python
@dataclass(frozen=True)
class GameStartResult:
    """对局启动结果。"""
    game_id: str
    player_count: int
    role_distribution: dict[str, str]  # player_id → role_name
    initial_phase: GamePhase

@dataclass(frozen=True)
class SubmitResult:
    """动作提交结果。"""
    accepted: bool
    action_id: Optional[str] = None    # 通过时返回动作 ID
    reason: Optional[str] = None       # 拒绝时返回原因
    requires_retry: bool = False       # Agent 是否可纠正后重试

@dataclass(frozen=True)
class AdvanceResult:
    """阶段推进结果。"""
    old_phase: GamePhase
    new_phase: GamePhase
    round: int
    deaths: list[str]                  # 本阶段结算导致的死亡玩家 ID
    game_over: bool = False
    winner: Optional[str] = None
    night_result: Optional[NightResolveResult] = None
    vote_result: Optional[VoteResolveResult] = None
```

## 3. 阶段推进决策表

`advance_phase()` 是引擎核心调度逻辑。决策表如下：

| 当前阶段 | 退出条件 | 结算动作 | 下一阶段 |
|----------|----------|----------|----------|
| `INIT` | 对局已启动 | 无 | `NIGHT_START` |
| `NIGHT_START` | 黑夜播报完成 | `resolver.begin_night()` | `NIGHT_WOLF_ACT` |
| `NIGHT_WOLF_ACT` | 所有狼人已提交动作或超时 | 无（暂存草稿） | `NIGHT_WITCH_ACT` |
| `NIGHT_WITCH_ACT` | 女巫已提交动作或超时 | 无（暂存草稿） | `NIGHT_SEER_ACT` |
| `NIGHT_SEER_ACT` | 预言家已提交动作或超时 | 无（暂存草稿） | `NIGHT_RESOLVE` |
| `NIGHT_RESOLVE` | 结算完成 | `resolver.resolve_night_actions()` → 死亡应用 → `evaluator.evaluate()` 胜负判定 | `DAY_START` 或 `GAME_OVER` |
| `DAY_START` | 天亮播报完成 | 检查猎人是否死亡需开枪 → 检查是否有遗言 | `HUNTER_SHOOT` / `LAST_WORDS` / `DAY_DISCUSSION` / `GAME_OVER` |
| `DAY_DISCUSSION` | 发言结束（所有存活玩家发言完毕或超时） | 无 | `DAY_VOTE` |
| `DAY_VOTE` | 所有存活玩家已投票或超时 | `vote_manager.resolve_vote()` → 死亡应用或平票 | `VOTE_RESOLVE` 或 `DAY_PK_DISCUSSION` |
| `VOTE_RESOLVE` | 投票结果播报完成 | `evaluator.evaluate()` 胜负判定；检查猎人死亡 | `HUNTER_SHOOT` / `LAST_WORDS` / `NIGHT_START` / `GAME_OVER` |
| `DAY_PK_DISCUSSION` | PK 发言结束 | 无 | `DAY_PK_VOTE` |
| `DAY_PK_VOTE` | 所有存活玩家已投票或超时 | `vote_manager.resolve_vote()`（PK 候选人限制） | `VOTE_RESOLVE` |
| `HUNTER_SHOOT` | 猎人已提交动作或超时 | `special_action_resolver.handle_action()` → 即时死亡 | `LAST_WORDS` / `DAY_DISCUSSION` / `GAME_OVER` |
| `LAST_WORDS` | 遗言发表完成或超时 | 无 | `DAY_DISCUSSION`（首夜）/ `NIGHT_START`（白天被票） |
| `GAME_OVER` | 游戏结束 | `lifecycle.end_game()` | 无 |

**超时处理**: 每个阶段有可配置的最大时长（通过 `config.py` 的 `settings` 管理）。超时后，未行动的玩家自动视为 PASS，引擎自动推进到下阶段。

## 4. 主循环伪代码

```python
async def advance_phase(self) -> AdvanceResult:
    """执行一次阶段推进。"""
    old_phase = await self.state_machine.get_current_phase()
    round_num = await self.state_machine.get_round()

    # ── Step 1: 执行当前阶段的退出逻辑（结算） ──
    deaths = []
    night_result = None
    vote_result = None
    game_over = False
    winner = None

    if old_phase == GamePhase.NIGHT_RESOLVE:
        night_result = await self.resolver.resolve_night_actions(self.roles)
        deaths = night_result.final_deaths
        # 结算后判定胜负
        eval_result = WinEvaluator.evaluate_detailed(self.roles)
        if eval_result.is_game_over:
            game_over = True
            winner = eval_result.winner.value if eval_result.winner else None

    elif old_phase in (GamePhase.DAY_VOTE, GamePhase.DAY_PK_VOTE):
        vote_result = await self.vote_manager.resolve_vote(self.roles, round_num)
        if vote_result.sole_voted_out:
            deaths = [vote_result.sole_voted_out]
        # 投票结算后判定胜负
        eval_result = WinEvaluator.evaluate_detailed(self.roles)
        if eval_result.is_game_over:
            game_over = True
            winner = eval_result.winner.value if eval_result.winner else None

    # ── Step 2: 确定下一阶段 ──
    next_phase = self._determine_next_phase(
        old_phase, deaths, game_over, vote_result
    )

    # ── Step 3: 执行阶段迁移 ──
    await self.lifecycle.advance_phase(next_phase)

    # ── Step 4: 如果游戏结束，调用 end_game ──
    if next_phase == GamePhase.GAME_OVER:
        await self.lifecycle.end_game(winner or "UNKNOWN")

    return AdvanceResult(
        old_phase=old_phase,
        new_phase=next_phase,
        round=round_num,
        deaths=deaths,
        game_over=game_over,
        winner=winner,
        night_result=night_result,
        vote_result=vote_result,
    )

def _determine_next_phase(
    self,
    current_phase: GamePhase,
    deaths: list[str],
    game_over: bool,
    vote_result: Optional[VoteResolveResult],
) -> GamePhase:
    """根据当前阶段和结算结果确定下一个阶段。

    **Why (独立方法)**: 将阶段决策逻辑从推进流程中分离，
    使得决策规则可被单独测试和审计。

    决策依赖:
    - PhaseStateMachine.VALID_TRANSITIONS 中定义的合法后继集合
    - 结算结果（死亡名单、平票情况、胜负判定）
    - 角色状态（猎人是否死亡/是否被毒杀）
    """
    # 此处根据第3节的决策表实现具体分支逻辑
    # 优先检查 game_over → HUNTER_SHOOT → LAST_WORDS → 常规流转
    ...
```

## 5. 与外部模块的集成

### 5.1 与 API 层的集成

```python
# api/routes/game.py 中的典型使用模式
@router.post("/games/{game_id}/actions")
async def submit_action(game_id: str, action: AgentAction):
    engine = get_game_engine(game_id)  # 从 Redis 或内存注册表中获取
    result = await engine.submit_action(action)
    if not result.accepted:
        return {"error": result.reason, "retry_allowed": result.requires_retry}
    return {"status": "accepted", "action_id": result.action_id}

@router.post("/games/{game_id}/advance")
async def advance_phase(game_id: str):
    engine = get_game_engine(game_id)
    result = await engine.advance_phase()
    # 通过 WebSocket 推送 AdvanceResult 给所有在线客户端
    await ws_manager.broadcast(game_id, result)
    return result
```

### 5.2 与 Celery Worker 的集成

```python
# worker.py 中的游戏主循环
@celery_app.task
def run_game_loop(game_id: str):
    """在 Celery Worker 中运行的独立游戏主循环。"""
    engine = build_game_engine(game_id)
    loop = asyncio.get_event_loop()

    async def _loop():
        await engine.start_game()
        while True:
            status = await engine.get_status()
            if status in (GameStatus.FINISHED, GameStatus.ABORTED):
                break
            # 等待当前阶段所有玩家提交动作（或超时）
            await wait_for_phase_completion(engine)
            result = await engine.advance_phase()
            if result.game_over:
                break

    loop.run_until_complete(_loop())
```

### 5.3 Engine 生命周期管理

Engine 实例不持久化，每次对局由 Celery Worker 在启动时创建，存在 Worker 进程内存中。所有持久状态（phase、round、status、votes）存储在 Redis 中。Worker 崩溃后，新 Worker 可通过 `LifecycleManager.load_from_redis()` 恢复状态并重建 Engine 实例。

## 6. 文件结构

```
ai_werewolf_core/core/engine/
├── state_machine.py           # PhaseStateMachine（已有）
├── lifecycle.py               # LifecycleManager（已有）
├── vote_manager.py            # VoteManager（已有）
├── resolver.py                # ActionResolver（已有）
├── evaluator.py               # WinEvaluator（已有）
├── special_action_resolver.py # SpecialActionResolver（已有）
├── exceptions.py              # 引擎异常定义（已有）
├── player_manager.py          # PlayerStatusManager（已有）
├── roles/                     # 角色定义（已有）
├── game_engine.py             # [新增] GameEngine 编排器 ← 本文档
└── __init__.py                # 更新导出
```

**新增文件**:
- `core/engine/game_engine.py`

**新增测试**:
- `tests/unit/core/engine/test_game_engine.py`

## 7. 与其他设计文档的关系

| 文档 | 关系 |
|------|------|
| `状态机与生命周期设计.md` | Engine 通过 `PhaseStateMachine` 和 `LifecycleManager` 管理状态 |
| `白天行动结算与投票管理器设计.md` | Engine 将投票和特殊行动路由到对应 Manager（该文档第3节已预定义路由逻辑） |
| `行动结算与胜负判定设计.md` | Engine 在结算阶段调用 `ActionResolver` 和 `WinEvaluator` |
| `角色与能力系统设计.md` | Engine 持有 `roles` 映射并在路由时传递给 Manager |
| `动作校验与防作弊系统设计.md` | Engine 在 `submit_action` 入口调用 `ActionGate.admit()` |

## 8. 遵循的架构规范

1. **零规则判定**: Engine 自身不做任何游戏规则判定，全部委托给子系统。
2. **引擎是唯一权威**: LLM 不可决定阶段流转、胜负、合法性——全部由 Engine + 子系统硬编码判定。
3. **无魔法字符串**: 所有阶段、状态、动作类型使用 `schemas/enums.py` 枚举。
4. **强类型声明**: 所有接口参数和返回值使用 Type Hinting 和 `@dataclass` 模型。
5. **异步优先**: 涉及 Redis、EventBus 的操作全部使用 `async/await`。
6. **结构化日志**: 每次阶段变更、动作提交、结算结果均通过 `structlog` 记录，注入 `game_id` 上下文。
7. **最小变更原则**: 新增 `game_engine.py` 不修改任何已有子系统代码，纯增量添加。
8. **注释规范**: 核心方法包含 `**Why**` 注释，解释设计决策和边界处理。
