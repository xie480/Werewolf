<script setup lang="ts">
/**
 * 回放对局看板 —— 复用 GameBoard 的布局，但数据源来自 ReplayStore。
 */
import { computed } from 'vue'
import { useReplayStore } from '../store/replay'
import BackgroundLayer from '../components/BackgroundLayer.vue'
import PlayerSeat from '../components/PlayerSeat.vue'
import SpeechBubble from '../components/SpeechBubble.vue'
import InnerOSPanel from '../components/InnerOSPanel.vue'

const props = defineProps<{
  gameId: string
}>()

const emit = defineEmits<{
  (e: 'leave'): void
}>()

const store = useReplayStore()

// ============================================================================
// 计算属性
// ============================================================================

const players = computed(() => Object.values(store.currentGameState.players))

/** 左侧玩家（U 形上半部分） */
const leftPlayers = computed(() => {
  const half = Math.ceil(players.value.length / 2)
  return players.value
    .filter(p => p.seat_number <= half)
    .sort((a, b) => a.seat_number - b.seat_number)
})

/** 右侧玩家（U 形下半部分，逆序） */
const rightPlayers = computed(() => {
  const half = Math.ceil(players.value.length / 2)
  return players.value
    .filter(p => p.seat_number > half)
    .sort((a, b) => b.seat_number - a.seat_number)
})

/** 最新发言事件的内容和发言人 */
const latestSpeech = computed(() => {
  const history = store.currentGameState.chatHistory
  if (history.length === 0) return null
  
  // 从后往前找最近的 SPEECH_EVENT
  for (let i = history.length - 1; i >= 0; i--) {
    const e = history[i]
    if (e.event_type === 'SPEECH_EVENT' && e.content) {
      const speakerId = e.speaker_id ?? ''
      return { speakerId, speakerName: getPlayerName(speakerId), content: e.content }
    }
  }
  return null
})

/** 当前阶段中文名（法官区使用） */
const phaseLabel = computed(() => {
  const phase = store.currentGameState.currentPhase
  const map: Record<string, string> = {
    INIT: '初始化',
    NIGHT_START: '黑夜降临',
    NIGHT_WOLF_ACT: '狼人行动',
    NIGHT_WITCH_ACT: '女巫行动',
    NIGHT_SEER_ACT: '预言家行动',
    NIGHT_RESOLVE: '夜间结算',
    DAY_START: '天亮',
    DAY_DISCUSSION: '白天讨论',
    DAY_VOTE: '投票阶段',
    VOTE_RESOLVE: '投票结算',
    HUNTER_SHOOT: '猎人开枪',
    LAST_WORDS: '遗言',
    GAME_OVER: '游戏结束',
    DAY_PK_DISCUSSION: 'PK 拉票',
    DAY_PK_VOTE: 'PK 投票',
  }
  return map[phase ?? ''] ?? phase ?? '未知'
})

const currentSpeakerId = computed(() => {
  const speaker = players.value.find(p => p.is_speaking)
  return speaker ? speaker.player_id : null
})

/** 根据 player_id 获取玩家名称 */
function getPlayerName(playerId: string): string {
  const player = players.value.find(p => p.player_id === playerId)
  return player?.name ?? playerId
}

/** 根据 player_id 获取目标玩家的座位号，用于 Badge 渲染 */
function getTargetSeat(targetId?: string | null): number | 'PASS' | null {
  if (!targetId) return null
  if (targetId === 'PASS') return 'PASS'
  const targetPlayer = players.value.find(p => p.player_id === targetId)
  return targetPlayer ? targetPlayer.seat_number : null
}

/** 当前发言对应的内心 OS（从 ReplayStore 的 innerThoughts 中提取） */
const currentInnerThought = computed(() => {
  return store.currentGameState.currentInnerThought?.innerThought ?? null
})
</script>

