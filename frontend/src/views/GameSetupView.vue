<script setup lang="ts">
/**
 * 对局准备界面 —— 座位表配置。
 *
 * **Why**: 创建对局后先进入座位表，用户为每个座位选择或创建 AI 玩家，
 * 确认后再分配角色并启动游戏。座位卡牌初始均为问号背面。
 *
 * **修改说明**: 新建 AI 玩家时使用 model_id 下拉框选择模型，
 * 替代旧的 model_name / model_provider / temperature 手动输入。
 *
 * 参考: [`docs/plan/前端界面设计方案.md`](../../../docs/plan/前端界面设计方案.md)
 */

import { ref, onMounted } from 'vue'
import * as aiPlayersApi from '../api/ai_players'
import type { AIProfileResponse } from '../api/ai_players'
import { useModelStore } from '../store/models'

const emit = defineEmits<{
  (e: 'startGame', gameId: string): void
  (e: 'back'): void
}>()

const modelStore = useModelStore()

/** 座位数量（默认 9 人局） */
const seatCount = ref(9)

/** 每个座位选择的 AI 玩家档案 ID (null 表示未选择) */
const seatAssignments = ref<(string | null)[]>(
  Array.from({ length: seatCount.value }, () => null)
)

/** 所有可供选择的 AI 玩家档案库 */
const aiPlayerPool = ref<AIProfileResponse[]>([])

/** 是否正在加载 AI 玩家列表 */
const loading = ref(false)

/** 是否正在创建对局 */
const creating = ref(false)

/** 错误消息 */
const errorMsg = ref<string | null>(null)

/** 成功创建后的 game_id */
const createdGameId = ref<string | null>(null)

/** 当前选中的座位索引（-1 表示未选中） */
const selectedSeatIndex = ref<number>(-1)
/** 控制弹窗显示 */
const showModal = ref<boolean>(false)

/** 创建新 AI 玩家的弹出框状态 */
const showCreateForm = ref(false)

/** 新建 AI 玩家的表单数据 */
const newPlayerForm = ref({
  name: '',
  model_id: '',
  system_prompt: '',
})

/** 加载 AI 玩家库 */
async function loadAiPlayers(): Promise<void> {
  loading.value = true
  try {
    const result = await aiPlayersApi.listAiPlayers(true)
    aiPlayerPool.value = result.players
  } catch (e) {
    console.error('加载 AI 玩家列表失败', e)
    // 静默失败，空列表也可以操作
  } finally {
    loading.value = false
  }
}

/** 为座位选择 AI 玩家 */
function selectForSeat(seatIndex: number, profileId: string | null): void {
  const newAssignments = [...seatAssignments.value]
  newAssignments[seatIndex] = profileId
  seatAssignments.value = newAssignments
  // 关闭弹窗并重置选中座位
  showModal.value = false
  selectedSeatIndex.value = -1
}

/** 创建新 AI 玩家并自动分配给当前座位 */
async function handleCreateNewPlayer(): Promise<void> {
  if (!newPlayerForm.value.name.trim()) return
  if (!newPlayerForm.value.model_id) {
    errorMsg.value = '请选择一个模型'
    return
  }

  try {
    const profile = await aiPlayersApi.createAiPlayer({
      name: newPlayerForm.value.name.trim(),
      model_id: newPlayerForm.value.model_id,
      system_prompt: newPlayerForm.value.system_prompt || undefined,
    })
    // 添加到玩家库
    aiPlayerPool.value.push(profile)
    // 如果当前有选中的座位，自动分配
    if (selectedSeatIndex.value >= 0) {
      selectForSeat(selectedSeatIndex.value, profile.id)
    }
    showCreateForm.value = false
    newPlayerForm.value = {
      name: '',
      model_id: '',
      system_prompt: '',
    }
  } catch (e) {
    errorMsg.value = `创建 AI 玩家失败: ${(e as Error).message}`
  }
}

