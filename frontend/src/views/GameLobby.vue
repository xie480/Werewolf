<script setup lang="ts">
/**
 * 对局大厅页面 —— 创建/加入对局、对局列表、控制台。
 *
 * **Why**: 游戏中界面设计的第一部分就是初始界面 (Lobby)，
 * 需要体现科技感与等待感。提供创建对局、加入已有对局、
 * 查看对局列表和 WebSocket 连接状态的功能。
 *
 * 参考: [`docs/plan/前端界面设计方案.md`](../../../docs/plan/前端界面设计方案.md)
 */

import { ref, onMounted } from 'vue'
import { useGameStore } from '../store/game'
import { GameStatus } from '../types/enums'

const emit = defineEmits<{
  (e: 'enterGame', gameId: string): void
  (e: 'viewReport', gameId: string): void
  (e: 'viewReplay', gameId: string): void
}>()

const store = useGameStore()

/** 大厅状态 */
const lobbyState = ref<'idle' | 'creating' | 'joining'>('idle')

/** 加入对局输入框 */
const joinGameId = ref('')

/** 查看复盘输入框 */
const reportGameId = ref('')

/** 对局列表 */
interface GameListItem {
  game_id: string
  status: string
  player_count: number
}
const gameList = ref<GameListItem[]>([])

/** 加载对局列表 */
async function loadGameList(): Promise<void> {
  try {
    const { listGames } = await import('../api/games')
    const result = await listGames()
    gameList.value = result.games.map(g => ({
      game_id: g.game_id,
      status: g.status,
      player_count: g.player_count,
    }))
  } catch {
    // 静默失败——大厅页面不强制要求列表可用
  }
}

/** 创建并进入对局 */
async function handleCreate(): Promise<void> {
  lobbyState.value = 'creating'
  try {
    const gameId = await store.createAndStart(9)
    emit('enterGame', gameId)
  } finally {
    lobbyState.value = 'idle'
  }
}

/** 加入已有对局 */
async function handleJoin(): Promise<void> {
  if (!joinGameId.value.trim()) return
  lobbyState.value = 'joining'
  try {
    await store.joinGame(joinGameId.value.trim())
    emit('enterGame', joinGameId.value.trim())
  } finally {
    lobbyState.value = 'idle'
  }
}

/** 进入已有对局（从列表） */
function handleEnterGame(gameId: string): void {
  emit('enterGame', gameId)
}

/** 查看复盘报告 */
function handleViewReportInput(): void {
  if (!reportGameId.value.trim()) return
  emit('viewReport', reportGameId.value.trim())
}

function handleViewReport(gameId: string): void {
  emit('viewReport', gameId)
}

/** 查看对局回放 */
function handleViewReplayInput(): void {
  if (!reportGameId.value.trim()) return
  emit('viewReplay', reportGameId.value.trim())
}

function handleViewReplay(gameId: string): void {
  emit('viewReplay', gameId)
}

onMounted(() => {
  loadGameList()
})
</script>

<template>
  <div class="lobby">
    <!-- 背景 -->
    <div class="lobby-bg" />

    <!-- 标题 -->
    <div class="lobby-header">
      <h1 class="lobby-title">🐺 狼人杀 · AI 多智能体</h1>
      <p class="lobby-subtitle">基于 LangGraph 的实时多智能体博弈平台</p>
    </div>

    <!-- 主操作区 -->
    <div class="lobby-main">
      <!-- 创建对局 -->
      <div class="lobby-card">
        <h2>创建对局</h2>
        <p class="card-desc">创建标准 9 人局（3狼/3民/预/女/猎），AI 自动分配角色。</p>
        <button
          class="btn btn--primary"
          :disabled="lobbyState !== 'idle'"
          @click="handleCreate"
        >
          {{ lobbyState === 'creating' ? '创建中...' : '创建并进入' }}
        </button>
      </div>

      <!-- 加入对局 -->
      <div class="lobby-card">
        <h2>加入对局</h2>
        <p class="card-desc">输入已有对局 ID 加入。</p>
        <div class="join-row">
          <input
            v-model="joinGameId"
            class="join-input"
            placeholder="输入对局 ID"
            :disabled="lobbyState !== 'idle'"
          />
          <button
            class="btn"
            :disabled="lobbyState !== 'idle' || !joinGameId.trim()"
            @click="handleJoin"
          >
            {{ lobbyState === 'joining' ? '加入中...' : '加入' }}
          </button>
        </div>
      </div>

      <!-- 查看复盘与回放 -->
      <div class="lobby-card">
        <h2>查看复盘与回放</h2>
        <p class="card-desc">输入已结束的对局 ID 查看五维评分或对局回放。</p>
        <div class="join-row">
          <input
            v-model="reportGameId"
            class="join-input"
            placeholder="输入对局 ID"
            :disabled="lobbyState !== 'idle'"
          />
          <button
            class="btn"
            :disabled="lobbyState !== 'idle' || !reportGameId.trim()"
            @click="handleViewReportInput"
          >
            报告
          </button>
          <button
            class="btn"
            :disabled="lobbyState !== 'idle' || !reportGameId.trim()"
            @click="handleViewReplayInput"
          >
            回放
          </button>
        </div>
      </div>
    </div>

    <!-- 对局列表 -->
    <div v-if="gameList.length > 0" class="lobby-games">
      <h3>活跃对局</h3>
      <div class="games-table">
        <div
          v-for="game in gameList"
          :key="game.game_id"
          class="game-row"
        >
          <span class="game-id">{{ game.game_id }}</span>
          <span class="game-status" :class="{ 'status-running': game.status === 'RUNNING' }">
            {{ game.status }}
          </span>
          <span class="game-count">{{ game.player_count }} 人</span>
          <button
            class="btn btn--small"
            @click="handleEnterGame(game.game_id)"
          >
            进入
          </button>
          <button
            v-if="game.status === 'FINISHED' || game.status === 'ABORTED'"
            class="btn btn--small"
            @click="handleViewReport(game.game_id)"
          >
            报告
          </button>
          <button
            v-if="game.status === 'FINISHED' || game.status === 'ABORTED'"
            class="btn btn--small"
            @click="handleViewReplay(game.game_id)"
          >
            回放
          </button>
        </div>
      </div>
    </div>

    <!-- 错误提示 -->
    <div v-if="store.error" class="lobby-error">
      {{ store.error }}
      <button class="dismiss-btn" @click="store.error = null">✕</button>
    </div>
  </div>
