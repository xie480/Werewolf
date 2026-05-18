"""全局枚举定义 - Phase 1 基础设施。

**Why**: 架构规范明确要求"绝对禁止魔法字符串"。所有代表游戏阶段、玩家角色、动作类型、事件类型、
可见性等语义常量必须定义为 `Enum` 枚举类，确保编译安全和统一引用。
如果在业务代码中直接使用字符串字面量（如 `if phase == "DAY_VOTE"`），
将导致难以排查的拼写错误和重构困难。本模块提供唯一的数据来源。
"""

from enum import Enum


class GameStatus(str, Enum):
    """游戏对局生命周期状态。
    
    与 [`Game Engine.md`](docs/system/Game%20Engine.md) 中的定义保持一致：
    ```text
    INIT -> START -> RUNNING -> SETTLING -> FINISHED / ABORTED
    ```
    """

    INIT = "INIT"           # 初始化玩家与身份
    START = "START"         # 开局
    RUNNING = "RUNNING"     # 对局进行中
    SETTLING = "SETTLING"   # 结算中
    FINISHED = "FINISHED"   # 正常结束
    ABORTED = "ABORTED"     # 异常中断


class GamePhase(str, Enum):
    """对局内阶段状态枚举。
    
    **Why**: 状态机的阶段流转必须严格硬编码，绝不允许 LLM 自由发挥。
    每个阶段对应明确的进入钩子（On_Enter）、行动窗口（Process_Window）和退出条件（Exit_Condition）。
    
    参考 [`Phase System.md`](docs/system/Phase%20System.md)。
    """

    # 准备阶段
    INIT = "INIT"

    # 夜晚阶段
    NIGHT_START = "NIGHT_START"         # 黑夜降临播报
    NIGHT_WOLF_ACT = "NIGHT_WOLF_ACT"   # 狼人行动阶段
    NIGHT_WITCH_ACT = "NIGHT_WITCH_ACT" # 女巫行动阶段
    NIGHT_SEER_ACT = "NIGHT_SEER_ACT"   # 预言家行动阶段
    NIGHT_RESOLVE = "NIGHT_RESOLVE"     # 系统结算夜间伤亡（不允许 LLM 参与）

    # 白天阶段
    DAY_START = "DAY_START"             # 天亮播报，公布昨夜死者
    DAY_DISCUSSION = "DAY_DISCUSSION"   # 顺序发言阶段
    DAY_VOTE = "DAY_VOTE"               # 并发投票阶段
    VOTE_RESOLVE = "VOTE_RESOLVE"       # 结算投票结果

    # 特殊阶段
    HUNTER_SHOOT = "HUNTER_SHOOT"       #猎人死亡开枪阶段
    LAST_WORDS = "LAST_WORDS"           # 遗言阶段
    GAME_OVER = "GAME_OVER"             # 游戏结束

    # 平票 PK 子阶段（动态插入）—— 用于解决投票平局死锁
    DAY_PK_DISCUSSION = "DAY_PK_DISCUSSION" # PK拉票阶段
    DAY_PK_VOTE = "DAY_PK_VOTE"             # PK投票阶段


class Role(str, Enum):
    """玩家身份枚举。

    基础版包含经典狼人杀的角色集合，后续可扩展新角色。
    """

    VILLAGER = "VILLAGER"       # 村民
    WEREWOLF = "WEREWOLF"       # 狼人
    SEER = "SEER"               # 预言家
    WITCH = "WITCH"             # 女巫
    HUNTER = "HUNTER"           # 猎人


class ActionType(str, Enum):
    """玩家动作类型枚举 —— 动作空间（Action Space）。

    **Why**: 大模型是混沌的，它可能在黑夜想要发言，或试图执行不存在的动作。
    通过严格限定动作空间，Action System 可以在解析阶段直接拦截非法动作，
    防止污染游戏状态。

    参考 [`Action System.md`](docs/system/Action%20System.md)。
    """

    # 通用动作（白天与全天可用）
    SPEAK = "SPEAK"             # 发言
    VOTE = "VOTE"               # 投票（可弃权，目标设为 null）
    PASS = "PASS"               # 空过／不发动技能（关键，用于女巫不救人等）

    # 夜间技能动作
    WOLF_KILL = "WOLF_KILL"     # 狼人刀人
    SEER_CHECK = "SEER_CHECK"   # 预言家验人
    WITCH_SAVE = "WITCH_SAVE"   # 女巫使用解药
    WITCH_POISON = "WITCH_POISON"  # 女巫使用毒药

    # 特殊技能动作
    HUNTER_SHOOT = "HUNTER_SHOOT"   # 猎人死亡开枪


class EventType(str, Enum):
    """事件类型枚举。

    用于 [`Event System`](docs/system/Event%20System.md) 中的事件路由与日志分类。
    每个生成的事件都必须携带明确的 event_type。
    """

    SPEECH_EVENT = "SPEECH_EVENT"                       # 玩家发言
    SPEECH_TURN_EVENT = "SPEECH_TURN_EVENT"             # 轮到某玩家发言
    VOTE_EVENT = "VOTE_EVENT"                           # 投票事件
    PHASE_TRANSITION_EVENT = "PHASE_TRANSITION_EVENT"   # 阶段切换
    PRIVATE_RESOLUTION_EVENT = "PRIVATE_RESOLUTION_EVENT"  # 私密结算（如验人结果）
    SYSTEM_ANNOUNCEMENT = "SYSTEM_ANNOUNCEMENT"         # 系统公告（天亮）
    PLAYER_DEATH = "PLAYER_DEATH"                       # 玩家死亡通知
    GAME_OVER_EVENT = "GAME_OVER_EVENT"                 # 游戏结束


