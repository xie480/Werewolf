/**
 * AI 玩家档案 API —— 封装 /api/ai-players 端点。
 *
 * **Why**: 在创建对局前，用户需要查看和管理 AI 玩家档案库，
 * 为每个座位选择或创建对应的 AI 玩家。
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
  avatar_url: string | null
  model_provider: string
  model_name: string
  system_prompt: string | null
  temperature: number
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
  avatar_url?: string
  model_provider?: string
  model_name: string
  system_prompt?: string
  temperature?: number
}

export interface AIProfileUpdateRequest {
  name?: string
  avatar_url?: string
  model_provider?: string
  model_name?: string
  system_prompt?: string
  temperature?: number
  is_active?: boolean
}


// ============================================================================
// API 函数
// ============================================================================

/** 查询所有 AI 玩家档案（含统计数据） */
export function listAiPlayers(activeOnly: boolean = false): Promise<AIProfileListResponse> {
  const query = activeOnly ? '?active_only=true' : ''
  return apiGet<AIProfileListResponse>(`/ai-players${query}`)
}

/** 查询单个 AI 玩家档案详情 */
export function getAiPlayer(profileId: string): Promise<AIProfileResponse> {
  return apiGet<AIProfileResponse>(`/ai-players/${profileId}`)
}

/** 创建新的 AI 玩家档案 */
export function createAiPlayer(data: AIProfileCreateRequest): Promise<AIProfileResponse> {
  return apiPost<AIProfileResponse>('/ai-players', data)
}

/** 更新 AI 玩家档案 */
export function updateAiPlayer(profileId: string, data: AIProfileUpdateRequest): Promise<AIProfileResponse> {
  return apiPut<AIProfileResponse>(`/ai-players/${profileId}`, data)
}

/** 删除 AI 玩家档案 */
export function deleteAiPlayer(profileId: string): Promise<{ status: string }> {
  return apiDelete<{ status: string }>(`/ai-players/${profileId}`)
}
