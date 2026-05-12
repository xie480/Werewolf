/**
 * 事件查询 API —— 封装 /api/games/{game_id}/events 端点。
 *
 * **Why**: 支持按 seq_num 增量拉取（since_seq 分页），
 * 前端断线重连后可通过 last_seq_num 恢复。
 *
 * 参考: [`ai_werewolf_core/api/routes/events.py`](../../../ai_werewolf_core/api/routes/events.py)
 */

import { apiGet } from './client'
import type { EventListResponse } from '../types/api'


/**
 * 查询对局事件流（支持增量分页）。
 *
 * @param gameId 对局 ID
 * @param sinceSeq 起始 seq_num（0 表示从头开始）
 * @param limit 最大返回数量，默认 100
 * @returns 事件列表 + has_more 标识
 */
export function getEvents(
  gameId: string,
  sinceSeq?: number,
  limit?: number,
): Promise<EventListResponse> {
  return apiGet<EventListResponse>(`/games/${gameId}/events`, {
    since_seq: sinceSeq ?? 0,
    limit: limit ?? 100,
  })
}
