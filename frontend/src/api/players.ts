/**
 * 玩家查询 API —— 封装 /api/games/{game_id}/players 端点。
 *
 * **Why**: 集中管理玩家状态查询 HTTP 调用，Store 层直接引用。
 *
 * 参考: [`ai_werewolf_core/api/routes/players.py`](../../../ai_werewolf_core/api/routes/players.py)
 */

import { apiGet } from './client'
import type { PlayerListResponse, PlayerResponse } from '../types/api'


/** 查询指定对局的所有玩家信息 */
export function getPlayers(gameId: string): Promise<PlayerListResponse> {
  return apiGet<PlayerListResponse>(`/games/${gameId}/players`)
}

/** 查询单个玩家信息 */
export function getPlayer(
  gameId: string,
  playerId: string,
): Promise<PlayerResponse> {
  return apiGet<PlayerResponse>(`/games/${gameId}/players/${playerId}`)
}
