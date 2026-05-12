/**
 * WebSocket 消息协议类型定义。
 *
 * **Why**: 前端通过 WebSocket 接收服务端推送的事件和系统消息。
 * 所有消息类型使用 TypeScript 联合类型，确保消息处理的类型安全。
 *
 * 参考:
 * - [`ai_werewolf_core/api/ws/routes.py`](../../ai_werewolf_core/api/ws/routes.py)
 * - [`docs/plan/WebSocket 实时推送系统设计.md`](../../docs/plan/WebSocket%20实时推送系统设计.md)
 */


// ============================================================================
// 服务端 → 客户端消息
// ============================================================================

/** 连接确认消息 —— 服务端在 WebSocket 连接建立后发送 */
export interface ConnectedMessage {
  type: 'connected'
  game_id: string
  message: string
}

/** 事件推送消息 —— 服务端转发 EventBus 的 PUBLIC 事件 */
export interface EventPushMessage {
  type: 'event'
  event_id: string
  game_id: string
  seq_num: number
  event_type: string
  timestamp: string  // ISO 8601
  payload: Record<string, unknown>
}

/** 心跳响应消息 */
export interface PongMessage {
  type: 'pong'
}

/** 服务端 WebSocket 消息联合类型 */
export type WsServerMessage = ConnectedMessage | EventPushMessage | PongMessage


// ============================================================================
// 客户端 → 服务端消息
// ============================================================================

/** 心跳请求消息 —— 客户端定期发送以保持连接 */
export interface PingMessage {
  type: 'ping'
}

/** 客户端 WebSocket 消息联合类型（当前仅支持 ping） */
export type WsClientMessage = PingMessage