</template>

<style scoped>
.lobby {
  position: relative;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 40px 20px;
  z-index: 1;
}

.lobby-bg {
  position: fixed;
  inset: 0;
  z-index: 0;
  background: url('/background-day.png') center/cover no-repeat;
  filter: blur(10px) brightness(0.8);
}

.lobby-header {
  text-align: center;
  margin-bottom: 40px;
  z-index: 1;
}

.lobby-title {
  font-size: 36px;
  color: #ffd700;
  margin: 0 0 8px;
  text-shadow: 0 0 20px rgba(255, 215, 0, 0.3);
}

.lobby-subtitle {
  color: #888;
  font-size: 14px;
  margin: 0;
}

.lobby-main {
  display: flex;
  gap: 24px;
  z-index: 1;
  flex-wrap: wrap;
  justify-content: center;
}

.lobby-card {
  background: rgba(10, 10, 30, 0.85);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 16px;
  padding: 28px;
  width: 360px;
  backdrop-filter: blur(12px);
}

.lobby-card h2 {
  margin: 0 0 8px;
  color: #e0e0e0;
  font-size: 20px;
}

.card-desc {
  color: #888;
  font-size: 13px;
  margin: 0 0 20px;
}

.join-row {
  display: flex;
  gap: 10px;
}

.join-input {
  flex: 1;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 8px;
  color: #e0e0e0;
  padding: 10px 14px;
  font-size: 14px;
}

.join-input:disabled {
  opacity: 0.4;
}

.btn {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 8px;
  color: #ccc;
  padding: 10px 24px;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}

.btn:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.15);
}

.btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.btn--primary {
  background: rgba(255, 215, 0, 0.15);
  border-color: rgba(255, 215, 0, 0.3);
  color: #ffd700;
  width: 100%;
}

.btn--small {
  padding: 4px 16px;
  font-size: 12px;
}

/* 对局列表 */
.lobby-games {
  margin-top: 40px;
  width: 100%;
  max-width: 760px;
  z-index: 1;
}

.lobby-games h3 {
  color: #aaa;
  font-size: 14px;
  margin: 0 0 12px;
}

.games-table {
  background: rgba(10, 10, 30, 0.85);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 12px;
  overflow: hidden;
}

.game-row {
  display: flex;
  align-items: center;
  padding: 12px 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  gap: 16px;
}

.game-row:last-child {
  border-bottom: none;
}

.game-id {
  flex: 1;
  font-family: monospace;
  font-size: 13px;
  color: #aaa;
}

.game-status {
  font-size: 12px;
  color: #888;
  padding: 2px 8px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.05);
}

.status-running {
  color: #4caf50;
  background: rgba(76, 175, 80, 0.1);
}

.game-count {
  font-size: 12px;
  color: #888;
}

.lobby-error {
  position: fixed;
  bottom: 20px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(244, 67, 54, 0.2);
  border: 1px solid rgba(244, 67, 54, 0.4);
  border-radius: 8px;
  padding: 10px 20px;
  color: #ff8a80;
  font-size: 13px;
  z-index: 200;
  display: flex;
  gap: 16px;
  align-items: center;
}

.dismiss-btn {
  background: none;
  border: none;
  color: #ff8a80;
  cursor: pointer;
  font-size: 14px;
}
</style>
