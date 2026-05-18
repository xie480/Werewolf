/**
 * 前端枚举定义 —— 与后端 ai_werewolf_core/schemas/enums.py 一一对齐。
 *
 * **Why**: 使用 const 对象而非 TypeScript enum，因为 tsconfig 的 isolatedModules
 * 模式下 const enum 不可靠。所有枚举值保持与后端一致的字符串值，确保前后端
 * 通信时无需额外转换。
 *
 * 参考: [`ai_werewolf_core/schemas/enums.py`](../../ai_werewolf_core/schemas/enums.py)
 */


// ============================================================================
// 对局生命周期状态
// ============================================================================

/** 游戏对局生命周期状态（与后端 GameStatus 对齐） */
export const GameStatus = {
  INIT: 'INIT',
  START: 'START',
  RUNNING: 'RUNNING',
  SETTLING: 'SETTLING',
  FINISHED: 'FINISHED',
  ABORTED: 'ABORTED',
} as const
export type GameStatus = (typeof GameStatus)[keyof typeof GameStatus]


// ============================================================================
// 对局内阶段
// ============================================================================

/** 对局内阶段状态枚举（与后端 GamePhase 对齐） */
export const GamePhase = {
  // 准备阶段
  INIT: 'INIT',
  // 夜晚阶段
  NIGHT_START: 'NIGHT_START',
  NIGHT_WOLF_ACT: 'NIGHT_WOLF_ACT',
  NIGHT_WITCH_ACT: 'NIGHT_WITCH_ACT',
  NIGHT_SEER_ACT: 'NIGHT_SEER_ACT',
  NIGHT_RESOLVE: 'NIGHT_RESOLVE',
  // 白天阶段
  DAY_START: 'DAY_START',
  DAY_DISCUSSION: 'DAY_DISCUSSION',
  DAY_VOTE: 'DAY_VOTE',
  VOTE_RESOLVE: 'VOTE_RESOLVE',
  // 特殊阶段
  HUNTER_SHOOT: 'HUNTER_SHOOT',
  LAST_WORDS: 'LAST_WORDS',
  GAME_OVER: 'GAME_OVER',
  // 平票 PK 子阶段
  DAY_PK_DISCUSSION: 'DAY_PK_DISCUSSION',
  DAY_PK_VOTE: 'DAY_PK_VOTE',
} as const
export type GamePhase = (typeof GamePhase)[keyof typeof GamePhase]

/** 判断当前阶段是否属于夜晚阶段（用于背景切换） */
export function isNightPhase(phase: string): boolean {
  return phase.startsWith('NIGHT_')
}

/** 判断当前阶段是否属于白天阶段（用于背景切换） */
export function isDayPhase(phase: string): boolean {
  return phase.startsWith('DAY_')
}


// ============================================================================
// 玩家身份
// ============================================================================

/** 玩家身份枚举（与后端 Role 对齐） */
export const Role = {
  VILLAGER: 'VILLAGER',
  WEREWOLF: 'WEREWOLF',
  SEER: 'SEER',
  WITCH: 'WITCH',
  HUNTER: 'HUNTER',
} as const
export type Role = (typeof Role)[keyof typeof Role]


// ============================================================================
// 动作类型
// ============================================================================

/** 玩家动作类型枚举（与后端 ActionType 对齐） */
export const ActionType = {
  SPEAK: 'SPEAK',
  VOTE: 'VOTE',
  PASS: 'PASS',
  WOLF_KILL: 'WOLF_KILL',
  SEER_CHECK: 'SEER_CHECK',
  WITCH_SAVE: 'WITCH_SAVE',
  WITCH_POISON: 'WITCH_POISON',
  HUNTER_SHOOT: 'HUNTER_SHOOT',
} as const
export type ActionType = (typeof ActionType)[keyof typeof ActionType]


// ============================================================================
// 事件类型
// ============================================================================

/** 事件类型枚举（与后端 EventType 对齐） */
export const EventType = {
  SPEECH_EVENT: 'SPEECH_EVENT',
  SPEECH_TURN_EVENT: 'SPEECH_TURN_EVENT',
  VOTE_EVENT: 'VOTE_EVENT',
  PHASE_TRANSITION_EVENT: 'PHASE_TRANSITION_EVENT',
  PRIVATE_RESOLUTION_EVENT: 'PRIVATE_RESOLUTION_EVENT',
  SYSTEM_ANNOUNCEMENT: 'SYSTEM_ANNOUNCEMENT',
  PLAYER_DEATH: 'PLAYER_DEATH',
  GAME_OVER_EVENT: 'GAME_OVER_EVENT',
} as const
export type EventType = (typeof EventType)[keyof typeof EventType]


// ============================================================================
// 可见性
// ============================================================================

/** 事件可见性枚举（与后端 Visibility 对齐） */
export const Visibility = {
  PUBLIC: 'PUBLIC',
  PRIVATE: 'PRIVATE',
  FACTION: 'FACTION',
} as const
export type Visibility = (typeof Visibility)[keyof typeof Visibility]


// ============================================================================
// 情绪
// ============================================================================

/** 发言情绪枚举（与后端 Emotion 对齐） */
export const Emotion = {
  NEUTRAL: 'NEUTRAL',
  CONFUSED: 'CONFUSED',
  CONFIDENT: 'CONFIDENT',
  RELIEVED: 'RELIEVED',
  SELF_RIGHTEOUS: 'SELF_RIGHTEOUS',
  ANXIOUS: 'ANXIOUS',
  DEFENSIVE: 'DEFENSIVE',
  HESITANT: 'HESITANT',
  AGGRESSIVE: 'AGGRESSIVE',
  PROVOCATIVE: 'PROVOCATIVE',
  SUSPICIOUS: 'SUSPICIOUS',
  ANGRY: 'ANGRY',
  SURPRISED: 'SURPRISED',
  DESPERATE: 'DESPERATE',
} as const
export type Emotion = (typeof Emotion)[keyof typeof Emotion]


// ============================================================================
// 阵营
// ============================================================================

/** 阵营枚举（与后端 Faction 对齐） */
export const Faction = {
  VILLAGER: 'VILLAGER',
  WEREWOLF: 'WEREWOLF',
} as const
export type Faction = (typeof Faction)[keyof typeof Faction]
