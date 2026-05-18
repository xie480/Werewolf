<script setup lang="ts">
/**
 * 昼夜背景层 —— 根据游戏阶段自动切换背景图。
 *
 * **Why**: 前端设计中要求白天/黑夜阶段使用不同的环境背景图，
 * 通过 CSS transition 实现 2 秒平滑渐变切换，增强沉浸感。
 *
 * 白天阶段 (DAY_*) → background-day.png (light)
 * 夜晚阶段 (NIGHT_*) → background-night.jpg (night)
 * 其他阶段 → 默认暗色背景
 */

import { computed } from 'vue'
import { useGameStore } from '../store/game'

const store = useGameStore()

/** 根据当前阶段计算背景图 CSS */
const backgroundStyle = computed(() => {
  const phase = store.phase
  if (!phase) {
    return {
      backgroundImage: 'none',
      backgroundColor: '#111',
    }
  }

  if (phase.startsWith('NIGHT_')) {
    return {
      backgroundImage: 'url(/background-night.jpg)',
      backgroundColor: '#0a0a1a',
    }
  }

  if (phase.startsWith('DAY_') || phase === 'HUNTER_SHOOT') {
    return {
      backgroundImage: 'url(/background-day.png)',
      backgroundColor: '#1a1a2e',
    }
  }

  // 默认（INIT / GAME_OVER 等）
  return {
    backgroundImage: 'none',
    backgroundColor: '#111',
  }
})

/** 当前阶段对应的亮度叠加 */
const overlayStyle = computed(() => {
  const phase = store.phase
  if (!phase) return { opacity: 0 }

  if (phase.startsWith('NIGHT_')) {
    // 黑夜：暗色蒙版 60%
    return { opacity: 0.6, backgroundColor: 'rgba(0,0,0,0.6)' }
  }

  if (phase.startsWith('DAY_') || phase === 'HUNTER_SHOOT') {
    // 白天：微弱暖光
    return { opacity: 0.15, backgroundColor: 'rgba(255,200,100,0.1)' }
  }

  return { opacity: 0 }
})
</script>

<template>
  <div class="background-layer" :style="backgroundStyle">
    <!-- 阶段氛围叠加层 -->
    <div class="phase-overlay" :style="overlayStyle" />
  </div>
</template>

<style scoped>
.background-layer {
  position: fixed;
  inset: 0;
  z-index: 0;
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
  /* 2 秒平滑过渡 */
  transition:
    background-image 2s ease,
    background-color 2s ease;
}

.phase-overlay {
  position: absolute;
  inset: 0;
  transition: opacity 2s ease, background-color 2s ease;
  pointer-events: none;
}
</style>
