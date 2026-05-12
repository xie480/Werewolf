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

/** 当前视图: 'lobby' | 'game' */
const currentView = ref<'lobby' | 'game'>('lobby')

/** 当前进入的对局 ID */
const activeGameId = ref<string>('')

/** 从大厅进入对局 */
function handleEnterGame(gameId: string): void {
  activeGameId.value = gameId
  currentView.value = 'game'
}

/** 从对局返回大厅 */
function handleLeaveGame(): void {
  currentView.value = 'lobby'
  activeGameId.value = ''
}
</script>

<template>
  <GameLobby
    v-if="currentView === 'lobby'"
    @enter-game="handleEnterGame"
  />
  <GameBoard
    v-else
    :game-id="activeGameId"
    @leave="handleLeaveGame"
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
