/**
 * AI 玩家档案 API —— 封装 /api/ai-players 端点。
 *
 * **Why**: 在创建对局前，用户需要查看和管理 AI 玩家档案库，
 * 为每个座位选择或创建对应的 AI 玩家。
 *
 * **修改说明**:
 * 1. AIProfileResponse 去除了 avatar_url、model_provider、model_name、temperature，新增 model_id
 * 2. 创建/更新时只传 model_id，后端自动关联
 */

import { apiGet, apiPost, apiPut, apiDelete } from './client'
import type { ApiError } from './client'

// ============================================================================
// 类型定义
// ============================================================================

export interface AIStatsResponse {
  total_games: number
  wins: number
  losses: number
  win_rate: number
  response_failures: number
  total_actions: number
  last_played_at: string | null
}

export interface AIProfileResponse {
  id: string
  name: string
  model_id: string | null
  system_prompt: string | null
  is_active: boolean
  created_at: string
  stats: AIStatsResponse | null
}

export interface AIProfileListResponse {
  players: AIProfileResponse[]
  total: number
}

export interface AIProfileCreateRequest {
  name: string
  model_id: string
  system_prompt?: string
}

export interface AIProfileUpdateRequest {
  name?: string
  model_id?: string
  system_prompt?: string
  is_active?: boolean
}


// ============================================================================
// API 函数
// ============================================================================

export function listAiPlayers(activeOnly: boolean = false): Promise<AIProfileListResponse> {
  const query = activeOnly ? '?active_only=true' : ''
  return apiGet<AIProfileListResponse>(`/ai-players${query}`)
}

export function getAiPlayer(profileId: string): Promise<AIProfileResponse> {
  return apiGet<AIProfileResponse>(`/ai-players/${profileId}`)
}

export function createAiPlayer(data: AIProfileCreateRequest): Promise<AIProfileResponse> {
  return apiPost<AIProfileResponse>('/ai-players', data)
}

export function updateAiPlayer(profileId: string, data: AIProfileUpdateRequest): Promise<AIProfileResponse> {
  return apiPut<AIProfileResponse>(`/ai-players/${profileId}`, data)
}

export function deleteAiPlayer(profileId: string): Promise<{ status: string }> {
  return apiDelete<{ status: string }>(`/ai-players/${profileId}`)
}
