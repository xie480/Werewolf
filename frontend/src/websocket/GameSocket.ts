/**
 * WebSocket 客户端 —— 管理与后端的持久化双向通信。
 *
 * **Why**: 前端通过 WebSocket 接收对局实时事件推送，避免轮询 REST API。
 * 支持自动重连（指数退避）、心跳保持、事件回调注册。
 *
 * 用法:
 * ```ts
 * const socket = createGameSocket('game_123')
 * socket.onMessage((msg) => { if (msg.type === 'event') { ... } })
 * socket.onConnectionChange((connected) => { ... })
 * socket.connect()
 * ```
 *
 * 参考:
 * - [`ai_werewolf_core/api/ws/routes.py`](../../../ai_werewolf_core/api/ws/routes.py)
 * - [`docs/plan/WebSocket 实时推送系统设计.md`](../../../docs/plan/WebSocket%20实时推送系统设计.md)
 */

import type { WsServerMessage } from '../types/websocket'


// ============================================================================
// 配置常量
// ============================================================================

/** 最大重连次数 */
const MAX_RECONNECT_ATTEMPTS = 5

/** 初始重连延迟（毫秒），后续指数增长 */
const INITIAL_RECONNECT_DELAY_MS = 1_000

/** 最大重连延迟（毫秒） */
const MAX_RECONNECT_DELAY_MS = 30_000

/** 心跳间隔（毫秒），服务端 30s 超时，客户端 25s 发送一次 */
const PING_INTERVAL_MS = 25_000


// ============================================================================
// 类型定义
// ============================================================================

/** 消息回调函数类型 */
export type MessageHandler = (message: WsServerMessage) => void

/** 连接状态变更回调函数类型 */
export type ConnectionChangeHandler = (connected: boolean) => void


// ============================================================================
// GameSocket 类
// ============================================================================

/**
 * 对局 WebSocket 客户端。
 *
 * 生命周期:
 * 1. new GameSocket(gameId) — 创建实例
 * 2. socket.onMessage(handler) — 注册消息回调
 * 3. socket.connect() — 建立连接，自动开始心跳
 * 4. socket.disconnect() — 主动断开（不会触发重连）
 *
 * 连接断开时自动重连，最多 5 次，使用指数退避算法。
 */
export class GameSocket {
  /** 对局 ID */
  private gameId: string
  /** 原生 WebSocket 实例 */
  private ws: WebSocket | null = null
  /** 消息回调列表 */
  private messageHandlers: MessageHandler[] = []
  /** 连接状态变更回调列表 */
  private connectionHandlers: ConnectionChangeHandler[] = []
  /** 当前重连次数 */
  private reconnectAttempts = 0
  /** 心跳定时器 ID */
  private pingTimer: ReturnType<typeof setInterval> | null = null
  /** 是否已主动调用 disconnect（用于区分主动断开和意外断开） */
  private intentionalClose = false

  constructor(gameId: string) {
    this.gameId = gameId
  }

  // ------------------------------------------------------------------
  // 公共 API
  // ------------------------------------------------------------------

