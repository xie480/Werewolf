"""全局枚举定义 - Phase 1 基础设施。

**Why**: 架构规范明确要求“绝对禁止魔法字符串”。所有代表游戏阶段、玩家角色、动作类型、事件类型、
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
    NIGHT_ACTION = "NIGHT_ACTION"       # 执行夜间技能
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


class EventType(str, Enum):
    """事件类型枚举。

    用于 [`Event System`](docs/system/Event%20System.md) 中的事件路由与日志分类。
    每个生成的事件都必须携带明确的 event_type。
    """

    SPEECH_EVENT = "SPEECH_EVENT"                       # 玩家发言
    VOTE_EVENT = "VOTE_EVENT"                           # 投票事件
    PHASE_TRANSITION_EVENT = "PHASE_TRANSITION_EVENT"   # 阶段切换
    PRIVATE_RESOLUTION_EVENT = "PRIVATE_RESOLUTION_EVENT"  # 私密结算（如验人结果）
    SYSTEM_ANNOUNCEMENT = "SYSTEM_ANNOUNCEMENT"         # 系统公告（天亮、死亡名单）
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


class Faction(str, Enum):
    """阵营枚举。

    与 [`Role`](#role) 不同，阵营决定胜负条件，而角色决定技能。
    例如：预言家属于村民阵营。
    """

    VILLAGER = "VILLAGER"
    WEREWOLF = "WEREWOLF"
