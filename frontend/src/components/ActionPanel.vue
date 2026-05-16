<script setup lang="ts">
/**
 * 动态行动面板 —— 根据当前阶段和玩家角色动态渲染操作按钮。
 *
 * **Why**: 设计中要求前端动态控制台根据后端推送的 GamePhase
 * 和玩家角色渲染对应的操作面板（投票/发言/技能按钮）。
 * 使用乐观更新机制：点击即禁用按钮，等后端确认。
 */

import { ref, computed } from 'vue'
import { useGameStore } from '../store/game'
import { GamePhase, ActionType } from '../types/enums'

const store = useGameStore()

// ============================================================================
// 本地状态
// ============================================================================

/** 发言输入内容 */
const speechContent = ref('')
/** 投票目标（null 表示弃权） */
const voteTarget = ref<string | null>(null)
/** 技能目标 */
const actionTarget = ref<string>('')
/** 当前选中的技能类型 */
const selectedAction = ref<string>(ActionType.PASS)
/** 是否正在提交 */
const isSubmitting = ref(false)

/** 根据 player_id 获取玩家名称 */
function getPlayerName(playerId: string): string {
  const player = store.players.find(p => p.player_id === playerId)
  return player?.name ?? playerId
}

// ============================================================================
// 计算属性：决定面板渲染内容
// ============================================================================

/** 当前阶段是否允许发言 */
const showSpeechPanel = computed(() => {
  const phase = store.phase
  return (
    phase === GamePhase.DAY_DISCUSSION ||
    phase === GamePhase.DAY_PK_DISCUSSION ||
    phase === GamePhase.LAST_WORDS
  )
})

/** 当前阶段是否允许投票 */
const showVotePanel = computed(() => {
  const phase = store.phase
  return phase === GamePhase.DAY_VOTE || phase === GamePhase.DAY_PK_VOTE
})

/** 当前阶段是否允许夜间技能 */
const showActionPanel = computed(() => {
  const phase = store.phase
  return (
    phase === GamePhase.NIGHT_WOLF_ACT ||
    phase === GamePhase.NIGHT_WITCH_ACT ||
    phase === GamePhase.NIGHT_SEER_ACT ||
    phase === GamePhase.HUNTER_SHOOT
  )
})

/** 是否显示初始化/房主控制面板（INIT 阶段） */
const showInitPanel = computed(() => {
  return store.phase === 'INIT'
})

/** 是否处于无操作的等待状态（没有任何面板需要显示） */
const isWaiting = computed(() => {
  return !showSpeechPanel.value && !showVotePanel.value && !showActionPanel.value && !showInitPanel.value
})

/** 当前阶段可用的技能列表 */
const availableActions = computed(() => {
  const phase = store.phase
  switch (phase) {
    case GamePhase.NIGHT_WOLF_ACT:
      return [ActionType.WOLF_KILL, ActionType.PASS]
    case GamePhase.NIGHT_WITCH_ACT:
      return [ActionType.WITCH_SAVE, ActionType.WITCH_POISON, ActionType.PASS]
    case GamePhase.NIGHT_SEER_ACT:
      return [ActionType.SEER_CHECK, ActionType.PASS]
    case GamePhase.HUNTER_SHOOT:
      return [ActionType.HUNTER_SHOOT, ActionType.PASS]
    default:
      return [ActionType.PASS]
  }
})

/** 可选投票目标列表（存活玩家） */
const alivePlayers = computed(() =>
  store.players.filter(p => p.is_alive)
)

/** 操作按钮是否禁用（乐观更新锁定中） */
const isLocked = computed(() => !!store.pendingAction || isSubmitting.value)

// ============================================================================
// 提交方法
// ============================================================================

/** 提交发言 */
async function handleSpeak(): Promise<void> {
  if (!speechContent.value.trim() || isLocked.value) return
  isSubmitting.value = true
  try {
    await store.submitSpeech('player_1', speechContent.value.trim())
    speechContent.value = ''
  } catch {
    // 错误已在 store.error 中
  } finally {
    isSubmitting.value = false
  }
}

/** 提交投票 */
async function handleVote(targetId: string | null): Promise<void> {
  if (isLocked.value) return
  voteTarget.value = targetId
  isSubmitting.value = true
  try {
    await store.submitVote('player_1', targetId)
  } catch {
    // 错误已在 store.error 中
  } finally {
    isSubmitting.value = false
  }
}

/** 提交技能 */
async function handleAction(): Promise<void> {
  if (isLocked.value) return
  if (selectedAction.value !== ActionType.PASS && !actionTarget.value) {
    return
  }
  isSubmitting.value = true
  try {
    await store.submitAction(
      'player_1',
      selectedAction.value,
      selectedAction.value !== ActionType.PASS ? actionTarget.value : undefined,
    )
    actionTarget.value = ''
  } catch {
    // 错误已在 store.error 中
  } finally {
    isSubmitting.value = false
  }
}

