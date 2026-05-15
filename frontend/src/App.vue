<script setup lang="ts">
/**
 * 根组件 —— 管理和切换对局大厅 / 游戏主界面两个视图。
 *
 * **Why**: 不使用 vue-router（单页应用仅两个视图），
 * 通过 `currentView` 响应式变量切换 GameLobby 和 GameBoard。
 * 进入对局时将 gameId 通过 prop 传递给 GameBoard。
 */

import { ref } from 'vue'
import GameLobby from './views/GameLobby.vue'
import GameBoard from './views/GameBoard.vue'
import ModelManagementView from './views/ModelManagementView.vue'
import MatchReportView from './views/MatchReportView.vue'
import ReplayView from './views/ReplayView.vue'

/** 当前视图: 'lobby' | 'game' | 'models' | 'report' | 'replay' */
const currentView = ref<'lobby' | 'game' | 'models' | 'report' | 'replay'>('lobby')

/** 当前进入的对局 ID */
const activeGameId = ref<string>('')

/** 从大厅进入对局 */
function handleEnterGame(gameId: string): void {
  activeGameId.value = gameId
  currentView.value = 'game'
}

/** 查看复盘报告 */
function handleViewReport(gameId: string): void {
  activeGameId.value = gameId
  currentView.value = 'report'
}

/** 查看对局回放 */
function handleViewReplay(gameId: string): void {
  activeGameId.value = gameId
  currentView.value = 'replay'
}

/** 返回大厅 */
function handleBackToLobby(): void {
  currentView.value = 'lobby'
  activeGameId.value = ''
}
</script>

<template>
  <div v-if="currentView === 'lobby' || currentView === 'models'" class="absolute top-4 right-4 z-50 flex gap-2">
    <button
      v-if="currentView === 'lobby'"
      @click="currentView = 'models'"
      class="px-4 py-2 bg-gray-800 text-white rounded-md hover:bg-gray-700 text-sm"
    >
      模型管理
    </button>
    <button
      v-if="currentView === 'models'"
      @click="currentView = 'lobby'"
      class="px-4 py-2 bg-gray-800 text-white rounded-md hover:bg-gray-700 text-sm"
    >
      返回大厅
    </button>
  </div>

  <GameLobby
    v-if="currentView === 'lobby'"
    @enter-game="handleEnterGame"
    @view-report="handleViewReport"
    @view-replay="handleViewReplay"
  />
  <GameBoard
    v-else-if="currentView === 'game'"
    :game-id="activeGameId"
    @leave="handleBackToLobby"
  />
  <ModelManagementView
    v-else-if="currentView === 'models'"
  />
  <MatchReportView
    v-else-if="currentView === 'report'"
    :game-id="activeGameId"
    @back="handleBackToLobby"
  />
  <ReplayView
    v-else-if="currentView === 'replay'"
    :game-id="activeGameId"
    @back="handleBackToLobby"
  />
</template>

<style>
/* 全局重置 */
body {
  margin: 0;
  font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
  background-color: #111;
  color: #e0e0e0;
  overflow: hidden;
}

/* 通用滚动条样式 */
::-webkit-scrollbar {
  width: 6px;
}
::-webkit-scrollbar-track {
  background: rgba(255, 255, 255, 0.05);
}
::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.15);
  border-radius: 3px;
}
</style>
