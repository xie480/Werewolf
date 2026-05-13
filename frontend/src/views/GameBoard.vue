<script setup lang="ts">
/**
 * 游戏主界面 —— 左右两侧玩家座位布局 + 顶部法官 + 底部控制面板。
 *
 * **Why**: 根据 UI 需求，将原环形布局改为左右两列布局，法官信息放置在顶部
 * - 左侧 5 人（座位 1~5 按顺序从上到下），右侧 4 人（座位 9~6 逆序从上到下）
 * - 顶部法官区展示 AnnouncementBanner 与当前阶段
 * - 底部控制区展示 ConnectionIndicator 与 ActionPanel
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

/** 左侧玩家（U 形上半部分） */
const leftPlayers = computed(() => {
  const half = Math.ceil((store.playerCount || 9) / 2)
  return store.players
    .filter(p => p.seat_number <= half)
    .sort((a, b) => a.seat_number - b.seat_number)
})

/** 右侧玩家（U 形下半部分，逆序） */
const rightPlayers = computed(() => {
  const half = Math.ceil((store.playerCount || 9) / 2)
  return store.players
    .filter(p => p.seat_number > half)
    .sort((a, b) => b.seat_number - a.seat_number)
})

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
    <!-- 背景层 -->
    <BackgroundLayer />

    <!-- 顶部状态栏 -->
    <div class="top-bar">
      <button class="back-btn" @click="emit('leave')">← 返回大厅</button>
      <div class="game-info">
        <span class="phase-badge">{{ phaseLabel }}</span>
        <span class="round-badge">第 {{ store.round }} 轮</span>
      </div>
      <span class="game-id-display">{{ props.gameId }}</span>
    </div>

    <!-- 顶部法官区 -->
    <div class="judge-area">
      <AnnouncementBanner />
      <div class="judge-phase-indicator">
        <span class="ring-phase">{{ phaseLabel }}</span>
      </div>
    </div>

    <!-- 主战场：左右两侧座位 + 中央发言区 -->
    <div class="main-battlefield">
      <!-- 左侧玩家 -->
      <div class="side-column left-side">
        <div v-for="player in leftPlayers" :key="player.player_id" class="seat-wrapper">
          <PlayerSeat :player="player" :is-speaker="player.player_id === store.currentSpeaker" position="left" />
        </div>
      </div>

      <!-- 中央发言区域 -->
      <div class="center-area">
        <div v-if="latestSpeech" class="speech-area">
          <SpeechBubble :speaker-id="latestSpeech.speakerId" :content="latestSpeech.content" />
        </div>
      </div>

      <!-- 右侧玩家 -->
      <div class="side-column right-side">
        <div v-for="player in rightPlayers" :key="player.player_id" class="seat-wrapper">
          <PlayerSeat :player="player" :is-speaker="player.player_id === store.currentSpeaker" position="right" />
        </div>
      </div>
    </div>

    <!-- 底部控制区 -->
    <div class="bottom-control-area">
      <ConnectionIndicator />
      <ActionPanel />
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

/* 顶部法官区 */
.judge-area {
  margin-top: 60px; /* 与 top-bar 留出间距 */
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
  padding: 80px 40px 120px 40px; /* 顶部留出 TopBar 空间(80px)，底部留出 ActionPanel 空间(120px) */
  overflow: hidden;
  z-index: 10;
}

/* 左右玩家列 */
.side-column {
  display: flex;
  flex-direction: column;
  justify-content: center;
  width: 200px; /* 关键修改：加宽以容纳横向的卡牌和文字 */
  height: 100%;
  gap: 15px; 
}


/* 座位容器 */
.seat-wrapper {
  /* 核心魔法：根据屏幕高度动态计算最大高度，按最多 5 张卡来算 */
  /* 100vh 减去顶部(80px)、底部(120px)和间距(约40px)，除以 5 */
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

/* 底部控制区 */
.bottom-control-area {
  position: relative;
  z-index: 50;
  padding-bottom: 20px;
  display: flex;
  flex-direction: column;
  align-items: center;
  background: linear-gradient(to top, rgba(0,0,0,0.8) 0%, transparent 100%);
}
</style>
