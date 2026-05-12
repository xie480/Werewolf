/**
 * 对局状态管理 Pinia Store —— 前端单一数据源。
 *
 * **Why**: 集中管理对局全生命周期状态（REST API 响应 + WebSocket 事件流），
 * 组件只读不写。通过 Reducer 模式处理 WebSocket 事件，保证事件溯源的时序一致性。
 *
 * 核心原则:
 * - 前端绝对不写游戏业务逻辑，仅做状态机驱动的事件播放器
 * - 所有 UI 渲染严格依赖后端推送的 GamePhase 和 Event 流
 * - 乐观更新: 操作点击即禁用按钮，等后端确认
 *
 * 参考:
 * - [`docs/plan/前端界面设计方案.md`](../../../docs/plan/前端界面设计方案.md)
 * - [`../api/games.ts`](../api/games.ts)
 * - [`../websocket/GameSocket.ts`](../websocket/GameSocket.ts)
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type {
  GameDetailResponse,
  GameStatusResponse,
  PlayerResponse,
} from '../types/api'
import type { WsServerMessage, EventPushMessage } from '../types/websocket'
import type { PlayerState, GameContext, EventLogEntry } from '../types/game'
import { getRoleImage, ROLE_IMAGE_MAP } from '../types/game'
import { GameStatus, GamePhase, EventType } from '../types/enums'
import * as gamesApi from '../api/games'
import * as playersApi from '../api/players'
import * as eventsApi from '../api/events'
import * as actionsApi from '../api/actions'
import { createGameSocket, GameSocket } from '../websocket/GameSocket'


// ============================================================================
// Store 定义
// ============================================================================

export const useGameStore = defineStore('game', () => {
  // ------------------------------------------------------------------
  // 核心状态
  // ------------------------------------------------------------------

  /** 对局 ID */
  const gameId = ref<string | null>(null)

  /** 对局生命周期状态 (GameStatus 枚举值) */
  const status = ref<string>('')

  /** 当前游戏阶段 (GamePhase 枚举值) */
  const phase = ref<string | null>(null)

  /** 当前轮次 */
  const round = ref<number>(0)

  /** 玩家总数 */
  const playerCount = ref<number>(0)

  /** 玩家列表（按 seat_number 排序） */
  const players = ref<PlayerState[]>([])

  /** 事件日志列表 */
  const events = ref<EventLogEntry[]>([])

  /** 最后接收的事件 seq_num（用于增量拉取和断线恢复） */
  const lastSeqNum = ref<number>(0)

  /** WebSocket 连接状态 */
  const wsConnected = ref<boolean>(false)

  /** WebSocket 客户端实例（仅在进入游戏时创建） */
  let socket: GameSocket | null = null

  /** 当前正在进行的操作（用于乐观更新锁定） */
  const pendingAction = ref<string | null>(null)

  /** 最新一条 SYSTEM_ANNOUNCEMENT 事件的消息文本 */
  const announcement = ref<string | null>(null)

  /** 错误信息 */
  const error = ref<string | null>(null)


  // ------------------------------------------------------------------
  // 计算属性
  // ------------------------------------------------------------------

  /** 游戏是否在进行中 */
  const isRunning = computed(() => status.value === GameStatus.RUNNING)

  /** 游戏是否可以启动（处于 START 状态） */
  const canStart = computed(() => status.value === GameStatus.START)

  /** 是否处于夜晚阶段（用于背景切换） */
  const isNight = computed(() => {
    if (!phase.value) return false
    return phase.value.startsWith('NIGHT_')
  })

  /** 是否处于白天阶段（用于背景切换） */
  const isDay = computed(() => {
    if (!phase.value) return false
    return phase.value.startsWith('DAY_')
  })

  /** 当前发言的玩家 ID（从 SPEECH_EVENT 中提取） */
  const currentSpeaker = computed(() => {
    if (events.value.length === 0) return null
    const lastEvent = events.value[events.value.length - 1]
    if (lastEvent.event_type === EventType.SPEECH_EVENT && lastEvent.speaker_id) {
      return lastEvent.speaker_id
    }
    return null
  })

  /** 游戏上下文快照（用于组件只读访问） */
  const context = computed<GameContext>(() => ({
    game_id: gameId.value ?? '',
    status: status.value,
    phase: phase.value,
    round: round.value,
    player_count: playerCount.value,
    players: players.value,
    event_count: events.value.length,
    last_seq_num: lastSeqNum.value,
    ws_connected: wsConnected.value,
  }))


  // ------------------------------------------------------------------
  // 动作: 对局生命周期
  // ------------------------------------------------------------------

  /** 创建新对局并返回 game_id */
  async function createAndStart(player_count = 9): Promise<string> {
    error.value = null
    try {
      const result = await gamesApi.createGame({ player_count })
      gameId.value = result.game_id
      status.value = result.status
      return result.game_id
    } catch (err) {
      error.value = `创建对局失败: ${(err as Error).message}`
      throw err
    }
  }

  /** 启动对局: START → RUNNING */
  async function startGame(): Promise<void> {
    if (!gameId.value) throw new Error('尚未创建对局')
    error.value = null
    try {
      const result = await gamesApi.startGame(gameId.value)
      status.value = result.status
      phase.value = result.phase ?? null
      round.value = result.round
      await loadPlayers()
      connectWebSocket()
    } catch (err) {
      error.value = `启动对局失败: ${(err as Error).message}`
      throw err
    }
  }

  /** 加载对局详情 */
  async function loadGame(id: string): Promise<void> {
    error.value = null
    try {
      const result = await gamesApi.getGame(id)
      _applyGameDetail(result)
      await loadPlayers()

      // 如果对局已在运行，同时连接 WebSocket
      if (result.status === GameStatus.RUNNING) {
        connectWebSocket()
        // 从后端拉取历史事件
        await loadEvents()
      }
    } catch (err) {
      error.value = `加载对局失败: ${(err as Error).message}`
      throw err
    }
  }

  /** 推进阶段 */
  async function advancePhase(nextPhase?: string): Promise<void> {
    if (!gameId.value) throw new Error('尚未创建对局')
    error.value = null
    try {
      const result = await gamesApi.advancePhase(gameId.value, nextPhase)
      _applyGameStatus(result)
      await loadPlayers()
    } catch (err) {
      error.value = `推进阶段失败: ${(err as Error).message}`
      throw err
    }
  }

  /** 中止对局 */
  async function abortGame(reason?: string): Promise<void> {
    if (!gameId.value) return
    error.value = null
    try {
      const result = await gamesApi.abortGame(gameId.value, reason)
      status.value = result.status
      phase.value = null
      disconnectWebSocket()
    } catch (err) {
      error.value = `中止对局失败: ${(err as Error).message}`
      throw err
    }
  }

  /** 加入已有对局 */
  async function joinGame(id: string): Promise<void> {
    error.value = null
    try {
      const result = await gamesApi.joinGame(id)
      gameId.value = result.game_id
      status.value = result.status
      phase.value = result.phase ?? null
      round.value = result.round
      await loadPlayers()
    } catch (err) {
      error.value = `加入对局失败: ${(err as Error).message}`
      throw err
    }
  }

  // ------------------------------------------------------------------
  // 动作: 数据加载
  // ------------------------------------------------------------------

  /** 加载玩家列表 */
  async function loadPlayers(): Promise<void> {
    if (!gameId.value) return
    const result = await playersApi.getPlayers(gameId.value)
    playerCount.value = result.total
    players.value = result.players.map((p: PlayerResponse): PlayerState => ({
      player_id: p.player_id,
      seat_number: p.seat_number,
      role: p.role,
      is_alive: p.is_alive,
      role_image: getRoleImage(p.role),
      is_speaking: false,
    }))
  }

  /** 增��加载事件（从 lastSeqNum+1 开始） */
  async function loadEvents(): Promise<void> {
    if (!gameId.value) return
    let sinceSeq = lastSeqNum.value
    let hasMore = true

    while (hasMore) {
      const result = await eventsApi.getEvents(gameId.value, sinceSeq, 100)
      for (const e of result.events) {
        const entry = _convertEvent(e)
        if (entry) {
          events.value.push(entry)
        }
        if (e.seq_num > lastSeqNum.value) {
          lastSeqNum.value = e.seq_num
        }
      }
      hasMore = result.has_more
      sinceSeq = lastSeqNum.value
    }
  }


  // ------------------------------------------------------------------
  // 动作: WebSocket
  // ------------------------------------------------------------------

  /** 建立 WebSocket 连接并注册回调 */
  function connectWebSocket(): void {
    if (!gameId.value) return
    if (socket) {
      socket.disconnect()
    }

    socket = createGameSocket(gameId.value)

    socket.onConnectionChange((connected: boolean) => {
      wsConnected.value = connected
    })

    socket.onMessage((msg: WsServerMessage) => {
      handleWsEvent(msg)
    })

    socket.connect()
  }

  /** 断开 WebSocket 连接 */
  function disconnectWebSocket(): void {
    if (socket) {
      socket.disconnect()
      socket = null
    }
    wsConnected.value = false
  }

  /**
   * WebSocket 事件 Reducer —— 根据 event_type 分发处理。
   *
   * **Why**: 这是前端事件溯源的核心。所有 WebSocket 推送的事件
   * 通过此函数处理，保证状态更新的时序一致性。
   */
  function handleWsEvent(msg: WsServerMessage): void {
    if (msg.type !== 'event') return

    const event: EventPushMessage = msg as EventPushMessage

    // 更新最后 seq_num
    if (event.seq_num > lastSeqNum.value) {
      lastSeqNum.value = event.seq_num
    }

    // 转换为 EventLogEntry
    const entry = _convertWsEvent(event)
    if (entry) {
      events.value.push(entry)
    }

    // 根据事件类型分发处理
    switch (event.event_type) {
      case EventType.PHASE_TRANSITION_EVENT:
        if (event.payload.to_phase) {
          phase.value = event.payload.to_phase as string
        }
        break

      case EventType.SPEECH_EVENT:
        // 更新发言玩家标记
        {
          const speakerId = (event.payload.actor_id ?? event.payload.speaker_id) as string | undefined
          for (const p of players.value) {
            p.is_speaking = p.player_id === speakerId
          }
        }
        break

      case EventType.SYSTEM_ANNOUNCEMENT:
        announcement.value = (event.payload.announcement ?? event.payload.content) as string ?? null
        // 3 秒后自动清除
        if (announcement.value) {
          setTimeout(() => { announcement.value = null }, 3_000)
        }
        break

      case EventType.PLAYER_DEATH:
        {
          const deadId = (event.payload.dead_player_id ?? event.payload.player_id) as string | undefined
          if (deadId) {
            const player = players.value.find(p => p.player_id === deadId)
            if (player) {
              player.is_alive = false
            }
          }
        }
        break

      case EventType.GAME_OVER_EVENT:
        status.value = GameStatus.FINISHED
        break

      default:
        break
    }

    // 解除乐观更新锁定（收到确认事件后）
    pendingAction.value = null
  }


  // ------------------------------------------------------------------
  // 动作: 玩家操作（乐观更新模式）
  // ------------------------------------------------------------------

  /** 提交投票（乐观更新） */
  async function submitVote(actorId: string, targetId: string | null): Promise<void> {
    if (!gameId.value) throw new Error('尚未创建对局')
    pendingAction.value = 'vote'
    error.value = null
    try {
      await actionsApi.submitVote(gameId.value, { actor_id: actorId, target_id: targetId })
    } catch (err) {
      pendingAction.value = null
      error.value = `投票提交失败: ${(err as Error).message}`
      throw err
    }
  }

  /** 提交发言（乐观更新） */
  async function submitSpeech(actorId: string, content: string, emotion?: string): Promise<void> {
    if (!gameId.value) throw new Error('尚未创建对局')
    pendingAction.value = 'speak'
    error.value = null
    try {
      await actionsApi.submitSpeech(gameId.value, { actor_id: actorId, content, emotion })
    } catch (err) {
      pendingAction.value = null
      error.value = `发言提交失败: ${(err as Error).message}`
      throw err
    }
  }

  /** 提交夜间技能（乐观更新） */
  async function submitAction(actorId: string, actionType: string, targetId?: string): Promise<void> {
    if (!gameId.value) throw new Error('尚未创建对局')
    pendingAction.value = actionType
    error.value = null
    try {
      await actionsApi.submitAction(gameId.value, { actor_id: actorId, action_type: actionType, target_id: targetId })
    } catch (err) {
      pendingAction.value = null
      error.value = `技能提交失败: ${(err as Error).message}`
      throw err
    }
  }


  // ------------------------------------------------------------------
  // 内部工具函数
  // ------------------------------------------------------------------

  /** 应用 GameDetailResponse 到状态 */
  function _applyGameDetail(result: GameDetailResponse): void {
    gameId.value = result.game_id
    status.value = result.status
    phase.value = result.phase ?? null
    round.value = result.round
    playerCount.value = result.player_count
  }

  /** 应用 GameStatusResponse 到状态 */
  function _applyGameStatus(result: GameStatusResponse): void {
    status.value = result.status
    phase.value = result.phase ?? null
    round.value = result.round
  }

  /** 将 API EventResponse 转换为 EventLogEntry */
  function _convertEvent(e: {
    event_id: string
    seq_num: number
    event_type: string
    timestamp: string
    payload: Record<string, unknown>
  }): EventLogEntry | null {
    const base: EventLogEntry = {
      seq_num: e.seq_num,
      event_type: e.event_type,
      timestamp: e.timestamp,
    }

    switch (e.event_type) {
      case EventType.SPEECH_EVENT:
        base.speaker_id = e.payload.actor_id as string
        base.content = e.payload.content as string
        break
      case EventType.SYSTEM_ANNOUNCEMENT:
        base.announcement = (e.payload.announcement ?? e.payload.content) as string
        break
      case EventType.PLAYER_DEATH:
        base.dead_player_id = (e.payload.dead_player_id ?? e.payload.player_id) as string
        break
      case EventType.PHASE_TRANSITION_EVENT:
        base.from_phase = e.payload.from_phase as string
        base.to_phase = e.payload.to_phase as string
        break
    }

    return base
  }

  /** 将 WebSocket EventPushMessage 转换为 EventLogEntry */
  function _convertWsEvent(event: EventPushMessage): EventLogEntry | null {
    const base: EventLogEntry = {
      seq_num: event.seq_num,
      event_type: event.event_type,
      timestamp: event.timestamp,
    }

    switch (event.event_type) {
      case EventType.SPEECH_EVENT:
        base.speaker_id = (event.payload.actor_id ?? event.payload.speaker_id) as string
        base.content = event.payload.content as string
        break
      case EventType.SYSTEM_ANNOUNCEMENT:
        base.announcement = (event.payload.announcement ?? event.payload.content) as string
        break
      case EventType.PLAYER_DEATH:
        base.dead_player_id = (event.payload.dead_player_id ?? event.payload.player_id) as string
        break
      case EventType.PHASE_TRANSITION_EVENT:
        base.from_phase = event.payload.from_phase as string
        base.to_phase = event.payload.to_phase as string
        break
    }

    return base
  }


  // ============================================================================
  // 导出
  // ============================================================================

  return {
    // 状态
    gameId,
    status,
    phase,
    round,
    playerCount,
    players,
    events,
    lastSeqNum,
    wsConnected,
    pendingAction,
    announcement,
    error,
    // 计算属性
    isRunning,
    canStart,
    isNight,
    isDay,
    currentSpeaker,
    context,
    // 动作
    createAndStart,
    startGame,
    loadGame,
    advancePhase,
    abortGame,
    joinGame,
    loadPlayers,
    loadEvents,
    connectWebSocket,
    disconnectWebSocket,
    handleWsEvent,
    submitVote,
    submitSpeech,
    submitAction,
  }
})
