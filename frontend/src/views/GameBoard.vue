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
import { GameStatus, GamePhase, EventType } from '../types/enums'
import BackgroundLayer from '../components/BackgroundLayer.vue'
import ConnectionIndicator from '../components/ConnectionIndicator.vue'
import PlayerSeat from '../components/PlayerSeat.vue'
import SpeechBubble from '../components/SpeechBubble.vue'
import AnnouncementBanner from '../components/AnnouncementBanner.vue'
import ActionPanel from '../components/ActionPanel.vue'
import InnerOSPanel from '../components/InnerOSPanel.vue'

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
      const speakerId = e.speaker_id ?? ''
      const speaker = store.players.find(p => p.player_id === speakerId)
      return {
        speakerId,
        speakerName: speaker?.name ?? speakerId,
        content: e.content,
      }
    }
  }
  return null
})

/** 当前阶段中文名（法官区使用） */
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

/** 全局生命周期状态中文名（顶栏使用） */
const statusLabel = computed(() => {
  const map: Record<string, string> = {
    INIT: '初始化',
    START: '等待开始',
    RUNNING: '进行中',
    SETTLING: '结算中',
    FINISHED: '已结束',
    ABORTED: '已中止',
  }
  return map[store.status ?? ''] ?? store.status ?? '未知'
})

/** 根据 player_id 获取目标玩家的座位号，用于 Badge 渲染 */
function getTargetSeat(targetId?: string | null): number | 'PASS' | null {
  if (!targetId) return null
  if (targetId === 'PASS') return 'PASS'
  const targetPlayer = store.players.find(p => p.player_id === targetId)
  return targetPlayer ? targetPlayer.seat_number : null
}

/** 投票汇总列表（用于 VOTE_RESOLVE 阶段展示投票结果面板） */
const voteSummary = computed(() => {
  return store.players
    .filter(p => p.action_type === 'VOTE' && p.action_target)
    .map(p => {
      const targetPlayer = store.players.find(t => t.player_id === p.action_target)
      return {
        voterId: p.player_id,
        voterSeat: p.seat_number,
        voterName: p.name,
        targetSeat: targetPlayer?.seat_number,
        targetName: targetPlayer?.name,
        isPass: p.action_target === 'PASS',
      }
    })
    .sort((a, b) => a.voterSeat - b.voterSeat)
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
        <span class="status-badge">{{ statusLabel }}</span>
        <span class="round-badge">第 {{ store.round }} 轮</span>
      </div>
      <span class="game-id-display">{{ props.gameId }}</span>
    </div>

    <!-- 顶部法官区 -->
    <div class="judge-area">
      <AnnouncementBanner />
      <div class="judge-phase-indicator">
        <span class="ring-phase">{{ phaseLabel }}</span>
        <span v-if="store.phaseCountdown > 0" class="countdown-badge">{{ store.phaseCountdown }}s</span>
      </div>
    </div>

    <!-- 主战场：左右两侧座位 + 中央发言/投票结果区 -->
    <div class="main-battlefield">
      <!-- 左侧玩家 -->
      <div class="side-column left-side">
        <div v-for="player in leftPlayers" :key="player.player_id" class="seat-wrapper">
          <PlayerSeat
            :player="player"
            :is-speaker="player.player_id === store.currentSpeaker"
            position="left"
            :target-seat="getTargetSeat(player.action_target)"
          />
        </div>
      </div>

      <!-- 中央区域内联条件渲染 -->
      <div class="center-area">
        <!-- 投票结果公示面板（VOTE_RESOLVE 阶段展示） -->
        <div v-if="store.phase === GamePhase.VOTE_RESOLVE" class="vote-summary-panel">
          <div class="vote-panel-header">📊 投票结果公示</div>
          <div v-if="voteSummary.length === 0" class="no-votes">暂无投票记录</div>
          <div class="vote-list">
            <div v-for="vote in voteSummary" :key="vote.voterId" class="vote-row">
              <span class="voter-label">座位 {{ vote.voterSeat }} ({{ vote.voterName }})</span>
              <span class="vote-arrow">👉</span>
              <span v-if="vote.isPass" class="target-pass-label">弃权</span>
              <span v-else class="target-label">座位 {{ vote.targetSeat }} ({{ vote.targetName }})</span>
            </div>
          </div>
        </div>

        <!-- 发言气泡（非 VOTE_RESOLVE 阶段显示） -->
        <div v-else-if="latestSpeech" class="speech-area">
          <SpeechBubble
            :speaker-id="latestSpeech.speakerId"
            :speaker-name="latestSpeech.speakerName"
            :content="latestSpeech.content"
          />
        </div>
      </div>

      <!-- 右侧玩家 -->
      <div class="side-column right-side">
        <div v-for="player in rightPlayers" :key="player.player_id" class="seat-wrapper">
          <PlayerSeat
            :player="player"
            :is-speaker="player.player_id === store.currentSpeaker"
            position="right"
            :target-seat="getTargetSeat(player.action_target)"
          />
        </div>
      </div>
    </div>

    <!-- 底部控制区 -->
    <div class="bottom-control-area">
      <ConnectionIndicator />
      <ActionPanel />
    </div>

    <!-- 内心OS面板（纯人机对局 GOD 视角下展示） -->
    <InnerOSPanel
      :speaker-id="store.currentSpeaker"
      :speech-content="store.currentSpeechContent"
      :inner-thought="store.currentInnerThought?.innerThought ?? null"
    />
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

.countdown-badge {
  margin-left: 12px;
  padding: 2px 12px;
  background: rgba(255, 215, 0, 0.2);
  border: 1px solid rgba(255, 215, 0, 0.4);
  border-radius: 12px;
  font-size: 14px;
  font-weight: 700;
  color: #ffd700;
  min-width: 36px;
  text-align: center;
  display: inline-block;
  animation: countdown-pulse 1s ease-in-out infinite;
}

@keyframes countdown-pulse {
  0%, 100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.8;
    transform: scale(1.05);
  }
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

/* 投票结果公示面板 */
.vote-summary-panel {
  background: rgba(20, 20, 40, 0.95);
  border: 1px solid rgba(255, 215, 0, 0.3);
  border-radius: 12px;
  padding: 20px 32px;
  min-width: 320px;
  max-width: 480px;
  backdrop-filter: blur(12px);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6);
  animation: vote-fade-in 0.4s ease-out;
}

.vote-panel-header {
  color: #ffd700;
  font-size: 18px;
  font-weight: bold;
  text-align: center;
  margin-bottom: 16px;
  border-bottom: 1px solid rgba(255, 215, 0, 0.2);
  padding-bottom: 12px;
  letter-spacing: 2px;
}

.vote-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 40vh;
  overflow-y: auto;
}

.vote-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: rgba(255, 255, 255, 0.03);
  border-radius: 6px;
  font-size: 14px;
  color: #e0e0e0;
}

.vote-arrow {
  margin: 0 16px;
  opacity: 0.6;
}

.target-label {
  color: #ff5252;
  font-weight: 600;
}

.target-pass-label {
  color: #888;
  font-style: italic;
  font-weight: normal;
}

.no-votes {
  text-align: center;
  color: #888;
  font-style: italic;
  padding: 20px 0;
}

@keyframes vote-fade-in {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
