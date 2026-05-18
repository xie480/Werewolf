/**
 * API 层 TypeScript 接口定义 —— 对齐后端 ai_werewolf_core/schemas/api.py。
 *
 * **Why**: 后端 Python Pydantic Schema 与前端 TypeScript interface 一一对应，
 * 确保 REST API 的请求体和响应体类型安全。所有字段名保持 snake_case 与后端 JSON 一致。
 *
 * 参考: [`ai_werewolf_core/schemas/api.py`](../../ai_werewolf_core/schemas/api.py)
 */


// ============================================================================
// 对局管理
// ============================================================================

/** 创建对局请求 */
export interface CreateGameRequest {
  /** 玩家人数，范围 6-12，默认 9 */
  player_count: number
  /** 角色配置列表 */
  role_setup?: string[]
  /** 玩家配置列表 */
  players?: PlayerSetupConfig[]
}

/** 玩家设置配置 */
export interface PlayerSetupConfig {
  /** 玩家类型 - existing (已有AI档案) 或 dynamic (动态创建) */
  type: string
  /** 玩家ID (当type为existing时必需) */
  player_id?: string
  /** 配置对象 (当type为dynamic时必需) */
  config?: Record<string, unknown>
}

/** 创建对局成功响应 */
export interface CreateGameResponse {
  /** 雪花算法生成的对局 ID */
  game_id: string
  /** 对局状态，创建后为 "START" */
  status: string
}

/** 对局详情响应 */
export interface GameDetailResponse {
  game_id: string
  status: string
  phase: string | null
  round: number
  player_count: number
  current_speaker?: string | null
  speech_queue?: string[]
}

/** 对局状态简要响应（start/advance/abort 操作后） */
export interface GameStatusResponse {
  game_id: string
  status: string
  phase: string | null
  round: number
  current_speaker?: string | null
  speech_queue?: string[]
}

/** 对局列表响应 */
export interface GameListResponse {
  games: GameDetailResponse[]
  total: number
}


// ============================================================================
// 玩家查询
// ============================================================================

/** 单个玩家信息响应 */
export interface PlayerResponse {
  player_id: string
  seat_number: number
  /** 角色字符串，如 "WEREWOLF", "SEER" */
  role: string
  is_alive: boolean
  /** 是否为真人玩家（否则为 AI） */
  is_human: boolean
  /** 玩家可读名称 */
  name: string
}

/** 玩家列表响应 */
export interface PlayerListResponse {
  game_id: string
  players: PlayerResponse[]
  total: number
}


// ============================================================================
// 事件查询
// ============================================================================

/** 单个事件响应 */
export interface EventResponse {
  event_id: string
  seq_num: number
  event_type: string
  visibility: string
  target_agents: string[]
  /** ISO 8601 格式 */
  timestamp: string
  payload: Record<string, unknown>
}

/** 事件列表响应 */
export interface EventListResponse {
  game_id: string
  events: EventResponse[]
  total: number
  has_more: boolean
}


// ============================================================================
// 投票 / 发言 / 技能操作
// ============================================================================

/** 提交投票请求 */
export interface SubmitVoteRequest {
  /** 投票人 ID */
  actor_id: string
  /** 被投人 ID，null 表示弃权 */
  target_id: string | null
}

/** 投票状态响应 */
export interface VoteStatusResponse {
  game_id: string
  /** voter_id → target_id 映射 */
  votes: Record<string, string>
  /** 已投票人数 */
  voter_count: number
  /** 是否为 PK 投票 */
  is_pk_vote: boolean
}

/** 提交发言请求 */
export interface SubmitSpeechRequest {
  /** 发言人 ID */
  actor_id: string
  /** 发言内容，1-2000 字符 */
  content: string
  /** 情绪标签，如 "CONFIDENT", "ANXIOUS" */
  emotion?: string
}

/** 提交夜间技能请求 */
export interface SubmitActionRequest {
  /** 行动者 ID */
  actor_id: string
  /** 动作类型，如 "WOLF_KILL", "WITCH_SAVE", "SEER_CHECK" */
  action_type: string
  /** 目标玩家 ID，PASS 时为 null */
  target_id?: string
}

/** 技能操作响应 */
export interface ActionResponse {
  success: boolean
  action_type: string
  actor_id: string
  target_id?: string
}


// ============================================================================
// 评测复盘
// ============================================================================

export interface AgentEvaluationResponse {
  player_id: string
  role: string
  rule_compliance_score: number
  logical_consistency_score: number
  roleplay_score: number
  deception_score: number | null
  god_deduction_score: number | null
  situational_awareness_score: number | null
  leadership_score: number | null
  strengths: string | null
  weaknesses: string | null
  overall_review: string | null
}

export interface MatchReportResponse {
  report_id: string
  game_id: string
  duration_seconds: number
  winner: string
  mvp_agent_id: string
  faction_win_probability_curve?: Record<number, number>
  evaluations: AgentEvaluationResponse[]
}

// ============================================================================
// 回放系统
// ============================================================================

export interface ReplayPlayerInfo {
  agent_id: string
  seat_number: number
  role: string
  /** 玩家可读名称（可选，回放数据可能包含） */
  name?: string
}

export interface ReplayInitialState {
  players: ReplayPlayerInfo[]
}

export interface ReplayPhaseChunk {
  phase_name: string
  events: EventResponse[]
}

export interface ReplayDayChunk {
  day_num: number
  phases: ReplayPhaseChunk[]
}

export interface ReplayResponse {
  game_id: string
  perspective: string
  agent_id?: string
  initial_state: ReplayInitialState
  timeline: ReplayDayChunk[]
}

// ============================================================================
// 通用
// ============================================================================

/** 统一错误响应 */
export interface ErrorResponse {
  error: string
  detail?: string
  game_id?: string
}