  /** 建立 WebSocket 连接 */
  connect(): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      return  // 已连接，跳过
    }

    this.intentionalClose = false

    // 构建 WebSocket URL——开发环境通过 Vite 代理转发
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/ws/games/${this.gameId}`

    this.ws = new WebSocket(wsUrl)

    this.ws.onopen = () => {
      this.reconnectAttempts = 0  // 连接成功，重置重连计数
      this._startPing()
      this._notifyConnectionChange(true)
    }

    this.ws.onmessage = (event: MessageEvent) => {
      try {
        const message = JSON.parse(event.data) as WsServerMessage
        this._notifyMessage(message)

        // 如果是 pong，不需要额外处理
        if (message.type === 'pong') {
          return
        }
      } catch {
        console.warn('[GameSocket] 无法解析 WebSocket 消息:', event.data)
      }
    }

    this.ws.onclose = () => {
      this._stopPing()
      this._notifyConnectionChange(false)
      this._tryReconnect()
    }

    this.ws.onerror = () => {
      // onerror 后通常会触发 onclose，重连逻辑放在 onclose 中
      console.warn('[GameSocket] WebSocket 连接错误, game_id:', this.gameId)
    }
  }

  /**
   * 主动断开连接。
   *
   * 调用后不会触发自动重连。
   */
  disconnect(): void {
    this.intentionalClose = true
    this._stopPing()
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
  }

  /**
   * 注册消息回调。
   *
   * @param handler 接收到服务端消息时调用的函数
   * @returns 取消注册的函数
   */
  onMessage(handler: MessageHandler): () => void {
    this.messageHandlers.push(handler)
    return () => {
      this.messageHandlers = this.messageHandlers.filter(h => h !== handler)
    }
  }

  /**
   * 注册连接状态变更回调。
   *
   * @param handler 连接状态变化时调用的函数
   * @returns 取消注册的函数
   */
  onConnectionChange(handler: ConnectionChangeHandler): () => void {
    this.connectionHandlers.push(handler)
    return () => {
      this.connectionHandlers = this.connectionHandlers.filter(h => h !== handler)
    }
  }

  /** 获取当前连接状态 */
  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }

  // ------------------------------------------------------------------
  // 内部方法
  // ------------------------------------------------------------------

  /** 通知所有消息回调 */
  private _notifyMessage(message: WsServerMessage): void {
    for (const handler of this.messageHandlers) {
      try {
        handler(message)
      } catch (err) {
        console.error('[GameSocket] 消息回调执行异常:', err)
      }
    }
  }

  /** 通知所有连接状态回调 */
  private _notifyConnectionChange(connected: boolean): void {
    for (const handler of this.connectionHandlers) {
      try {
        handler(connected)
      } catch (err) {
        console.error('[GameSocket] 连接状态回调执行异常:', err)
      }
    }
  }

  /** 启动心跳定时器 */
  private _startPing(): void {
    this._stopPing()
    this.pingTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'ping' }))
      }
    }, PING_INTERVAL_MS)
  }

  /** 停止心跳定时器 */
  private _stopPing(): void {
    if (this.pingTimer !== null) {
      clearInterval(this.pingTimer)
      this.pingTimer = null
    }
  }

  /**
   * 尝试自动重连（指数退避算法）。
   *
   * 重连延迟公式: min(INITIAL_DELAY * 2^(attempt-1), MAX_DELAY)
   * 达到 MAX_RECONNECT_ATTEMPTS 后停止重连。
   */
  private _tryReconnect(): void {
    if (this.intentionalClose) {
      return  // 主动断开，不重连
    }

    if (this.reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      console.warn(
        `[GameSocket] 已达最大重连次数 (${MAX_RECONNECT_ATTEMPTS})，停止重连, game_id:`,
        this.gameId,
      )
      return
    }

    this.reconnectAttempts++
    const delay = Math.min(
      INITIAL_RECONNECT_DELAY_MS * Math.pow(2, this.reconnectAttempts - 1),
      MAX_RECONNECT_DELAY_MS,
    )

    console.info(
      `[GameSocket] 将在 ${delay}ms 后尝试第 ${this.reconnectAttempts} 次重连, game_id:`,
      this.gameId,
    )

    setTimeout(() => {
      if (!this.intentionalClose && !this.isConnected) {
        this.connect()
      }
    }, delay)
  }
}


// ============================================================================
// 工厂函数
// ============================================================================

/**
 * 创建 GameSocket 实例的工厂函数。
 *
 * 每次调用返回新的 GameSocket 实例，同一 gameId 不应创建多个实例。
 *
 * @param gameId 对局 ID
 * @returns 新的 GameSocket 实例
 */
export function createGameSocket(gameId: string): GameSocket {
  return new GameSocket(gameId)
}