/** 创建对局并启动 */
async function handleStartGame(): Promise<void> {
  creating.value = true
  errorMsg.value = null

  try {
    // 构建玩家配置
    const players: { type: string; player_id?: string; config?: Record<string, unknown> }[] = []
    for (let i = 0; i < seatCount.value; i++) {
      const profileId = seatAssignments.value[i]
      if (profileId) {
        players.push({ type: 'existing', player_id: profileId })
      } else {
        // 未选择 AI 玩家的座位，使用动态创建（默认配置）
        players.push({
          type: 'dynamic',
          config: { model_name: 'default_model' },
        })
      }
    }

    // 引入 API 并创建对局
    const { createGame } = await import('../api/games')
    const { startGame } = await import('../api/games')

    const result = await createGame({
      player_count: seatCount.value,
      players: players,
    })

    createdGameId.value = result.game_id

    // 启动对局
    await startGame(result.game_id)

    emit('startGame', result.game_id)
  } catch (e) {
    errorMsg.value = `启动对局失败: ${(e as Error).message}`
    createdGameId.value = null
  } finally {
    creating.value = false
  }
}

/** 格式化胜率显示 */
function formatWinRate(stats: AIProfileResponse['stats']): string {
  if (!stats || stats.total_games === 0) return '未对战'
  return `${(stats.win_rate * 100).toFixed(0)}%`
}

/** 格式化统计数据显示 */
function formatStats(stats: AIProfileResponse['stats']): string {
  if (!stats || stats.total_games === 0) return '暂无数据'
  return `${stats.total_games}局 ${stats.wins}胜 ${stats.losses}败`
}

onMounted(() => {
  loadAiPlayers()
  // 加载模型列表供下拉选择
  if (modelStore.models.length === 0) {
    modelStore.fetchModels()
  }
})
</script>