<template>
  <div class="game-board">
    <!-- 背景层 -->
    <BackgroundLayer />

    <!-- 顶部状态栏 -->
    <div class="top-bar">
      <button class="back-btn" @click="emit('leave')">← 返回大厅</button>
      <div class="game-info">
        <span class="status-badge">回放模式 ({{ store.perspective }})</span>
        <span class="round-badge">第 {{ store.currentGameState.currentDay }} 轮</span>
      </div>
      <span class="game-id-display">{{ props.gameId }}</span>
    </div>

    <!-- 顶部法官区 -->
    <div class="judge-area">
      <div class="judge-phase-indicator">
        <span class="ring-phase">{{ phaseLabel }}</span>
      </div>
    </div>

    <!-- 主战场：左右两侧座位 + 中央发言区 -->
    <div class="main-battlefield">
      <!-- 左侧玩家 -->
      <div class="side-column left-side">
        <div v-for="player in leftPlayers" :key="player.player_id" class="seat-wrapper" style="position: relative;">
          <PlayerSeat
            :player="player"
            :is-speaker="player.player_id === currentSpeakerId"
            position="left"
            :target-seat="getTargetSeat(player.action_target)"
          />
          <InnerOSPanel
            v-if="store.currentGameState.currentInnerThought?.speakerId === player.player_id && !player.is_speaking"
            :speaker-id="player.player_id"
            :speaker-name="player.name"
            :inner-thought="currentInnerThought"
            variant="seat-left"
          />
        </div>
      </div>

      <!-- 中央发言区域 -->
      <div class="center-area">
        <div v-if="latestSpeech" class="speech-area" style="position: relative;">
          <SpeechBubble :speaker-id="latestSpeech.speakerId" :speaker-name="latestSpeech.speakerName" :content="latestSpeech.content" />
          <InnerOSPanel
            v-if="store.currentGameState.currentInnerThought?.speakerId === latestSpeech.speakerId && currentSpeakerId === latestSpeech.speakerId"
            :speaker-id="latestSpeech.speakerId"
            :speaker-name="latestSpeech.speakerName"
            :speech-content="latestSpeech.content"
            :inner-thought="currentInnerThought"
            variant="speech"
          />
        </div>
      </div>

      <!-- 右侧玩家 -->
      <div class="side-column right-side">
        <div v-for="player in rightPlayers" :key="player.player_id" class="seat-wrapper" style="position: relative;">
          <PlayerSeat
            :player="player"
            :is-speaker="player.player_id === currentSpeakerId"
            position="right"
            :target-seat="getTargetSeat(player.action_target)"
          />
          <InnerOSPanel
            v-if="store.currentGameState.currentInnerThought?.speakerId === player.player_id && !player.is_speaking"
            :speaker-id="player.player_id"
            :speaker-name="player.name"
            :inner-thought="currentInnerThought"
            variant="seat-right"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.game-board {
  position: relative;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  background: #111;
  display: flex;
  flex-direction: column;
}

/* 顶部状态栏 */
.top-bar {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 20px;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}

.back-btn {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 8px;
  color: #ccc;
  padding: 6px 16px;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.2s;
}
.back-btn:hover {
  background: rgba(255, 255, 255, 0.15);
}

.game-info {
  display: flex;
  gap: 10px;
}
.status-badge {
  background: rgba(147, 51, 234, 0.15);
  border: 1px solid rgba(147, 51, 234, 0.3);
  border-radius: 6px;
  padding: 4px 12px;
  font-size: 13px;
  color: #d8b4fe;
}
.round-badge {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px;
  padding: 4px 12px;
  font-size: 13px;
  color: #aaa;
}
.game-id-display {
  font-family: monospace;
  font-size: 12px;
  color: #666;
}

/* 顶部法官区 */
.judge-area {
  margin-top: 60px;
  display: flex;
  flex-direction: column;
  align-items: center;
  z-index: 10;
}
.judge-phase-indicator {
  margin-top: 10px;
  padding: 8px 32px;
  background: rgba(0, 0, 0, 0.5);
  border: 2px solid rgba(255, 215, 0, 0.3);
  border-radius: 24px;
  box-shadow: 0 0 15px rgba(255, 215, 0, 0.1);
  backdrop-filter: blur(4px);
}
.ring-phase {
  font-size: 16px;
  font-weight: bold;
  color: #ffd700;
  letter-spacing: 2px;
}

/* 主战场布局 */
.main-battlefield {
  flex: 1;
  display: flex;
  justify-content: space-between;
  padding: 80px 40px 120px 40px;
  overflow: hidden;
  z-index: 10;
}

/* 左右玩家列 */
.side-column {
  display: flex;
  flex-direction: column;
  justify-content: center;
  width: 200px;
  height: 100%;
  gap: 15px; 
}

/* 座位容器 */
.seat-wrapper {
  height: min(160px, calc((100vh - 240px) / 5)); 
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
}

/* 中央发言区域 */
.center-area {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 40px;
  position: relative;
}
.speech-area {
  width: 100%;
  max-width: 450px;
  margin-bottom: 10vh;
}
</style>
