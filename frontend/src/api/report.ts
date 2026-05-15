import axios from 'axios'
import type { MatchReportResponse } from '../types/api'

const API_BASE = '/api/v1'

/**
 * 获取对局复盘报告
 * @param gameId 对局 ID
 */
export async function getGameReport(gameId: string): Promise<MatchReportResponse> {
  const response = await axios.get<MatchReportResponse>(`${API_BASE}/games/${gameId}/report`)
  return response.data
}
