/**
 * 前端专用游戏状态聚合类型。
 *
 * **Why**: API 和 WebSocket 的类型是"传输契约"，而本文件的类型是 frontend
 * 内部使用的"UI 模型"。包括：
 * - 从前端视角重新组织的玩家状态（含身份牌路径映射）
 * - 游戏上下文快照
 * - 事件日志条目（简化版，仅保留 UI 渲染关注的字段）
 *
 * 参考: [`../api/types/enums.ts`](./enums.ts)
 */

import type { Role } from './enums'


// ============================================================================
// 身份牌图片路径映射
// ============================================================================

/**
 * 角色 → 身份牌图片的 public 路径映射。
 *
 * **Why**: 集中管理图片路径，组件中通过 role 直接查表获取图片 URL，
 * 避免在模板中散落路径字符串。
 */
export const ROLE_IMAGE_MAP: Readonly<Record<Role, string>> = {
  VILLAGER: '/role-villager.webp',
  WEREWOLF: '/role-werewolf.webp',
  SEER: '/role-seer.webp',
  WITCH: '/role-witch.webp',
  HUNTER: '/role-hunter.webp',
}

/**
 * 根据角色字符串获取身份牌图片路径。
 *
 * @param role 角色字符串（如 "WEREWOLF"）
 * @returns public 目录下的图片路径；未知角色返回空字符串
 */
export function getRoleImage(role: string): string {
  return ROLE_IMAGE_MAP[role as Role] ?? ''
}


// ============================================================================
// 玩家前端状态
// ============================================================================

/** 前端玩家状态（聚合 API 数据 + UI 渲染辅助字段） */
export interface PlayerState {
  player_id: string
  seat_number: number
  role: string
  is_alive: boolean
  /** 是否为真人玩家（否则为 AI） */
  is_human: boolean
  /** 玩家可读名称 */
  name: string
  /** 身份牌图片路径（从 ROLE_IMAGE_MAP 自动查表） */
  role_image: string
  /** 是否正在发言（用于高亮渲染） */
  is_speaking: boolean
  /** 最近一次发言内容摘要（用于座位旁气泡预览） */
  last_speech?: string
  /** 当前行动目标 player_id（投票/技能目标，null=无，'PASS'=弃权） */
  action_target?: string | null
  /** 当前行动类型（'VOTE'/'WOLF_KILL'/'SEER_CHECK' 等，由后端事件注入） */
  action_type?: string | null
}


// ============================================================================
// 游戏上下文
// ============================================================================

/** 游戏上下文快照（用于 Store 和组件访问） */
export interface GameContext {
  game_id: string
  status: string
  phase: string | null
  round: number
  player_count: number
  /** 玩家列表（按 seat_number 排序） */
  players: PlayerState[]
  /** 已接收的事件数量 */
  event_count: number
  /** 最后接收的事件 seq_num（用于增量拉取和断线恢复） */
  last_seq_num: number
  /** WebSocket 连接状态 */
  ws_connected: boolean
}


// ============================================================================
// 事件日志
// ============================================================================

/** 前端事件日志条目（简化版 Event，仅保留 UI 渲染关注的字段） */
export interface EventLogEntry {
  seq_num: number
  event_type: string
  timestamp: string
  /** 发言人 ID（SPEECH_EVENT 时有效） */
  speaker_id?: string
  /** 发言内容（SPEECH_EVENT 时有效） */
  content?: string
  /** 内心 OS（用于透视，GOD 视角下可看到 AI 的真实推理过程） */
  inner_thought?: string
  /** 系统公告文本（SYSTEM_ANNOUNCEMENT 时有效） */
  announcement?: string
  /** 死亡玩家 ID（PLAYER_DEATH 时有效） */
  dead_player_id?: string
  /** 阶段迁移信息（PHASE_TRANSITION_EVENT 时有效） */
  from_phase?: string
  to_phase?: string
}