class Visibility(str, Enum):
    """事件可见性枚举。

    **Why**: 狼人杀是信息不对称博弈。必须严格区分不同可见性等级的事件，
    避免信息泄露。每种可见性对应不同的路由策略：
    - PUBLIC   → 所有玩家的公共记忆池 + 前端观战大屏
    - PRIVATE  → 仅目标玩家的私有记忆池 + 前端观战大屏（上帝视角）
    - FACTION  → 同阵营玩家共享 + 前端观战大屏
    """

    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"
    FACTION = "FACTION"


class Emotion(str, Enum):
    """发言情绪枚举。

    限定了 AI 玩家在发言时能表达的情绪状态，用于前端驱动虚拟角色表情动画，
    并为复盘分析提供结构化的情绪数据。绝对禁止 LLM 自由输出任意情绪字符串。
    覆盖狼人杀对局中从强势带节奏到无奈表水的完整情绪光谱。
    """

    # --- 基础情绪 ---
    NEUTRAL = "NEUTRAL"                 # 平静/中立（默认状态）
    CONFUSED = "CONFUSED"               # 疑惑/迷茫（闭眼村民常见）

    # --- 正面/自信类 ---
    CONFIDENT = "CONFIDENT"             # 自信/笃定（预言家报查验、强神跳身份）
    RELIEVED = "RELIEVED"               # 如释重负（危机解除、被救活）
    SELF_RIGHTEOUS = "SELF_RIGHTEOUS"   # 义正言辞（强神立威、真预言家正气）

    # --- 防御/弱势类 ---
    ANXIOUS = "ANXIOUS"                 # 焦虑/紧张（被污蔑、即将被归票）
    DEFENSIVE = "DEFENSIVE"             # 防御性/委屈（表水、被踩后的解释）
    HESITANT = "HESITANT"               # 犹豫/摇摆（投票不确定、分不清真假预言家）

    # --- 攻击/强势类 ---
    AGGRESSIVE = "AGGRESSIVE"           # 攻击性/强势（带节奏、踩人、归票）
    PROVOCATIVE = "PROVOCATIVE"         # 挑衅/煽动（狼人煽风点火、激将法）

    # --- 应激/极端类 ---
    SUSPICIOUS = "SUSPICIOUS"           # 怀疑/猜忌（质问他人、指出矛盾）
    ANGRY = "ANGRY"                     # 愤怒（被严重污蔑或背叛时的激烈反应）
    SURPRISED = "SURPRISED"             # 惊讶（预言家验出意外结果、女巫救人等反转）
    DESPERATE = "DESPERATE"             # 绝望/拼命（最后一搏、遗言、被归票时的无力感）


class Faction(str, Enum):
    """阵营枚举。

    与 [`Role`](#role) 不同，阵营决定胜负条件，而角色决定技能。
    例如：预言家属于村民阵营。
    """

    VILLAGER = "VILLAGER"
    WEREWOLF = "WEREWOLF"


class SurvivalRequirement(str, Enum):
    """角色对动作的生存状态要求。

    **Why**: 集中式 ActionValidator 需要知道"当前角色对此动作要求存活还是死亡"，
    但不能在 Validator 中硬编码角色逻辑。通过枚举让每个角色声明自己的需求，
    Validator 只负责查 Redis BitMap 并比对。

    用于 ActionValidator 的生存状态校验环节：
    - MUST_BE_ALIVE → Validator 拒绝已死亡的玩家
    - MUST_BE_DEAD  → Validator 拒绝仍存活的玩家
    - ANY           → 不校验生存状态（如 PASS 动作）
    """

    MUST_BE_ALIVE = "MUST_BE_ALIVE"
    MUST_BE_DEAD = "MUST_BE_DEAD"
    ANY = "ANY"


# ============================================================================
# 阶段分组常量
# ============================================================================

# 夜晚行动阶段集合（用于动作路由：狼人刀人、女巫救人/毒人、预言家验人）
NIGHT_ACT_PHASES: frozenset[GamePhase] = frozenset({
    GamePhase.NIGHT_WOLF_ACT,
    GamePhase.NIGHT_WITCH_ACT,
    GamePhase.NIGHT_SEER_ACT,
})

# 投票阶段集合（用于动作路由：投票 + PK投票）
VOTE_PHASES: frozenset[GamePhase] = frozenset({
    GamePhase.DAY_VOTE,
    GamePhase.DAY_PK_VOTE,
})

# 发言阶段集合（Engine 直接处理，不需要复杂结算）
SPEECH_PHASES: frozenset[GamePhase] = frozenset({
    GamePhase.DAY_DISCUSSION,
    GamePhase.DAY_PK_DISCUSSION,
    GamePhase.LAST_WORDS,
})

# 结算阶段集合（不接受动作提交）
RESOLVE_PHASES: frozenset[GamePhase] = frozenset({
    GamePhase.NIGHT_RESOLVE,
    GamePhase.VOTE_RESOLVE,
    GamePhase.NIGHT_START,
    GamePhase.DAY_START,
    GamePhase.GAME_OVER,
    GamePhase.INIT,
})
