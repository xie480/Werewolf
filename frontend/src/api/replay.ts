import { apiGet } from './client'
import type { ReplayResponse } from '../types/api'

/**
 * 获取对局回放数据
 * @param gameId 对局 ID
 * @param perspective 视角模式 ('GOD' | 'POV')
 * @param agentId POV 视角下的玩家 ID
 */
export async function getGameReplay(
  gameId: string,
  perspective: 'GOD' | 'POV' = 'GOD',
  agentId?: string
): Promise<ReplayResponse> {
  const params: Record<string, string> = { perspective }
  if (perspective === 'POV' && agentId) {
    params.agent_id = agentId
  }
  
  return apiGet<ReplayResponse>(`/games/${gameId}/replay`, params)
}