<template>
  <div class="setup">
    <!-- 背景 -->
    <div class="setup-bg" />

    <!-- 标题 -->
    <div class="setup-header">
      <button class="back-btn" @click="emit('back')">← 返回</button>
      <h1 class="setup-title">🐺 对局准备</h1>
      <p class="setup-subtitle">为每个座位选择 AI 玩家，卡牌将在开始游戏后揭示身份</p>
    </div>

    <!-- 座位表 -->
    <div class="seat-grid">
      <div
        v-for="(_, index) in seatCount"
        :key="index"
        class="seat-card"
        :class="{ 'seat-expanded': selectedSeatIndex === index }"
      >
        <!-- 卡牌正面（选择后显示玩家名） -->
        <div
          v-if="seatAssignments[index]"
          class="seat-face"
          @click="selectedSeatIndex = index; showModal = true"
        >
          <div class="seat-number">#{{ index + 1 }}</div>
          <div class="card-back">
            <span class="card-icon">❓</span>
          </div>
          <div class="seat-player-name">
            {{ aiPlayerPool.find(p => p.id === seatAssignments[index])?.name || 'AI 玩家' }}
          </div>
        </div>

        <!-- 卡牌背面（未选择时） -->
        <div
          v-else
          class="seat-face"
          @click="selectedSeatIndex = index; showModal = true"
        >
          <div class="seat-number">#{{ index + 1 }}</div>
          <div class="card-back card-back--empty">
            <span class="card-icon">❓</span>
          </div>
          <div class="seat-player-name seat-player-name--empty">未分配</div>
        </div>

      </div>
    </div>

    <!-- 操作栏 -->
    <div v-if="showModal" class="modal-overlay" @click.self="showModal = false">
      <div class="modal-content">
        <div class="picker-header">
          <span>选择 座位 #{{ selectedSeatIndex + 1 }} 的 AI 玩家</span>
          <button class="close-btn" @click="showModal = false">✕</button>
        </div>

        <!-- 已有玩家列表 -->
        <div v-if="loading" class="picker-loading">加载中...</div>
        <div v-else-if="aiPlayerPool.length === 0" class="picker-empty">
          暂无 AI 玩家，请先创建一个
        </div>
        <div v-else class="picker-list">
          <div
            v-for="player in aiPlayerPool"
            :key="player.id"
            class="picker-item"
            :class="{ 'picker-item--selected': seatAssignments[selectedSeatIndex] === player.id }"
            @click="selectForSeat(selectedSeatIndex, player.id)"
          >
            <div class="picker-item-name">{{ player.name }}</div>
              <div class="picker-item-model">{{ modelStore.models.find(m => m.id === player.model_id)?.model_name || player.model_id || '未绑定模型' }}</div>
            <div class="picker-item-stats">
              <span class="stat-badge stat-games">{{ player.stats?.total_games ?? 0 }}局</span>
              <span class="stat-badge stat-winrate">{{ formatWinRate(player.stats) }}</span>
              <span class="stat-badge stat-detail">{{ formatStats(player.stats) }}</span>
            </div>
          </div>
        </div>

        <!-- 创建新玩家按钮 -->
        <button
          class="create-btn"
          @click="showCreateForm = !showCreateForm"
        >
          {{ showCreateForm ? '取消' : '+ 创建新 AI 玩家' }}
        </button>

        <!-- 创建新玩家表单 -->
        <div v-if="showCreateForm" class="create-form">
          <input
            v-model="newPlayerForm.name"
            class="form-input"
            placeholder="玩家名称（必填）"
          />
          <select
            v-model="newPlayerForm.model_id"
            class="form-select"
            required
          >
            <option value="" disabled>选择绑定的模型</option>
            <option
              v-for="m in modelStore.models"
              :key="m.id"
              :value="m.id"
            >
              {{ m.model_name }} ({{ m.provider }})
            </option>
          </select>
          <p v-if="modelStore.models.length === 0" class="form-hint">
            暂无可用模型，请先在模型管理中添加模型
          </p>
          <textarea
            v-model="newPlayerForm.system_prompt"
            class="form-textarea"
            placeholder="性格 Prompt（可选）"
            rows="2"
          />
          <button class="confirm-btn" @click="handleCreateNewPlayer">
            创建并选择
          </button>
        </div>
      </div>
    </div>
    <div class="setup-actions">
      <div v-if="errorMsg" class="error-msg">{{ errorMsg }}</div>
      <button
        class="btn btn--primary btn--large"
        :disabled="creating"
        @click="handleStartGame"
      >
        {{ creating ? '创建中...' : '开始游戏' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.setup {
  position: relative;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 20px;
  z-index: 1;
}

.setup-bg {
  position: fixed;
  inset: 0;
  z-index: 0;
  background: url('/background-day.png') center/cover no-repeat;
  filter: blur(10px) brightness(0.8);
}

.setup-header {
  text-align: center;
  margin-bottom: 24px;
  z-index: 1;
  position: relative;
}

.back-btn {
  position: absolute;
  left: -120px;
  top: 50%;
  transform: translateY(-50%);
  background: rgba(255,255,255,0.1);
  border: 1px solid rgba(255,255,255,0.2);
  color: #ccc;
  padding: 6px 16px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
}

.back-btn:hover {
  background: rgba(255,255,255,0.2);
}

.setup-title {
  font-size: 28px;
  color: #ffd700;
  margin: 0 0 6px;
  text-shadow: 0 0 20px rgba(255, 215, 0, 0.3);
}

.setup-subtitle {
  color: #888;
  font-size: 13px;
  margin: 0;
}

.seat-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  max-width: 800px;
  width: 100%;
  z-index: 1;
  margin-bottom: 24px;
}

.seat-card {
  background: rgba(10, 10, 30, 0.85);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 12px;
  overflow: hidden;
  transition: border-color 0.2s;
}

.seat-card:hover {
  border-color: rgba(255, 215, 0, 0.3);
}

.seat-expanded {
  border-color: #ffd700;
}

.seat-face {
  padding: 16px;
  cursor: pointer;
  text-align: center;
}

.seat-number {
  font-size: 12px;
  color: #888;
  margin-bottom: 4px;
}

.card-back {
  width: 64px;
  height: 80px;
  margin: 0 auto 8px;
  background: linear-gradient(135deg, #1a1a4e, #2d2d6b);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2px solid #ffd700;
}

.card-back--empty {
  background: linear-gradient(135deg, #2a2a2a, #3a3a3a);
  border-color: #555;
}

.card-icon {
  font-size: 28px;
}

.seat-player-name {
  font-size: 13px;
  color: #ddd;
  font-weight: 500;
}

.seat-player-name--empty {
  color: #666;
}

/* 选择面板 */
.seat-picker {
  background: #1a1a2e;
  padding: 12px;
  border-top: 1px solid rgba(255,255,255,0.1);
}

.picker-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: #aaa;
  margin-bottom: 8px;
}

.close-btn {
  background: none;
  border: none;
  color: #888;
  cursor: pointer;
  font-size: 14px;
}

.picker-loading,
.picker-empty {
  font-size: 12px;
  color: #666;
  text-align: center;
  padding: 12px 0;
}

.picker-list {
  max-height: 180px;
  overflow-y: auto;
}

.picker-item {
  padding: 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
  margin-bottom: 4px;
}

.picker-item:hover {
  background: rgba(255,255,255,0.08);
}

.picker-item--selected {
  background: rgba(255, 215, 0, 0.15);
  border: 1px solid rgba(255, 215, 0, 0.3);
}

.picker-item-name {
  font-size: 13px;
  color: #ddd;
  font-weight: 500;
}

.picker-item-model {
  font-size: 11px;
  color: #888;
  margin-top: 2px;
}

.picker-item-stats {
  display: flex;
  gap: 6px;
  margin-top: 4px;
}

.stat-badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 4px;
}

.stat-games {
  background: rgba(100, 149, 237, 0.2);
  color: #88b0ff;
}

.stat-winrate {
  background: rgba(255, 215, 0, 0.2);
  color: #ffd700;
}

.stat-detail {
  background: rgba(100, 200, 100, 0.2);
  color: #8f8;
}

/* 创建表单 */
.create-btn {
  width: 100%;
  padding: 8px;
  margin-top: 8px;
  background: rgba(255, 215, 0, 0.1);
  border: 1px dashed rgba(255, 215, 0, 0.4);
  color: #ffd700;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
}

.create-btn:hover {
  background: rgba(255, 215, 0, 0.2);
}

.create-form {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-input,
.form-select,
.form-textarea {
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(255,255,255,0.15);
  color: #ddd;
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 12px;
  outline: none;
}

.form-select {
  cursor: pointer;
}

.form-select option {
  background: #1a1a2e;
  color: #ddd;
}

.form-hint {
  font-size: 11px;
  color: #ff6b6b;
  margin: 0;
}

.form-input:focus,
.form-select:focus,
.form-textarea:focus {
  border-color: #ffd700;
}

.form-textarea {
  resize: vertical;
  min-height: 40px;
}

.confirm-btn {
  padding: 6px;
  background: #ffd700;
  color: #1a1a2e;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
}

.confirm-btn:hover {
  background: #ffe44d;
}

/* 操作栏 */
.setup-actions {
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.error-msg {
  color: #ff6b6b;
  font-size: 13px;
}

.btn {
  padding: 12px 48px;
  border: none;
  border-radius: 12px;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s, transform 0.1s;
}

.btn:active {
  transform: scale(0.97);
}

.btn--primary {
  background: linear-gradient(135deg, #ffd700, #ffaa00);
  color: #1a1a2e;
}

.btn--primary:hover {
  background: linear-gradient(135deg, #ffe44d, #ffbb33);
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.modal-content {
  background: #1a1a2e;
  padding: 20px;
  border-radius: 12px;
  max-width: 500px;
  width: 90%;
  box-shadow: 0 4px 12px rgba(0,0,0,0.3);
}
</style>