/** 获取技能中文名 */
function actionLabel(type: string): string {
  const map: Record<string, string> = {
    [ActionType.WOLF_KILL]: '刀人',
    [ActionType.WITCH_SAVE]: '使用解药',
    [ActionType.WITCH_POISON]: '使用毒药',
    [ActionType.SEER_CHECK]: '查验身份',
    [ActionType.HUNTER_SHOOT]: '开枪',
    [ActionType.PASS]: '空过',
  }
  return map[type] ?? type
}

/** 开始游戏（INIT 阶段由房主触发） */
async function handleStartGame(): Promise<void> {
  if (isLocked.value) return
  isSubmitting.value = true
  try {
    await store.startGame()
  } catch {
    // 错误已在 store.error 中
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <div class="action-panel">
    <!-- 错误提示 -->
    <div v-if="store.error" class="panel-error">
      {{ store.error }}
      <button class="dismiss-btn" @click="store.error = null">✕</button>
    </div>

    <!-- 初始化/开始游戏面板（INIT 阶段，房主控制台） -->
    <div v-if="showInitPanel" class="panel-section" style="align-items: center;">
      <button
        class="action-btn action-btn--primary"
        style="width: 200px; font-size: 16px; padding: 12px;"
        :disabled="isLocked"
        @click="handleStartGame"
      >
        {{ isLocked ? '启动中...' : '开始游戏' }}
      </button>
    </div>

    <!-- 等待状态提示（没有任何面板需要显示时，保持视觉锚点） -->
    <div v-if="isWaiting" class="panel-section" style="align-items: center; color: #888;">
      等待游戏进程推进...
    </div>

    <!-- 发言面板 -->
    <div v-if="showSpeechPanel" class="panel-section">
      <textarea
        v-model="speechContent"
        class="speech-input"
        placeholder="输入发言内容..."
        :disabled="isLocked"
        maxlength="2000"
        rows="3"
      />
      <button
        class="action-btn action-btn--primary"
        :disabled="isLocked || !speechContent.trim()"
        @click="handleSpeak"
      >
        {{ isLocked ? '提交中...' : '发言' }}
      </button>
    </div>

    <!-- 投票面板 -->
    <div v-if="showVotePanel" class="panel-section">
      <div class="vote-targets">
        <button
          v-for="player in alivePlayers"
          :key="player.player_id"
          class="action-btn"
          :class="{ 'action-btn--selected': voteTarget === player.player_id }"
          :disabled="isLocked"
          @click="handleVote(player.player_id)"
        >
          投 {{ player.name }}
        </button>
        <button
          class="action-btn action-btn--pass"
          :class="{ 'action-btn--selected': voteTarget === null }"
          :disabled="isLocked"
          @click="handleVote(null)"
        >
          弃权
        </button>
      </div>
    </div>

    <!-- 夜间技能面板 -->
    <div v-if="showActionPanel" class="panel-section">
      <div class="action-row">
        <select
          v-model="selectedAction"
          class="action-select"
          :disabled="isLocked"
        >
          <option
            v-for="act in availableActions"
            :key="act"
            :value="act"
          >
            {{ actionLabel(act) }}
          </option>
        </select>

        <select
          v-if="selectedAction !== ActionType.PASS"
          v-model="actionTarget"
          class="action-select"
          :disabled="isLocked"
        >
          <option value="" disabled>选择目标</option>
          <option
            v-for="player in alivePlayers"
            :key="player.player_id"
            :value="player.player_id"
          >
            {{ player.name }}
          </option>
        </select>

        <button
          class="action-btn action-btn--primary"
          :disabled="isLocked || (selectedAction !== ActionType.PASS && !actionTarget)"
          @click="handleAction"
        >
          {{ isLocked ? '执行中...' : '执行' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.action-panel {
  position: fixed;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  z-index: 150;
  width: 90%;
  max-width: 720px;
  background: rgba(10, 10, 30, 0.95);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-bottom: none;
  border-radius: 16px 16px 0 0;
  padding: 16px 20px;
  backdrop-filter: blur(12px);
}

.panel-error {
  background: rgba(244, 67, 54, 0.2);
  border: 1px solid rgba(244, 67, 54, 0.4);
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 12px;
  color: #ff8a80;
  font-size: 13px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.dismiss-btn {
  background: none;
  border: none;
  color: #ff8a80;
  cursor: pointer;
  font-size: 14px;
}

.panel-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.speech-input {
  width: 100%;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 8px;
  color: #e0e0e0;
  padding: 10px 12px;
  font-size: 14px;
  resize: vertical;
  min-height: 60px;
}

.speech-input:disabled {
  opacity: 0.5;
}

.vote-targets {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.action-row {
  display: flex;
  gap: 10px;
  align-items: center;
}

.action-select {
  flex: 1;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 8px;
  color: #e0e0e0;
  padding: 8px 12px;
  font-size: 14px;
}

.action-btn {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 8px;
  color: #ccc;
  padding: 8px 16px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}

.action-btn:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.15);
}

.action-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.action-btn--primary {
  background: rgba(255, 215, 0, 0.15);
  border-color: rgba(255, 215, 0, 0.3);
  color: #ffd700;
}

.action-btn--pass {
  border-style: dashed;
}

.action-btn--selected {
  border-color: #ffd700;
  box-shadow: 0 0 8px rgba(255, 215, 0, 0.3);
}
</style>
