<script setup lang="ts">
/**
 * 游戏主界面 —— 环形玩家座位布局 + 昼夜背景 + 法官播报 + 行动面板。
 *
 * **Why**: 这是狼人杀游戏的核心渲染页面，按设计文档要求实现：
 * - 左盘右台（环形布局）：12 个座位围绕中央
 * - 昼夜背景平滑切换：BackgroundLayer 组件
 * - 法官播报横幅：AnnouncementBanner 组件
 * - AI 聊天气泡：SpeechBubble 组件
 * - 动态控制台：ActionPanel 组件
 * - God View 默认（纯 AI 对局所有身份牌翻开）
 *
 * 参考: [`docs/plan/前端界面设计方案.md`](../../../docs/plan/前端界面设计方案.md)
 */

import { onMounted, onBeforeUnmount, computed } from 'vue'
import { useGameStore } from '../store/game'
import { EventType } from '../types/enums'
import BackgroundLayer from '../components/BackgroundLayer.vue'
import ConnectionIndicator from '../components/ConnectionIndicator.vue'
import PlayerSeat from '../components/PlayerSeat.vue'
import SpeechBubble from '../components/SpeechBubble.vue'
import AnnouncementBanner from '../components/AnnouncementBanner.vue'
import ActionPanel from '../components/ActionPanel.vue'

const props = defineProps<{
  gameId: string
}>()

const emit = defineEmits<{
  (e: 'leave'): void
}>()

const store = useGameStore()

// ============================================================================
// 生命周期
// ============================================================================

onMounted(async () => {
  // 加载对局和玩家数据
  await store.loadGame(props.gameId)
})

onBeforeUnmount(() => {
  store.disconnectWebSocket()
})

// ============================================================================
// 计算属性
// ============================================================================

/** 环形布局参数 */
const ringRadius = 240 // 环形半径 (px)

/** 玩家座位位置 */
function seatPosition(seatNumber: number): { x: number; y: number } {
  const totalSeats = store.playerCount || 9
  const anglePerSeat = (2 * Math.PI) / totalSeats
  // 从顶部开始（-90°），顺时针排列
  const angle = (seatNumber - 1) * anglePerSeat - Math.PI / 2
  return {
    x: Math.cos(angle) * ringRadius,
    y: Math.sin(angle) * ringRadius,
  }
}

/** 最新发言事件的内容和发言人 */
const latestSpeech = computed(() => {
  if (store.events.length === 0) return null
  // 从后往前找最近的 SPEECH_EVENT
  for (let i = store.events.length - 1; i >= 0; i--) {
    const e = store.events[i]
    if (e.event_type === EventType.SPEECH_EVENT && e.content) {
      return { speakerId: e.speaker_id ?? '', content: e.content }
    }
  }
  return null
})

/** 当前阶段中文名 */
const phaseLabel = computed(() => {
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
  return map[store.phase ?? ''] ?? store.phase ?? '未知'
})
</script>

<template>
  <div class="game-board">
    <!-- 昼夜背景 -->
    <BackgroundLayer />

    <!-- 法官播报横幅 -->
    <AnnouncementBanner />

    <!-- 顶部状态栏 -->
    <div class="top-bar">
      <button class="back-btn" @click="emit('leave')">← 返回大厅</button>
      <div class="game-info">
        <span class="phase-badge">{{ phaseLabel }}</span>
        <span class="round-badge">第 {{ store.round }} 轮</span>
      </div>
      <span class="game-id-display">{{ props.gameId }}</span>
    </div>

    <!-- 中央环形座位区 -->
    <div class="ring-container">
      <div class="ring-center">
        <span class="ring-phase">{{ phaseLabel }}</span>
      </div>
      <div
        v-for="player in store.players"
        :key="player.player_id"
        class="seat-wrapper"
        :style="{
          left: `calc(50% + ${seatPosition(player.seat_number).x}px)`,
          top: `calc(50% + ${seatPosition(player.seat_number).y}px)`,
        }"
      >
        <PlayerSeat
          :player="player"
          :is-speaker="player.player_id === store.currentSpeaker"
        />
      </div>
    </div>

    <!-- 发言气泡（最新发言） -->
    <div v-if="latestSpeech" class="speech-area">
      <SpeechBubble
        :speaker-id="latestSpeech.speakerId"
        :content="latestSpeech.content"
      />
    </div>

    <!-- WebSocket 连接指示灯 -->
    <ConnectionIndicator />

    <!-- 动态行动面板 -->
    <ActionPanel />
  </div>
</template>

<style scoped>
.game-board {
  position: relative;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  background: #111;
}

/* 顶部状态栏 */
.top-bar {
  position: fixed;
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

.phase-badge {
  background: rgba(255, 215, 0, 0.15);
  border: 1px solid rgba(255, 215, 0, 0.3);
  border-radius: 6px;
  padding: 4px 12px;
  font-size: 13px;
  color: #ffd700;
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

/* 环形座位区 */
.ring-container {
  position: absolute;
  inset: 80px 0 120px;
  z-index: 10;
}

.ring-center {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 120px;
  height: 120px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.4);
  border: 2px solid rgba(255, 255, 255, 0.1);
  display: flex;
  align-items: center;
  justify-content: center;
}

.ring-phase {
  font-size: 14px;
  color: #ffd700;
  text-align: center;
  line-height: 1.4;
}

.seat-wrapper {
  position: absolute;
  transform: translate(-50%, -50%);
}

/* 发言气泡区域 */
.speech-area {
  position: fixed;
  right: 20px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 50;
  max-width: 380px;
}
</style>
