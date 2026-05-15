<script setup lang="ts">
/**
 * 对局准备界面 —— 座位表配置。
 *
 * **Why**: 创建对局后先进入座位表，用户为每个座位选择或创建 AI 玩家，
 * 确认后再分配角色并启动游戏。座位卡牌初始均为问号背面。
 *
 * 参考: [`docs/plan/前端界面设计方案.md`](../../../docs/plan/前端界面设计方案.md)
 */

import { ref, onMounted } from 'vue'
import * as aiPlayersApi from '../api/ai_players'
import type { AIProfileResponse } from '../api/ai_players'

const emit = defineEmits<{
  (e: 'startGame', gameId: string): void
  (e: 'back'): void
}>()

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

/** 当前展开的座位索引（-1 表示无展开） */
const expandedSeat = ref<number>(-1)

/** 创建新 AI 玩家的弹出框状态 */
const showCreateForm = ref(false)

/** 新建 AI 玩家的表单数据 */
const newPlayerForm = ref({
  name: '',
  model_name: 'deepseek-v4-flash',
  model_provider: 'openai',
  temperature: 0.7,
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
  expandedSeat.value = -1
}

/** 创建新 AI 玩家并自动分配给当前座位 */
async function handleCreateNewPlayer(): Promise<void> {
  if (!newPlayerForm.value.name.trim()) return

  try {
    const profile = await aiPlayersApi.createAiPlayer({
      name: newPlayerForm.value.name.trim(),
      model_name: newPlayerForm.value.model_name,
      model_provider: newPlayerForm.value.model_provider,
      temperature: newPlayerForm.value.temperature,
      system_prompt: newPlayerForm.value.system_prompt || undefined,
    })
    // 添加到玩家库
    aiPlayerPool.value.push(profile)
    // 如果当前有展开的座位，自动分配
    if (expandedSeat.value >= 0) {
      selectForSeat(expandedSeat.value, profile.id)
    }
    showCreateForm.value = false
    newPlayerForm.value = {
      name: '',
      model_name: 'deepseek-v4-flash',
      model_provider: 'openai',
      temperature: 0.7,
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
        :class="{ 'seat-expanded': expandedSeat === index }"
      >
        <!-- 卡牌正面（选择后显示玩家名） -->
        <div
          v-if="seatAssignments[index]"
          class="seat-face"
          @click="expandedSeat = expandedSeat === index ? -1 : index"
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
          @click="expandedSeat = expandedSeat === index ? -1 : index"
        >
          <div class="seat-number">#{{ index + 1 }}</div>
          <div class="card-back card-back--empty">
            <span class="card-icon">❓</span>
          </div>
          <div class="seat-player-name seat-player-name--empty">未分配</div>
        </div>

        <!-- 展开的选择面板 -->
        <div v-if="expandedSeat === index" class="seat-picker">
          <div class="picker-header">
            <span>选择 座位 #{{ index + 1 }} 的 AI 玩家</span>
            <button class="close-btn" @click="expandedSeat = -1">✕</button>
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
              :class="{ 'picker-item--selected': seatAssignments[index] === player.id }"
              @click="selectForSeat(index, player.id)"
            >
              <div class="picker-item-name">{{ player.name }}</div>
              <div class="picker-item-model">{{ player.model_name }}</div>
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
            <input
              v-model="newPlayerForm.model_name"
              class="form-input"
              placeholder="模型名称"
            />
            <div class="form-row">
              <input
                v-model="newPlayerForm.model_provider"
                class="form-input form-input--small"
                placeholder="提供商"
              />
              <input
                v-model.number="newPlayerForm.temperature"
                class="form-input form-input--small"
                placeholder="温度"
                type="number"
                step="0.1"
                min="0"
                max="2"
              />
            </div>
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
    </div>

    <!-- 操作栏 -->
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
  color: #666;
  margin-bottom: 8px;
}

.card-back {
  width: 64px;
  height: 88px;
  margin: 0 auto 8px;
  background: linear-gradient(135deg, #2a1a3e, #1a1a3e);
  border: 2px solid rgba(255, 255, 255, 0.2);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.card-back--empty {
  background: linear-gradient(135deg, #1a1a2e, #2a1a1a);
  border-color: rgba(255, 255, 255, 0.1);
}

.card-icon {
  font-size: 28px;
  opacity: 0.6;
}

.seat-player-name {
  font-size: 12px;
  color: #e0e0e0;
  word-break: break-all;
}

.seat-player-name--empty {
  color: #555;
  font-style: italic;
}

.seat-picker {
  border-top: 1px solid rgba(255, 255, 255, 0.1);
  padding: 12px;
  max-height: 280px;
  overflow-y: auto;
}

.picker-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
  font-size: 12px;
  color: #aaa;
}

.close-btn {
  background: none;
  border: none;
  color: #888;
  cursor: pointer;
  font-size: 16px;
}

.picker-loading,
.picker-empty {
  text-align: center;
  color: #666;
  padding: 20px;
  font-size: 13px;
}

.picker-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 160px;
  overflow-y: auto;
}

.picker-item {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  padding: 8px 10px;
  cursor: pointer;
  transition: background 0.2s;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.picker-item:hover {
  background: rgba(255, 255, 255, 0.1);
}

.picker-item--selected {
  border-color: #ffd700;
  background: rgba(255, 215, 0, 0.1);
}

.picker-item-name {
  font-size: 13px;
  color: #e0e0e0;
  font-weight: 600;
}

.picker-item-model {
  font-size: 11px;
  color: #888;
}

.picker-item-stats {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.stat-badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 4px;
  color: #ccc;
}

.stat-games {
  background: rgba(100, 100, 255, 0.2);
}

.stat-winrate {
  background: rgba(255, 215, 0, 0.2);
  color: #ffd700;
}

.stat-detail {
  background: rgba(100, 200, 100, 0.15);
  color: #8c8;
}

.create-btn {
  width: 100%;
  margin-top: 10px;
  padding: 8px;
  background: rgba(255, 215, 0, 0.1);
  border: 1px dashed rgba(255, 215, 0, 0.3);
  color: #ffd700;
  border-radius: 8px;
  cursor: pointer;
  font-size: 12px;
  transition: background 0.2s;
}

.create-btn:hover {
  background: rgba(255, 215, 0, 0.2);
}

.create-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
  padding: 12px;
  background: rgba(0, 0, 0, 0.3);
  border-radius: 8px;
}

.form-input {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: #e0e0e0;
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 12px;
  outline: none;
}

.form-input:focus {
  border-color: #ffd700;
}

.form-input--small {
  flex: 1;
}

.form-row {
  display: flex;
  gap: 8px;
}

.form-textarea {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: #e0e0e0;
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 12px;
  resize: vertical;
  outline: none;
}

.form-textarea:focus {
  border-color: #ffd700;
}

.confirm-btn {
  padding: 8px;
  background: #ffd700;
  border: none;
  color: #111;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
}

.confirm-btn:hover {
  background: #ffed4a;
}

.setup-actions {
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

.error-msg {
  color: #ff6b6b;
  font-size: 13px;
  text-align: center;
}

.btn {
  padding: 10px 24px;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
  transition: all 0.2s;
}

.btn--primary {
  background: #ffd700;
  color: #111;
}

.btn--primary:hover {
  background: #ffed4a;
}

.btn--primary:disabled {
  background: #555;
  color: #888;
  cursor: not-allowed;
}

.btn--large {
  padding: 14px 48px;
  font-size: 16px;
}
</style>
