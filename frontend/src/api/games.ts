/**
 * 对局管理 API —— 封装所有 /api/games 端点。
 *
 * **Why**: 集中管理对局生命周期相关 HTTP 调用，Store 层直接引用，
 * 避免在状态管理中散落 fetch 调用。
 *
 * 参考: [`ai_werewolf_core/api/routes/games.py`](../../../ai_werewolf_core/api/routes/games.py)
 */

import { apiGet, apiPost, apiPostQuery } from './client'
import type {
  CreateGameRequest,
  CreateGameResponse,
  GameDetailResponse,
  GameListResponse,
  GameStatusResponse,
} from '../types/api'


// ============================================================================
// P0: 对局生命周期
// ============================================================================

/** 创建新对局 */
export function createGame(body?: CreateGameRequest): Promise<CreateGameResponse> {
  return apiPost<CreateGameResponse>('/games', body ?? { player_count: 9 })
}

/** 启动对局: START → RUNNING */
export function startGame(gameId: string): Promise<GameStatusResponse> {
  return apiPost<GameStatusResponse>(`/games/${gameId}/start`)
}

/** 查询对局当前状态 */
export function getGame(gameId: string): Promise<GameDetailResponse> {
  return apiGet<GameDetailResponse>(`/games/${gameId}`)
}


// ============================================================================
// P1: 阶段推进
// ============================================================================

/** 推进游戏阶段 */
export function advancePhase(
  gameId: string,
  nextPhase?: string,
): Promise<GameStatusResponse> {
  return apiPost<GameStatusResponse>(
    `/games/${gameId}/advance`,
    nextPhase ? { next_phase: nextPhase } : undefined,
  )
}


// ============================================================================
// P2: 对局中止
// ============================================================================

/** 中止对局 */
export function abortGame(gameId: string, reason?: string): Promise<GameStatusResponse> {
  return apiPostQuery<GameStatusResponse>(`/games/${gameId}/abort`, {
    reason: reason ?? 'user_abort',
  })
}


// ============================================================================
// P3: 加入对局 + 对局列表
// ============================================================================

/** 加入已有对局 */
export function joinGame(gameId: string): Promise<GameStatusResponse> {
  return apiPost<GameStatusResponse>(`/games/${gameId}/join`)
}

/** 获取活跃对局列表 */
export function listGames(): Promise<GameListResponse> {
  return apiGet<GameListResponse>('/games')
}
