<script setup lang="ts">
/**
 * WebSocket 连接状态指示灯。
 *
 * **Why**: 设计中要求角落放置连接状态指示灯，
 * 绿灯表示 WebSocket 已连接，红灯表示断开。
 * 悬浮在页面右下角，不干扰主界面。
 */

import { computed } from 'vue'
import { useGameStore } from '../store/game'

const store = useGameStore()

const statusColor = computed(() =>
  store.wsConnected ? '#4caf50' : '#f44336'
)

const statusText = computed(() =>
  store.wsConnected ? '已连接' : '未连接'
)
</script>

<template>
  <div class="connection-indicator" :title="statusText">
    <span class="dot" :style="{ backgroundColor: statusColor }" />
    <span class="label">{{ statusText }}</span>
  </div>
</template>

<style scoped>
.connection-indicator {
  position: fixed;
  bottom: 16px;
  right: 16px;
  z-index: 100;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  border-radius: 20px;
  background: rgba(0, 0, 0, 0.7);
  backdrop-filter: blur(8px);
  font-size: 13px;
  color: #ccc;
  user-select: none;
}

.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  display: inline-block;
  box-shadow: 0 0 6px currentColor;
  transition: background-color 0.3s ease;
}

.label {
  white-space: nowrap;
}
</style>
