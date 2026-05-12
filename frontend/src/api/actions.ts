/**
 * 玩家操作 API —— 封装投票/发言/夜间技能端点。
 *
 * **Why**: 集中管理所有玩家交互操作的 HTTP 调用。
 * Store 层通过乐观更新模式调用这些函数，点击即禁用按钮，
 * 等后端确认后再解除。
 *
 * 参考: [`ai_werewolf_core/api/routes/actions.py`](../../../ai_werewolf_core/api/routes/actions.py)
 */

import { apiGet, apiPost } from './client'
import type {
  ActionResponse,
  SubmitActionRequest,
  SubmitSpeechRequest,
  SubmitVoteRequest,
  VoteStatusResponse,
} from '../types/api'


// ============================================================================
// 投票
// ============================================================================

/** 提交投票 */
export function submitVote(
  gameId: string,
  body: SubmitVoteRequest,
): Promise<ActionResponse> {
  return apiPost<ActionResponse>(`/games/${gameId}/vote`, body)
}

/** 查询当前投票状态 */
export function getVoteStatus(gameId: string): Promise<VoteStatusResponse> {
  return apiGet<VoteStatusResponse>(`/games/${gameId}/vote/status`)
}


// ============================================================================
// 发言
// ============================================================================

/** 提交发言 */
export function submitSpeech(
  gameId: string,
  body: SubmitSpeechRequest,
): Promise<ActionResponse> {
  return apiPost<ActionResponse>(`/games/${gameId}/speak`, body)
}


// ============================================================================
// 夜间技能
// ============================================================================

/** 提交夜间技能动作（狼刀/女巫救毒/预言家验人/猎人开枪/PASS） */
export function submitAction(
  gameId: string,
  body: SubmitActionRequest,
): Promise<ActionResponse> {
  return apiPost<ActionResponse>(`/games/${gameId}/action`, body)
}
