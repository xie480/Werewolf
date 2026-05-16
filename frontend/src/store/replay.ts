import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getGameReplay } from '../api/replay'
import type { ReplayResponse, ReplayDayChunk, EventResponse } from '../types/api'
import { getRoleImage, type PlayerState, type EventLogEntry } from '../types/game'

export interface ReplayGameState {
  players: Record<string, PlayerState>
  currentDay: number
  currentPhase: string | null
  chatHistory: EventLogEntry[]
  innerThoughts: Record<number, string> // seq_num -> inner_thought
}

export const useReplayStore = defineStore('replay', () => {
  // ============================================================================
  // State
  // ============================================================================
  const gameId = ref<string>('')
  const perspective = ref<'GOD' | 'POV'>('GOD')
  const agentId = ref<string | undefined>(undefined)
  
  const replayData = ref<ReplayResponse | null>(null)
  
  const currentSeqId = ref<number>(0)
  const isPlaying = ref<boolean>(false)
  const playbackSpeed = ref<number>(1) // 1x, 2x, 4x
  
  let playTimer: number | null = null

  // ============================================================================
  // Getters
  // ============================================================================
  
  /** 扁平化的所有事件列表，按 seq_num 排序 */
  const allEvents = computed<EventResponse[]>(() => {
    if (!replayData.value) return []
    const events: EventResponse[] = []
    for (const day of replayData.value.timeline) {
      for (const phase of day.phases) {
        events.push(...phase.events)
      }
    }
    return events.sort((a, b) => a.seq_num - b.seq_num)
  })

  /** 最大 seq_num */
  const maxSeqId = computed<number>(() => {
    const events = allEvents.value
    return events.length > 0 ? events[events.length - 1].seq_num : 0
  })

  /** 
   * 核心 Reducer：根据 initial_state 和 allEvents 计算 currentSeqId 时的游戏状态 
   */
  const currentGameState = computed<ReplayGameState>(() => {
    const state: ReplayGameState = {
      players: {},
      currentDay: 0,
      currentPhase: null,
      chatHistory: [],
      innerThoughts: {}
    }

    if (!replayData.value) return state

    // 初始化玩家状态
    for (const p of replayData.value.initial_state.players) {
      state.players[p.agent_id] = {
        player_id: p.agent_id,
        seat_number: p.seat_number,
        role: p.role,
        is_alive: true,
        name: p.name || `玩家 ${p.seat_number}`,
        role_image: getRoleImage(p.role),
        is_speaking: false,
        last_speech: undefined
      }
    }

    // 遍历事件应用状态变更
    for (const event of allEvents.value) {
      if (event.seq_num > currentSeqId.value) break

      // 提取 inner_thought
      if (event.payload && typeof event.payload.inner_thought === 'string') {
        state.innerThoughts[event.seq_num] = event.payload.inner_thought
      }

      switch (event.event_type) {
        case 'PHASE_TRANSITION_EVENT':
          state.currentDay = (event.payload.round as number) || state.currentDay
          state.currentPhase = (event.payload.new_phase as string) || state.currentPhase
          // 阶段切换时清除所有人的发言状态
          Object.values(state.players).forEach(p => {
            p.is_speaking = false
            p.last_speech = undefined
          })
          break
        case 'PLAYER_DEATH_EVENT':
        case 'VOTED_OUT_EVENT':
          const deadId = event.payload.player_id as string || event.payload.target_id as string
          if (deadId && state.players[deadId]) {
            state.players[deadId].is_alive = false
          }
          break
        case 'SPEECH_EVENT':
          const speakerId = event.payload.actor_id as string
          const content = event.payload.content as string
          if (speakerId && state.players[speakerId]) {
            // 清除其他人的发言状态
            Object.values(state.players).forEach(p => p.is_speaking = false)
            state.players[speakerId].is_speaking = true
            state.players[speakerId].last_speech = content
          }
          state.chatHistory.push({
            seq_num: event.seq_num,
            event_type: event.event_type,
            timestamp: event.timestamp,
            speaker_id: speakerId,
            content: content
          })
          break
        case 'SYSTEM_ANNOUNCEMENT_EVENT':
          state.chatHistory.push({
            seq_num: event.seq_num,
            event_type: event.event_type,
            timestamp: event.timestamp,
            announcement: event.payload.message as string
          })
          break
      }
    }

    return state
  })

  // ============================================================================
  // Actions
  // ============================================================================

  async function fetchReplayData(id: string, mode: 'GOD' | 'POV' = 'GOD', povAgentId?: string) {
    gameId.value = id
    perspective.value = mode
    agentId.value = povAgentId
    currentSeqId.value = 0
    pause()
    
    try {
      replayData.value = await getGameReplay(id, mode, povAgentId)
    } catch (error) {
      console.error('Failed to fetch replay data:', error)
      throw error
    }
  }

  function play() {
    if (isPlaying.value) return
    if (currentSeqId.value >= maxSeqId.value) {
      currentSeqId.value = 0 // 如果到底了，重头开始
    }
    
    isPlaying.value = true
    scheduleNextStep()
  }

  function pause() {
    isPlaying.value = false
    if (playTimer !== null) {
      clearTimeout(playTimer)
      playTimer = null
    }
  }

  function togglePlay() {
    if (isPlaying.value) pause()
    else play()
  }

  function seek(seqId: number) {
    currentSeqId.value = Math.max(0, Math.min(seqId, maxSeqId.value))
  }

  function setSpeed(speed: number) {
    playbackSpeed.value = speed
    if (isPlaying.value) {
      pause()
      play()
    }
  }

  function scheduleNextStep() {
    if (!isPlaying.value) return

    // 基础间隔 1000ms，根据倍速调整
    const interval = 1000 / playbackSpeed.value

    playTimer = window.setTimeout(() => {
      stepForward()
      if (isPlaying.value) {
        scheduleNextStep()
      }
    }, interval)
  }

  function stepForward() {
    const events = allEvents.value
    if (events.length === 0) return

    // 找到下一个 seq_num
    const nextEvent = events.find(e => e.seq_num > currentSeqId.value)
    if (nextEvent) {
      currentSeqId.value = nextEvent.seq_num
    } else {
      // 播放结束
      pause()
    }
  }

  return {
    gameId,
    perspective,
    agentId,
    replayData,
    currentSeqId,
    isPlaying,
    playbackSpeed,
    allEvents,
    maxSeqId,
    currentGameState,
    fetchReplayData,
    play,
    pause,
    togglePlay,
    seek,
    setSpeed,
    stepForward
  }
})
